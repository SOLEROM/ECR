"""
Generic per-node status container + the gate-rollup vocabulary.

This module is the **engine-side, generic** half of health. It owns the gate-state
vocabulary (``OK/WARN/FAIL/NA``), the ``NodeStatus`` container the orchestrator fills
from the configured gates, and ``overall_gate`` which rolls the per-gate severities into
one card color. None of that changes per app.

The gate **logic** is no longer here (or in ``domain/gates.py``): it is operator-editable
config under ``gates/``, parsed + evaluated by the generic engine ``core/gates_config.py``
and run by ``core/orchestrator.py`` (config over code, P8 — see ``plan2.md``). A
``GateResult`` (built by ``gates_config.gate_result``) carries a named ``color`` *and* a
derived ``state`` (severity), so this rollup and the Compiler acceptance gate keep working
unchanged while the operator only ever picks colors.
"""

from typing import Any, Dict, Optional
from dataclasses import dataclass, field, asdict

# GATE severities (the rollup vocabulary; shared with core.gates_config)
OK, WARN, FAIL, NA = "ok", "warn", "fail", "na"


# --- node status (generic container) ----------------------------------------
@dataclass
class NodeStatus:
    """One node's evaluated gates + the reachability the orchestrator observed.

    ``gates`` maps a gate key → the ``gates_config.gate_result`` dict
    (``{key,label,kind,on,color,state,detail,fields,processes}``). ``reachable_*`` are the
    control-plane connects the gate poll made this tick (used for the short-circuit and by
    the UI)."""
    node: str
    variant: str
    reachable_roleA: bool = False
    reachable_roleB: Optional[bool] = None
    gates: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def overall_gate(gates: Dict[str, Dict[str, Any]]) -> str:
    """Roll the per-gate severities into one summary color for a node card.

    Reads each gate's ``state`` (severity, derived from its color by
    ``gates_config.color_to_severity``); ``na`` gates are ignored. Worst wins:
    fail > warn > ok > na."""
    states = [g.get("state") for g in gates.values() if g.get("state") != NA]
    if not states:
        return NA
    if any(s == FAIL for s in states):
        return FAIL
    if any(s == WARN for s in states):
        return WARN
    return OK
