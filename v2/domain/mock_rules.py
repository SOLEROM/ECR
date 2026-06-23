"""
domain/mock_rules.py — the ``--mock`` producer side of the string contract.

When ``app.py --mock`` runs, the mock SSH backend pattern-matches the **real
synthesized commands** and answers from an in-memory world. The *generic* part of
that (the pidfile/systemd supervisor protocol, stream pacing, file-op no-ops) lives
in :mod:`core.mock_ssh`. The *domain* part lives here:

  - which daemons exist (``serviceA`` / ``serviceB`` / ``serviceC``),
  - how a collector/probe command is recognised, and
  - the exact text each collector/probe emits.

These emitted strings are the partner of :mod:`domain.gates`'s parsers — a renamed
log tag or probe string must change on **both** sides or ``--mock`` goes dark while
unit tests still pass (CLAUDE.md §7). They are co-located in ``domain/`` precisely so
the Compiler regenerates the two halves together. ``/tmp/ccflet`` and the
``ccflet-*`` markers are load-bearing brand tokens — keep them.
"""

import json
import re
import time

# the parser side's expectations — referenced here so producer and consumer of the
# contract literally share the "good" value and the log tags.
from .gates import CHECK_GOOD, CHECK_TAG, CHECK2_TAG

# daemon name (as embedded in /tmp/ccflet/<name>.{pid,log} and systemd units) →
# internal state key. The names line up, so this is effectively an identity map.
DAEMONS = ("serviceA", "serviceB", "serviceC")
DAEMON_KEY = {d: d for d in DAEMONS}

PIDFILE_RE = re.compile(r"/tmp/ccflet/([\w.-]+)\.(?:pid|log)")
SYSTEMD_RE = re.compile(r"systemctl\s+(start|stop)\s+(\S+)")

# systemd unit the supervisor may prefer (the one daemon shipped as a unit in the demo)
SYSTEMD_UNIT = "serviceA"


def daemon_name(command: str):
    """Which daemon a synthesized supervisor command targets (or None)."""
    m = PIDFILE_RE.search(command)
    if m:
        return DAEMON_KEY.get(m.group(1))
    m = SYSTEMD_RE.search(command)  # systemd-only stop without a pidfile
    if m:
        return DAEMON_KEY.get(m.group(2).replace(".service", ""))
    return None


def systemd_installed(state, command: str) -> bool:
    """Does `systemctl cat` find a unit for this command in the simulated world?"""
    return state.systemd_serviceA and SYSTEMD_UNIT in command


# --- simulated peers / collector + probe content ----------------------------
def peer_ids(state, node: str):
    """Peers this node hears: other reachable nodes with serviceA up, **same variant**.

    Two nodes in different variants form one-way links (different egress / broadcast
    address), so they don't hear each other — modeling the physical constraint the
    per-node-variant mechanism hands to the operator.
    """
    if not state.is_up(node, "serviceA") or node in state.offline:
        return []
    my_variant = state.node_variant(node)
    peers = []
    for other in state.fleet.nodes:
        if other.name == node or other.name in state.offline:
            continue
        if state.is_up(other.name, "serviceA") and state.node_variant(other.name) == my_variant:
            peers.append(other.id)
    return sorted(peers)


def links_json(state, node: str) -> str:
    ids = peer_ids(state, node)
    me = state.fleet.get(node)
    now = time.time()
    peers = {}
    for pid in ids:
        age = 40 + ((pid * 37 + int(now * 5)) % 220)  # < 1s, jitters over time
        peers[str(pid)] = {"last_seen_unix": round(now - age / 1000, 3), "age_ms": age}
    return '{"own_id": %d, "peers": %s}' % (me.id, json.dumps(peers))


def links_log_tail(state, node: str, n: int = 200) -> str:
    ids = peer_ids(state, node)
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


def check_lines(state, node: str) -> str:
    """serviceB.log tail: [CHECK] (path 1), plus [CHECK2] (path 2) in variant B."""
    if not state.is_up(node, "serviceB") or node in state.offline:
        return ""
    now = time.time()
    age = round(0.05 + (int(now * 3) % 30) / 100, 2)
    lines = [f"{CHECK_TAG} value={CHECK_GOOD} age={age} unit=ok"]
    if state.node_variant(node) == "B" and state.is_up(node, "serviceC"):
        lines.append(f"{CHECK2_TAG} value={CHECK_GOOD} age={age} unit=ok")
    return "\n".join(lines)


def servicec_stats(state, node: str) -> str:
    if not state.is_up(node, "serviceC") or node in state.offline:
        return ""
    n_peers = len(peer_ids(state, node))
    now = int(time.time()) % 1000
    up = 20
    down = 20 * max(n_peers, 0)
    signal = -68 - (hash(node) % 20)
    return (f"+{now}s up={up} ({up}/s) down={down} ({down}/s) "
            f"drop: bad_lan=0 loop={down} bad_air=0 self=0 "
            f"err: tx=0 lan=0 signal={signal}dB")


def probe_a_text(state, node: str) -> str:
    return ("status/probeA:\n  PROBEA: READY"
            if state.is_reachable(node) else "connection refused")


def probe_b_text(state, node: str) -> str:
    return ("status/probeB:\n  value: 3\n  PROBEB_OK"
            if state.is_reachable(node) else "connection refused")


# --- command routing (which read a synthesized command maps to) -------------
def is_build_command(command: str) -> bool:
    return "make" in command and "serviceA" in command


def do_build(state, node: str) -> str:
    state.built[node] = True
    return "cc ... serviceA.c -o serviceA\n[mock] build ok"


def domain_read(state, node: str, command: str):
    """Return the simulated stdout for a build/probe/collector command, or None if
    this command is not a recognised domain read (the caller then falls through to
    the generic supervisor matches / echo)."""
    if is_build_command(command):
        return do_build(state, node)
    if "probeA" in command:
        return probe_a_text(state, node)
    if "probeB" in command:
        return probe_b_text(state, node)
    if "links.json" in command or ("serviceA.rx" in command and "tail" in command):
        return (links_json(state, node) if state.systemd_serviceA
                else links_log_tail(state, node))
    if "serviceB.log" in command and "tail" in command:
        return check_lines(state, node)
    if "serviceC.log" in command and "tail" in command:
        return servicec_stats(state, node)
    return None


def stream_kind(command: str) -> str:
    """The collector kind a live `tail -F` command streams."""
    if "serviceC.log" in command:
        return "servicec"
    if "serviceB.log" in command:
        return "check"
    return "links"


def stream_line(state, node: str, kind: str) -> str:
    if kind == "servicec":
        return servicec_stats(state, node)
    if kind == "check":
        return (check_lines(state, node).splitlines() or [""])[0]
    rx = links_log_tail(state, node, n=1)
    return rx.splitlines()[0] if rx else ""
