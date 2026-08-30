"""Bounded replacement-process acknowledgment for ADR 013 updates."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sys
from pathlib import Path
from typing import Any, Callable

from .application_pointer_helper import observe_pointers
from .application_update import VerifiedApplicationRelease
from .db import SCHEMA_VERSION, integrity_ok, open_database
from .fsops import atomic_write_text
from .operations.recovery import RecoveryClass
from .operations.workflow import ProbeResult, StepFailure
from .paths import AppPaths
from .runtime_process import (
    CommandKind,
    ProcessCommandSpec,
    ProcessFailure,
    RuntimeProcessRunner,
)

ACK_SCHEMA_VERSION = 1
INTENT_SCHEMA_VERSION = 1
MAX_RECEIPT_BYTES = 32 * 1024
_HEX64 = re.compile(r"[0-9a-f]{64}")
_OPERATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def _receipt(paths: AppPaths, operation_id: str, kind: str) -> Path:
    if not _OPERATION.fullmatch(operation_id):
        raise ValueError("operation identity is invalid")
    return paths.migration_receipts_dir / f"application-{kind}-{operation_id}.json"


def _read_receipt(path: Path, schema: int) -> dict[str, Any] | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_RECEIPT_BYTES:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return (
        value if isinstance(value, dict) and value.get("schema_version") == schema
        else None
    )


def run_post_update_mode(
    *,
    paths: AppPaths,
    operation_id: str,
    release_set_digest: str,
    process_nonce: str,
    target_schema: int,
    executable_prefix: Path | None = None,
) -> int:
    """Replacement-process entry. It starts no managed component or model."""
    if (
        not _OPERATION.fullmatch(operation_id)
        or not _HEX64.fullmatch(release_set_digest)
        or not _HEX64.fullmatch(process_nonce)
        or not isinstance(target_schema, int)
        or target_schema != SCHEMA_VERSION
    ):
        return 78
    observed = observe_pointers(paths.app_dir)
    expected_slot = paths.application_releases_dir / release_set_digest
    prefix = (executable_prefix or Path(sys.prefix)).resolve()
    try:
        expected_prefix = (expected_slot / "venv").resolve(strict=True)
    except OSError:
        return 78
    if observed.current != release_set_digest or prefix != expected_prefix:
        return 78

    # Composition is the normal migration boundary. No worker, server,
    # gateway, WebUI, Tailscale, or model is started by composition.
    from .app import Application

    application = Application.compose(paths)
    if not application.operational:
        return 78
    try:
        conn = open_database(paths.database_path, mode="read")
        try:
            if not integrity_ok(conn):
                return 78
            schema = int(conn.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0])
        finally:
            conn.close()
        if schema != target_schema:
            return 78
        application.query.snapshot()
        application.model_library.entries()
        platform = application.platform.status()
        if platform.get("integration_tier") == "unsupported":
            return 78
    except Exception:
        return 78

    nonce_digest = _digest_bytes(process_nonce.encode("ascii"))
    ack = {
        "schema_version": ACK_SCHEMA_VERSION,
        "operation_id": operation_id,
        "release_set_digest": release_set_digest,
        "nonce_digest": nonce_digest,
        "database_schema": schema,
        "probe_codes": [
            "CURRENT_SLOT_VERIFIED", "DATABASE_INTEGRITY_OK",
            "COMPOSITION_OK", "SNAPSHOT_OK", "MODEL_LIBRARY_READ_OK",
            "PLATFORM_OBSERVED", "NO_MODEL_STARTED",
        ],
    }
    text = _canonical(ack)
    ack["acknowledgment_digest"] = _digest_bytes(text.encode("utf-8"))
    atomic_write_text(_receipt(paths, operation_id, "update-ack"), _canonical(ack))
    return 0


class ApplicationPostUpdateProcessPort:
    def __init__(
        self,
        paths: AppPaths,
        units: Any,
        *,
        runner: RuntimeProcessRunner | None = None,
        backup_supplier: Callable[[], Any] | None = None,
        nonce_factory: Callable[[], str] | None = None,
    ) -> None:
        self.paths = paths
        self.units = units
        self.runner = runner or RuntimeProcessRunner()
        self.backup_supplier = backup_supplier
        self.nonce_factory = nonce_factory or (lambda: secrets.token_hex(32))

    def _intent_path(self, operation_id: str) -> Path:
        return _receipt(self.paths, operation_id, "update-intent")

    def _ack_path(self, operation_id: str) -> Path:
        return _receipt(self.paths, operation_id, "update-ack")

    def _verified_ack(
        self, operation_id: str, release: VerifiedApplicationRelease
    ) -> dict[str, Any] | None:
        intent = _read_receipt(self._intent_path(operation_id), INTENT_SCHEMA_VERSION)
        ack = _read_receipt(self._ack_path(operation_id), ACK_SCHEMA_VERSION)
        if intent is None or ack is None:
            return None
        expected_ack = dict(ack)
        recorded_digest = expected_ack.pop("acknowledgment_digest", None)
        expected_digest = _digest_bytes(_canonical(expected_ack).encode("utf-8"))
        if (
            set(intent) != {
                "schema_version", "operation_id", "release_set_digest",
                "nonce_digest", "target_schema",
            }
            or set(ack) != {
                "schema_version", "operation_id", "release_set_digest",
                "nonce_digest", "database_schema", "probe_codes",
                "acknowledgment_digest",
            }
            or intent.get("operation_id") != operation_id
            or ack.get("operation_id") != operation_id
            or intent.get("release_set_digest") != release.release_set_digest
            or ack.get("release_set_digest") != release.release_set_digest
            or intent.get("nonce_digest") != ack.get("nonce_digest")
            or intent.get("target_schema") != release.target_schema
            or ack.get("database_schema") != release.target_schema
            or recorded_digest != expected_digest
            or ack.get("probe_codes") != [
                "CURRENT_SLOT_VERIFIED", "DATABASE_INTEGRITY_OK",
                "COMPOSITION_OK", "SNAPSHOT_OK", "MODEL_LIBRARY_READ_OK",
                "PLATFORM_OBSERVED", "NO_MODEL_STARTED",
            ]
        ):
            return None
        return ack

    def launch(
        self, *, operation_id: str, release: VerifiedApplicationRelease,
        backup: dict[str, Any], publication: dict[str, Any],
    ) -> dict[str, Any]:
        del backup
        existing = self._verified_ack(operation_id, release)
        if existing is not None:
            return {
                "acknowledgment_digest": existing["acknowledgment_digest"],
                "database_schema": existing["database_schema"],
                "recovered": True,
            }
        if publication.get("candidate_installation_id") != release.release_set_digest:
            raise StepFailure(
                "POINTER_IDENTITY_MISMATCH", "published slot identity changed",
                mutation_possible=True,
            )
        nonce = self.nonce_factory()
        if not isinstance(nonce, str) or not _HEX64.fullmatch(nonce):
            raise StepFailure("POST_UPDATE_ACK_INVALID", "nonce source failed")
        intent = {
            "schema_version": INTENT_SCHEMA_VERSION,
            "operation_id": operation_id,
            "release_set_digest": release.release_set_digest,
            "nonce_digest": _digest_bytes(nonce.encode("ascii")),
            "target_schema": release.target_schema,
        }
        atomic_write_text(self._intent_path(operation_id), _canonical(intent))
        python = self.paths.application_current_link / "venv/bin/python"
        try:
            self.runner.run(ProcessCommandSpec(
                kind=CommandKind.SMOKE,
                argv=(
                    str(python), "-m", "bc250_llm_mode", "_post-update",
                    "--operation-id", operation_id,
                    "--release-set-digest", release.release_set_digest,
                    "--nonce", nonce,
                    "--target-schema", str(release.target_schema),
                ),
                working_directory=str(self.paths.app_dir),
                timeout_seconds=120.0,
                max_output_bytes=8 * 1024,
                redaction_tokens=(nonce,),
            ))
        except ProcessFailure as exc:
            code = (
                "POST_UPDATE_ACK_TIMEOUT" if exc.code == "PROCESS_TIMEOUT"
                else "POST_UPDATE_ACK_INVALID"
            )
            raise StepFailure(code, "replacement process did not acknowledge",
                              mutation_possible=True) from exc
        ack = self._verified_ack(operation_id, release)
        if ack is None:
            raise StepFailure(
                "POST_UPDATE_ACK_INVALID", "replacement acknowledgment failed",
                mutation_possible=True,
            )
        return {
            "acknowledgment_digest": ack["acknowledgment_digest"],
            "database_schema": ack["database_schema"],
            "recovered": False,
        }

    def probe(
        self, *, operation_id: str, release: VerifiedApplicationRelease
    ) -> ProbeResult:
        ack = self._verified_ack(operation_id, release)
        if ack is not None:
            return ProbeResult(
                RecoveryClass.COMPLETE, "POST_UPDATE_ACK_VERIFIED",
                {"acknowledgment_digest": ack["acknowledgment_digest"],
                 "database_schema": ack["database_schema"]},
            )
        if self._ack_path(operation_id).exists():
            return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "POST_UPDATE_ACK_INVALID")
        return ProbeResult(RecoveryClass.ABSENT, "POST_UPDATE_ACK_ABSENT")

    def health(
        self, *, operation_id: str, release: VerifiedApplicationRelease
    ) -> dict[str, Any]:
        ack = self._verified_ack(operation_id, release)
        if ack is None:
            raise StepFailure("POST_UPDATE_ACK_INVALID", "ack is not verified",
                              mutation_possible=True)
        try:
            conn = open_database(self.paths.database_path, mode="read")
            try:
                healthy = integrity_ok(conn)
                schema = int(conn.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0])
            finally:
                conn.close()
        except Exception as exc:
            raise StepFailure(
                "PROFILE_MIGRATION_FAILED", "profile health observation failed",
                mutation_possible=True,
            ) from exc
        pointers = observe_pointers(self.paths.app_dir)
        if not healthy or schema != release.target_schema or pointers.current != release.release_set_digest:
            raise StepFailure(
                "PROFILE_MIGRATION_FAILED", "post-update health did not verify",
                mutation_possible=True,
            )
        return {
            "database_schema": schema,
            "release_set_digest": release.release_set_digest,
            "model_started": False,
        }

    def probe_health(
        self, *, operation_id: str, release: VerifiedApplicationRelease
    ) -> ProbeResult:
        try:
            value = self.health(operation_id=operation_id, release=release)
        except StepFailure:
            return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "POST_UPDATE_HEALTH_INVALID")
        return ProbeResult(RecoveryClass.COMPLETE, "POST_UPDATE_HEALTH_VERIFIED", value)

    def restore_profile(
        self, *, operation_id: str, release: VerifiedApplicationRelease,
        backup: dict[str, Any],
    ) -> dict[str, Any]:
        if release.target_schema == release.source_schema:
            return {"profile_restore_required": False}
        service = self.backup_supplier() if self.backup_supplier else None
        backup_id = backup.get("backup_id")
        digest = backup.get("manifest_digest")
        if service is None or not isinstance(backup_id, str) or not isinstance(digest, str):
            raise StepFailure(
                "PROFILE_RESTORE_FAILED", "verified backup identity is absent",
                mutation_possible=True,
            )
        outcome = service.restore_start(backup_id, digest, requested_by="application-update")
        if not outcome.ok:
            raise StepFailure(
                "PROFILE_RESTORE_FAILED", "durable profile restore did not finish",
                mutation_possible=True,
            )
        receipt = {
            "schema_version": 1,
            "operation_id": operation_id,
            "backup_id": backup_id,
            "manifest_digest": digest,
        }
        atomic_write_text(_receipt(self.paths, operation_id, "profile-restore"), _canonical(receipt))
        return {"profile_restore_required": True, "backup_id": backup_id}

    def probe_profile_restored(
        self, *, operation_id: str, release: VerifiedApplicationRelease
    ) -> ProbeResult:
        if release.target_schema == release.source_schema:
            return ProbeResult(RecoveryClass.COMPLETE, "PROFILE_RESTORE_NOT_REQUIRED")
        receipt = _read_receipt(_receipt(self.paths, operation_id, "profile-restore"), 1)
        if receipt is None:
            return ProbeResult(RecoveryClass.ABSENT, "PROFILE_NOT_RESTORED")
        return ProbeResult(RecoveryClass.COMPLETE, "PROFILE_RESTORE_RECORDED")


__all__ = ["ApplicationPostUpdateProcessPort", "run_post_update_mode"]
