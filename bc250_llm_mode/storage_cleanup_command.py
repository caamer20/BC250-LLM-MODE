"""Preview-bound command service for durable ``STORAGE_CLEANUP v1``."""

from __future__ import annotations

import datetime as _datetime
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .legacy_import import utcnow
from .operations.model import OperationState, OperationType
from .operations.repositories import OperationRepository

PREVIEW_SECONDS = 15 * 60


@dataclass(frozen=True)
class CleanupPreview:
    mode: str
    candidates: tuple[dict[str, Any], ...]
    selected: tuple[dict[str, Any], ...]
    excluded: tuple[dict[str, Any], ...]
    reclaimable_bytes: int
    preview_digest: str
    confirmation_token: str
    expires_at: str

    @property
    def ready(self) -> bool:
        return bool(self.selected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "mode": self.mode,
            "candidates": list(self.candidates),
            "selected": list(self.selected),
            "excluded": list(self.excluded),
            "selected_count": len(self.selected),
            "reclaimable_bytes": self.reclaimable_bytes,
            "preview_digest": self.preview_digest,
            "confirmation_token": self.confirmation_token,
            "expires_at": self.expires_at,
            "deleted_bytes": 0,
            "deleted_anything": False,
        }


@dataclass(frozen=True)
class CleanupOutcome:
    status: str
    operation_id: str | None
    result_code: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "SUCCEEDED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status,
            "operation_id": self.operation_id,
            "result_code": self.result_code,
            **self.detail,
        }


class StorageCleanupCommandService:
    def __init__(
        self,
        *,
        units: Any,
        enqueue: Any,
        engine_factory: Callable[[], Any],
        adapter: Any,
        clock: Callable[[], str] = utcnow,
    ) -> None:
        self._units = units
        self._enqueue = enqueue
        self._engine_factory = engine_factory
        self._adapter = adapter
        self._clock = clock

    def preview(
        self, *, mode: str = "QUARANTINE",
        target_ids: Iterable[str] | None = None,
    ) -> CleanupPreview:
        mode = str(mode).upper()
        if mode not in {"QUARANTINE", "RESTORE", "PURGE"}:
            raise ValueError("unknown cleanup mode")
        candidates = tuple(self._adapter.discover())
        requested = tuple(dict.fromkeys(str(item) for item in (target_ids or ())))
        by_id = {item["target_id"]: item for item in candidates}
        if requested and any(item not in by_id for item in requested):
            raise ValueError("cleanup target is unknown; preview again")
        if requested:
            selected = tuple(
                by_id[item] for item in requested
                if mode in by_id[item]["eligible_modes"]
            )
        else:
            selected = tuple(
                item for item in candidates
                if mode in item["eligible_modes"]
                and bool(item.get("default_selected"))
            )
        selected_ids = {item["target_id"] for item in selected}
        excluded = tuple({
            "target_id": item["target_id"],
            "kind": item["kind"],
            "reason_code": (
                "NOT_SELECTED" if mode in item["eligible_modes"]
                else "MODE_NOT_ELIGIBLE"
            ),
        } for item in candidates if item["target_id"] not in selected_ids)
        window, expires = self._window()
        payload = {
            "contract_version": 1,
            "mode": mode,
            "targets": [self._request_target(item) for item in selected],
            "window": window,
            "expires_at": expires,
        }
        digest = hashlib.sha256(_canonical(payload)).hexdigest()
        return CleanupPreview(
            mode, candidates, selected, excluded,
            sum(int(item["expected_bytes"]) for item in selected),
            digest, f"CLEANUP-{digest[:12].upper()}", expires,
        )

    def apply(
        self,
        *,
        mode: str = "QUARANTINE",
        target_ids: Iterable[str] | None = None,
        preview_digest: str,
        confirmation_token: str,
        requested_by: str = "cli",
    ) -> CleanupOutcome:
        with self._units.read() as conn:
            from .repositories import SettingsRepository
            if not bool(SettingsRepository(conn).get("disclaimer_ack", False)):
                return CleanupOutcome(
                    "REFUSED", None, "SAFETY_ACKNOWLEDGMENT_REQUIRED"
                )
        current = self.preview(mode=mode, target_ids=target_ids)
        if not current.ready:
            return CleanupOutcome("REFUSED", None, "NO_ELIGIBLE_TARGETS")
        if (current.preview_digest != preview_digest
                or current.confirmation_token != confirmation_token):
            return CleanupOutcome("REFUSED", None, "CLEANUP_PREVIEW_STALE")
        # Re-observe a second time at the mutation boundary. Any changed tree
        # identity or operation state changes the digest and refuses.
        repeated = self.preview(mode=mode, target_ids=(
            item["target_id"] for item in current.selected
        ))
        if repeated.preview_digest != current.preview_digest:
            return CleanupOutcome("REFUSED", None, "CLEANUP_PREVIEW_STALE")
        record = self._enqueue.enqueue(
            operation_type=OperationType.STORAGE_CLEANUP,
            payload={
                "mode": current.mode,
                "targets": [self._request_target(item) for item in current.selected],
                "preview_digest": current.preview_digest,
                "requested_by": requested_by,
            },
            surface=requested_by,
        )
        outcome = self._engine_factory().execute_one(record.id)
        with self._units.read() as conn:
            final = OperationRepository(conn).require(record.id)
        if final.state is OperationState.SUCCEEDED:
            status = "SUCCEEDED"
        elif final.state is OperationState.RECOVERY_REQUIRED:
            status = "RECOVERY_REQUIRED"
        elif final.state is OperationState.FAILED_ROLLED_BACK:
            status = "FAILED_ROLLED_BACK"
        elif final.state is OperationState.CANCELLED:
            status = "CANCELLED"
        else:
            status = "FAILED_SAFE"
        return CleanupOutcome(
            status, record.id,
            final.result_code or final.error_code or getattr(outcome, "kind", "UNKNOWN"),
            {
                "mode": current.mode,
                "selected_count": len(current.selected),
                "reclaimable_bytes": current.reclaimable_bytes,
            },
        )

    def quarantine_target(
        self, target_id: str | None, *, requested_by: str = "repair"
    ) -> CleanupOutcome:
        if not target_id:
            return CleanupOutcome("REFUSED", None, "TARGET_REQUIRED")
        preview = self.preview(mode="QUARANTINE", target_ids=(target_id,))
        return self.apply(
            mode="QUARANTINE", target_ids=(target_id,),
            preview_digest=preview.preview_digest,
            confirmation_token=preview.confirmation_token,
            requested_by=requested_by,
        )

    def restore_target(
        self, target_id: str, *, requested_by: str = "undo"
    ) -> CleanupOutcome:
        preview = self.preview(mode="RESTORE", target_ids=(target_id,))
        return self.apply(
            mode="RESTORE", target_ids=(target_id,),
            preview_digest=preview.preview_digest,
            confirmation_token=preview.confirmation_token,
            requested_by=requested_by,
        )

    @staticmethod
    def _request_target(item: dict[str, Any]) -> dict[str, Any]:
        return {
            key: item.get(key)
            for key in (
                "target_id", "kind", "relative_name", "expected_tree_digest",
                "expected_bytes", "expected_files", "owner_operation_id",
                "quarantine_operation_id", "retention_until",
            )
        }

    def _window(self) -> tuple[int, str]:
        now = _parse(self._clock())
        window = int(now.timestamp()) // PREVIEW_SECONDS
        expires = _datetime.datetime.fromtimestamp(
            (window + 1) * PREVIEW_SECONDS,
            tz=_datetime.timezone.utc,
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        return window, expires


def _parse(value: str) -> _datetime.datetime:
    parsed = _datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_datetime.timezone.utc)
    return parsed.astimezone(_datetime.timezone.utc)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


__all__ = ["CleanupOutcome", "CleanupPreview", "StorageCleanupCommandService"]
