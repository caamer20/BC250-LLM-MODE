"""Foreground activation command (Session 5C plan §11).

``ActivationCommandService`` is the ONE entry every frontend reaches:
it refuses-or-resumes an existing ``runtime-active`` activation according
to DURABLE state, enqueues atomically, executes one operation in the
current process, and maps the durable terminal row to a typed result.
It never starts a worker/thread and never performs host effects itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .operations.activation import ACTIVATION_RESOURCE, OPERATION_TYPE
from .operations.model import (
    OperationState,
    OperationType,
)
from .operations.repositories import (
    LeaseRepository,
    OperationRepository,
)


@dataclass(frozen=True)
class ActivationOutcome:
    """Typed terminal result; frontends render text from stable codes."""

    operation_id: str | None
    status: str  # SUCCEEDED | FAILED_ROLLED_BACK | FAILED_SAFE |
    # CANCELLED | RECOVERY_REQUIRED | BUSY
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "SUCCEEDED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "operation_id": self.operation_id,
            **self.detail,
        }


class ActivationCommandService:
    """Enqueue + foreground execute + terminal mapping (no detach)."""

    def __init__(
        self,
        *,
        units: Any,
        enqueue: Any,
        engine_factory: Callable[[], Any],
    ) -> None:
        self._units = units
        self._enqueue = enqueue
        self._engine_factory = engine_factory

    # -- durable-state inspection ---------------------------------------------
    def _scan_existing(self):
        with self._units.begin() as conn:
            ops = OperationRepository(conn)
            leases = LeaseRepository(conn)
            lease = leases.get(ACTIVATION_RESOURCE)
            if lease is not None:
                owner = ops.get(lease.operation_id)
                if (
                    owner is not None
                    and owner.operation_type is OperationType.MODEL_ACTIVATE
                    and owner.state is OperationState.RECOVERY_REQUIRED
                ):
                    return ("barrier", lease.operation_id)
            for record in ops.list_active():
                if record.operation_type is not OperationType.MODEL_ACTIVATE:
                    continue
                from .legacy_import import utcnow

                expired = bool(
                    lease is not None
                    and lease.operation_id == record.id
                    and lease.expires_at <= utcnow()
                )
                return ("active", record.id, expired)
            return ("none",)

    # -- public API --------------------------------------------------------------
    def activate(self, payload: dict[str, Any]) -> ActivationOutcome:
        """Resume-or-refuse, then enqueue and drive ONE activation."""
        found = self._scan_existing()
        if found[0] == "barrier":
            return ActivationOutcome(
                found[1],
                "RECOVERY_REQUIRED",
                {
                    "reason": (
                        "A previous activation needs repair; run recovery "
                        "before activating."
                    )
                },
            )
        if found[0] == "active":
            operation_id, expired = found[1], found[2]
            if not expired:
                return ActivationOutcome(
                    operation_id,
                    "BUSY",
                    {"reason": "An activation is already running."},
                )
            # An interrupted activation resumes FIRST; never leapfrogged.
            outcome = self._engine_factory().execute_one(operation_id)
            return self._map(operation_id, outcome)

        record = self._enqueue.enqueue(
            operation_type=OPERATION_TYPE,
            payload=dict(payload),
            surface=str(payload.get("requested_by", "cli")),
        )
        outcome = self._engine_factory().execute_one(record.id)
        return self._map(record.id, outcome)

    # -- terminal mapping ----------------------------------------------------------
    def _map(self, operation_id: str, outcome: Any) -> ActivationOutcome:
        if outcome.kind == "SKIPPED_BUSY":
            return ActivationOutcome(operation_id, "BUSY", {"reason": "resource held"})
        if outcome.kind == "LOST_LEASE":
            return ActivationOutcome(
                operation_id,
                "RECOVERY_REQUIRED",
                {"reason": "lease lost mid-run; inspect before retrying."},
            )
        with self._units.begin() as conn:
            record = OperationRepository(conn).require(operation_id)
        state = record.state
        detail: dict[str, Any] = {
            "result_code": record.result_code,
            "error_code": record.error_code,
        }
        mapping = {
            OperationState.SUCCEEDED: "SUCCEEDED",
            OperationState.FAILED_ROLLED_BACK: "FAILED_ROLLED_BACK",
            OperationState.FAILED_SAFE: "FAILED_SAFE",
            OperationState.CANCELLED: "CANCELLED",
            OperationState.RECOVERY_REQUIRED: "RECOVERY_REQUIRED",
        }
        status = mapping.get(state)
        if status is None:
            # Non-terminal after execution (paused/version unavailable).
            return ActivationOutcome(
                operation_id,
                "BUSY" if state is OperationState.PAUSED else "RECOVERY_REQUIRED",
                {"reason": f"durable state {state.value}"},
            )
        if status == "FAILED_ROLLED_BACK":
            detail["reason"] = (
                "Activation failed; the previous working configuration was "
                "restored and verified."
            )
        elif status == "RECOVERY_REQUIRED":
            detail["reason"] = (
                "The activation could not be proven safe; repair is "
                "required. The candidate is NOT confirmed running."
            )
        return ActivationOutcome(operation_id, status, detail)
