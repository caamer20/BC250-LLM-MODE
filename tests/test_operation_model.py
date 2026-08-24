"""Session 5A: the frozen ADR 002 state machine and typed records.

The transition tables are the contract: complete, terminal-frozen, and
rejecting unknown strings before anything reaches persistence.
"""

from __future__ import annotations

import pytest

from bc250_llm_mode.operations.model import (
    TERMINAL_STATES,
    TRANSITIONS,
    InvalidTransition,
    OperationState,
    OperationType,
    StepState,
    assert_step_transition,
    assert_transition,
    can_transition,
    is_terminal,
)


def test_every_operation_state_has_a_table_entry():
    for state in OperationState:
        assert state in TRANSITIONS, f"{state} missing from transition table"
        assert isinstance(TRANSITIONS[state], frozenset)


def test_every_transition_target_is_a_declared_state():
    for source, targets in TRANSITIONS.items():
        for target in targets:
            assert target in TRANSITIONS, (
                f"{source} -> {target}: target not a declared state"
            )


def test_terminals_have_no_outgoing_transitions():
    for terminal in TERMINAL_STATES:
        assert TRANSITIONS[terminal] == frozenset(), (
            f"terminal {terminal} must have no outgoing transitions"
        )


def test_failed_safe_distinct_from_failed_rolled_back():
    """ADR 002 correction: pre-mutation failure has its own terminal."""
    assert OperationState.FAILED_SAFE in TERMINAL_STATES
    assert OperationState.FAILED_ROLLED_BACK in TERMINAL_STATES
    assert OperationState.FAILED_SAFE != OperationState.FAILED_ROLLED_BACK
    # RUNNING may end either way depending on what compensation proves.
    assert OperationState.FAILED_SAFE in TRANSITIONS[OperationState.RUNNING]
    assert (
        OperationState.FAILED_ROLLED_BACK
        in TRANSITIONS[OperationState.RUNNING]
    )


@pytest.mark.parametrize(
    "source,target",
    [
        ("QUEUED", "PREPARING"),
        ("QUEUED", "CANCELLED"),
        ("QUEUED", "FAILED_SAFE"),
        ("PREPARING", "RUNNING"),
        ("RUNNING", "VERIFYING"),
        ("RUNNING", "COMMITTING"),
        ("RUNNING", "ROLLING_BACK"),
        ("VERIFYING", "COMMITTING"),
        ("COMMITTING", "SUCCEEDED"),
        ("CANCEL_REQUESTED", "CANCELLED"),
        ("ROLLING_BACK", "FAILED_ROLLED_BACK"),
        ("ROLLING_BACK", "RECOVERY_REQUIRED"),
        ("ROLLING_BACK", "CANCELLED"),
        ("PAUSED", "RUNNING"),
        ("PAUSED", "CANCEL_REQUESTED"),
    ],
)
def test_allowed_transitions_succeed(source, target):
    assert can_transition(source, target) is True
    assert_transition(source, target)  # must not raise


@pytest.mark.parametrize(
    "source,target",
    [
        # Cancellation can never be requested inside the critical section.
        ("COMMITTING", "CANCEL_REQUESTED"),
        # Terminals are frozen.
        ("SUCCEEDED", "RUNNING"),
        ("SUCCEEDED", "FAILED_SAFE"),
        ("CANCELLED", "QUEUED"),
        ("FAILED_SAFE", "RUNNING"),
        ("FAILED_ROLLED_BACK", "RECOVERY_REQUIRED"),
        ("RECOVERY_REQUIRED", "SUCCEEDED"),
        # No skipping over the critical section.
        ("RUNNING", "SUCCEEDED"),
        ("VERIFYING", "SUCCEEDED"),
        # No resurrection of rollback.
        ("QUEUED", "ROLLING_BACK"),
        ("PREPARING", "COMMITTING"),
        # Same-state operation transitions are never allowed.
        ("RUNNING", "RUNNING"),
    ],
)
def test_disallowed_transitions_raise(source, target):
    assert can_transition(source, target) is False
    with pytest.raises(InvalidTransition):
        assert_transition(source, target)


def test_unknown_state_strings_are_rejected():
    with pytest.raises(ValueError):
        OperationState("NOT_A_STATE")
    with pytest.raises(ValueError):
        can_transition("BOGUS", "RUNNING")


def test_unknown_operation_type_strings_are_rejected():
    with pytest.raises(ValueError):
        OperationType("TELEPORT_MODEL")


def test_step_transitions_allow_reclaim_but_not_skipping():
    assert_step_transition("PENDING", "RUNNING")
    assert_step_transition("RUNNING", "CHECKPOINTED")
    assert_step_transition("RUNNING", "RUNNING")  # reclaim after death
    assert_step_transition("CHECKPOINTED", "VERIFIED")
    assert_step_transition("VERIFIED", "COMPENSATING")
    assert_step_transition("COMPENSATING", "COMPENSATED")

    with pytest.raises(InvalidTransition):
        assert_step_transition("PENDING", "CHECKPOINTED")  # no skipped intent
    with pytest.raises(InvalidTransition):
        assert_step_transition("PENDING", "VERIFIED")
    with pytest.raises(InvalidTransition):
        assert_step_transition("COMPENSATED", "RUNNING")
    with pytest.raises(InvalidTransition):
        assert_step_transition("FAILED", "RUNNING")


def test_is_terminal_accepts_plain_strings():
    assert is_terminal("SUCCEEDED") is True
    assert is_terminal("RUNNING") is False
