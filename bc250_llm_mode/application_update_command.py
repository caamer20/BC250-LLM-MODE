"""One preview-bound command surface for application update and rollback."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .application_bundle import (
    ApplicationBundleError,
    OfflineApplicationBundleStore,
)
from .application_installation import (
    ApplicationInstallationRepository,
    ApplicationInstallationStateRepository,
    InstallationState,
    SmokeState,
)
from .application_update import (
    APPLICATION_UPDATE_QUERY_SCHEMA_VERSION,
    MAX_PREVIEW_AGE_SECONDS,
    ApplicationUpdatePreview,
    ApplicationUpdateQueryService,
    InstalledApplication,
    UpdateCode,
    UpdateOutcome,
)
from .legacy_import import utcnow
from .operations.model import OperationState, OperationType
from .operations.repositories import OperationRepository

_HEX64 = re.compile(r"[0-9a-f]{64}")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True)
class ApplicationUpdateOutcome:
    outcome: UpdateOutcome
    reason_code: str
    operation_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.outcome in {UpdateOutcome.SUCCEEDED, UpdateOutcome.ROLLED_BACK}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": APPLICATION_UPDATE_QUERY_SCHEMA_VERSION,
            "outcome": self.outcome.value,
            "reason_code": self.reason_code,
            "operation_id": self.operation_id,
            **self.detail,
        }


@dataclass(frozen=True)
class UpdateCleanupPreview:
    retained: tuple[str, ...]
    candidates: tuple[dict[str, Any], ...]
    excluded: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "dry_run": True,
            "deleted_anything": False,
            "retained_installation_ids": list(self.retained),
            "candidates": list(self.candidates),
            "excluded": list(self.excluded),
        }


class ApplicationUpdateCommandService:
    def __init__(
        self,
        *,
        units: Any,
        query: ApplicationUpdateQueryService,
        store: OfflineApplicationBundleStore,
        enqueue: Any,
        engine_factory: Callable[[], Any],
        now: Callable[[], str] = utcnow,
    ) -> None:
        self.units = units
        self.query = query
        self.store = store
        self.enqueue = enqueue
        self.engine_factory = engine_factory
        self.now = now

    def status(self):
        return self.query.status()

    def check(self):
        return self.query.check()

    def preview(self, version: str):
        return self.query.preview(version)

    def import_bundle(self, path: str | Path):
        try:
            return self.store.import_bundle(path)
        except ApplicationBundleError as exc:
            return ApplicationUpdateOutcome(
                UpdateOutcome.UNAVAILABLE, exc.code,
            )

    def installed(self) -> InstalledApplication:
        from . import __version__
        from .db import SCHEMA_VERSION

        with self.units.read() as conn:
            state = ApplicationInstallationStateRepository(conn).get()
            current = (
                ApplicationInstallationRepository(conn).get(
                    state.current_installation_id
                ) if state.current_installation_id else None
            )
        return InstalledApplication(
            version=current.version if current else __version__,
            source_commit=current.source_commit if current else None,
            release_set_digest=current.release_set_digest if current else None,
            slot_id=current.release_directory_identity if current else None,
            database_schema=SCHEMA_VERSION,
            channel_id=self.store.channel_id if self.store.available else "unavailable",
            current_installation_id=state.current_installation_id,
            previous_installation_id=state.previous_installation_id,
            pointer_generation=state.pointer_generation,
            pending_operation_id=state.pending_update_operation_id,
            recovery_barrier=state.recovery_barrier,
            revision=state.revision,
        )

    def apply(
        self, version: str, *, preview_digest: str,
        confirmation_token: str, requested_by: str = "cli",
    ) -> ApplicationUpdateOutcome:
        preview = self.preview(version)
        return self._execute(
            "APPLY", preview, preview_digest=preview_digest,
            confirmation_token=confirmation_token, requested_by=requested_by,
        )

    def rollback_preview(self) -> ApplicationUpdatePreview:
        installed = self.installed()
        prior_id = installed.previous_installation_id
        current_id = installed.current_installation_id
        if (
            not prior_id or not current_id
            or not _HEX64.fullmatch(prior_id) or not _HEX64.fullmatch(current_id)
        ):
            return ApplicationUpdatePreview(
                UpdateOutcome.UNAVAILABLE, UpdateCode.ROLLBACK_UNAVAILABLE
            )
        try:
            held = self.store.resolve(prior_id)
        except ApplicationBundleError:
            return ApplicationUpdatePreview(
                UpdateOutcome.UNAVAILABLE, UpdateCode.ROLLBACK_UNAVAILABLE
            )
        release = held.release
        if not (
            release.minimum_readable_schema
            <= installed.database_schema
            <= release.maximum_readable_schema
        ):
            return ApplicationUpdatePreview(
                UpdateOutcome.UNAVAILABLE, UpdateCode.DATABASE_INCOMPATIBLE,
                version=release.version, release_set_digest=prior_id,
            )
        parsed = datetime.fromisoformat(self.now().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("update clock must be timezone aware")
        expires = datetime.fromtimestamp(
            parsed.timestamp() + MAX_PREVIEW_AGE_SECONDS, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = {
            "contract_version": APPLICATION_UPDATE_QUERY_SCHEMA_VERSION,
            "mode": "ROLLBACK",
            "release_set_digest": prior_id,
            "expected_current_installation": current_id,
            "expected_previous_installation": prior_id,
            "expected_pointer_generation": installed.pointer_generation,
            "expected_installation_revision": installed.revision,
            "source_schema": release.source_schema,
            "target_schema": release.target_schema,
            "expires_at": expires,
        }
        preview_hash = _digest(body)
        token = _digest({
            "preview_digest": preview_hash,
            "confirm": "APPLICATION_ROLLBACK",
        })
        return ApplicationUpdatePreview(
            UpdateOutcome.READY, UpdateCode.OK,
            version=release.version,
            release_set_digest=prior_id,
            source_commit=release.source_commit,
            source_ref=release.source_ref,
            published_at=release.published_at,
            platform_profile=release.platform_profile,
            source_schema=release.source_schema,
            target_schema=release.target_schema,
            backup_required=True,
            restart_required=True,
            rollback_installation_id=current_id,
            profile_restore_on_rollback=True,
            release_notes_plain_text=release.notes,
            expected_installation_revision=installed.revision,
            preview_digest=preview_hash,
            confirmation_token=token,
            expires_at=expires,
        )

    def rollback(
        self, *, preview_digest: str, confirmation_token: str,
        requested_by: str = "cli",
    ) -> ApplicationUpdateOutcome:
        return self._execute(
            "ROLLBACK", self.rollback_preview(),
            preview_digest=preview_digest,
            confirmation_token=confirmation_token,
            requested_by=requested_by,
        )

    def _execute(
        self, mode: str, preview: ApplicationUpdatePreview, *,
        preview_digest: str, confirmation_token: str, requested_by: str,
    ) -> ApplicationUpdateOutcome:
        if preview.outcome is not UpdateOutcome.READY:
            return ApplicationUpdateOutcome(
                UpdateOutcome.UNAVAILABLE, preview.reason_code.value
            )
        if preview.preview_digest != preview_digest:
            return ApplicationUpdateOutcome(
                UpdateOutcome.FAILED_SAFE, UpdateCode.PREVIEW_STALE.value
            )
        if preview.confirmation_token != confirmation_token:
            return ApplicationUpdateOutcome(
                UpdateOutcome.FAILED_SAFE, UpdateCode.CONFIRMATION_REQUIRED.value
            )
        installed = self.installed()
        if (
            installed.current_installation_id is None
            or not _HEX64.fullmatch(installed.current_installation_id)
            or preview.release_set_digest is None
        ):
            # A legacy in-place installation is displayed honestly but cannot
            # be overwritten. A separately qualified installer must establish
            # the initial immutable slot first.
            return ApplicationUpdateOutcome(
                UpdateOutcome.UNAVAILABLE, UpdateCode.ROLLBACK_UNAVAILABLE.value,
                detail={"legacy_installation_requires_slot_migration": True},
            )
        current_preview = (
            self.preview(preview.version or "")
            if mode == "APPLY" else self.rollback_preview()
        )
        if current_preview.preview_digest != preview_digest:
            return ApplicationUpdateOutcome(
                UpdateOutcome.FAILED_SAFE, UpdateCode.PREVIEW_STALE.value
            )
        record = self.enqueue.enqueue(
            operation_type=OperationType.APPLICATION_UPDATE,
            payload={
                "mode": mode,
                "release_set_digest": preview.release_set_digest,
                "expected_current_installation_id": installed.current_installation_id,
                "expected_previous_installation_id": installed.previous_installation_id,
                "expected_pointer_generation": installed.pointer_generation,
                "preview_digest": preview_digest,
                "confirmation_digest": hashlib.sha256(
                    confirmation_token.encode("ascii")
                ).hexdigest(),
                "requested_by": requested_by,
            },
            surface=requested_by,
        )
        result = self.engine_factory().execute_one(record.id)
        with self.units.read() as conn:
            final = OperationRepository(conn).require(record.id)
        if final.state is OperationState.SUCCEEDED:
            outcome = (
                UpdateOutcome.ROLLED_BACK if mode == "ROLLBACK"
                else UpdateOutcome.SUCCEEDED
            )
        elif final.state is OperationState.RECOVERY_REQUIRED:
            outcome = UpdateOutcome.RECOVERY_REQUIRED
        else:
            outcome = UpdateOutcome.FAILED_SAFE
        return ApplicationUpdateOutcome(
            outcome,
            final.result_code or final.error_code or getattr(result, "kind", "UNKNOWN"),
            record.id,
        )

    def cleanup_preview(self) -> UpdateCleanupPreview:
        with self.units.read() as conn:
            state = ApplicationInstallationStateRepository(conn).get()
            rows = ApplicationInstallationRepository(conn).list(limit=64)
        retained = tuple(item for item in (
            state.current_installation_id, state.previous_installation_id
        ) if item)
        candidates: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        retained_set = set(retained)
        for row in rows:
            item = {
                "installation_id": row.installation_id,
                "version": row.version,
                "state": row.state.value,
                "smoke_state": row.smoke_state.value,
            }
            if (
                row.installation_id in retained_set
                or state.pending_update_operation_id is not None
                or state.recovery_barrier is not None
                or row.state is not InstallationState.QUARANTINED
                or row.smoke_state is not SmokeState.PASSED
            ):
                excluded.append({**item, "reason_code": "RETENTION_PROTECTED"})
            else:
                candidates.append({**item, "reason_code": "UNREFERENCED_QUARANTINE"})
        return UpdateCleanupPreview(retained, tuple(candidates), tuple(excluded))


__all__ = [
    "ApplicationUpdateCommandService", "ApplicationUpdateOutcome",
    "UpdateCleanupPreview",
]
