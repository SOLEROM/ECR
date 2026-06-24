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
# contract literally share the "good" value, the log tags and the probe markers. There is
# ONE source for these strings (domain.gates); the Compiler patches them there and this
# producer follows automatically, so the two halves of the contract can't drift.
from .gates import (CHECK_GOOD, CHECK_TAG, CHECK2_TAG, PROBE_A_READY, PROBE_B_OK,
                    CHECK_VALUE_KEY, SIGNAL_KEY,
                    LINKS_CMD_MARK, CHECK_LOG_MARK, SERVICEC_LOG_MARK,
                    PROBE_A_CMD_MARK, PROBE_B_CMD_MARK, BUILD_CMD_MARK)

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
    lines = [f"{CHECK_TAG} {CHECK_VALUE_KEY}={CHECK_GOOD} age={age} unit=ok"]
    if state.node_variant(node) == "B" and state.is_up(node, "serviceC"):
        lines.append(f"{CHECK2_TAG} {CHECK_VALUE_KEY}={CHECK_GOOD} age={age} unit=ok")
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
            f"err: tx=0 lan=0 {SIGNAL_KEY}={signal}dB")


def probe_a_text(state, node: str) -> str:
    return (f"status/probeA:\n  {PROBE_A_READY}"
            if state.is_reachable(node) else "connection refused")


def probe_b_text(state, node: str) -> str:
    return (f"status/probeB:\n  value: {CHECK_GOOD}\n  {PROBE_B_OK}"
            if state.is_reachable(node) else "connection refused")


# --- command routing (which read a synthesized command maps to) -------------
def is_build_command(command: str) -> bool:
    return "make" in command and BUILD_CMD_MARK in command


def do_build(state, node: str) -> str:
    state.built[node] = True
    return "cc ... serviceA.c -o serviceA\n[mock] build ok"


def domain_read(state, node: str, command: str):
    """Return the simulated stdout for a build/probe/collector command, or None if
    this command is not a recognised domain read (the caller then falls through to
    the generic supervisor matches / echo). Commands are matched by the spec-driven
    routing markers in :mod:`domain.gates`, so a fork's renamed log files / probe
    endpoints route correctly here without editing this file."""
    if is_build_command(command):
        return do_build(state, node)
    if PROBE_A_CMD_MARK in command:
        return probe_a_text(state, node)
    if PROBE_B_CMD_MARK in command:
        return probe_b_text(state, node)
    if LINKS_CMD_MARK in command or ("serviceA.rx" in command and "tail" in command):
        return (links_json(state, node) if state.systemd_serviceA
                else links_log_tail(state, node))
    if CHECK_LOG_MARK in command and "tail" in command:
        return check_lines(state, node)
    if SERVICEC_LOG_MARK in command and "tail" in command:
        return servicec_stats(state, node)
    return None


def stream_kind(command: str) -> str:
    """The collector kind a live `tail -F` command streams."""
    if SERVICEC_LOG_MARK in command:
        return "servicec"
    if CHECK_LOG_MARK in command:
        return "check"
    return "links"


def stream_line(state, node: str, kind: str) -> str:
    if kind == "servicec":
        return servicec_stats(state, node)
    if kind == "check":
        return (check_lines(state, node).splitlines() or [""])[0]
    rx = links_log_tail(state, node, n=1)
    return rx.splitlines()[0] if rx else ""


# --- gates (the --mock producer for config-driven gates) --------------------
def gate_mock(state, node: str, spec) -> dict:
    """Simulate one config-driven gate (``core/gates_config.GateSpec``) against the
    in-memory fleet — the ``--mock`` side of the Gates subsystem.

    Unlike the old string-contract producers, this keys off the **simulated world**
    (reachable? daemon up?), not a parsed command, so the demo stays faithful with no
    per-command contract to keep in sync (see ``plan2.md`` §7):

      - ``reach``   → ``state.is_reachable`` → the gate's up/down color.
      - ``process`` → ``state.is_up`` per entry, folded by mandatory flags → so a bring-up
        flips the proc gate green, the most valuable mock behavior.
      - ``metric``  → the gate's ``mock.healthy`` fields when its ``mock.up_when`` daemon is
        up (and the node is reachable), else an empty reading → its ``default`` level
        (typically red). So a metric goes green only once its backing service is up.
    """
    from core import gates_config as GC

    reachable = state.is_reachable(node)
    if spec.kind == "reach":
        if reachable:
            return GC.gate_result(spec, spec.colors.get("up", "green"),
                                  "reachable (simulated)")
        return GC.gate_result(spec, spec.colors.get("down", "red"),
                              "no answer (simulated)")
    if not reachable:
        return GC.gate_result(spec, "red", "node unreachable (simulated)")

    if spec.kind == "process":
        procs, mand_down, opt_down = [], False, False
        variant = state.node_variant(node)
        for p in spec.processes:
            if p.variants is not None and variant not in p.variants:
                continue                              # process not present in this variant
            up = bool(state.is_up(node, p.name))
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

    # metric
    up_when = spec.mock.get("up_when")
    healthy = up_when is None or bool(state.is_up(node, up_when))
    fields = dict(spec.mock.get("healthy") or {}) if healthy else {}
    lvl = GC.evaluate_levels(fields, spec.levels)
    detail = GC.render_detail(lvl.detail or spec.detail, fields)
    return GC.gate_result(spec, lvl.color, detail, fields=fields)
