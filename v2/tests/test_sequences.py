"""Generic sequence engine: step extraction + ordering-invariant enforcement.

These cover the Phase-0 extraction of orchestration step lists into
``domain/sequences.yaml`` + ``core/sequences.py``: the engine must read the
operator-pinned order and refuse a sequence that breaks its declared invariant.
"""

import pytest

from core import sequences as SEQ


def test_default_domain_sequences_validate():
    """The shipped domain/sequences.yaml honours its own invariants."""
    seqs = SEQ.load()
    SEQ.validate(seqs)  # must not raise


def test_deploy_steps_shape_and_build():
    seqs = SEQ.load()
    assert SEQ.deploy_steps(seqs) == [("roleA", "deploy_serviceB"), ("roleA", "deploy_serviceA")]
    assert SEQ.deploy_steps(seqs, build=True)[-1] == ("roleA", "serviceA_build")


def test_bring_up_order_serviceA_before_serviceB():
    seqs = SEQ.load()
    a = [s[1] for s in SEQ.bring_up_steps(seqs, "A")]
    assert a.index("serviceA_start") < a.index("serviceB_start")
    b = [s[1] for s in SEQ.bring_up_steps(seqs, "B")]
    # serviceC before serviceA before serviceB in variant B
    assert b.index("serviceC_start") < b.index("serviceA_start") < b.index("serviceB_start")


def test_bring_up_steps_carry_status_action():
    seqs = SEQ.load()
    for role, start, status in SEQ.bring_up_steps(seqs, "B"):
        assert role in ("roleA", "roleB")
        assert status.endswith("_status")


def test_tear_down_is_reverse_dependency_order():
    seqs = SEQ.load()
    a = [s[1] for s in SEQ.tear_down_steps(seqs, "A")]
    assert a.index("serviceB_stop") < a.index("serviceA_stop")


def test_validate_rejects_out_of_order_bring_up():
    bad = {
        "bring_up": {"variants": {"A": [
            {"role": "roleA", "start": "serviceB_start", "status": "serviceB_status"},
            {"role": "roleA", "start": "serviceA_start", "status": "serviceA_status"},
        ]}},
        "invariants": {"bring_up": {"A": ["serviceA_start", "serviceB_start"]}},
    }
    with pytest.raises(SEQ.SequenceError):
        SEQ.validate(bad)


def test_validate_rejects_out_of_order_tear_down():
    bad = {
        "tear_down": {"variants": {"B": [
            {"role": "roleA", "action": "serviceA_stop"},
            {"role": "roleA", "action": "serviceB_stop"},
        ]}},
        "invariants": {"tear_down": {"B": ["serviceB_stop", "serviceA_stop"]}},
    }
    with pytest.raises(SEQ.SequenceError):
        SEQ.validate(bad)


def test_validate_ignores_absent_actions():
    """An invariant referencing an action that isn't in the sequence is a no-op."""
    ok = {
        "bring_up": {"variants": {"A": [
            {"role": "roleA", "start": "serviceA_start", "status": "serviceA_status"},
        ]}},
        "invariants": {"bring_up": {"A": ["serviceC_start", "serviceA_start", "serviceB_start"]}},
    }
    SEQ.validate(ok)  # only serviceA_start present → trivially ordered
