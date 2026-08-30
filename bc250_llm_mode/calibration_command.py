"""Foreground command service for durable profile calibration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .operations.calibration import OPERATION_TYPE
from .operations.model import OperationState
from .operations.repositories import OperationRepository, json_loads_or_none


@dataclass(frozen=True)
class CalibrationOutcome:
    operation_id: str | None
    status: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "SUCCEEDED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "status": self.status,
            **self.detail,
        }


class CalibrationCommandService:
    def __init__(self, *, units, enqueue, engine_factory: Callable[[], Any]) -> None:
        self._units = units
        self._enqueue = enqueue
        self._engine_factory = engine_factory

    def calibrate(
        self,
        *,
        profile_id: str,
        expected_profile_revision: int,
        model_alias: str,
        candidate_policy: str = "balanced-v1",
        accept_tight: bool = False,
        requested_by: str = "cli",
    ) -> CalibrationOutcome:
        with self._units.read() as conn:
            active = tuple(
                item
                for item in OperationRepository(conn).list_active()
                if item.operation_type is OPERATION_TYPE
            )
        if active:
            return CalibrationOutcome(
                active[0].id,
                "BUSY",
                {"reason": "A calibration is already active or awaiting recovery."},
            )
        record = self._enqueue.enqueue(
            operation_type=OPERATION_TYPE,
            payload={
                "profile_id": profile_id,
                "expected_profile_revision": expected_profile_revision,
                "model_alias": model_alias,
                "candidate_policy": candidate_policy,
                "accept_tight": accept_tight,
                "requested_by": requested_by,
            },
            surface=requested_by,
        )
        outcome = self._engine_factory().execute_one(record.id)
        if outcome.kind == "SKIPPED_BUSY":
            return CalibrationOutcome(record.id, "BUSY", dict(outcome.detail))
        with self._units.read() as conn:
            final = OperationRepository(conn).require(record.id)
        mapping = {
            OperationState.SUCCEEDED: "SUCCEEDED",
            OperationState.CANCELLED: "CANCELLED",
            OperationState.FAILED_SAFE: "FAILED_SAFE",
            OperationState.FAILED_ROLLED_BACK: "FAILED_ROLLED_BACK",
            OperationState.RECOVERY_REQUIRED: "RECOVERY_REQUIRED",
        }
        status = mapping.get(final.state, "BUSY")
        return CalibrationOutcome(
            record.id,
            status,
            {
                "result_code": final.result_code,
                "result": json_loads_or_none(final.result_detail),
                "error_code": final.error_code,
                "applied": False,
            },
        )


__all__ = ["CalibrationCommandService", "CalibrationOutcome"]
