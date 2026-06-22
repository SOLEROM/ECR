"""
Status collectors → structured health → GATE A–D map.

Everything in this module is **pure**: parsers turn raw remote text into numbers,
`build_status` folds the collected signals into a NodeStatus, and `compute_gates`
maps that to the health-checklist gates, gated by the node's variant. The
orchestrator does the I/O (running collectors over SSH) and hands the text here,
which keeps this the heavily-tested, network-free core.

The four gates are generic:
  - GATE A — reachability (roleA always; roleB + probes in variant B)
  - GATE B — processes (serviceA + serviceB; serviceC in variant B)
  - GATE C — check (a per-variant-B sensor/value check; N/A in variant A)
  - GATE D — link (peer/link liveness; serviceC transport stats in variant B)
"""

import json
import re
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict

# GATE states
OK, WARN, FAIL, NA = "ok", "warn", "fail", "na"

# thresholds
LINK_FRESH_MS = 1000          # links.json age considered "live"
CHECK_FRESH_S = 1.0           # check value age considered fresh
CHECK_GOOD = 3                # the "good" check value (e.g. a full reading)
SERVICEC_MIN_UP = 15          # serviceC frames/s floor, allow slack
SIGNAL_OK_RANGE = (-95, -40)  # serviceC signal sane window


# --- parsers -----------------------------------------------------------------
def parse_links(text: str, own_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Parse link/peer liveness. Prefers serviceA's links.json
    ({own_id, peers:{id:{last_seen_unix, age_ms}}}); falls back to a recent-log
    tail (distinct recent `from=<id>`). Returns {source, peers:{id:age_ms|None}, count}.
    """
    text = (text or "").strip()
    if not text:
        return {"source": None, "peers": {}, "count": 0}
    # try JSON links.json
    try:
        data = json.loads(text)
        peers_raw = data.get("peers", data) if isinstance(data, dict) else {}
        peers: Dict[int, Optional[int]] = {}
        for k, v in peers_raw.items():
            try:
                pid = int(k)
            except (TypeError, ValueError):
                continue
            if own_id is not None and pid == own_id:
                continue
            if isinstance(v, dict):
                peers[pid] = v.get("age_ms")
            elif isinstance(v, (int, float)):
                peers[pid] = int(v)
            else:
                peers[pid] = None
        return {"source": "links.json", "peers": peers, "count": len(peers)}
    except (json.JSONDecodeError, ValueError):
        pass
    # fall back to a recent-log tail
    ids = {}
    for m in re.finditer(r"from=(\d+)", text):
        pid = int(m.group(1))
        if own_id is not None and pid == own_id:
            continue
        ids[pid] = None
    return {"source": "log", "peers": ids, "count": len(ids)}


def parse_check(text: str, tag: str = "[CHECK]") -> Dict[str, Any]:
    """Parse the last `tag` line for a numeric value and age."""
    text = text or ""
    last = None
    for line in text.splitlines():
        if tag in line:
            last = line
    if last is None:
        return {"present": False, "value": None, "age": None, "raw": ""}
    val = re.search(r"value\s*[=:]\s*(\d+)", last)
    age = re.search(r"age\s*[=:]\s*([\d.]+)", last)
    return {
        "present": True,
        "value": int(val.group(1)) if val else None,
        "age": float(age.group(1)) if age else None,
        "raw": last.strip(),
    }


def parse_servicec_stats(text: str) -> Dict[str, Any]:
    """Parse the serviceC 1 Hz stats line (up/down/loop/self/err tx/signal)."""
    text = text or ""
    last = None
    for line in text.splitlines():
        if "up=" in line:
            last = line
    if last is None:
        return {"present": False}

    def grab(pat, cast=int, default=None):
        m = re.search(pat, last)
        return cast(m.group(1)) if m else default

    return {
        "present": True,
        "up": grab(r"up=(\d+)"),
        "down": grab(r"down=(\d+)"),
        "loop": grab(r"loop=(\d+)"),
        "self": grab(r"self=(\d+)"),
        "err_tx": grab(r"\btx=(\d+)"),
        "signal": grab(r"signal=(-?\d+)"),
        "raw": last.strip(),
    }


def parse_probe_a(text: str) -> bool:
    """True when probe A reports READY."""
    t = (text or "")
    return bool(re.search(r"PROBEA\s*[:=]?\s*READY", t)) or "PROBEA: READY" in t


def parse_probe_b(text: str) -> bool:
    """True when probe B reports OK/synced."""
    return "PROBEB_OK" in (text or "")


# --- node status -------------------------------------------------------------
@dataclass
class NodeStatus:
    node: str
    variant: str
    reachable_roleA: bool = False
    reachable_roleB: Optional[bool] = None
    probe_a_ok: Optional[bool] = None
    probe_b_ok: Optional[bool] = None
    serviceA: Dict[str, Any] = field(default_factory=lambda: {"up": False})
    serviceB: Dict[str, Any] = field(default_factory=lambda: {"up": False})
    serviceC: Optional[Dict[str, Any]] = None
    links: Dict[str, Any] = field(default_factory=lambda: {"count": 0, "peers": {}})
    expected_links: int = 0
    check1: Dict[str, Any] = field(default_factory=lambda: {"present": False})
    check2: Optional[Dict[str, Any]] = None
    servicec_stats: Optional[Dict[str, Any]] = None
    gates: Dict[str, Dict[str, str]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _gate(state: str, detail: str) -> Dict[str, str]:
    return {"state": state, "detail": detail}


def compute_gates(ns: NodeStatus) -> Dict[str, Dict[str, str]]:
    """Map a NodeStatus to GATE A–D, gated by the node's variant."""
    variant = ns.variant
    gates: Dict[str, Dict[str, str]] = {}

    # GATE A — reachability
    if not ns.reachable_roleA:
        gates["A"] = _gate(FAIL, "roleA unreachable")
    elif variant == "A":
        gates["A"] = _gate(OK, "roleA reachable")
    else:
        problems = []
        if ns.reachable_roleB is False:
            problems.append("roleB unreachable")
        if ns.probe_a_ok is False:
            problems.append("probe A not READY")
        if ns.probe_b_ok is False:
            problems.append("probe B not OK")
        if problems:
            gates["A"] = _gate(FAIL, "; ".join(problems))
        elif ns.reachable_roleB is None:
            gates["A"] = _gate(WARN, "roleB not yet probed")
        else:
            gates["A"] = _gate(OK, "roleA+roleB, probe A READY, probe B OK")

    # GATE B — processes
    serviceA_up = bool(ns.serviceA.get("up"))
    serviceB_up = bool(ns.serviceB.get("up"))
    b_problems = []
    if not serviceA_up:
        b_problems.append("serviceA down")
    if not serviceB_up:
        b_problems.append("serviceB down")
    if variant == "B":
        serviceC_up = bool(ns.serviceC and ns.serviceC.get("up"))
        if not serviceC_up:
            b_problems.append("serviceC down")
    gates["B"] = _gate(FAIL if b_problems else OK,
                       "; ".join(b_problems) if b_problems else "serviceA + serviceB up")

    # GATE C — check (variant B only)
    if variant == "A":
        gates["C"] = _gate(NA, "no check (variant A)")
    else:
        c1, c2 = ns.check1, ns.check2 or {"present": False}
        def fresh(c):
            return (c.get("present") and c.get("value") == CHECK_GOOD
                    and (c.get("age") is None or c.get("age") <= CHECK_FRESH_S))
        if fresh(c1) and fresh(c2):
            gates["C"] = _gate(OK, f"both checks fresh, value={CHECK_GOOD}")
        elif c1.get("present") or c2.get("present"):
            gates["C"] = _gate(WARN, "check present but stale/low-value")
        else:
            gates["C"] = _gate(FAIL, "no check")

    # GATE D — link
    count = ns.links.get("count", 0)
    expected = ns.expected_links
    ages = [a for a in ns.links.get("peers", {}).values() if isinstance(a, (int, float))]
    stale = [a for a in ages if a > LINK_FRESH_MS]
    if expected <= 0:
        if count == 0:
            # single-node fleet (or none expected) — link gate is not applicable
            gates["D"] = _gate(NA, "no peers expected (single node)")
            return gates
        link_ok = True
        link_detail = f"{count} peer(s)"
    else:
        link_ok = count >= expected and not stale
        link_detail = f"{count}/{expected} peers" + (" (some stale)" if stale else "")
    if variant == "B":
        cs = ns.servicec_stats or {"present": False}
        if cs.get("present"):
            up = cs.get("up") or 0
            servicec_ok = (up >= SERVICEC_MIN_UP and (cs.get("self") or 0) == 0
                           and (cs.get("err_tx") or 0) == 0
                           and cs.get("signal") is not None
                           and SIGNAL_OK_RANGE[0] <= cs["signal"] <= SIGNAL_OK_RANGE[1])
            link_detail += f" · serviceC up={up}/s signal={cs.get('signal')}dB"
        else:
            servicec_ok = False
            link_detail += " · no serviceC stats"
        link_ok = link_ok and servicec_ok
    if count == 0 and expected > 0:
        gates["D"] = _gate(FAIL, link_detail)
    else:
        gates["D"] = _gate(OK if link_ok else WARN, link_detail)

    return gates


def build_status(node: str, variant: str, raw: Dict[str, Any], expected_links: int = 0,
                 own_id: Optional[int] = None) -> NodeStatus:
    """
    Fold collected raw signals into a NodeStatus + gates. `raw` keys (all optional):
      reachable_roleA, reachable_roleB, serviceA, serviceB, serviceC (status dicts),
      links_text, check1_text, check2_text, servicec_text, probe_a_text, probe_b_text
    """
    ns = NodeStatus(node=node, variant=variant, expected_links=expected_links)
    ns.reachable_roleA = bool(raw.get("reachable_roleA", False))
    if variant == "B":
        ns.reachable_roleB = raw.get("reachable_roleB")
        ns.probe_a_ok = (parse_probe_a(raw["probe_a_text"])
                         if raw.get("probe_a_text") is not None else None)
        ns.probe_b_ok = (parse_probe_b(raw["probe_b_text"])
                         if raw.get("probe_b_text") is not None else None)
    ns.serviceA = raw.get("serviceA") or {"up": False}
    ns.serviceB = raw.get("serviceB") or {"up": False}
    if variant == "B":
        ns.serviceC = raw.get("serviceC") or {"up": False}
    ns.links = parse_links(raw.get("links_text", ""), own_id=own_id)
    ns.check1 = parse_check(raw.get("check1_text", ""), tag="[CHECK]")
    if variant == "B":
        # [CHECK2] is logged alongside [CHECK] in serviceB's stdout (serviceB.log), so
        # check path 2 falls back to the path-1 text when no separate feed is given.
        check2_text = raw.get("check2_text") or raw.get("check1_text", "")
        ns.check2 = parse_check(check2_text, tag="[CHECK2]")
        ns.servicec_stats = parse_servicec_stats(raw.get("servicec_text", ""))
    ns.gates = compute_gates(ns)
    return ns


def overall_gate(gates: Dict[str, Dict[str, str]]) -> str:
    """Roll the per-gate states into one summary color for a node card."""
    states = [g.get("state") for g in gates.values() if g.get("state") != NA]
    if not states:
        return NA
    if any(s == FAIL for s in states):
        return FAIL
    if any(s == WARN for s in states):
        return WARN
    return OK
