"""P8 §14.2/§14.3: fail-closed backup restore validation (pure, no I/O).

Every restore begins with a DRY-RUN gate that refuses BEFORE any mutation when
the archive is tampered, partial, wrong-key, path-traversing, newer-schema, or
low-space. The current profile is never touched unless every check passes. This
module is pure: it validates an inspected archive description + a target-profile
description and returns a typed result. It never reads the filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .backup_manifest import (
    BACKUP_MANIFEST_SCHEMA_VERSION,
    verify_manifest_digest,
)

RESTORE_VALIDATION_SCHEMA_VERSION = 1


class RestoreRefusalCode(str, Enum):
    """Closed refusal vocabulary (P8 §14.2 fail-closed)."""

    TAMPERED_MANIFEST = "TAMPERED_MANIFEST"
    INCOMPLETE_ARCHIVE = "INCOMPLETE_ARCHIVE"
    WRONG_KEY = "WRONG_KEY"
    PATH_TRAVERSAL = "PATH_TRAVERSAL"
    NEWER_SCHEMA = "NEWER_SCHEMA"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    LOW_SPACE = "LOW_SPACE"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"


@dataclass(frozen=True)
class RestoreCheckResult:
    """Outcome of the dry-run restore gate."""

    ok: bool
    refusal_code: str | None = None
    reason: str = ""
    checks_passed: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RESTORE_VALIDATION_SCHEMA_VERSION,
            "ok": self.ok,
            "refusal_code": self.refusal_code,
            "reason": self.reason,
            "checks_passed": list(self.checks_passed),
        }


def _refuse(code: RestoreRefusalCode, reason: str,
            passed: list[str]) -> RestoreCheckResult:
    return RestoreCheckResult(ok=False, refusal_code=code.value, reason=reason,
                              checks_passed=tuple(passed))


def validate_restore(
    *,
    manifest_doc: dict[str, Any] | None,
    archive_complete: bool,
    key_matches: bool,
    contained_paths: bool,
    current_schema_version: int,
    available_space_bytes: int,
    required_space_bytes: int,
    writable_target: bool,
    identity_matches: bool = True,
) -> RestoreCheckResult:
    """Dry-run restore gate (P8 §14.3 steps 1-2). Refuses before any mutation.

    Check order: manifest presence/integrity -> completeness -> key ->
    containment -> schema -> space -> permissions -> identity.
    """
    passed: list[str] = []

    if manifest_doc is None:
        return _refuse(RestoreRefusalCode.INCOMPLETE_ARCHIVE,
                       "archive has no manifest", passed)
    if not verify_manifest_digest(manifest_doc):
        return _refuse(RestoreRefusalCode.TAMPERED_MANIFEST,
                       "manifest digest mismatch (tampered)", passed)
    passed.append("manifest-integrity")

    if not archive_complete:
        return _refuse(RestoreRefusalCode.INCOMPLETE_ARCHIVE,
                       "archive is incomplete", passed)
    passed.append("archive-complete")

    if not key_matches:
        return _refuse(RestoreRefusalCode.WRONG_KEY,
                       "wrong encryption key", passed)
    passed.append("key")

    if not contained_paths:
        return _refuse(RestoreRefusalCode.PATH_TRAVERSAL,
                       "archive paths escape the restore root", passed)
    passed.append("containment")

    backup_schema = int(manifest_doc.get("database_schema_version", 0))
    manifest_schema = int(
        manifest_doc.get("backup_manifest_schema_version", 0))
    if manifest_schema > BACKUP_MANIFEST_SCHEMA_VERSION:
        return _refuse(RestoreRefusalCode.UNSUPPORTED_SCHEMA,
                       "backup manifest schema newer than supported", passed)
    if backup_schema > current_schema_version:
        return _refuse(RestoreRefusalCode.NEWER_SCHEMA,
                       "backup database schema newer than current; upgrade "
                       "instead of restore", passed)
    passed.append("schema")

    if available_space_bytes < required_space_bytes:
        return _refuse(RestoreRefusalCode.LOW_SPACE,
                       "insufficient disk space for restore", passed)
    passed.append("space")

    if not writable_target:
        return _refuse(RestoreRefusalCode.PERMISSION_DENIED,
                       "target profile is not writable", passed)
    passed.append("permissions")

    if not identity_matches:
        return _refuse(RestoreRefusalCode.IDENTITY_MISMATCH,
                       "artifact/runtime identity mismatch", passed)
    passed.append("identity")

    return RestoreCheckResult(ok=True, checks_passed=tuple(passed))
