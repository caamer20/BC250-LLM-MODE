"""Production host adapters for ``BACKUP_CREATE v1`` / ``BACKUP_RESTORE v1``
(ADR 006). ONE production host satisfies each workflow port.

BACKUP_CREATE: a hot-consistent SQLite snapshot (``sqlite3`` backup API), a
secret-free manifest, and a no-replace tar archive published to the backups dir.
Model/runtime bytes are excluded unless explicitly included. Secrets are
excluded by construction (the gateway secret never lives in the database).

BACKUP_RESTORE: validate the source against the confirmation digest, extract
into a fresh contained sibling staging profile, validate it, then ONE atomic
same-filesystem profile exchange, post-restore verification, and promote
(retain prior) or exchange back. Every pre-publication failure leaves the active
profile unchanged; an uncertain state retains both profiles.

The adapters operate on injected ``AppPaths`` + ``UnitOfWorkFactory`` so the
exact semantics are exercised by fake-world tests (no systemd/hardware here).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from . import __version__ as APPLICATION_RELEASE
from .backup_archive import inspect_archive
from .backup_lifecycle import BackupSetRepository, RestoreAttemptRepository
from .backup_manifest import (
    BackupManifest,
    FileEntry,
    build_backup_manifest,
    verify_manifest_digest,
)
from .db import SCHEMA_VERSION
from .legacy_import import utcnow
from .operations.backup import (
    CODE_BACKUP_CREATED,
    CODE_RESTORE_PUBLISHED,
    CODE_RESTORE_ROLLED_BACK,
    BackupCreateHost,
    BackupRestoreHost,
)
from .operations.recovery import RecoveryClass
from .operations.validation import OperationValidationError
from .operations.workflow import ProbeResult
from .paths import AppPaths
from .profile_exchange_helper import Refusal, run_local_profile_exchange
from .unit_of_work import UnitOfWorkFactory

MANIFEST_NAME = "backup-manifest.json"
_SNAPSHOT_NAME = "state.db"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _bare_digest(digest: str) -> str:
    """Normalize a ``sha256:<hex>`` manifest digest to bare 64-char hex (the
    form persisted in ``backup_sets`` and bound to restore confirmations)."""
    return digest[7:] if digest.startswith("sha256:") else digest


class BackupHostAdapter(BackupCreateHost, BackupRestoreHost):
    """ONE production host for both backup workflows."""

    def __init__(
        self,
        units: UnitOfWorkFactory,
        paths: AppPaths,
        *,
        clock=utcnow,
        exchange=run_local_profile_exchange,
        quiesce=None,
    ) -> None:
        self._units = units
        self._paths = paths
        self._clock = clock
        self._exchange = exchange
        self._quiesce = quiesce

    # -- shared helpers -----------------------------------------------------

    def _backup_staging_dir(self, operation_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", operation_id):
            raise OperationValidationError("invalid backup operation identity")
        return self._paths.staging_dir / f"backup-{operation_id}"

    def _restore_staging_profile(self, operation_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", operation_id):
            raise OperationValidationError("invalid restore operation identity")
        return self._paths.app_dir.parent / (
            f"{self._paths.app_dir.name}-restore-{operation_id}")

    def _archive_path(self, label: str) -> Path:
        # Contained within the backups dir; never an absolute/escaping path.
        parts = Path(label).parts
        if not parts or label.startswith("/") or ".." in parts:
            raise OperationValidationError(
                "destination_label must be a contained relative path")
        path = self._paths.backups_dir
        if path.is_symlink():
            raise OperationValidationError("BACKUP_INVALID: symlinked archive directory")
        for part in parts:
            path = path / part
            if path.is_symlink():
                raise OperationValidationError("BACKUP_INVALID: symlinked archive path")
        return path

    # ======================================================================
    # BACKUP_CREATE v1
    # ======================================================================

    def snapshot_database(self, ctx) -> dict[str, Any]:
        staging = self._backup_staging_dir(ctx.operation_id)
        staging.mkdir(parents=True, exist_ok=True)
        os.chmod(staging, 0o700)
        snapshot_path = staging / _SNAPSHOT_NAME
        # Hot-consistent snapshot via the maintained sqlite backup API.
        src = sqlite3.connect(str(self._paths.database_path))
        try:
            dst = sqlite3.connect(str(snapshot_path))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        return {"snapshot_path": str(snapshot_path),
                "snapshot_digest": _sha256_file(snapshot_path)}

    def probe_snapshot(self, ctx) -> "ProbeResult":

        snapshot = self._backup_staging_dir(ctx.operation_id) / _SNAPSHOT_NAME
        if snapshot.is_file():
            try:
                with closing(sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)) as conn:
                    if conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok":
                        return ProbeResult(RecoveryClass.COMPLETE, "SNAPSHOT_PRESENT")
            except sqlite3.Error:
                pass
        return ProbeResult(RecoveryClass.ABSENT, "SNAPSHOT_MISSING")

    def inventory_and_stage(self, ctx) -> dict[str, Any]:
        staging = self._backup_staging_dir(ctx.operation_id)
        snapshot = staging / _SNAPSHOT_NAME
        if not snapshot.is_file():
            raise OperationValidationError("snapshot missing before inventory")
        files = [
            FileEntry(
                relative_path=_SNAPSHOT_NAME,
                sha256=_sha256_file(snapshot),
                size_bytes=snapshot.stat().st_size,
                mode=0o600,
            )
        ]
        manifest = BackupManifest(
            application_release=APPLICATION_RELEASE,
            database_schema_version=SCHEMA_VERSION,
            created_at=self._clock(),
            model_bytes_included=False,
            files=files,
        )
        doc = build_backup_manifest(manifest)  # refuses secrets/traversal
        manifest_path = staging / MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(doc, sort_keys=True, indent=2), encoding="utf-8")
        return {"manifest_digest": _bare_digest(doc["manifest_digest"]),
                "manifest_path": str(manifest_path),
                "file_count": len(files)}

    def probe_staged(self, ctx) -> "ProbeResult":

        staging = self._backup_staging_dir(ctx.operation_id)
        if (staging / MANIFEST_NAME).is_file() and (
                staging / _SNAPSHOT_NAME).is_file():
            return ProbeResult(RecoveryClass.COMPLETE, "STAGED")
        return ProbeResult(RecoveryClass.ABSENT, "STAGING_INCOMPLETE")

    def publish_archive(self, ctx) -> dict[str, Any]:
        staging = self._backup_staging_dir(ctx.operation_id)
        archive = self._archive_path(ctx.request.destination_label)
        archive.parent.mkdir(parents=True, exist_ok=True)
        if archive.exists():
            # A collision NEVER overwrites an existing backup.
            raise OperationValidationError(
                f"BACKUP_COLLISION: {ctx.request.destination_label!r} exists")
        tmp = archive.with_suffix(archive.suffix + ".partial")
        with tarfile.open(tmp, "w", format=tarfile.USTAR_FORMAT) as tar:
            tar.add(str(staging / MANIFEST_NAME), arcname=MANIFEST_NAME)
            tar.add(str(staging / _SNAPSHOT_NAME), arcname=_SNAPSHOT_NAME)
        # fsync then no-replace publish.
        fd = os.open(str(tmp), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.link(str(tmp), str(archive))  # no-replace (EEXIST if present)
        except FileExistsError as exc:
            raise OperationValidationError(
                f"BACKUP_COLLISION: {ctx.request.destination_label!r} exists"
            ) from exc
        finally:
            tmp.unlink(missing_ok=True)
        return {"archive_path": str(archive),
                "archive_digest": _sha256_file(archive),
                "bytes_total": archive.stat().st_size}

    def probe_published(self, ctx) -> "ProbeResult":

        archive = self._archive_path(ctx.request.destination_label)
        if archive.is_file():
            return ProbeResult(RecoveryClass.COMPLETE, "PUBLISHED")
        return ProbeResult(RecoveryClass.ABSENT, "PUBLISH_INCOMPLETE")

    def verify_archive(self, ctx) -> dict[str, Any]:
        archive = self._archive_path(ctx.request.destination_label)
        inspected = inspect_archive(archive)
        doc = inspected.manifest
        return {"verified": True,
                "manifest_digest": _bare_digest(doc["manifest_digest"])}

    def probe_verified(self, ctx) -> "ProbeResult":

        archive = self._archive_path(ctx.request.destination_label)
        if archive.is_file():
            try:
                self.verify_archive(ctx)
                return ProbeResult(RecoveryClass.COMPLETE, "VERIFIED")
            except OperationValidationError:
                return ProbeResult(RecoveryClass.ABSENT, "VERIFY_FAILED")
        return ProbeResult(RecoveryClass.ABSENT, "ARCHIVE_MISSING")

    def record_backup(self, ctx) -> dict[str, Any]:
        archive = self._archive_path(ctx.request.destination_label)
        doc = inspect_archive(archive).manifest
        digest = _bare_digest(doc["manifest_digest"])
        with self._units.begin() as conn:
            repo = BackupSetRepository(conn)
            existing = conn.execute(
                "SELECT backup_id FROM backup_sets WHERE manifest_digest = ?",
                (digest,)).fetchone()
            if existing is None:
                row = repo.insert(
                    backup_id=f"bk-{ctx.operation_id}",
                    manifest_digest=digest,
                    storage_path_label=ctx.request.destination_label,
                    created_by_operation_id=ctx.operation_id,
                    bytes_total=archive.stat().st_size,
                    encryption_mode="none",
                )
                repo.mark_verification(
                    row.backup_id, state="verified",
                    expected_revision=row.revision)
        # Secure cleanup of the staging dir (retention window expired inline).
        staging = self._backup_staging_dir(ctx.operation_id)
        for child in staging.iterdir():
            child.unlink(missing_ok=True)
        staging.rmdir()
        return {"disposition": CODE_BACKUP_CREATED,
                "backup_id": f"bk-{ctx.operation_id}",
                "manifest_digest": digest}

    def probe_record(self, ctx) -> "ProbeResult":

        with self._units.read() as conn:
            row = conn.execute(
                "SELECT backup_id FROM backup_sets WHERE "
                "created_by_operation_id = ?", (ctx.operation_id,)).fetchone()
        if row is not None:
            return ProbeResult(RecoveryClass.COMPLETE, "RECORDED")
        return ProbeResult(RecoveryClass.ABSENT, "RECORD_MISSING")

    # ======================================================================
    # BACKUP_RESTORE v1
    # ======================================================================

    def _load_backup(self, backup_id: str, *, heartbeat=lambda: None) -> tuple[Path, dict[str, Any]]:
        with self._units.read() as conn:
            row = conn.execute(
                "SELECT storage_path_label, manifest_digest FROM backup_sets "
                "WHERE backup_id = ?", (backup_id,)).fetchone()
        if row is None:
            raise OperationValidationError(
                f"SOURCE_INVALID: unknown backup_id {backup_id!r}")
        archive = self._archive_path(row["storage_path_label"])
        if not archive.is_file():
            raise OperationValidationError("SOURCE_INVALID: archive missing")
        doc = inspect_archive(archive, heartbeat=heartbeat).manifest
        return archive, doc

    def validate_source(self, ctx) -> dict[str, Any]:
        archive, doc = self._load_backup(ctx.request.backup_id, heartbeat=getattr(ctx, "pulse", lambda: None))
        from .backup_restore import validate_restore
        from .restore_profile import inventory_local
        import shutil
        _, local_bytes = inventory_local(self._paths.app_dir)
        result = validate_restore(
            manifest_doc=doc, archive_complete=True, key_matches=True,
            contained_paths=True, current_schema_version=SCHEMA_VERSION,
            available_space_bytes=shutil.disk_usage(self._paths.app_dir).free,
            required_space_bytes=local_bytes + sum(f["size_bytes"] for f in doc["files"]) + 64 * 1024**2,
            writable_target=os.access(self._paths.app_dir.parent, os.W_OK))
        if not result.ok:
            raise OperationValidationError(result.refusal_code or "SOURCE_INVALID")
        if not verify_manifest_digest(doc):
            raise OperationValidationError("SOURCE_INVALID: manifest digest")
        if _bare_digest(doc["manifest_digest"]) != ctx.request.confirmation_digest:
            raise OperationValidationError(
                "SOURCE_INVALID: confirmation_digest does not bind this "
                "backup (dry-run digest mismatch)")
        return {"manifest_digest": _bare_digest(doc["manifest_digest"]),
                "archive_path": str(archive),
                "required_space_bytes": local_bytes + sum(f["size_bytes"] for f in doc["files"]) + 64 * 1024**2}

    def probe_source_valid(self, ctx) -> "ProbeResult":

        try:
            self.validate_source(ctx)
            return ProbeResult(RecoveryClass.COMPLETE, "SOURCE_VALID")
        except OperationValidationError:
            return ProbeResult(RecoveryClass.ABSENT, "SOURCE_INVALID")

    def stage_candidate(self, ctx) -> dict[str, Any]:
        with self._units.read() as conn:
            competing = conn.execute(
                "SELECT id FROM operations WHERE id != ? AND state IN "
                "('RUNNING','PREPARING','QUEUED','CANCELLING','ROLLING_BACK','RECOVERY_REQUIRED') LIMIT 1",
                (ctx.operation_id,)).fetchone()
        if competing is not None:
            raise OperationValidationError("PROFILE_BUSY: finish or repair other operations before restore")
        if self._quiesce is not None and self._quiesce().get("active") is not False:
            raise OperationValidationError("RESTORE_QUIESCE: model service did not stop")
        archive, doc = self._load_backup(ctx.request.backup_id, heartbeat=getattr(ctx, "pulse", lambda: None))
        candidate = self._restore_staging_profile(ctx.operation_id)
        if candidate.exists():
            # Idempotent resume: a partially staged candidate is re-staged.
            import shutil
            shutil.rmtree(candidate)
        candidate.mkdir(parents=True)
        os.chmod(candidate, 0o700)
        inspected = inspect_archive(archive, destination=candidate, heartbeat=ctx.pulse)
        if _bare_digest(inspected.manifest["manifest_digest"]) != ctx.request.confirmation_digest:
            raise OperationValidationError("SOURCE_INVALID: confirmation digest changed")
        # Migrate the staged database only (schema forward).
        staged_db = candidate / _SNAPSHOT_NAME
        from .db import initialize, open_database
        conn = open_database(staged_db, mode="migration")
        try:
            initialize(conn)
        finally:
            conn.close()
        from .restore_profile import preserve_local, preserve_current_database
        preserve_local(self._paths.app_dir, candidate,
                       heartbeat=ctx.pulse)
        preserve_current_database(self._paths.database_path, staged_db)
        from .fsops import atomic_write_text
        from .restore_profile import directory_identity
        atomic_write_text(candidate / ".restore-staged.json", json.dumps({
            "operation_id": ctx.operation_id,
            "manifest_digest": ctx.request.confirmation_digest,
            "directory_identity": directory_identity(candidate),
            "database_digest": _sha256_file(staged_db),
        }), mode=0o600)
        return {"candidate_profile": str(candidate),
                "candidate_identity": _sha256_file(staged_db)}

    def probe_staged_candidate(self, ctx) -> "ProbeResult":

        candidate = self._restore_staging_profile(ctx.operation_id)
        if (candidate / ".restore-staged.json").is_file():
            try:
                self.validate_staged(ctx)
                return ProbeResult(RecoveryClass.COMPLETE, "STAGED")
            except (OperationValidationError, OSError, ValueError):
                pass
        return ProbeResult(RecoveryClass.ABSENT, "STAGING_INCOMPLETE")

    def validate_staged(self, ctx) -> dict[str, Any]:
        candidate = self._restore_staging_profile(ctx.operation_id)
        staged_db = candidate / _SNAPSHOT_NAME
        if not staged_db.is_file():
            raise OperationValidationError("STAGED_INVALID: db missing")
        from .restore_profile import directory_identity
        try:
            with (candidate / ".restore-staged.json").open("rb") as source:
                raw = source.read(4097)
            if len(raw) > 4096:
                raise ValueError("size")
            staged = json.loads(raw)
            if (staged.get("operation_id") != ctx.operation_id
                    or staged.get("manifest_digest") != ctx.request.confirmation_digest
                    or staged.get("directory_identity") != directory_identity(candidate)
                    or staged.get("database_digest") != _sha256_file(staged_db)):
                raise ValueError("identity mismatch")
        except (OSError, ValueError) as exc:
            raise OperationValidationError("STAGED_INVALID: incomplete or changed staged profile") from exc
        conn = sqlite3.connect(str(staged_db))
        try:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if ok != "ok":
                raise OperationValidationError("STAGED_INVALID: integrity")
        finally:
            conn.close()
        return {"staged_integrity": "ok"}

    def probe_staged_validated(self, ctx) -> "ProbeResult":

        try:
            self.validate_staged(ctx)
            return ProbeResult(RecoveryClass.COMPLETE, "STAGED_VALID")
        except OperationValidationError:
            return ProbeResult(RecoveryClass.ABSENT, "STAGED_INVALID")

    def _carry_operation_record(self, operation_id: str,
                                prior_db: Path, restored_db: Path) -> None:
        """The profile exchange swaps the database the engine records the
        operation in. Carry the restore operation's durable rows (operation,
        steps, leases, events) from the retained prior profile into the
        restored database so the engine's fenced checkpoint protocol can
        continue across the exchange. A failure here leaves both profiles
        retained and converges to RECOVERY_REQUIRED (never a lost record)."""
        src = sqlite3.connect(str(prior_db))
        src.row_factory = sqlite3.Row
        dst = sqlite3.connect(str(restored_db))
        try:
            def _copy(table: str, key_col: str) -> None:
                rows = src.execute(
                    f"SELECT * FROM {table} WHERE {key_col} = ?",
                    (operation_id,)).fetchall()
                if not rows:
                    return
                cols = rows[0].keys()
                colnames = ",".join(cols)
                placeholders = ",".join("?" for _ in cols)
                for r in rows:
                    dst.execute(
                        f"INSERT OR REPLACE INTO {table} ({colnames}) "
                        f"VALUES ({placeholders})",
                        tuple(r[c] for c in cols))
            _copy("operations", "id")
            _copy("operation_steps", "operation_id")
            _copy("operation_leases", "operation_id")
            # operation_events (audit history) are intentionally NOT copied to
            # avoid cursor collisions; the pre-exchange history stays with the
            # retained prior profile and new events append in the restored DB.
            dst.commit()
        finally:
            src.close()
            dst.close()

    def publish_exchange(self, ctx) -> dict[str, Any]:
        from .restore_profile import (directory_identity, preserve_current_database,
                                      read_receipt, write_receipt)
        candidate = self._restore_staging_profile(ctx.operation_id)
        active = self._paths.app_dir
        receipt = read_receipt(active, ctx.operation_id)
        if receipt is not None:
            probe = self.probe_restore_published(ctx)
            if probe.classification is RecoveryClass.COMPLETE:
                return {"exchanged": True, "active_profile": str(active)}
            if probe.classification is not RecoveryClass.ABSENT:
                raise OperationValidationError("RESTORE_IDENTITY: publication requires Repair")
        # Carry the whole current authority graph BEFORE exchange. A process
        # death immediately after rename therefore cannot lose its operation.
        if receipt is None:
            self.validate_staged(ctx)
        elif _sha256_file(candidate / _SNAPSHOT_NAME) != receipt["candidate_database_digest"]:
            raise OperationValidationError("RESTORE_IDENTITY: prepared database changed")
        preserve_current_database(self._paths.database_path, candidate / _SNAPSHOT_NAME)
        receipt = {"operation_id": ctx.operation_id, "phase": "intent",
                   "manifest_digest": ctx.request.confirmation_digest,
                   "prior": directory_identity(active),
                   "candidate": directory_identity(candidate),
                   "candidate_database_digest": _sha256_file(candidate / _SNAPSHOT_NAME)}
        write_receipt(active, receipt)
        try:
            self._exchange(str(active), str(candidate), approved_root=str(active.parent))
        except (Refusal, SystemExit) as exc:
            raise OperationValidationError("PUBLISH_INCOMPLETE: profile exchange refused") from exc
        if directory_identity(active) != receipt["candidate"] or directory_identity(candidate) != receipt["prior"]:
            raise OperationValidationError("RESTORE_IDENTITY: exchange outcome is uncertain")
        receipt["phase"] = "published"
        write_receipt(active, receipt)
        return {"exchanged": True, "active_profile": str(active)}

    def probe_restore_published(self, ctx) -> "ProbeResult":
        from .restore_profile import directory_identity, read_receipt
        candidate = self._restore_staging_profile(ctx.operation_id)
        receipt = read_receipt(self._paths.app_dir, ctx.operation_id)
        if receipt is None:
            return ProbeResult(RecoveryClass.ABSENT, "PUBLISH_INCOMPLETE")
        try:
            active_id = directory_identity(self._paths.app_dir)
            candidate_id = directory_identity(candidate)
            if receipt["phase"] in {"rollback_intent", "rolled_back"} and active_id == receipt["prior"] and candidate_id == receipt["candidate"]:
                return ProbeResult(RecoveryClass.COMPLETE, "ROLLBACK_PUBLISHED")
            if active_id == receipt["candidate"] and candidate_id == receipt["prior"]:
                return ProbeResult(RecoveryClass.COMPLETE, "PUBLISHED")
            if active_id == receipt["prior"] and candidate_id == receipt["candidate"] and receipt["phase"] == "intent":
                return ProbeResult(RecoveryClass.ABSENT, "PUBLISH_INCOMPLETE")
        except (OSError, KeyError):
            pass
        return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "RESTORE_IDENTITY_UNCERTAIN")

    def verify_post_restore(self, ctx) -> dict[str, Any]:
        from .restore_profile import directory_identity, read_receipt, write_receipt
        receipt = read_receipt(self._paths.app_dir, ctx.operation_id)
        if receipt is None:
            raise OperationValidationError("POST_VERIFY_FAILED: missing exchange receipt")
        db = self._paths.database_path
        if receipt["phase"] in {"rollback_intent", "rolled_back"} and directory_identity(self._paths.app_dir) == receipt["prior"]:
            with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise OperationValidationError("ROLLBACK_UNCERTAIN: prior database invalid")
            receipt["phase"] = "rolled_back"
            write_receipt(self._paths.app_dir, receipt)
            return {"post_integrity": "rolled_back"}
        try:
            if receipt["phase"] == "rollback_intent":
                raise OperationValidationError("POST_VERIFY_FAILED: resume rollback")
            if directory_identity(self._paths.app_dir) != receipt["candidate"]:
                raise OperationValidationError("POST_VERIFY_FAILED: wrong active profile")
            with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
                if conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] != SCHEMA_VERSION:
                    raise OperationValidationError("POST_VERIFY_FAILED: schema")
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise OperationValidationError("POST_VERIFY_FAILED: integrity")
                if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise OperationValidationError("POST_VERIFY_FAILED: references")
        except (OSError, sqlite3.Error, OperationValidationError):
            candidate = self._restore_staging_profile(ctx.operation_id)
            if directory_identity(candidate) != receipt["prior"]:
                raise OperationValidationError("ROLLBACK_UNCERTAIN: prior identity changed")
            try:
                self._carry_operation_record(ctx.operation_id, db, candidate / _SNAPSHOT_NAME)
            except sqlite3.Error:
                pass  # Recovery resumes from the prior copy if corruption prevents carry.
            receipt["phase"] = "rollback_intent"
            write_receipt(self._paths.app_dir, receipt)
            self._exchange(str(self._paths.app_dir), str(candidate),
                           approved_root=str(self._paths.app_dir.parent))
            if directory_identity(self._paths.app_dir) != receipt["prior"]:
                raise OperationValidationError("ROLLBACK_UNCERTAIN: exchange unconfirmed")
            with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise OperationValidationError("ROLLBACK_UNCERTAIN: prior database invalid")
            receipt["phase"] = "rolled_back"
            write_receipt(self._paths.app_dir, receipt)
            return {"post_integrity": "rolled_back"}
        receipt["phase"] = "verified"
        write_receipt(self._paths.app_dir, receipt)
        return {"post_integrity": "ok"}

    def probe_post_verified(self, ctx) -> "ProbeResult":
        from .restore_profile import read_receipt
        receipt = read_receipt(self._paths.app_dir, ctx.operation_id)
        if receipt and receipt["phase"] in {"verified", "promoted", "rolled_back"}:
            try:
                with closing(sqlite3.connect(f"file:{self._paths.database_path}?mode=ro", uri=True)) as conn:
                    if conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and conn.execute("PRAGMA foreign_key_check").fetchone() is None:
                        return ProbeResult(RecoveryClass.COMPLETE, "POST_VERIFIED")
            except sqlite3.Error:
                pass
        return ProbeResult(RecoveryClass.ABSENT, "POST_VERIFY_FAILED")

    def promote_or_rollback(self, ctx) -> dict[str, Any]:
        from .restore_profile import read_receipt, write_receipt
        receipt = read_receipt(self._paths.app_dir, ctx.operation_id)
        if receipt and receipt["phase"] == "rolled_back":
            return {"disposition": CODE_RESTORE_ROLLED_BACK}
        # Post-verification passed (engine only reaches here when verified).
        # The profile database was swapped during the exchange, so the terminal
        # outcome is recorded in the NEW (restored) database — making the
        # restored profile self-documenting — while the pre-exchange tracking
        # row remains with the retained prior profile for rollback review.
        with self._units.begin() as conn:
            repo = RestoreAttemptRepository(conn)
            existing = conn.execute(
                "SELECT restore_id, revision FROM restore_attempts WHERE "
                "created_by_operation_id = ?", (ctx.operation_id,)).fetchone()
            if existing is None:
                # The restored database has no row for this restore operation
                # (it predates the restore), so the terminal record is recorded
                # without an operation FK; restore_id carries the lineage.
                row = repo.insert(
                    restore_id=f"rs-{ctx.operation_id}",
                    source_manifest_digest=ctx.request.confirmation_digest,
                    created_by_operation_id=None)
                repo.set_state(
                    row.restore_id, expected_revision=row.revision,
                    publish_state="published", post_verify_state="passed",
                    rollback_state="not_needed")
            else:
                repo.set_state(
                    existing["restore_id"],
                    expected_revision=existing["revision"],
                    publish_state="published", post_verify_state="passed",
                    rollback_state="not_needed")
        if receipt is None or receipt["phase"] != "verified":
            raise OperationValidationError("RESTORE_IDENTITY: verification receipt missing")
        receipt["phase"] = "promoted"
        write_receipt(self._paths.app_dir, receipt)
        return {"disposition": CODE_RESTORE_PUBLISHED,
                "retained_prior_profile": str(
                    self._restore_staging_profile(ctx.operation_id))}

    def probe_terminal(self, ctx) -> "ProbeResult":

        from .restore_profile import read_receipt
        receipt = read_receipt(self._paths.app_dir, ctx.operation_id)
        if receipt and receipt["phase"] in {"promoted", "rolled_back"}:
            return ProbeResult(RecoveryClass.COMPLETE, "TERMINAL_RECORDED")
        with self._units.read() as conn:
            row = conn.execute(
                "SELECT publish_state FROM restore_attempts WHERE "
                "created_by_operation_id = ?", (ctx.operation_id,)).fetchone()
        if row is not None and row["publish_state"] == "published":
            return ProbeResult(RecoveryClass.COMPLETE, "PROMOTED")
        return ProbeResult(RecoveryClass.ABSENT, "TERMINAL_MISSING")
