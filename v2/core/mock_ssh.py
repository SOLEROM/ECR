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

import re
import threading
import time
from typing import Dict, Iterator, List, Optional

from .result import CommandResult

# daemon name (as embedded in /tmp/ccflet/<name>.{pid,log} and systemd units) →
# internal state key. The names line up, so this is effectively an identity map.
_DAEMON_KEY = {"serviceA": "serviceA", "serviceB": "serviceB", "serviceC": "serviceC"}
_PIDFILE_RE = re.compile(r"/tmp/ccflet/([\w.-]+)\.(?:pid|log)")
_SYSTEMD_RE = re.compile(r"systemctl\s+(start|stop)\s+(\S+)")


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
                     for k in ("serviceA", "serviceB", "serviceC")}
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
                    for k in ("serviceA", "serviceB", "serviceC")}
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

    # ---- simulated peers / collectors ------------------------------------
    def peer_ids(self, node: str) -> List[int]:
        """Peers this node hears: other reachable nodes with serviceA up, **same variant**.

        Two nodes in different variants form one-way links (different egress / broadcast
        address), so they don't hear each other — modeling the physical constraint the
        per-node-variant mechanism hands to the operator.
        """
        if not self.is_up(node, "serviceA") or node in self.offline:
            return []
        my_variant = self.node_variant(node)
        peers = []
        for other in self.fleet.nodes:
            if other.name == node or other.name in self.offline:
                continue
            if self.is_up(other.name, "serviceA") and self.node_variant(other.name) == my_variant:
                peers.append(other.id)
        return sorted(peers)

    def links_json(self, node: str) -> str:
        ids = self.peer_ids(node)
        me = self.fleet.get(node)
        now = time.time()
        peers = {}
        for i, pid in enumerate(ids):
            age = 40 + ((pid * 37 + int(now * 5)) % 220)  # < 1s, jitters over time
            peers[str(pid)] = {"last_seen_unix": round(now - age / 1000, 3),
                               "age_ms": age}
        return '{"own_id": %d, "peers": %s}' % (
            me.id, _json_obj(peers))

    def links_log_tail(self, node: str, n: int = 200) -> str:
        ids = self.peer_ids(node)
        if not ids:
            return ""
        lines = []
        now = time.time()
        tick = int(now * 20)
        for k in range(min(n, len(ids) * 4)):
            pid = ids[k % len(ids)]
            ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now))
            lines.append(
                f'{ts}Z rx from={pid} bytes=214 '
                f'msg="{{\\"type\\":\\"sync\\",\\"node_id\\":{pid},\\"seq\\":{tick - k}}}"'
            )
        return "\n".join(lines)

    def check_lines(self, node: str) -> str:
        """serviceB.log tail: [CHECK] (path 1), plus [CHECK2] (path 2) in variant B."""
        if not self.is_up(node, "serviceB") or node in self.offline:
            return ""
        now = time.time()
        age = round(0.05 + (int(now * 3) % 30) / 100, 2)
        lines = [f"[CHECK] value=3 age={age} unit=ok"]
        if self.node_variant(node) == "B" and self.is_up(node, "serviceC"):
            lines.append(f"[CHECK2] value=3 age={age} unit=ok")
        return "\n".join(lines)

    def servicec_stats(self, node: str) -> str:
        if not self.is_up(node, "serviceC") or node in self.offline:
            return ""
        n_peers = len(self.peer_ids(node))
        now = int(time.time()) % 1000
        up = 20
        down = 20 * max(n_peers, 0)
        signal = -68 - (hash(node) % 20)
        return (f"+{now}s up={up} ({up}/s) down={down} ({down}/s) "
                f"drop: bad_lan=0 loop={down} bad_air=0 self=0 "
                f"err: tx=0 lan=0 signal={signal}dB")

    def probe_a_text(self, node: str) -> str:
        return ("status/probeA:\n  PROBEA: READY"
                if self.is_reachable(node) else "connection refused")

    def probe_b_text(self, node: str) -> str:
        return ("status/probeB:\n  value: 3\n  PROBEB_OK"
                if self.is_reachable(node) else "connection refused")

    def simulate_transfer(self, node: str, which: str) -> str:
        with self._lock:
            self.deployed[node] = True
            if which == "serviceA":
                self.built[node] = self.built.get(node, False)
        return (f"sending incremental file list\npayload/{which}/\n"
                f"sent 1,234,567 bytes  received 4,321 bytes  total size 1,250,000\n"
                f"[mock] deployed {which} to {node}")


def _json_obj(d: dict) -> str:
    import json
    return json.dumps(d)


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
            installed = self.state.systemd_serviceA and "serviceA" in command
            return self._ok(command, code=0 if installed else 1)

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

        # serviceA build
        if "make" in command and "serviceA" in command:
            self.state.built[self.node] = True
            return self._ok(command, "cc ... serviceA.c -o serviceA\n[mock] build ok")

        # roleB probes
        if "probeA" in command:
            return self._ok(command, self.state.probe_a_text(self.node))
        if "probeB" in command:
            return self._ok(command, self.state.probe_b_text(self.node))

        # collectors / tails (non-streaming reads)
        if "links.json" in command or ("serviceA.rx" in command and "tail" in command):
            text = (self.state.links_json(self.node) if self.state.systemd_serviceA
                    else self.state.links_log_tail(self.node))
            return self._ok(command, text)
        if "serviceB.log" in command and "tail" in command:
            return self._ok(command, self.state.check_lines(self.node))
        if "serviceC.log" in command and "tail" in command:
            return self._ok(command, self.state.servicec_stats(self.node))

        # reachability probe / generic
        if command.strip() in ("true", "echo ccflet-ok") or command.startswith("uname"):
            return self._ok(command, "ccflet-ok")

        # custom operator command (commands.yaml) or anything else unrecognized —
        # echo it so --mock shows believable output instead of nothing.
        stripped = command.strip()
        first = stripped.splitlines()[0] if stripped else ""
        return self._ok(command, f"[mock] ran on {self.node}: {first}")

    def _daemon_name(self, command: str) -> Optional[str]:
        m = _PIDFILE_RE.search(command)
        if m:
            return _DAEMON_KEY.get(m.group(1))
        # systemd-only stop without pidfile
        m = _SYSTEMD_RE.search(command)
        if m:
            return _DAEMON_KEY.get(m.group(2).replace(".service", ""))
        return None

    # ---- streaming -------------------------------------------------------
    def exec_stream(self, command: str, stop_event: Optional[threading.Event] = None
                    ) -> Iterator[str]:
        if not self.state.is_reachable(self.node):
            yield "[stream] no route to host"
            return
        kind = ("servicec" if "serviceC.log" in command
                else "check" if "serviceB.log" in command else "links")
        while not (stop_event and stop_event.is_set()):
            line = self._stream_line(kind)
            if line:
                yield line
            # pace the synthetic stream
            for _ in range(10):
                if stop_event and stop_event.is_set():
                    return
                time.sleep(0.05)

    def _stream_line(self, kind: str) -> str:
        if kind == "servicec":
            return self.state.servicec_stats(self.node)
        if kind == "check":
            return (self.state.check_lines(self.node).splitlines() or [""])[0]
        rx = self.state.links_log_tail(self.node, n=1)
        return rx.splitlines()[0] if rx else ""

    # ---- file ops (no-ops in mock) --------------------------------------
    def get_file(self, remote_path: str, local_path: str):
        return False, "[mock] no file transfer"

    def put_file(self, local_path: str, remote_path: str):
        return True, ""

    def file_exists(self, remote_path: str) -> bool:
        return True
