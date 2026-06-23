"""
Generic sequence engine: read the per-app ordered step lists from
``domain/sequences.yaml`` and enforce their ordering invariants.

This is the engine-side, **pure** half of orchestration sequencing. The *what runs
and in what order* is data (``domain/sequences.yaml``, owned by the Compiler); the
*how it is run* — fan-out, per-node locking, health-wait guards, audit — stays in
:mod:`core.orchestrator`. Keeping the order in data + enforcing the declared
``invariants`` here is how "ordering invariants stay enforced generically" (the
serviceA-before-serviceB and serviceC-before-serviceA rules) survives regeneration.
"""

from typing import Any, Dict, List, Tuple

import domain


class SequenceError(ValueError):
    """A sequence definition violates a declared ordering invariant."""


def load() -> Dict[str, Any]:
    """Load the variant-aware sequence definitions from the domain pack."""
    return domain.load_sequences()


# --- step extraction (shapes match what the orchestrator runs) ---------------
def deploy_steps(seqs: Dict[str, Any], build: bool = False) -> List[Tuple[str, str]]:
    """``[(role, action), …]`` for a deploy (+ build steps when ``build``)."""
    dep = seqs.get("deploy", {}) or {}
    steps = [(s["role"], s["action"]) for s in (dep.get("steps") or [])]
    if build:
        steps += [(s["role"], s["action"]) for s in (dep.get("build_steps") or [])]
    return steps


def bring_up_steps(seqs: Dict[str, Any], variant: str) -> List[Tuple[str, str, str]]:
    """``[(role, start_action, status_action), …]`` for a node's bring-up."""
    variants = (seqs.get("bring_up", {}) or {}).get("variants", {}) or {}
    rows = variants.get(variant) or []
    return [(r["role"], r["start"], r["status"]) for r in rows]


def tear_down_steps(seqs: Dict[str, Any], variant: str) -> List[Tuple[str, str]]:
    """``[(role, action), …]`` for a node's tear-down."""
    variants = (seqs.get("tear_down", {}) or {}).get("variants", {}) or {}
    rows = variants.get(variant) or []
    return [(r["role"], r["action"]) for r in rows]


# --- invariant enforcement (generic) -----------------------------------------
def _actual_order(rows: List[dict], key: str) -> List[str]:
    return [r[key] for r in rows if key in r]


def _check_relative_order(actual: List[str], required: List[str], where: str):
    """The members of ``required`` that appear in ``actual`` must keep ``required``'s
    relative order — otherwise the operator pinned a contradictory sequence."""
    positions = [actual.index(a) for a in required if a in actual]
    if positions != sorted(positions):
        raise SequenceError(
            f"{where}: ordering invariant violated — expected "
            f"{[a for a in required if a in actual]} in that relative order, "
            f"got {actual}")


def validate(seqs: Dict[str, Any]) -> None:
    """Raise :class:`SequenceError` if any sequence breaks its declared invariant.

    Called once at orchestrator construction so a mis-ordered ``domain/sequences.yaml``
    fails fast (never runs a bring-up that violates serviceA-before-serviceB, etc.).
    """
    inv = seqs.get("invariants", {}) or {}
    for variant, rows in ((seqs.get("bring_up", {}) or {}).get("variants", {}) or {}).items():
        required = (inv.get("bring_up", {}) or {}).get(variant)
        if required:
            _check_relative_order(_actual_order(rows, "start"), required,
                                  f"bring_up[{variant}]")
    for variant, rows in ((seqs.get("tear_down", {}) or {}).get("variants", {}) or {}).items():
        required = (inv.get("tear_down", {}) or {}).get(variant)
        if required:
            _check_relative_order(_actual_order(rows, "action"), required,
                                  f"tear_down[{variant}]")
