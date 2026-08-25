"""Foreground acquisition command (U1.1 §7).

``ModelAcquisitionCommandService`` is the ONE entry every frontend reaches
for catalog install and local import: it refuses-or-resumes according to
durable ``model-storage`` truth, enqueues through the shared service,
drives ONE operation in the current process, and maps only durable
terminal/paused truth to a frozen outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .operations.model import OperationState, OperationType
from .operations.repositories import LeaseRepository, OperationRepository


@dataclass(frozen=True)
class AcquisitionOutcome:
    """Typed terminal result; frontends render text from stable codes."""

    operation_id: str | None
    status: str  # INSTALLED | REUSED | QUARANTINED | CANCELLED |
    # CANCELLED_PARTIAL_RETAINED | FAILED_SAFE | PAUSED | BUSY |
    # RECOVERY_REQUIRED
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in ("INSTALLED", "REUSED")

    @property
    def activation_allowed(self) -> bool:
        return self.ok

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "operation_id": self.operation_id, **self.detail}


_STATUS_MAP = {
    "MODEL_INSTALLED": "INSTALLED",
    "MODEL_REUSED": "REUSED",
    "ARTIFACT_QUARANTINED": "QUARANTINED",
    "CANCELLED_NO_EFFECT": "CANCELLED",
    "CANCELLED_PARTIAL_RETAINED": "CANCELLED_PARTIAL_RETAINED",
}


class ModelAcquisitionCommandService:
    """Enqueue + foreground execute + terminal mapping (no detach)."""

    def __init__(
        self,
        *,
        units: Any,
        enqueue: Any,
        engine_factory: Callable[[], Any],
        fingerprint_for=None,
    ) -> None:
        self._units = units
        self._enqueue = enqueue
        self._engine_factory = engine_factory
        self._fingerprint_for = fingerprint_for or (lambda *a: "")

    def _scan_existing(self):
        with self._units.begin() as conn:
            ops = OperationRepository(conn)
            leases = LeaseRepository(conn)
            lease = leases.get("model-storage")
            if lease is not None:
                owner = ops.get(lease.operation_id)
                if (
                    owner is not None
                    and owner.operation_type
                    in (OperationType.MODEL_ACQUIRE, OperationType.MODEL_IMPORT)
                    and owner.state is OperationState.RECOVERY_REQUIRED
                ):
                    return ("barrier", lease.operation_id)
            for record in ops.list_active():
                if record.operation_type not in (
                    OperationType.MODEL_ACQUIRE,
                    OperationType.MODEL_IMPORT,
                ):
                    continue
                from .legacy_import import utcnow

                expired = bool(
                    lease is not None
                    and lease.operation_id == record.id
                    and lease.expires_at <= utcnow()
                )
                return ("active", record.id, expired)
            return ("none",)

    def acquire_catalog(
        self,
        model_id: str,
        quantization: str,
        requested_by: str = "cli",
        progress_observer=None,
    ) -> AcquisitionOutcome:
        payload = {
            "model_id": model_id,
            "quantization": quantization,
            "catalog_entry_fingerprint": self._fingerprint_for(
                model_id, quantization
            ),
            "requested_by": requested_by,
        }
        return self._drive("MODEL_ACQUIRE", payload, progress_observer)

    def import_local(
        self,
        source_path: str,
        alias: str | None = None,
        display_name: str | None = None,
        requested_by: str = "cli",
        progress_observer=None,
    ) -> AcquisitionOutcome:
        payload = {"source_path": source_path, "requested_by": requested_by}
        if alias:
            payload["alias"] = alias
        if display_name:
            payload["display_name"] = display_name
        return self._drive("MODEL_IMPORT", payload, progress_observer)

    def _drive(self, operation_type, payload, observer) -> AcquisitionOutcome:
        found = self._scan_existing()
        if found[0] == "barrier":
            return AcquisitionOutcome(
                found[1],
                "RECOVERY_REQUIRED",
                {"reason": "A previous acquisition needs repair before continuing."},
            )
        if found[0] == "active":
            operation_id, expired = found[1], found[2]
            if not expired:
                return AcquisitionOutcome(
                    operation_id,
                    "BUSY",
                    {"reason": "An acquisition is already running."},
                )
            outcome = self._run(operation_id, observer)
            return self._map(operation_id, outcome)

        record = self._enqueue.enqueue(
            operation_type=operation_type,
            payload=dict(payload),
            surface=str(payload.get("requested_by", "cli")),
        )
        outcome = self._run(record.id, observer)
        return self._map(record.id, outcome)

    def _run(self, operation_id: str, observer):
        engine = self._engine_factory()
        if observer is not None:
            original_pulse_factory = engine._make_pulse

            def pulsing(op_id_inner, held):
                base = original_pulse_factory(op_id_inner, held)

                def wrapped(**kwargs):
                    base(**kwargs)  # cancellation may raise; observer skipped
                    try:
                        observer(**kwargs)
                    except Exception:
                        pass  # transient UI errors never mutate operation truth

                return wrapped

            engine._make_pulse = pulsing
        return engine.execute_one(operation_id)

    def _map(self, operation_id: str, outcome: Any) -> AcquisitionOutcome:
        if outcome.kind == "SKIPPED_BUSY":
            return AcquisitionOutcome(
                operation_id, "BUSY", {"reason": "storage resource held"}
            )
        if outcome.kind == "LOST_LEASE":
            return AcquisitionOutcome(
                operation_id,
                "RECOVERY_REQUIRED",
                {"reason": "lease lost mid-run; inspect before retrying."},
            )
        with self._units.begin() as conn:
            record = OperationRepository(conn).require(operation_id)
        detail = {
            "result_code": record.result_code,
            "error_code": record.error_code,
        }
        state = record.state
        code = record.result_code or record.error_code or ""
        mapped = _STATUS_MAP.get(code)
        if state is OperationState.SUCCEEDED and mapped in ("INSTALLED", "REUSED"):
            return AcquisitionOutcome(operation_id, mapped, detail)
        if state is OperationState.CANCELLED and mapped:
            return AcquisitionOutcome(operation_id, mapped, detail)
        if state is OperationState.FAILED_SAFE:
            return AcquisitionOutcome(
                operation_id, mapped if mapped else "FAILED_SAFE", detail
            )
        if state is OperationState.RECOVERY_REQUIRED:
            return AcquisitionOutcome(
                operation_id,
                "RECOVERY_REQUIRED",
                {**detail, "reason": "Repair required before this operation can finish."},
            )
        if state is OperationState.PAUSED:
            return AcquisitionOutcome(operation_id, "PAUSED", detail)
        return AcquisitionOutcome(
            operation_id,
            "BUSY",
            {**detail, "reason": f"durable state {state.value}"},
        )