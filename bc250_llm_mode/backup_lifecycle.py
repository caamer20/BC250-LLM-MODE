"""Durable backup/restore publication repositories (ADR 006 / C2.2).

Typed SQL access over migration-010's ``backup_sets`` and
``restore_attempts``. Repositories never commit — callers own the unit-of-work
boundary. Raw SQL is confined to this module (and ``db.py`` migrations). Only
labels, digests, and states are persisted: never passphrases, raw keys, private
paths, prompts, or full archive listings. Updates are revision-fenced (CAS).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from .legacy_import import utcnow

_DIGEST_HEX = "0123456789abcdef"


def _is_sha256_hex(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in _DIGEST_HEX for c in value)
    )


class BackupRepositoryError(ValueError):
    """A malformed identity or a lost CAS fence."""


@dataclass(frozen=True)
class BackupSetRow:
    backup_id: str
    manifest_digest: str
    format_version: int
    created_by_operation_id: str | None
    storage_path_label: str
    bytes_total: int
    encryption_mode: str
    verification_state: str
    created_at: str
    verified_at: str | None
    revision: int


@dataclass(frozen=True)
class RestoreAttemptRow:
    restore_id: str
    source_manifest_digest: str
    created_by_operation_id: str | None
    staging_identity: str | None
    prior_profile_identity: str | None
    candidate_profile_identity: str | None
    publish_state: str
    post_verify_state: str | None
    rollback_state: str | None
    created_at: str
    updated_at: str
    revision: int


class BackupSetRepository:
    """backup_sets: one row per durable backup archive (label + digest)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert(
        self,
        *,
        backup_id: str,
        manifest_digest: str,
        storage_path_label: str,
        created_by_operation_id: str | None = None,
        format_version: int = 1,
        bytes_total: int = 0,
        encryption_mode: str = "none",
    ) -> BackupSetRow:
        if not _is_sha256_hex(manifest_digest):
            raise BackupRepositoryError("manifest_digest must be sha256 hex")
        if encryption_mode not in ("none", "aes-256-gcm"):
            raise BackupRepositoryError("unknown encryption_mode")
        now = utcnow()
        self._conn.execute(
            """
            INSERT INTO backup_sets (
                backup_id, manifest_digest, format_version,
                created_by_operation_id, storage_path_label, bytes_total,
                encryption_mode, verification_state, created_at, revision)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, 1)
            """,
            (backup_id, manifest_digest, format_version,
             created_by_operation_id, storage_path_label, bytes_total,
             encryption_mode, now),
        )
        return self.get(backup_id)  # type: ignore[return-value]

    def get(self, backup_id: str) -> BackupSetRow | None:
        row = self._conn.execute(
            "SELECT backup_id, manifest_digest, format_version, "
            "created_by_operation_id, storage_path_label, bytes_total, "
            "encryption_mode, verification_state, created_at, verified_at, "
            "revision FROM backup_sets WHERE backup_id = ?",
            (backup_id,),
        ).fetchone()
        return BackupSetRow(*row) if row else None

    def list(self) -> list[BackupSetRow]:
        rows = self._conn.execute(
            "SELECT backup_id, manifest_digest, format_version, "
            "created_by_operation_id, storage_path_label, bytes_total, "
            "encryption_mode, verification_state, created_at, verified_at, "
            "revision FROM backup_sets ORDER BY created_at DESC",
        ).fetchall()
        return [BackupSetRow(*r) for r in rows]

    def mark_verification(
        self, backup_id: str, *, state: str, expected_revision: int
    ) -> BackupSetRow:
        if state not in ("verified", "failed"):
            raise BackupRepositoryError("bad verification state")
        cur = self._conn.execute(
            """
            UPDATE backup_sets
               SET verification_state = ?, verified_at = ?,
                   revision = revision + 1
             WHERE backup_id = ? AND revision = ?
            """,
            (state, utcnow(), backup_id, expected_revision),
        )
        if cur.rowcount != 1:
            raise BackupRepositoryError("verification CAS fence lost")
        return self.get(backup_id)  # type: ignore[return-value]


class RestoreAttemptRepository:
    """restore_attempts: one row per restore publication attempt."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert(
        self,
        *,
        restore_id: str,
        source_manifest_digest: str,
        created_by_operation_id: str | None = None,
    ) -> RestoreAttemptRow:
        if not _is_sha256_hex(source_manifest_digest):
            raise BackupRepositoryError(
                "source_manifest_digest must be sha256 hex")
        now = utcnow()
        self._conn.execute(
            """
            INSERT INTO restore_attempts (
                restore_id, source_manifest_digest, created_by_operation_id,
                publish_state, created_at, updated_at, revision)
            VALUES (?, ?, ?, 'pending', ?, ?, 1)
            """,
            (restore_id, source_manifest_digest, created_by_operation_id,
             now, now),
        )
        return self.get(restore_id)  # type: ignore[return-value]

    def get(self, restore_id: str) -> RestoreAttemptRow | None:
        row = self._conn.execute(
            "SELECT restore_id, source_manifest_digest, "
            "created_by_operation_id, staging_identity, "
            "prior_profile_identity, candidate_profile_identity, "
            "publish_state, post_verify_state, rollback_state, created_at, "
            "updated_at, revision FROM restore_attempts WHERE restore_id = ?",
            (restore_id,),
        ).fetchone()
        return RestoreAttemptRow(*row) if row else None

    def set_identities(
        self,
        restore_id: str,
        *,
        expected_revision: int,
        staging_identity: str | None = None,
        prior_profile_identity: str | None = None,
        candidate_profile_identity: str | None = None,
    ) -> RestoreAttemptRow:
        cur = self._conn.execute(
            """
            UPDATE restore_attempts
               SET staging_identity = COALESCE(?, staging_identity),
                   prior_profile_identity =
                       COALESCE(?, prior_profile_identity),
                   candidate_profile_identity =
                       COALESCE(?, candidate_profile_identity),
                   updated_at = ?, revision = revision + 1
             WHERE restore_id = ? AND revision = ?
            """,
            (staging_identity, prior_profile_identity,
             candidate_profile_identity, utcnow(), restore_id,
             expected_revision),
        )
        if cur.rowcount != 1:
            raise BackupRepositoryError("identity CAS fence lost")
        return self.get(restore_id)  # type: ignore[return-value]

    def set_state(
        self,
        restore_id: str,
        *,
        expected_revision: int,
        publish_state: str | None = None,
        post_verify_state: str | None = None,
        rollback_state: str | None = None,
    ) -> RestoreAttemptRow:
        if publish_state is not None and publish_state not in (
                "pending", "staged", "published", "rolled_back",
                "recovery_required"):
            raise BackupRepositoryError("bad publish_state")
        if post_verify_state is not None and post_verify_state not in (
                "pending", "passed", "failed"):
            raise BackupRepositoryError("bad post_verify_state")
        if rollback_state is not None and rollback_state not in (
                "not_needed", "succeeded", "failed"):
            raise BackupRepositoryError("bad rollback_state")
        cur = self._conn.execute(
            """
            UPDATE restore_attempts
               SET publish_state = COALESCE(?, publish_state),
                   post_verify_state = COALESCE(?, post_verify_state),
                   rollback_state = COALESCE(?, rollback_state),
                   updated_at = ?, revision = revision + 1
             WHERE restore_id = ? AND revision = ?
            """,
            (publish_state, post_verify_state, rollback_state, utcnow(),
             restore_id, expected_revision),
        )
        if cur.rowcount != 1:
            raise BackupRepositoryError("state CAS fence lost")
        return self.get(restore_id)  # type: ignore[return-value]
