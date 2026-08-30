"""Post-commit producers for the closed EXP-4 notification vocabulary.

These adapters never own domain work.  They inspect already-committed truth,
construct a closed identity, and isolate every receipt/delivery failure from
the operation, thermal, or maintenance result that triggered them.
"""

from __future__ import annotations

import datetime as _datetime
import hashlib
import json
from typing import Any, Callable, Mapping

from .legacy_import import utcnow
from .notifications import NotificationEvent
from .operations.model import OperationState, OperationType
from .operations.repositories import OperationRepository


_LONG_OPERATIONS = frozenset({
    OperationType.MODEL_ACQUIRE,
    OperationType.MODEL_IMPORT,
    OperationType.MODEL_REMOVE,
    OperationType.RUNTIME_UPDATE,
    OperationType.RUNTIME_ROLLBACK,
    OperationType.BACKUP_CREATE,
    OperationType.BACKUP_RESTORE,
    OperationType.PROFILE_CALIBRATE,
    OperationType.STORAGE_CLEANUP,
})
_FAILURE_STATES = frozenset({
    OperationState.FAILED_SAFE,
    OperationState.FAILED_ROLLED_BACK,
    OperationState.RECOVERY_REQUIRED,
})
_BACKUP_OPERATIONS = frozenset({
    OperationType.BACKUP_CREATE,
    OperationType.BACKUP_RESTORE,
})


def _safe_notify(coordinator: Any, event: NotificationEvent) -> dict[str, Any] | None:
    try:
        outcome = coordinator.notify(event)
    except Exception:  # noqa: BLE001 - presentation cannot change domain truth
        return None
    return outcome.to_dict() if hasattr(outcome, "to_dict") else None


class OperationNotificationProducer:
    """Observe one completed engine call after its transactions have closed."""

    def __init__(self, units: Any, coordinator: Any) -> None:
        self._units = units
        self._coordinator = coordinator

    def after_execution(self, operation_id: str) -> dict[str, Any] | None:
        try:
            with self._units.read() as conn:
                record = OperationRepository(conn).get(operation_id)
            if record is None or record.operation_type not in _LONG_OPERATIONS:
                return None
            if record.state is OperationState.SUCCEEDED:
                category = "OPERATION_SUCCESS"
            elif record.state in _FAILURE_STATES:
                category = (
                    "BACKUP_FAILURE"
                    if record.operation_type in _BACKUP_OPERATIONS
                    else "OPERATION_FAILURE"
                )
            else:
                return None
            event = NotificationEvent.operation(
                operation_id=record.id,
                terminal_state=record.state.value,
                revision=record.state_revision,
                category=category,
            )
        except Exception:  # noqa: BLE001 - no producer failure escapes
            return None
        return _safe_notify(self._coordinator, event)


class ThermalNotificationProducer:
    """Translate an existing watchdog latch transition into fixed copy."""

    def __init__(
        self, coordinator: Any, *, clock: Callable[[], str] = utcnow
    ) -> None:
        self._coordinator = coordinator
        self._clock = clock

    def after_transition(self, latch_state: str) -> dict[str, Any] | None:
        category = {
            "throttled": "THERMAL_WARNING",
            "stopped": "THERMAL_STOP",
        }.get(str(latch_state).lower())
        if category is None:
            return None
        try:
            event = NotificationEvent.thermal(
                category=category,
                transitioned_at=self._clock(),
                latch_state=str(latch_state).lower(),
            )
        except Exception:  # noqa: BLE001
            return None
        return _safe_notify(self._coordinator, event)


def _six_hour_window(value: str) -> str:
    parsed = _datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_datetime.timezone.utc)
    parsed = parsed.astimezone(_datetime.timezone.utc).replace(
        hour=(parsed.hour // 6) * 6,
        minute=0,
        second=0,
        microsecond=0,
    )
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def _evidence_fingerprint(item: Mapping[str, Any]) -> str:
    # Only closed maintenance classifications enter this identity. No title,
    # impact, path, resource label, address, or arbitrary evidence is used.
    document = {
        "category": str(item.get("category")),
        "code": str(item.get("code")),
        "freshness": str(item.get("evidence_freshness")),
        "age_day": max(0, int(item.get("evidence_age_seconds") or 0)) // 86400,
    }
    encoded = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


class MaintenanceNotificationProducer:
    """Notify from one explicit, committed maintenance check snapshot."""

    def __init__(self, coordinator: Any) -> None:
        self._coordinator = coordinator

    def after_check(
        self, snapshot: Mapping[str, Any], checked_at: str
    ) -> tuple[dict[str, Any], ...]:
        try:
            window = _six_hour_window(checked_at)
        except (TypeError, ValueError):
            return ()
        outcomes: list[dict[str, Any]] = []
        items = snapshot.get("items")
        if not isinstance(items, (list, tuple)):
            return ()
        for item in items[:5]:
            if not isinstance(item, Mapping):
                continue
            code = str(item.get("code"))
            fingerprint = _evidence_fingerprint(item)
            if code == "STORAGE_CRITICALLY_LOW":
                event = NotificationEvent.storage(
                    evidence_fingerprint=fingerprint, window_start=window
                )
            elif code in {"BACKUP_STALE", "BACKUP_UNVERIFIED"}:
                event = NotificationEvent.backup_stale(
                    evidence_fingerprint=fingerprint, window_start=window
                )
            else:
                continue
            outcome = _safe_notify(self._coordinator, event)
            if outcome is not None:
                outcomes.append(outcome)
        return tuple(outcomes)


__all__ = [
    "MaintenanceNotificationProducer",
    "OperationNotificationProducer",
    "ThermalNotificationProducer",
]
