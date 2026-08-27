"""Operation domain model: states, transition tables, typed records.

Everything here is pure — no I/O. Persistence lives in
``operations.repositories``; payload sanitization in ``operations.validation``.
The frozen transition table (ADR 002 §2) is enforced by
:func:`assert_transition` before any row is written.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OperationType(str, Enum):
    """Known durable operation types (ADR 002 §4)."""

    MODEL_ACTIVATE = "MODEL_ACTIVATE"
    MODEL_ACQUIRE = "MODEL_ACQUIRE"
    MODEL_IMPORT = "MODEL_IMPORT"
    MODEL_REMOVE = "MODEL_REMOVE"
    RUNTIME_UPDATE = "RUNTIME_UPDATE"
    RUNTIME_ROLLBACK = "RUNTIME_ROLLBACK"


class OperationState(str, Enum):
    """Closed operation state machine (ADR 002 §1.1/§2)."""

    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    COMMITTING = "COMMITTING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    ROLLING_BACK = "ROLLING_BACK"
    PAUSED = "PAUSED"
    # Terminals — no outgoing transitions.
    SUCCEEDED = "SUCCEEDED"
    CANCELLED = "CANCELLED"
    FAILED_SAFE = "FAILED_SAFE"
    FAILED_ROLLED_BACK = "FAILED_ROLLED_BACK"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class StepState(str, Enum):
    """Step lifecycle (ADR 002 §1.2)."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    CHECKPOINTED = "CHECKPOINTED"
    VERIFIED = "VERIFIED"
    COMPENSATING = "COMPENSATING"
    COMPENSATED = "COMPENSATED"
    FAILED = "FAILED"


TERMINAL_STATES: frozenset[OperationState] = frozenset(
    {
        OperationState.SUCCEEDED,
        OperationState.CANCELLED,
        OperationState.FAILED_SAFE,
        OperationState.FAILED_ROLLED_BACK,
        OperationState.RECOVERY_REQUIRED,
    }
)

# Frozen transition table (ADR 002 §2). Anything absent is disallowed.
TRANSITIONS: dict[OperationState, frozenset[OperationState]] = {
    OperationState.QUEUED: frozenset(
        {
            OperationState.PREPARING,
            OperationState.CANCEL_REQUESTED,
            OperationState.CANCELLED,
            OperationState.PAUSED,
            OperationState.FAILED_SAFE,
        }
    ),
    OperationState.PREPARING: frozenset(
        {
            OperationState.RUNNING,
            OperationState.ROLLING_BACK,
            OperationState.CANCEL_REQUESTED,
            OperationState.PAUSED,
            OperationState.FAILED_SAFE,
        }
    ),
    OperationState.RUNNING: frozenset(
        {
            OperationState.VERIFYING,
            OperationState.COMMITTING,
            OperationState.ROLLING_BACK,
            OperationState.CANCEL_REQUESTED,
            OperationState.PAUSED,
            OperationState.FAILED_SAFE,
            OperationState.FAILED_ROLLED_BACK,
            OperationState.RECOVERY_REQUIRED,
        }
    ),
    OperationState.VERIFYING: frozenset(
        {
            OperationState.COMMITTING,
            OperationState.ROLLING_BACK,
            OperationState.CANCEL_REQUESTED,
            OperationState.PAUSED,
            OperationState.FAILED_SAFE,
            OperationState.FAILED_ROLLED_BACK,
            OperationState.RECOVERY_REQUIRED,
        }
    ),
    # Critical section: cancellation never enters; the section cycles back
    # to VERIFYING after a verified critical step, or to ROLLING_BACK when a
    # mutation-possible failure occurs inside it (Session 5C ADR correction).
    OperationState.COMMITTING: frozenset(
        {
            OperationState.VERIFYING,
            OperationState.ROLLING_BACK,
            OperationState.SUCCEEDED,
            OperationState.FAILED_SAFE,
            OperationState.FAILED_ROLLED_BACK,
            OperationState.RECOVERY_REQUIRED,
        }
    ),
    OperationState.CANCEL_REQUESTED: frozenset(
        {OperationState.ROLLING_BACK, OperationState.CANCELLED}
    ),
    OperationState.ROLLING_BACK: frozenset(
        {
            OperationState.CANCELLED,
            OperationState.FAILED_ROLLED_BACK,
            OperationState.RECOVERY_REQUIRED,
        }
    ),
    OperationState.PAUSED: frozenset(
        {
            OperationState.PREPARING,
            OperationState.RUNNING,
            OperationState.VERIFYING,
            OperationState.CANCEL_REQUESTED,
            OperationState.CANCELLED,
            OperationState.FAILED_SAFE,
        }
    ),
}
for _terminal in TERMINAL_STATES:
    TRANSITIONS[_terminal] = frozenset()

STEP_TRANSITIONS: dict[StepState, frozenset[StepState]] = {
    StepState.PENDING: frozenset({StepState.RUNNING}),
    StepState.RUNNING: frozenset(
        {
            StepState.CHECKPOINTED,
            StepState.FAILED,
            StepState.COMPENSATING,
            StepState.RUNNING,  # reclaim after process death (attempts++)
        }
    ),
    StepState.CHECKPOINTED: frozenset(
        {StepState.VERIFIED, StepState.FAILED, StepState.COMPENSATING}
    ),
    StepState.VERIFIED: frozenset({StepState.COMPENSATING, StepState.FAILED}),
    StepState.COMPENSATING: frozenset({StepState.COMPENSATED, StepState.FAILED}),
    StepState.COMPENSATED: frozenset(),
    StepState.FAILED: frozenset(),
}


def is_terminal(state: OperationState | str) -> bool:
    return OperationState(state) in TERMINAL_STATES


def can_transition(current: OperationState | str, target: OperationState | str) -> bool:
    current = OperationState(current)
    target = OperationState(target)
    if current is target:
        return False  # same-state operation transitions are never allowed
    return target in TRANSITIONS[current]


def assert_transition(current: OperationState | str, target: OperationState | str) -> None:
    """Raise :class:`InvalidTransition` unless the move is in the table."""
    current = OperationState(current)
    target = OperationState(target)
    if not can_transition(current, target):
        raise InvalidTransition(
            f"operation transition {current.value} -> {target.value} is not allowed"
        )


def assert_step_transition(current: StepState | str, target: StepState | str) -> None:
    current = StepState(current)
    target = StepState(target)
    if current is not target and target not in STEP_TRANSITIONS[current]:
        raise InvalidTransition(
            f"step transition {current.value} -> {target.value} is not allowed"
        )


def as_request_dict(decoded_request: Any) -> dict[str, Any]:
    """Canonical plain-dict view of a decoded typed request.

    Typed requests are frozen dataclasses; anything else is rejected so the
    durable request JSON always mirrors a declared shape.
    """
    import dataclasses

    if dataclasses.is_dataclass(decoded_request) and not isinstance(
        decoded_request, type
    ):
        return dataclasses.asdict(decoded_request)
    raise OperationValidationError(
        "decoded requests must be frozen dataclasses;"
        f" got {type(decoded_request).__name__}"
    )


# --- Typed records ----------------------------------------------------------


@dataclass(frozen=True)
class OperationRecord:
    id: str
    operation_type: OperationType
    request_version: int
    recovery_policy_version: int
    request_json: str  # sanitized canonical JSON (never raw caller input)
    state: OperationState
    state_revision: int
    progress_phase: str | None
    progress_current: int
    progress_total: int | None
    progress_unit: str | None
    progress_summary: str | None
    surface: str
    cancel_requested_at: str | None
    result_code: str | None
    result_detail: str | None
    error_code: str | None
    error_detail: str | None
    parent_operation_id: str | None
    created_at: str
    started_at: str | None
    updated_at: str
    finished_at: str | None
    dismissed_at: str | None = None

    @property
    def active(self) -> bool:
        return self.state not in TERMINAL_STATES


@dataclass(frozen=True)
class OperationStepRecord:
    operation_id: str
    step_key: str
    sequence: int
    implementation_version: int
    state: StepState
    attempts: int
    input_json: str | None
    output_json: str | None
    external_effect_id: str | None
    failure_code: str | None
    failure_detail: str | None
    started_at: str | None
    checkpointed_at: str | None
    finished_at: str | None


@dataclass(frozen=True)
class OperationEvent:
    cursor: int
    operation_id: str
    ts: str
    level: str
    code: str | None
    summary: str
    detail: dict[str, Any] | None = field(default=None)
    progress: dict[str, Any] | None = field(default=None)


@dataclass(frozen=True)
class OperationLease:
    resource_key: str
    operation_id: str
    owner: str
    lease_revision: int
    acquired_at: str
    heartbeat_at: str
    expires_at: str


# --- Typed exceptions -------------------------------------------------------


class OperationError(RuntimeError):
    """Base class for durable-operation errors."""


class InvalidTransition(OperationError):
    """A state move outside the frozen ADR 002 tables."""


class OperationConflict(OperationError):
    """Stale revision, lost race, duplicate identity, or foreign lease."""


class OperationValidationError(OperationError):
    """Request/state/event content failed validation before persistence."""


