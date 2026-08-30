"""Foreground backup/restore command (ADR 006, plan §C2.6).

``BackupCommandService`` is the ONE entry every frontend reaches for backup
create/list/verify and restore inspect/start. ``restore_inspect`` is a
query-only dry run that returns the confirmation digest the caller must echo
back to ``restore_start``; ``restore_start`` refuses-or-enqueues according to
durable truth and drives ONE ``BACKUP_RESTORE v1`` operation. Encryption is
refused fail-closed before any effect (ADR 006 D2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .operations.model import OperationState, OperationType
from .operations.repositories import LeaseRepository, OperationRepository
from .operations.validation import OperationValidationError

_CREATE_STATUS_MAP = {
    "BACKUP_CREATED": "CREATED",
    "BACKUP_COLLISION": "COLLISION",
}
_RESTORE_STATUS_MAP = {
    "RESTORE_PUBLISHED": "RESTORED",
    "RESTORE_ROLLED_BACK": "ROLLED_BACK",
}


@dataclass(frozen=True)
class BackupOutcome:
    operation_id: str | None
    status: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in ("CREATED", "RESTORED")

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status,
                "operation_id": self.operation_id, **self.detail}


class BackupCommandService:
    def __init__(
        self,
        *,
        units: Any,
        enqueue: Any,
        engine_factory: Callable[[], Any],
        adapter: Any,
    ) -> None:
        self._units = units
        self._enqueue = enqueue
        self._engine_factory = engine_factory
        self._adapter = adapter

    # -- create -------------------------------------------------------------

    def create_backup(
        self,
        destination_label: str,
        *,
        include_models: bool = False,
        include_runtime: bool = False,
        encrypt: bool = False,
        requested_by: str = "cli",
        parent_operation_id: str | None = None,
    ) -> BackupOutcome:
        if encrypt:
            # ADR 006 D2: refuse BEFORE any effect until reviewed crypto exists.
            return BackupOutcome(
                None, "ENCRYPTION_UNAVAILABLE",
                {"reason": "encryption is not available in this build"})
        record = self._enqueue.enqueue(
            operation_type="BACKUP_CREATE",
            payload={"destination_label": destination_label,
                     "include_models": include_models,
                     "include_runtime": include_runtime,
                     "encrypt": False,
                     "requested_by": requested_by},
            surface=requested_by,
            parent_operation_id=parent_operation_id,
        )
        return self._map_create(record.id, self._run(record.id))

    def _map_create(self, operation_id: str, outcome: Any) -> BackupOutcome:
        if outcome.kind == "SKIPPED_BUSY":
            return BackupOutcome(
                operation_id, "BUSY", {"reason": "backup resource held"})
        if outcome.kind == "LOST_LEASE":
            return BackupOutcome(
                operation_id, "RECOVERY_REQUIRED",
                {"reason": "lease lost mid-run; inspect before retrying."})
        with self._units.begin() as conn:
            record = OperationRepository(conn).require(operation_id)
        detail = {"result_code": record.result_code,
                  "error_code": record.error_code}
        code = record.result_code or record.error_code or ""
        mapped = _CREATE_STATUS_MAP.get(code)
        if record.state is OperationState.SUCCEEDED and mapped:
            return BackupOutcome(operation_id, mapped, detail)
        if record.state is OperationState.FAILED_SAFE:
            return BackupOutcome(operation_id, mapped or "FAILED_SAFE", detail)
        if record.state is OperationState.RECOVERY_REQUIRED:
            return BackupOutcome(
                operation_id, "RECOVERY_REQUIRED",
                {**detail, "reason": "Repair required before this finishes."})
        return BackupOutcome(
            operation_id, mapped or "BUSY",
            {**detail, "reason": f"state {record.state.value}"})

    # -- list / verify (query-only) -----------------------------------------

    def list_backups(self) -> list[dict[str, Any]]:
        with self._units.read() as conn:
            rows = conn.execute(
                "SELECT backup_id, manifest_digest, storage_path_label, "
                "bytes_total, encryption_mode, verification_state, created_at "
                "FROM backup_sets ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def verify_backup(self, backup_id: str) -> dict[str, Any]:
        """Re-verify an existing archive's manifest digest (query-only)."""
        with self._units.read() as conn:
            row = conn.execute(
                "SELECT storage_path_label, manifest_digest FROM backup_sets "
                "WHERE backup_id = ?", (backup_id,)).fetchone()
        if row is None:
            return {"backup_id": backup_id, "found": False, "valid": False}
        archive = self._adapter._archive_path(row["storage_path_label"])
        if not archive.is_file():
            return {"backup_id": backup_id, "found": True, "valid": False,
                    "reason": "archive-missing"}
        from .backup_adapter import MANIFEST_NAME, _bare_digest
        from .backup_manifest import verify_manifest_digest
        import json
        import tarfile
        with tarfile.open(archive, "r") as tar:
            doc = json.loads(tar.extractfile(MANIFEST_NAME).read())
        digest_ok = verify_manifest_digest(doc)
        bound_ok = _bare_digest(doc["manifest_digest"]) == row["manifest_digest"]
        return {"backup_id": backup_id, "found": True,
                "valid": digest_ok and bound_ok,
                "manifest_digest": row["manifest_digest"]}

    # -- restore inspect (query-only dry run) -------------------------------

    def restore_inspect(self, backup_id: str) -> dict[str, Any]:
        """Query-only dry run: what a restore would replace, plus the
        confirmation digest the caller must echo to ``restore_start``."""
        with self._units.read() as conn:
            row = conn.execute(
                "SELECT backup_id, manifest_digest, storage_path_label, "
                "bytes_total, encryption_mode, verification_state "
                "FROM backup_sets WHERE backup_id = ?", (backup_id,)).fetchone()
        if row is None:
            return {"backup_id": backup_id, "found": False, "restorable": False,
                    "blockers": ["backup-unknown"]}
        blockers: list[str] = []
        if row["verification_state"] != "verified":
            blockers.append("backup-not-verified")
        if row["encryption_mode"] != "none":
            blockers.append("encryption-unsupported")
        archive = self._adapter._archive_path(row["storage_path_label"])
        if not archive.is_file():
            blockers.append("archive-missing")
        return {
            "backup_id": backup_id,
            "found": True,
            "restorable": not blockers,
            "blockers": blockers,
            "confirmation_digest": row["manifest_digest"],
            "storage_path_label": row["storage_path_label"],
            "bytes_total": row["bytes_total"],
            "impact": {
                "replaces": "active-profile",
                "prior_profile": "retained-for-rollback",
                "disposition": "atomic-exchange" if not blockers else "refused",
            },
        }

    # -- restore start ------------------------------------------------------

    def restore_start(
        self,
        backup_id: str,
        confirmation_digest: str,
        requested_by: str = "cli",
    ) -> BackupOutcome:
        plan = self.restore_inspect(backup_id)
        if not plan["found"]:
            return BackupOutcome(
                None, "UNKNOWN_BACKUP", {"reason": "backup not found"})
        if plan["blockers"]:
            return BackupOutcome(
                None, "BLOCKED",
                {"reason": "; ".join(plan["blockers"]), "dry_run": plan})
        if plan["confirmation_digest"] != confirmation_digest:
            return BackupOutcome(
                None, "CONFIRMATION_MISMATCH",
                {"reason": "confirmation_digest does not match the inspected "
                           "backup; re-run restore inspect"})
        record = self._enqueue.enqueue(
            operation_type="BACKUP_RESTORE",
            payload={"backup_id": backup_id,
                     "confirmation_digest": confirmation_digest,
                     "requested_by": requested_by},
            surface=requested_by,
        )
        return self._map_restore(record.id, self._run(record.id))

    def _run(self, operation_id: str):
        return self._engine_factory().execute_one(operation_id)

    def _map_restore(self, operation_id: str, outcome: Any) -> BackupOutcome:
        if outcome.kind == "SKIPPED_BUSY":
            return BackupOutcome(
                operation_id, "BUSY", {"reason": "restore resource held"})
        if outcome.kind == "LOST_LEASE":
            return BackupOutcome(
                operation_id, "RECOVERY_REQUIRED",
                {"reason": "lease lost mid-run; inspect before retrying."})
        # A restore swaps the profile database, so the operation row may now
        # live in the retained prior profile. The authoritative post-exchange
        # outcome is the terminal restore_attempts record in the restored DB.
        with self._units.read() as conn:
            terminal = conn.execute(
                "SELECT publish_state, post_verify_state, rollback_state FROM "
                "restore_attempts WHERE restore_id = ?",
                (f"rs-{operation_id}",)).fetchone()
        if terminal is not None and terminal["publish_state"] == "published":
            return BackupOutcome(
                operation_id, "RESTORED",
                {"result_code": "RESTORE_PUBLISHED",
                 "post_verify_state": terminal["post_verify_state"]})
        # No terminal record: publication did not complete. Read the operation
        # row (still present while the DB was not swapped).
        from .operations.model import OperationConflict
        try:
            with self._units.begin() as conn:
                record = OperationRepository(conn).require(operation_id)
        except OperationConflict:
            return BackupOutcome(
                operation_id, "RECOVERY_REQUIRED",
                {"reason": "restore state is uncertain after the exchange; "
                           "both profiles are retained for inspection."})
        detail = {"result_code": record.result_code,
                  "error_code": record.error_code}
        code = record.result_code or record.error_code or ""
        mapped = _RESTORE_STATUS_MAP.get(code)
        if record.state is OperationState.SUCCEEDED and mapped:
            return BackupOutcome(operation_id, mapped, detail)
        if record.state is OperationState.FAILED_SAFE:
            return BackupOutcome(operation_id, mapped or "FAILED_SAFE", detail)
        if record.state is OperationState.RECOVERY_REQUIRED:
            return BackupOutcome(
                operation_id, "RECOVERY_REQUIRED",
                {**detail, "reason": "Repair required before this finishes."})
        return BackupOutcome(
            operation_id, mapped or "BUSY",
            {**detail, "reason": f"state {record.state.value}"})
