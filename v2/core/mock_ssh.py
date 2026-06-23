"""
Mock SSH backend — a stateful simulated fleet for ccflet.

ccflet assumes provisioned, booted nodes; for development and tests there is no
fleet wired up, so this module *mocks what is needed* to drive and demonstrate the
whole stack end-to-end. A MockSSHClient implements the same surface as
ssh_client.SSHClientWrapper (execute / exec_stream / get_file / put_file /
file_exists) but, instead of touching the network, it pattern-matches the **real
synthesized commands** (supervisor start/stop/status, collectors, probes, tails)
against a shared MockFleetState. So the supervisor's command synthesis, the
orchestrator's sequencing and the status parsers are all genuinely exercised — only
the wire is faked.

The world is stateful: bring-up flips services to "up", peers then see each other,
check/serviceC collectors start producing fresh lines, and the dashboard gates go
green — exactly as a real bring-up would look.
"""

import threading
import time
from typing import Dict, Iterator, List, Optional

from .result import CommandResult
# the per-app command rules + collector/probe text generators (the producer side of
# the mock↔status string contract) live in the domain pack so the Compiler can
# regenerate them with the matching parsers in domain.gates.
from domain import mock_rules as MR

# re-exported for any caller that referenced the old module-level constants
_DAEMON_KEY = MR.DAEMON_KEY
_SYSTEMD_RE = MR.SYSTEMD_RE


class MockFleetState:
    """Shared, mutable simulated world for the whole fleet."""

    def __init__(self, fleet, systemd_serviceA: bool = True):
        self.fleet = fleet
        self.systemd_serviceA = systemd_serviceA
        self._lock = threading.RLock()
        self.offline: set = set()
        # per-node service state: {node: {serviceA/serviceB/serviceC: {up,pid,source}}}
        self.daemons: Dict[str, Dict[str, dict]] = {
            n.name: {k: {"up": False, "pid": None, "source": None}
                     for k in MR.DAEMONS}
            for n in fleet.nodes
        }
        self.deployed: Dict[str, bool] = {n.name: False for n in fleet.nodes}
        self.built: Dict[str, bool] = {n.name: False for n in fleet.nodes}
        self._pid = 4000

    def reload(self, fleet):
        """Rebuild per-node simulated state after a fleet-inventory edit.

        Nodes that still exist keep their service/deploy state; new nodes start
        fresh; removed nodes are dropped (incl. from the offline set). The `fleet`
        reference is updated for completeness — in practice it is the same object,
        reloaded in place by `Fleet.reload_from_dict`.
        """
        fresh = {n.name for n in fleet.nodes}
        with self._lock:
            self.fleet = fleet
            self.daemons = {
                name: self.daemons.get(name) or {
                    k: {"up": False, "pid": None, "source": None}
                    for k in MR.DAEMONS}
                for name in fresh
            }
            self.deployed = {name: self.deployed.get(name, False) for name in fresh}
            self.built = {name: self.built.get(name, False) for name in fresh}
            self.offline = {x for x in self.offline if x in fresh}

    # ---- helpers ---------------------------------------------------------
    def node_variant(self, node: str) -> str:
        """This node's live variant (per-node)."""
        return self.fleet.node_variant(node)

    def _next_pid(self) -> int:
        self._pid += 1
        return self._pid

    def set_offline(self, node: str, offline: bool = True):
        with self._lock:
            (self.offline.add if offline else self.offline.discard)(node)

    def is_reachable(self, node: str) -> bool:
        return node not in self.offline

    def start_daemon(self, node: str, daemon: str, source: str = "pidfile"):
        with self._lock:
            self.daemons[node][daemon] = {
                "up": True, "pid": self._next_pid(), "source": source}

    def stop_daemon(self, node: str, daemon: str):
        with self._lock:
            self.daemons[node][daemon] = {"up": False, "pid": None, "source": None}

    def is_up(self, node: str, daemon: str) -> bool:
        return self.daemons[node][daemon]["up"]

    # ---- simulated peers / collectors (domain rules in domain/mock_rules.py) --
    # These thin wrappers delegate to the domain pack so the emitted collector/probe
    # strings stay paired with the parsers in domain.gates (the string contract).
    def peer_ids(self, node: str) -> List[int]:
        return MR.peer_ids(self, node)

    def links_json(self, node: str) -> str:
        return MR.links_json(self, node)

    def links_log_tail(self, node: str, n: int = 200) -> str:
        return MR.links_log_tail(self, node, n)

    def check_lines(self, node: str) -> str:
        return MR.check_lines(self, node)

    def servicec_stats(self, node: str) -> str:
        return MR.servicec_stats(self, node)

    def probe_a_text(self, node: str) -> str:
        return MR.probe_a_text(self, node)

    def probe_b_text(self, node: str) -> str:
        return MR.probe_b_text(self, node)

    def simulate_transfer(self, node: str, which: str) -> str:
        with self._lock:
            self.deployed[node] = True
            if which == "serviceA":
                self.built[node] = self.built.get(node, False)
        return (f"sending incremental file list\npayload/{which}/\n"
                f"sent 1,234,567 bytes  received 4,321 bytes  total size 1,250,000\n"
                f"[mock] deployed {which} to {node}")


class MockSSHClient:
    """Drop-in mock of SSHClientWrapper bound to one (node, role) of the fleet."""

    def __init__(self, state: MockFleetState, node: str, role: str):
        self.state = state
        self.node = node
        self.role = role  # 'roleA' or 'roleB'
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        self._connected = self.state.is_reachable(self.node)
        return self._connected

    def disconnect(self):
        self._connected = False

    def _ok(self, cmd: str, out: str = "", err: str = "", code: int = 0) -> CommandResult:
        t = time.time()
        return CommandResult(cmd, code, out, err, t, t)

    # ---- the heart: pattern-match real commands --------------------------
    def execute(self, command: str, timeout: Optional[int] = None) -> CommandResult:
        if not self.state.is_reachable(self.node):
            t = time.time()
            return CommandResult(command, 255, "", "ssh: connect: No route to host", t, t)

        # systemd unit probe (supervisor prefer_systemd)
        if command.startswith("systemctl cat"):
            return self._ok(command, code=0 if MR.systemd_installed(self.state, command) else 1)

        # systemd start/stop
        m = _SYSTEMD_RE.search(command)
        if m and "is-active" not in command:
            verb, unit = m.group(1), m.group(2)
            key = _DAEMON_KEY.get(unit.replace(".service", ""), None)
            if key:
                if verb == "start":
                    self.state.start_daemon(self.node, key, source="systemd")
                    return self._ok(command, f"ccflet-started source=systemd unit={unit}")
                else:
                    self.state.stop_daemon(self.node, key)
                    # fallthrough to also handle pkill in same command → still stopped

        # detached daemon start
        if "setsid nohup" in command:
            name = self._daemon_name(command)
            if name:
                self.state.start_daemon(self.node, name, source="pidfile")
                pid = self.state.daemons[self.node][name]["pid"]
                return self._ok(command, f"ccflet-started pid={pid} daemon={name}")

        # daemon stop (pidfile/pkill synthesized by supervisor)
        if "ccflet-stopped" in command:
            name = self._daemon_name(command)
            if name:
                self.state.stop_daemon(self.node, name)
            return self._ok(command, "ccflet-stopped")

        # daemon status
        if "source=pidfile" in command or ("pgrep -f" in command and "echo" in command):
            name = self._daemon_name(command)
            if name:
                st = self.state.daemons[self.node][name]
                if st["up"]:
                    return self._ok(command,
                                    f"up pid={st['pid']} source={st['source']}")
                return self._ok(command, "down")

        # domain reads: serviceA build, roleB probes, collectors/tails — the
        # command rules + emitted text live in domain/mock_rules.py (the producer
        # side of the mock↔status string contract).
        text = MR.domain_read(self.state, self.node, command)
        if text is not None:
            return self._ok(command, text)

        # reachability probe / generic
        if command.strip() in ("true", "echo ccflet-ok") or command.startswith("uname"):
            return self._ok(command, "ccflet-ok")

        # custom operator command (commands.yaml) or anything else unrecognized —
        # echo it so --mock shows believable output instead of nothing.
        stripped = command.strip()
        first = stripped.splitlines()[0] if stripped else ""
        return self._ok(command, f"[mock] ran on {self.node}: {first}")

    def _daemon_name(self, command: str) -> Optional[str]:
        return MR.daemon_name(command)

    # ---- streaming -------------------------------------------------------
    def exec_stream(self, command: str, stop_event: Optional[threading.Event] = None
                    ) -> Iterator[str]:
        if not self.state.is_reachable(self.node):
            yield "[stream] no route to host"
            return
        kind = MR.stream_kind(command)
        while not (stop_event and stop_event.is_set()):
            line = MR.stream_line(self.state, self.node, kind)
            if line:
                yield line
            # pace the synthetic stream
            for _ in range(10):
                if stop_event and stop_event.is_set():
                    return
                time.sleep(0.05)

    # ---- file ops (no-ops in mock) --------------------------------------
    def get_file(self, remote_path: str, local_path: str):
        return False, "[mock] no file transfer"

    def put_file(self, local_path: str, remote_path: str):
        return True, ""

    def file_exists(self, remote_path: str) -> bool:
        return True
