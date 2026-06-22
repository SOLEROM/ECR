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

MAX_FANOUT = 10
HEALTH_WAIT_TIMEOUT = 15.0
HEALTH_WAIT_POLL = 0.4


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
                 sync_manager=None, dry_run: bool = False):
        self.fleet = fleet
        self.profiles = {"roleA": profile_mgr.load("roleA"),
                         "roleB": profile_mgr.load("roleB")}
        self.pool = ConnectionPool(client_factory)
        self.events = event_stream
        self.sync = sync_manager
        self.dry_run = dry_run
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_stop = threading.Event()
        self._last_gates: Dict[str, Dict] = {}
        self.statuses: Dict[str, S.NodeStatus] = {}
        self.poll_interval = 3.0
        # guards the status dicts (poll thread writes, web thread reads)
        self._status_lock = threading.Lock()
        # one lock per node so a node can't run two sequences at once
        self._node_locks: Dict[str, threading.Lock] = {
            n.name: threading.Lock() for n in fleet.nodes
        }
        # operator command catalog (D8) — wired by configure_commands()
        self.commands = None
        self.commands_dir = None
        self.allow_local = True
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
        steps = ["deploy_serviceB", "deploy_serviceA"] + (["serviceA_build"] if build else [])
        with self._node_lock(node_name):
            return self._run_sequence("deploy", node_name,
                                      [("roleA", s) for s in steps], user)

    def bring_up(self, node_name: str, user: Optional[dict] = None) -> List[ActionResult]:
        variant = self.fleet.node_variant(node_name)
        seq: List = []
        if variant == "B":
            seq.append(("roleB", "serviceC_start", "serviceC_status"))
        seq.append(("roleA", "serviceA_start", "serviceA_status"))
        seq.append(("roleA", "serviceB_start", "serviceB_status"))
        with self._node_lock(node_name):
            return self._run_guarded_sequence("bring_up", node_name, seq, user)

    def tear_down(self, node_name: str, user: Optional[dict] = None) -> List[ActionResult]:
        variant = self.fleet.node_variant(node_name)
        steps = [("roleA", "serviceB_stop"), ("roleA", "serviceA_stop")]
        if variant == "B":
            steps.append(("roleB", "serviceC_stop"))
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

    # ---- status polling --------------------------------------------------
    def poll_node(self, node_name: str) -> S.NodeStatus:
        node = self.fleet.get(node_name)
        variant = self.fleet.node_variant(node_name)
        params = self.fleet.params(node)
        raw: Dict[str, Any] = {}
        aclient = self.pool.get(node_name, "roleA")
        reachable = aclient.connect()
        raw["reachable_roleA"] = reachable
        if reachable:
            raw["serviceA"] = self._status_of(node_name, "roleA", "serviceA_status")
            raw["serviceB"] = self._status_of(node_name, "roleA", "serviceB_status")
            raw["links_text"] = self._collect(node_name, "roleA", "links")
            raw["check1_text"] = self._collect(node_name, "roleA", "check1")
            if variant == "B":
                raw["check2_text"] = raw["check1_text"]  # [CHECK2] lines share serviceB.log
                bclient = self.pool.get(node_name, "roleB")
                raw["reachable_roleB"] = bclient.connect()
                if raw["reachable_roleB"]:
                    raw["serviceC"] = self._status_of(node_name, "roleB", "serviceC_status")
                    raw["servicec_text"] = self._collect(node_name, "roleB", "servicec")
                    raw["probe_a_text"] = self._exec_text(node_name, "roleB", "probeA_status")
                    raw["probe_b_text"] = self._exec_text(node_name, "roleB", "probeB_status")
        expected = max(0, len(self.fleet.nodes) - 1)
        ns = S.build_status(node_name, variant, raw, expected_links=expected, own_id=node.id)
        with self._status_lock:
            self.statuses[node_name] = ns
        self._publish_status(ns)
        return ns

    def _status_of(self, node, role, action_name) -> dict:
        res = self.run_action_silent(node, role, action_name)
        return res.extra if res.extra else {"up": False}

    def _collect(self, node, role, collector_name) -> str:
        prof = self.profiles[role]
        coll = prof.collectors.get(collector_name)
        if not coll:
            return ""
        params = self.fleet.params(self.fleet.get(node))
        cmd = P.substitute(coll.command, params)
        client = self.pool.get(node, role)
        return client.execute(cmd, timeout=coll.timeout).stdout

    def _exec_text(self, node, role, action_name) -> str:
        res = self.run_action_silent(node, role, action_name)
        return res.stdout

    def run_action_silent(self, node_name, role, action_name) -> ActionResult:
        """Run an action without emitting audit events (used by the poller)."""
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

    def _publish_status(self, ns: S.NodeStatus):
        with self._status_lock:
            prev = self._last_gates.get(ns.node)
            changed = prev != ns.gates
            if changed:
                self._last_gates[ns.node] = ns.gates
        if changed:
            self._emit(EventType.GATE_CHANGED, {"node": ns.node, "gates": ns.gates})
            if self.sync:
                self.sync.broadcast_gate(ns.node, ns.gates)
        if self.sync:
            self.sync.broadcast_node_status(ns.to_dict())

    def poll_all(self):
        for n in self.fleet.names():
            try:
                self.poll_node(n)
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
