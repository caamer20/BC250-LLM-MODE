"""Interruption classification and recovery decisions (ADR 002 §9).

The vocabulary is a closed enum; a decision carries only sanitized evidence
(stable reason code, bounded detail) plus an optional recovered output that
must still pass normal output validation before persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .model import OperationState


class RecoveryClass(str, Enum):
    """What an interrupted step's external effect looks like right now."""

    ABSENT = "ABSENT"
    COMPLETE = "COMPLETE"
    PARTIALLY_RESUMABLE = "PARTIALLY_RESUMABLE"
    DISCARDABLE = "DISCARDABLE"
    REVERTIBLE = "REVERTIBLE"
    UNCERTAIN_MANUAL = "UNCERTAIN_MANUAL"


class RecoveryAction(str, Enum):
    """What the executor should do next for an interrupted step."""

    EXECUTE = "EXECUTE"
    VERIFY = "VERIFY"
    RESUME = "RESUME"
    DISCARD_AND_RETRY = "DISCARD_AND_RETRY"
    ROLL_BACK = "ROLL_BACK"
    PAUSE = "PAUSE"
    REQUIRE_RECOVERY = "REQUIRE_RECOVERY"


@dataclass(frozen=True)
class RecoveryDecision:
    classification: RecoveryClass
    reason_code: str
    action: RecoveryAction
    detail: dict[str, Any] = field(default_factory=dict)
    recovered_output: dict[str, Any] | None = None


# Default classification → action mapping given the durable step state
# (ADR 002 §10 / Session 5B plan §10). Workflows may refine within these
# bounds but may not invent actions outside RecoveryAction.
_DEFAULT_ACTIONS: dict[tuple[RecoveryClass, str], RecoveryAction] = {
    (RecoveryClass.ABSENT, "RUNNING"): RecoveryAction.EXECUTE,
    (RecoveryClass.COMPLETE, "RUNNING"): RecoveryAction.VERIFY,
    (RecoveryClass.PARTIALLY_RESUMABLE, "RUNNING"): RecoveryAction.RESUME,
    (RecoveryClass.DISCARDABLE, "RUNNING"): RecoveryAction.DISCARD_AND_RETRY,
    (RecoveryClass.REVERTIBLE, "RUNNING"): RecoveryAction.ROLL_BACK,
}


def decide_recovery(
    classification: RecoveryClass,
    *,
    operation_state: OperationState | str,
    safe_if_uncertain: bool,
    auto_recovery_allowed: bool = True,
) -> RecoveryDecision:
    """Typed decision for an interrupted RUNNING step."""
    state = OperationState(operation_state).value
    if classification is RecoveryClass.UNCERTAIN_MANUAL:
        if safe_if_uncertain:
            return RecoveryDecision(
                classification,
                "UNCERTAIN_BUT_QUIESCENT",
                RecoveryAction.PAUSE,
            )
        return RecoveryDecision(
            classification,
            "UNCERTAIN_UNSAFE",
            RecoveryAction.REQUIRE_RECOVERY,
        )
    action = _DEFAULT_ACTIONS.get((classification, state))
    if action is None:
        return RecoveryDecision(
            classification,
            "NO_AUTOMATIC_ACTION",
            RecoveryAction.PAUSE
            if auto_recovery_allowed
            else RecoveryAction.REQUIRE_RECOVERY,
        )
    if not auto_recovery_allowed and action in (
        RecoveryAction.EXECUTE,
        RecoveryAction.RESUME,
        RecoveryAction.DISCARD_AND_RETRY,
    ):
        return RecoveryDecision(
            classification,
            "AUTO_RECOVERY_DISALLOWED",
            RecoveryAction.PAUSE,
        )
    return RecoveryDecision(classification, f"CLASSIFIED_{classification.value}", action)
