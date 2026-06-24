"""
Fleet orchestrator for ccflet (the orchestration engine).

Responsibilities:
  - connection pool + factory (real paramiko, or the mock backend)
  - run a single action against one node/role, dispatched by kind
  - fan-out: any action across a selection, in parallel, per-node results
  - variant-aware ordered sequences: deploy / bring_up / tear_down (+ fleet variants)
  - a background status poller that builds NodeStatus → GATE map and emits it

Every action and its result is appended to the session's events.jsonl and
broadcast to operators (audit is the safety net). The serviceA-before-serviceB and
serviceC-before-serviceA (variant B) ordering invariants are enforced here in code.
"""

import os
import shlex
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import profiles as P
from .events import EventStream, EventType
from .result import CommandResult
from .supervisor import Supervisor
from . import transfer as T
from . import status as S
from . import sequences as SEQ
from . import gates_config as GC
from .gates_config import GateRegistry
from .state_monitor import ping_once

MAX_FANOUT = 10
MAX_GATE_WORKERS = 8
HEALTH_WAIT_TIMEOUT = 15.0
HEALTH_WAIT_POLL = 0.4


def _default_gates_dir() -> str:
    """The default profile's ``gates/`` dir (``yamls/default/gates/``), resolved from this
    file (cwd-independent) so an Orchestrator built without an explicit registry (e.g. in
    tests) still has gates."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "yamls", "default", "gates")


@dataclass
class ActionResult:
    node: str
    role: str
    action: str
    kind: str
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node": self.node, "role": self.role, "action": self.action,
            "kind": self.kind, "success": self.success, "exit_code": self.exit_code,
            "stdout": self.stdout, "stderr": self.stderr,
            "duration": round(self.duration, 3), "extra": self.extra,
        }


class ConnectionPool:
    """Pools one client per (node, role); built by an injected factory."""

    def __init__(self, factory: Callable[[str, str], Any]):
        self._factory = factory
        self._clients: Dict[tuple, Any] = {}
        self._lock = threading.Lock()

    def get(self, node: str, role: str):
        key = (node, role)
        with self._lock:
            if key not in self._clients:
                self._clients[key] = self._factory(node, role)
            return self._clients[key]

    def close_all(self):
        with self._lock:
            for c in self._clients.values():
                try:
                    c.disconnect()
                except Exception:
                    pass
            self._clients.clear()


class Orchestrator:
    def __init__(self, fleet, profile_mgr, client_factory,
                 event_stream: Optional[EventStream] = None,
                 sync_manager=None, dry_run: bool = False,
                 gates: Optional[GateRegistry] = None, allow_local: bool = True):
        self.fleet = fleet
        self.profiles = {"roleA": profile_mgr.load("roleA"),
                         "roleB": profile_mgr.load("roleB")}
        self.pool = ConnectionPool(client_factory)
        self.events = event_stream
        self.sync = sync_manager
        self.dry_run = dry_run
        # config-driven health gates (P8 — gates/*.yaml, see plan2.md). The orchestrator
        # holds the registry by reference so a Config-page edit (reload in place) takes
        # effect with no restart. Default to the repo gates/ dir when none is injected.
        self.gates = gates if gates is not None else GateRegistry(_default_gates_dir())
        # variant-aware ordered sequences come from the domain pack
        # (domain/sequences.yaml); their ordering invariants are checked here so a
        # mis-ordered spec fails fast rather than running a bad bring-up.
        self.sequences = SEQ.load()
        SEQ.validate(self.sequences)
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_stop = threading.Event()
        # change detection for the gate audit/broadcast — keyed on COLORS only, so a
        # metric's value jitter (same color) doesn't spam GATE_CHANGED.
        self._last_gate_colors: Dict[str, Dict[str, str]] = {}
        # per-gate cadence: when each (node, gate) last ran + its cached result, so a slow
        # gate honors its own `interval` instead of running every poll tick.
        self._gate_last_run: Dict[tuple, float] = {}
        self._gate_cache: Dict[tuple, Dict] = {}
        self.statuses: Dict[str, S.NodeStatus] = {}
        # guards the status dicts (poll thread writes, web thread reads)
        self._status_lock = threading.Lock()
        # one lock per node so a node can't run two sequences at once
        self._node_locks: Dict[str, threading.Lock] = {
            n.name: threading.Lock() for n in fleet.nodes
        }
        # operator command catalog (D8) — wired by configure_commands()
        self.commands = None
        self.commands_dir = None
        self.allow_local = allow_local
        self.runs_dir = None
        self.mock = False

    def configure_commands(self, catalog, commands_dir, allow_local=True,
                           runs_dir=None, mock=False):
        """Wire the operator command catalog (D8) so `run_custom` can use it."""
        self.commands = catalog
        self.commands_dir = commands_dir
        self.allow_local = allow_local
        self.runs_dir = runs_dir
        self.mock = mock

    # ---- hot reload (D8) -------------------------------------------------
    def reload_profiles(self, profile_mgr):
        """Re-snapshot the role profiles after their YAML changed on disk.

        `__init__` caches `{"roleA":…, "roleB":…}`; the Config page edits the files, so
        we must re-`load` them (after the manager's cache is invalidated).
        """
        self.profiles = {"roleA": profile_mgr.load("roleA"),
                         "roleB": profile_mgr.load("roleB")}

    def reload_gates(self):
        """Re-read the gate registry in place after gates/*.yaml changed (P8). The
        registry object is shared by reference, so this is enough; we also drop the
        per-gate cadence cache so every gate re-evaluates on the next poll."""
        self.gates.reload()
        self._gate_last_run.clear()
        self._gate_cache.clear()

    def sync_node_locks(self):
        """Ensure a per-node lock exists for every current fleet node (after a
        fleet reload may have added nodes)."""
        for n in self.fleet.nodes:
            self._node_locks.setdefault(n.name, threading.Lock())

    # ---- audit + broadcast ----------------------------------------------
    def set_event_stream(self, stream: EventStream):
        self.events = stream

    def _emit(self, etype: EventType, data: dict, user: Optional[dict] = None):
        if self.events:
            ev = self.events.append(etype, data, user=user)
            if self.sync:
                self.sync.broadcast_event(ev.to_dict())
        elif self.sync:
            self.sync.broadcast_event(
                {"type": etype.value, "data": data, "user": user})

    # ---- one action ------------------------------------------------------
    def run_action(self, node_name: str, role: str, action_name: str,
                   user: Optional[dict] = None) -> ActionResult:
        prof = self.profiles.get(role)
        node = self.fleet.get(node_name)
        if prof is None or node is None:
            return ActionResult(node_name, role, action_name, "?", False,
                                stderr=f"unknown node/role {node_name}/{role}")
        action = prof.action(action_name)
        if action is None:
            return ActionResult(node_name, role, action_name, "?", False,
                                stderr=f"unknown action {action_name}")
        params = self.fleet.params(node)
        action = P.render_action(action, params)
        if self.sync:
            self.sync.broadcast_action(node_name, action_name, "running", user)
        self._emit(EventType.ACTION_STARTED, {
            "node": node_name, "role": role, "action": action_name,
            "kind": action.kind, "command": action.command}, user=user)

        res = self._dispatch(node, role, action, params)

        etype = EventType.ACTION_COMPLETED if res.success else EventType.ACTION_FAILED
        self._emit(etype, res.to_dict(), user=user)
        if self.sync:
            self.sync.broadcast_action(
                node_name, action_name, "done" if res.success else "failed", user)
        return res

    def _dispatch(self, node, role, action, params) -> ActionResult:
        kind = action.kind
        name = node.name
        if kind == "transfer":
            return self._do_transfer(node, role, action, params)
        client = self.pool.get(name, role)
        if kind == "exec":
            if self.dry_run:
                return self._dry(node.name, role, action, action.command)
            r = client.execute(action.command, timeout=action.timeout)
            return _ar(node.name, role, action, r)
        if kind == "daemon":
            if self.dry_run:
                from .supervisor import detached_start_cmd
                return self._dry(node.name, role, action,
                                 detached_start_cmd(action.daemon, action.command))
            sup = Supervisor(client)
            r = sup.start(action.daemon, action.command,
                          prefer_systemd=action.prefer_systemd, timeout=action.timeout)
            self._emit(EventType.DAEMON_STARTED,
                       {"node": name, "daemon": action.daemon})
            return _ar(name, role, action, r)
        if kind == "daemon_stop":
            if self.dry_run:
                from .supervisor import stop_cmd
                return self._dry(name, role, action,
                                 stop_cmd(action.daemon, action.match))
            sup = Supervisor(client)
            r = sup.stop(action.daemon, action.match,
                         unit=action.prefer_systemd, timeout=action.timeout)
            self._emit(EventType.DAEMON_STOPPED, {"node": name, "daemon": action.daemon})
            return _ar(name, role, action, r)
        if kind == "daemon_status":
            sup = Supervisor(client)
            st = sup.status(action.daemon, action.match,
                            unit=action.prefer_systemd, timeout=action.timeout)
            return ActionResult(name, role, action.name, kind, True,
                                stdout=st.get("raw", ""), extra=st)
        return ActionResult(name, role, action.name, kind, False,
                            stderr=f"unhandled kind {kind}")

    def _do_transfer(self, node, role, action, params) -> ActionResult:
        name = node.name
        which = "serviceA" if "serviceA" in action.name else "serviceB"
        self._emit(EventType.DEPLOY_STARTED,
                   {"node": name, "action": action.name, "src": action.src,
                    "dst": action.dst})
        client = self.pool.get(name, role)
        # mock backend simulates the transfer through its shared state
        if hasattr(client, "state"):
            out = client.state.simulate_transfer(name, which)
            r = CommandResult(f"rsync {action.src} -> {action.dst}", 0, out, "",
                              time.time(), time.time())
        elif self.dry_run:
            cmd = T.rsync_push_cmd(action.src, action.dst, params["roleA_user"],
                                   params["HOST_A"], params["ssh_opts"])
            r = T.run_transfer(cmd, dry_run=True)
        else:
            if action.method == "scp":
                cmd = T.scp_to_roleB_cmd(action.src, action.dst, params["roleA_user"],
                                         params["HOST_A"], params["roleB_user"],
                                         params["HOST_B"], params["ssh_opts"])
            else:
                cmd = T.rsync_push_cmd(action.src, action.dst, params["roleA_user"],
                                       params["HOST_A"], params["ssh_opts"])
            r = T.run_transfer(cmd, timeout=action.timeout)
        etype = EventType.DEPLOY_COMPLETED if r.success else EventType.DEPLOY_FAILED
        self._emit(etype, {"node": name, "action": action.name,
                           "exit_code": r.exit_code})
        return _ar(name, role, action, r)

    def _dry(self, node, role, action, cmd) -> ActionResult:
        return ActionResult(node, role, action.name, action.kind, True,
                            stdout=f"[dry-run] {cmd}")

    # ---- fan-out ---------------------------------------------------------
    def fan_out(self, role: str, action_name: str, node_names: List[str],
                user: Optional[dict] = None) -> Dict[str, ActionResult]:
        results: Dict[str, ActionResult] = {}
        with ThreadPoolExecutor(max_workers=min(MAX_FANOUT, max(1, len(node_names)))) as ex:
            futs = {ex.submit(self.run_action, n, role, action_name, user): n
                    for n in node_names}
            for fut, n in futs.items():
                try:
                    results[n] = fut.result()
                except Exception as e:  # noqa: BLE001
                    results[n] = ActionResult(n, role, action_name, "?", False,
                                              stderr=str(e))
        return results

    # ---- operator commands (D8 — config over code) ----------------------
    def run_custom(self, name: str, node: Optional[str] = None,
                   nodes: Optional[List[str]] = None,
                   user: Optional[dict] = None) -> Dict[str, Any]:
        """Run an operator-defined command from the catalog (``commands.yaml``).

        Dispatch by the command's own config:
          - ``on: local``   → one subprocess on the base station (echo-only in
                              mock/dry-run; refused if ``--no-local-commands``).
          - ``on: remote`` + ``scope: node``  → SSH to ``node``.
          - ``on: remote`` + ``scope: fleet`` → SSH fan-out across ``nodes`` (or all).
        Audited like any action (D6): one ACTION_STARTED + an ACTION_COMPLETED/FAILED
        carrying every per-target result.
        """
        cmd = self.commands.get(name) if self.commands else None
        if cmd is None:
            return {"ok": False, "error": f"unknown command {name!r}"}
        self._emit(EventType.ACTION_STARTED,
                   {"action": name, "kind": "custom", "on": cmd.on,
                    "scope": cmd.scope, "danger": cmd.danger, "node": node}, user=user)
        results: List[ActionResult] = []
        if cmd.on == "local":
            if not self.allow_local:
                results.append(ActionResult("base", "local", name, "custom", False,
                               stderr="local commands are disabled (--no-local-commands)",
                               extra={"on": "local"}))
            else:
                results.append(self._exec_local(cmd, nodes))
        elif cmd.scope == "fleet":
            targets = [n for n in (nodes or self.fleet.names()) if self.fleet.get(n)]
            with ThreadPoolExecutor(max_workers=min(MAX_FANOUT, max(1, len(targets)))) as ex:
                futs = [ex.submit(self._exec_remote, cmd, n) for n in targets]
                for f in futs:
                    results.append(f.result())
        else:
            results.append(self._exec_remote(cmd, node))
        ok = bool(results) and all(r.success for r in results)
        self._emit(EventType.ACTION_COMPLETED if ok else EventType.ACTION_FAILED,
                   {"action": name, "on": cmd.on, "scope": cmd.scope,
                    "results": [r.to_dict() for r in results]}, user=user)
        if self.sync:
            self.sync.broadcast_action(node or "fleet", name,
                                       "done" if ok else "failed", user)
        return {"ok": ok, "command": name, "label": cmd.label, "on": cmd.on,
                "scope": cmd.scope, "danger": cmd.danger,
                "results": [r.to_dict() for r in results]}

    def _exec_remote(self, cmd, node_name) -> ActionResult:
        node = self.fleet.get(node_name)
        if node is None:
            return ActionResult(node_name or "-", cmd.role, cmd.name, "custom", False,
                                stderr="unknown node", extra={"on": "remote"})
        params = self.fleet.params(node)
        prog = self._command_program(cmd, params)
        if prog is None:
            return ActionResult(node_name, cmd.role, cmd.name, "custom", False,
                                stderr=f"script not found: {cmd.script}",
                                extra={"on": "remote"})
        if self.dry_run:
            return ActionResult(node_name, cmd.role, cmd.name, "custom", True,
                                stdout=f"[dry-run] {prog}", extra={"on": "remote"})
        client = self.pool.get(node_name, cmd.role)
        r = client.execute(prog, timeout=cmd.timeout)
        return ActionResult(node_name, cmd.role, cmd.name, "custom", r.success,
                            stdout=r.stdout, stderr=r.stderr, exit_code=r.exit_code,
                            duration=r.duration, extra={"on": "remote"})

    def _exec_local(self, cmd, nodes) -> ActionResult:
        from . import local_exec as L
        ctx = self._local_context(nodes)
        env = _ccflet_env(ctx)
        echo = self.dry_run or self.mock          # never touch the base station in mock
        if cmd.script:
            path = self.commands.script_path(cmd) if self.commands else None
            if not path:
                return ActionResult("base", "local", cmd.name, "custom", False,
                                    stderr=f"script not found: {cmd.script}",
                                    extra={"on": "local"})
            r = L.run_local(["bash", path], env=env, timeout=cmd.timeout, dry_run=echo)
        else:
            r = L.run_local(P.substitute(cmd.run, ctx), env=env,
                            timeout=cmd.timeout, dry_run=echo)
        return ActionResult("base", "local", cmd.name, "custom", r.success,
                            stdout=r.stdout, stderr=r.stderr, exit_code=r.exit_code,
                            duration=r.duration, extra={"on": "local"})

    def _command_program(self, cmd, params) -> Optional[str]:
        """The shell program to run remotely for a command.

        Inline ``run:`` gets ``{param}`` substitution (bare-token-safe node params);
        a ``script:`` is run as-is with the node params exported as ``CCFLET_*`` env
        (so ``${SHELL_VARS}`` in the script are never mangled by substitution)."""
        if cmd.script:
            path = self.commands.script_path(cmd) if self.commands else None
            body = _read_text(path) if path else None
            if body is None:
                return None
            return _env_prefix(_ccflet_env(params)) + body
        return P.substitute(cmd.run, params)

    def _local_context(self, nodes) -> Dict[str, str]:
        names = [n for n in (nodes or self.fleet.names()) if self.fleet.get(n)]
        # VARIANT is per-node; a local command may fan over a mixed-variant selection,
        # so expose the fleet default here as an informational hint only.
        return {"RUNS_DIR": self.runs_dir or "", "FLEET": self.fleet.name,
                "VARIANT": self.fleet.default_variant, "ALGO": self.fleet.algo,
                "NODES": " ".join(names)}

    # ---- health wait -----------------------------------------------------
    def _wait_daemon(self, node_name: str, role: str, status_action: str) -> bool:
        if self.dry_run:
            return True
        deadline = time.time() + HEALTH_WAIT_TIMEOUT
        while time.time() < deadline:
            res = self.run_action(node_name, role, status_action)
            if res.extra.get("up"):
                return True
            time.sleep(HEALTH_WAIT_POLL)
        return False

    # ---- sequences (variant-aware, ordered) -----------------------------
    def _node_lock(self, name: str) -> threading.Lock:
        return self._node_locks.setdefault(name, threading.Lock())

    def deploy(self, node_name: str, build: bool = False,
               user: Optional[dict] = None) -> List[ActionResult]:
        steps = SEQ.deploy_steps(self.sequences, build=build)
        with self._node_lock(node_name):
            return self._run_sequence("deploy", node_name, steps, user)

    def bring_up(self, node_name: str, user: Optional[dict] = None) -> List[ActionResult]:
        variant = self.fleet.node_variant(node_name)
        seq = SEQ.bring_up_steps(self.sequences, variant)
        with self._node_lock(node_name):
            return self._run_guarded_sequence("bring_up", node_name, seq, user)

    def tear_down(self, node_name: str, user: Optional[dict] = None) -> List[ActionResult]:
        variant = self.fleet.node_variant(node_name)
        steps = SEQ.tear_down_steps(self.sequences, variant)
        with self._node_lock(node_name):
            return self._run_sequence("tear_down", node_name, steps, user)

    def _run_sequence(self, seq_name, node_name, steps, user) -> List[ActionResult]:
        self._emit(EventType.SEQUENCE_STARTED,
                   {"sequence": seq_name, "node": node_name,
                    "variant": self.fleet.node_variant(node_name)},
                   user=user)
        results = []
        for role, action_name in steps:
            self._emit(EventType.SEQUENCE_STEP,
                       {"sequence": seq_name, "node": node_name, "step": action_name})
            res = self.run_action(node_name, role, action_name, user)
            results.append(res)
            if not res.success:
                self._emit(EventType.SEQUENCE_FAILED,
                           {"sequence": seq_name, "node": node_name, "step": action_name})
                return results
        self._emit(EventType.SEQUENCE_COMPLETED,
                   {"sequence": seq_name, "node": node_name})
        return results

    def _run_guarded_sequence(self, seq_name, node_name, steps, user) -> List[ActionResult]:
        """Each step = (role, start_action, status_action); wait healthy between."""
        self._emit(EventType.SEQUENCE_STARTED,
                   {"sequence": seq_name, "node": node_name,
                    "variant": self.fleet.node_variant(node_name)},
                   user=user)
        results = []
        for role, start_action, status_action in steps:
            self._emit(EventType.SEQUENCE_STEP,
                       {"sequence": seq_name, "node": node_name, "step": start_action})
            res = self.run_action(node_name, role, start_action, user)
            results.append(res)
            healthy = res.success and self._wait_daemon(node_name, role, status_action)
            if not healthy:
                self._emit(EventType.SEQUENCE_FAILED,
                           {"sequence": seq_name, "node": node_name, "step": start_action})
                return results
        self._emit(EventType.SEQUENCE_COMPLETED, {"sequence": seq_name, "node": node_name})
        return results

    # ---- fleet variants (staggered) -------------------------------------
    def _fleet_apply(self, fn, node_names, user, stagger=None):
        stagger = self.fleet.defaults.stagger if stagger is None else stagger
        out: Dict[str, Any] = {}
        threads = []

        def worker(n):
            out[n] = fn(n, user=user)

        for i, n in enumerate(node_names):
            t = threading.Thread(target=worker, args=(n,), daemon=True)
            threads.append(t)
            t.start()
            if stagger and i < len(node_names) - 1:
                time.sleep(stagger)
        for t in threads:
            t.join()
        return out

    def bring_up_fleet(self, node_names, user=None):
        return self._fleet_apply(self.bring_up, node_names, user)

    def tear_down_fleet(self, node_names, user=None):
        return self._fleet_apply(self.tear_down, node_names, user, stagger=0)

    def deploy_fleet(self, node_names, user=None, build=False):
        return self._fleet_apply(
            lambda n, user=None: self.deploy(n, build=build, user=user),
            node_names, user, stagger=0)

    # ---- status polling (config-driven gates, P8) ------------------------
    def run_action_silent(self, node_name, role, action_name) -> ActionResult:
        """Run a profile action without emitting audit events (utility for callers that
        want raw output, e.g. a manual status probe)."""
        prof = self.profiles.get(role)
        node = self.fleet.get(node_name)
        if not prof or not node:
            return ActionResult(node_name, role, action_name, "?", False)
        action = prof.action(action_name)
        if not action:
            return ActionResult(node_name, role, action_name, "?", False)
        params = self.fleet.params(node)
        action = P.render_action(action, params)
        return self._dispatch(node, role, action, params)

    @property
    def poll_interval(self) -> float:
        """Background poll cadence = the most-frequent gate's interval (floored at 1s)."""
        return self.gates.poll_interval

    def _mock_state(self, node_name: str):
        """The shared MockFleetState if this node's clients are mock clients, else None
        (so gate evaluation can short-circuit to the simulate hook, like _do_transfer)."""
        client = self.pool.get(node_name, "roleA")
        return getattr(client, "state", None)

    def poll_node(self, node_name: str, force: bool = True) -> S.NodeStatus:
        """Evaluate every configured gate for one node and publish the result.

        Reachability is checked once per role per tick and **short-circuits** that role's
        gates (an unreachable role's gates fail immediately instead of stacking timeouts).
        Each due gate (its `interval` elapsed, or `force`) runs in parallel; not-due gates
        keep their cached result. Under --mock/--dry-run nothing touches the wire — the
        gate is produced from the simulated world (domain.mock_rules.gate_mock) or a
        healthy preview."""
        node = self.fleet.get(node_name)
        variant = self.fleet.node_variant(node_name)
        params = self.fleet.params(node)
        mstate = self._mock_state(node_name)
        simulate = self.mock or self.dry_run

        # which roles do the applicable gates need? (always probe roleA — control plane)
        applicable = [s for s in self.gates.specs if s.applies_to(variant)]
        needed_roles = {"roleA"} | {s.on for s in applicable if s.on in ("roleA", "roleB")}
        reach = self._tick_reachability(node_name, needed_roles, mstate, simulate)

        now = time.time()
        gates: Dict[str, Dict[str, Any]] = {}
        due: List[GC.GateSpec] = []
        for spec in self.gates.specs:
            if not spec.applies_to(variant):
                gates[spec.key] = GC.na_result(spec)
                continue
            key = (node_name, spec.key)
            if not force and (key in self._gate_cache) and \
                    (now - self._gate_last_run.get(key, 0.0) < spec.interval):
                gates[spec.key] = self._gate_cache[key]   # not due → reuse cached
            else:
                due.append(spec)

        if due:
            evaluated = self._evaluate_due(node_name, due, params, variant, reach,
                                           mstate, simulate)
            for spec in due:
                res = evaluated[spec.key]
                gates[spec.key] = res
                self._gate_cache[(node_name, spec.key)] = res
                self._gate_last_run[(node_name, spec.key)] = now

        ns = S.NodeStatus(node=node_name, variant=variant,
                          reachable_roleA=bool(reach.get("roleA", False)),
                          reachable_roleB=reach.get("roleB"), gates=gates)
        with self._status_lock:
            self.statuses[node_name] = ns
        self._publish_status(ns)
        return ns

    def _tick_reachability(self, node_name, roles, mstate, simulate) -> Dict[str, bool]:
        """Connect each needed role once for this tick → {role: reachable}. roleB is only
        recorded if a gate needs it (so a variant-A node reports roleB as None)."""
        reach: Dict[str, bool] = {}
        for role in ("roleA", "roleB"):
            if role not in roles:
                continue
            if mstate is not None:
                reach[role] = bool(mstate.is_reachable(node_name))
            elif simulate:
                reach[role] = True                  # dry-run preview, no wire
            else:
                try:
                    reach[role] = bool(self.pool.get(node_name, role).connect())
                except Exception:                   # noqa: BLE001 — a connect crash → down
                    reach[role] = False
        return reach

    def _evaluate_due(self, node_name, due, params, variant, reach, mstate, simulate
                      ) -> Dict[str, Dict[str, Any]]:
        """Evaluate the due gates in parallel (a slow gate can't stall the others)."""
        out: Dict[str, Dict[str, Any]] = {}

        def one(spec):
            try:
                return self._eval_gate(node_name, spec, params, reach, mstate, simulate)
            except Exception as e:                  # noqa: BLE001 — never 500 a poll
                return GC.gate_result(spec, "gray", f"eval error: {e}")

        if len(due) == 1:
            out[due[0].key] = one(due[0])
            return out
        with ThreadPoolExecutor(max_workers=min(MAX_GATE_WORKERS, len(due))) as ex:
            futs = {ex.submit(one, s): s for s in due}
            for fut, spec in futs.items():
                out[spec.key] = fut.result()
        return out

    def _eval_gate(self, node_name, spec, params, reach, mstate, simulate
                   ) -> Dict[str, Any]:
        """One gate → its GateResult dict. Mock / dry-run short-circuit to a simulated
        result; otherwise run the kind's real transport (connect / ping / exec / local)."""
        if mstate is not None:
            from domain import mock_rules as MR
            return MR.gate_mock(mstate, node_name, spec)
        if simulate:
            return self._simulated_gate(spec, variant=self.fleet.node_variant(node_name))
        # real evaluation — short-circuit role gates on an unreachable role. A process
        # gate still lists its configured processes (all down) so the per-process LEDs
        # render red instead of vanishing to a blank row.
        if spec.on in ("roleA", "roleB") and not reach.get(spec.on, False):
            procs = GC.down_processes(spec, self.fleet.node_variant(node_name))
            return GC.gate_result(spec, "red", f"{spec.on} unreachable", processes=procs)
        if spec.kind == "reach":
            return self._eval_reach(node_name, spec, params, reach)
        if spec.kind == "process":
            return self._eval_process(node_name, spec, params)
        return self._eval_metric(node_name, spec, params)

    def _simulated_gate(self, spec, variant) -> Dict[str, Any]:
        """A healthy preview (dry-run with the real factory, no mock world)."""
        if spec.kind == "reach":
            return GC.gate_result(spec, spec.colors.get("up", "green"),
                                  "reachable (simulated)")
        if spec.kind == "process":
            procs = [{"name": p.name, "up": True, "mandatory": p.mandatory}
                     for p in spec.processes
                     if p.variants is None or variant in p.variants]
            return GC.gate_result(spec, spec.colors.get("all_up", "green"),
                                  "all processes up (simulated)", processes=procs)
        fields = dict(spec.mock.get("healthy") or {})
        lvl = GC.evaluate_levels(fields, spec.levels)
        return GC.gate_result(spec, lvl.color,
                              GC.render_detail(lvl.detail or spec.detail, fields),
                              fields=fields)

    def _eval_reach(self, node_name, spec, params, reach) -> Dict[str, Any]:
        if spec.method == "ping":
            if not self.allow_local:
                return GC.gate_result(spec, "gray", "ping disabled (--no-local-commands)")
            host = P.substitute(spec.host, params) if spec.host else None
            up = bool(host and ping_once(host, spec.timeout))
            return GC.gate_result(spec, spec.colors.get("up" if up else "down",
                                  "green" if up else "red"),
                                  (f"{host} reachable" if up else f"{host} no reply"))
        up = bool(reach.get(spec.on, False))
        return GC.gate_result(spec, spec.colors.get("up" if up else "down",
                              "green" if up else "red"),
                              f"{spec.on} {'reachable' if up else 'unreachable'}")

    def _eval_process(self, node_name, spec, params) -> Dict[str, Any]:
        client = self.pool.get(node_name, spec.on)
        variant = self.fleet.node_variant(node_name)
        procs, mand_down, opt_down = [], False, False
        for p in spec.processes:
            if p.variants is not None and variant not in p.variants:
                continue
            cmd = P.substitute(spec.check, {**params, "pattern": p.pattern, "name": p.name})
            try:
                up = client.execute(cmd, timeout=int(spec.timeout)).exit_code == 0
            except Exception:                       # noqa: BLE001 — exec failure → down
                up = False
            procs.append({"name": p.name, "up": up, "mandatory": p.mandatory})
            if not up:
                mand_down = mand_down or p.mandatory
                opt_down = opt_down or not p.mandatory
        if mand_down:
            color, detail = spec.colors.get("mandatory_down", "red"), "mandatory process down"
        elif opt_down:
            color, detail = spec.colors.get("optional_down", "yellow"), "optional process down"
        else:
            color, detail = spec.colors.get("all_up", "green"), "all processes up"
        return GC.gate_result(spec, color, detail, processes=procs)

    def _eval_metric(self, node_name, spec, params) -> Dict[str, Any]:
        cmd = P.substitute(spec.cmd, params)
        if spec.on == "base":
            if not self.allow_local:
                return GC.gate_result(spec, "gray", "local exec disabled (--no-local-commands)")
            from . import local_exec as L
            out = L.run_local(cmd, timeout=int(spec.timeout), dry_run=False).stdout
        else:
            out = self.pool.get(node_name, spec.on).execute(cmd, timeout=int(spec.timeout)).stdout
        fields = GC.extract_fields(out, spec.fields, spec.parse)
        lvl = GC.evaluate_levels(fields, spec.levels)
        return GC.gate_result(spec, lvl.color,
                              GC.render_detail(lvl.detail or spec.detail, fields),
                              fields=fields)

    def _publish_status(self, ns: S.NodeStatus):
        """Audit + broadcast. GATE_CHANGED (the whole map) fires only when a gate's COLOR
        changes (so metric value-jitter at the same color is quiet); each individual color
        flip also drops a human-readable session-log line (P6, like STATE_CHANGED)."""
        colors = {k: g.get("color") for k, g in ns.gates.items()}
        with self._status_lock:
            prev = self._last_gate_colors.get(ns.node)
            changed = prev != colors
            flips = ([] if prev is None else
                     [(k, prev.get(k), c) for k, c in colors.items()
                      if prev.get(k) is not None and prev.get(k) != c])
            if changed:
                self._last_gate_colors[ns.node] = colors
        if changed:
            self._emit(EventType.GATE_CHANGED, {"node": ns.node, "gates": ns.gates})
            if self.sync:
                self.sync.broadcast_gate(ns.node, ns.gates)
        for key, oldc, newc in flips:
            g = ns.gates.get(key, {})
            self._emit(EventType.NOTE, {"text":
                f"GATE {key} ({g.get('label', key)}) on {ns.node} {oldc}→{newc}"
                + (f" ({g.get('detail')})" if g.get("detail") else "")})
        if self.sync:
            self.sync.broadcast_node_status(ns.to_dict())

    def poll_all(self):
        for n in self.fleet.names():
            try:
                self.poll_node(n, force=False)
            except Exception as e:  # noqa: BLE001
                self._emit(EventType.ERROR, {"node": n, "error": str(e)})

    def start_polling(self):
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._poll_stop.clear()

        def loop():
            while not self._poll_stop.is_set():
                self.poll_all()
                self._poll_stop.wait(self.poll_interval)

        self._poll_thread = threading.Thread(target=loop, daemon=True)
        self._poll_thread.start()

    def stop_polling(self):
        self._poll_stop.set()

    def snapshot(self) -> Dict[str, dict]:
        with self._status_lock:
            return {n: s.to_dict() for n, s in self.statuses.items()}


def _ar(node, role, action, r: CommandResult) -> ActionResult:
    return ActionResult(
        node=node, role=role, action=action.name, kind=action.kind,
        success=r.success, stdout=r.stdout, stderr=r.stderr,
        exit_code=r.exit_code, duration=r.duration)


# --- operator-command helpers (D8) ------------------------------------------
def _read_text(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _ccflet_env(d: Dict[str, Any]) -> Dict[str, str]:
    """A {param} dict → CCFLET_<KEY> environment variables for scripts."""
    return {f"CCFLET_{k.upper()}": str(v) for k, v in d.items()}


def _env_prefix(env: Dict[str, str]) -> str:
    """Leading `export K=val` lines so a remote script body sees CCFLET_* vars."""
    return "".join(f"export {k}={shlex.quote(v)}\n" for k, v in env.items())
