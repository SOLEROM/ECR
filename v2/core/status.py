"""
Generic status fold: collected signals → NodeStatus → GATE map.

This module is the **engine-side, generic** half of health. It owns the
``NodeStatus`` container, the ``build_status`` fold that turns collected raw text
into a structured status, and ``overall_gate`` which rolls the per-gate states into
one card colour. None of that changes per app.

The per-app logic — the **parsers**, the **GATE A–D rules** and their
**thresholds** — lives in :mod:`domain.gates` (loaded here), the partner of
:mod:`domain.mock_rules`. Keeping the two sides of the ``mock ↔ status`` string
contract in ``domain/`` means the Compiler regenerates them together.

For backwards compatibility the historical ``status.parse_*`` / ``status.compute_gates``
names and the threshold constants still resolve on this module — they are forwarded
to :mod:`domain.gates` lazily (see ``__getattr__``), so existing callers and tests
need not know the logic moved.
"""

from typing import Any, Dict, Optional
from dataclasses import dataclass, field, asdict

# GATE states (the vocabulary; shared by the engine and domain.gates)
OK, WARN, FAIL, NA = "ok", "warn", "fail", "na"


def gate(state: str, detail: str) -> Dict[str, str]:
    """Build a single gate cell ``{state, detail}`` (used by domain.gates too)."""
    return {"state": state, "detail": detail}


# kept as a private alias for any in-module use / older imports
_gate = gate


# --- node status (generic container) ----------------------------------------
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


def build_status(node: str, variant: str, raw: Dict[str, Any], expected_links: int = 0,
                 own_id: Optional[int] = None) -> NodeStatus:
    """
    Fold collected raw signals into a NodeStatus + gates. `raw` keys (all optional):
      reachable_roleA, reachable_roleB, serviceA, serviceB, serviceC (status dicts),
      links_text, check1_text, check2_text, servicec_text, probe_a_text, probe_b_text

    The per-app parsers and gate rules come from :mod:`domain.gates` (imported here
    lazily so this module loads without a domain→core→domain cycle).
    """
    from domain import gates as G

    ns = NodeStatus(node=node, variant=variant, expected_links=expected_links)
    ns.reachable_roleA = bool(raw.get("reachable_roleA", False))
    if variant == "B":
        ns.reachable_roleB = raw.get("reachable_roleB")
        ns.probe_a_ok = (G.parse_probe_a(raw["probe_a_text"])
                         if raw.get("probe_a_text") is not None else None)
        ns.probe_b_ok = (G.parse_probe_b(raw["probe_b_text"])
                         if raw.get("probe_b_text") is not None else None)
    ns.serviceA = raw.get("serviceA") or {"up": False}
    ns.serviceB = raw.get("serviceB") or {"up": False}
    if variant == "B":
        ns.serviceC = raw.get("serviceC") or {"up": False}
    ns.links = G.parse_links(raw.get("links_text", ""), own_id=own_id)
    ns.check1 = G.parse_check(raw.get("check1_text", ""), tag=G.CHECK_TAG)
    if variant == "B":
        # [CHECK2] is logged alongside [CHECK] in serviceB's stdout (serviceB.log), so
        # check path 2 falls back to the path-1 text when no separate feed is given.
        check2_text = raw.get("check2_text") or raw.get("check1_text", "")
        ns.check2 = G.parse_check(check2_text, tag=G.CHECK2_TAG)
        ns.servicec_stats = G.parse_servicec_stats(raw.get("servicec_text", ""))
    ns.gates = G.compute_gates(ns)
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


# --- backwards-compat shim ---------------------------------------------------
# Historical names (status.parse_links, status.compute_gates, status.CHECK_GOOD, …)
# now live in domain.gates. Forward attribute access there so callers/tests that
# reach through `core.status` keep working after the Phase-0 extraction.
_FORWARDED = {
    "parse_links", "parse_check", "parse_servicec_stats", "parse_probe_a",
    "parse_probe_b", "compute_gates",
    "LINK_FRESH_MS", "CHECK_FRESH_S", "CHECK_GOOD", "SERVICEC_MIN_UP",
    "SIGNAL_OK_RANGE", "CHECK_TAG", "CHECK2_TAG",
}


def __getattr__(name: str):
    if name in _FORWARDED:
        from domain import gates as G
        return getattr(G, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
