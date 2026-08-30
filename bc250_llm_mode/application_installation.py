"""Typed repositories for migration-014 application installation state.

Repositories never commit.  Trust decisions are made by
``ApplicationReleaseVerifier`` before these methods are called; persistence
records only content identities and lifecycle facts.  All mutations are
revision-fenced and all vocabularies are closed (ADR 013 D4).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from .application_update import (
    APPLICATION_UPDATE_VERIFIER_POLICY_VERSION,
    VerifiedApplicationRelease,
)
from .legacy_import import utcnow


_HEX64 = "0123456789abcdef"


def _hex(value: str) -> str:
    normalized = value[7:] if isinstance(value, str) and value.startswith("sha256:") else value
    if (
        not isinstance(normalized, str)
        or len(normalized) != 64
        or any(char not in _HEX64 for char in normalized)
    ):
        raise ApplicationInstallationError("identity must be lowercase sha256 hex")
    return normalized


class InstallationState(str, Enum):
    STAGED = "STAGED"
    CURRENT = "CURRENT"
    PREVIOUS = "PREVIOUS"
    QUARANTINED = "QUARANTINED"


class SmokeState(str, Enum):
    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"


class ImportSource(str, Enum):
    CHANNEL = "CHANNEL"
    OFFLINE = "OFFLINE"


class ImportState(str, Enum):
    VERIFIED = "VERIFIED"
    CONSUMED = "CONSUMED"
    QUARANTINED = "QUARANTINED"


_INSTALL_TRANSITIONS = {
    InstallationState.STAGED: frozenset({
        InstallationState.CURRENT, InstallationState.QUARANTINED,
    }),
    InstallationState.CURRENT: frozenset({InstallationState.PREVIOUS}),
    InstallationState.PREVIOUS: frozenset({
        InstallationState.CURRENT, InstallationState.QUARANTINED,
    }),
    InstallationState.QUARANTINED: frozenset(),
}


class ApplicationInstallationError(ValueError):
    """Malformed identity, invalid transition, or lost revision fence."""


@dataclass(frozen=True)
class ApplicationInstallationRow:
    installation_id: str
    version: str
    source_commit: str
    source_ref: str
    repository: str
    release_set_digest: str
    manifest_digest: str
    inventory_digest: str
    wheel_digest: str
    source_schema: int
    minimum_readable_schema: int
    maximum_readable_schema: int
    target_schema: int
    platform_qualification_id: str
    release_directory_identity: str
    state: InstallationState
    smoke_state: SmokeState
    created_by_operation_id: str | None
    created_at: str
    published_at: str | None
    last_verified_at: str | None
    revision: int


@dataclass(frozen=True)
class ApplicationInstallationStateRow:
    current_installation_id: str | None
    previous_installation_id: str | None
    pointer_generation: int
    pending_update_operation_id: str | None
    recovery_barrier: str | None
    last_ack_installation_id: str | None
    last_ack_process_nonce_digest: str | None
    updated_at: str
    revision: int


@dataclass(frozen=True)
class ApplicationUpdateImportRow:
    release_set_digest: str
    source_class: ImportSource
    verifier_policy_version: int
    trust_root_id: str
    state: ImportState
    verified_at: str
    revision: int


class ApplicationInstallationRepository:
    _COLUMNS = (
        "installation_id, version, source_commit, source_ref, repository, "
        "release_set_digest, manifest_digest, inventory_digest, wheel_digest, "
        "source_schema, minimum_readable_schema, maximum_readable_schema, "
        "target_schema, platform_qualification_id, "
        "release_directory_identity, state, smoke_state, "
        "created_by_operation_id, created_at, published_at, last_verified_at, "
        "revision"
    )

    def __init__(
        self, conn: sqlite3.Connection, *, clock: Callable[[], str] = utcnow
    ) -> None:
        self.conn = conn
        self.clock = clock

    @staticmethod
    def _row(row: sqlite3.Row | None) -> ApplicationInstallationRow | None:
        if row is None:
            return None
        values = list(row)
        values[15] = InstallationState(values[15])
        values[16] = SmokeState(values[16])
        return ApplicationInstallationRow(*values)

    def insert_staged(
        self,
        release: VerifiedApplicationRelease,
        *,
        created_by_operation_id: str | None,
    ) -> ApplicationInstallationRow:
        if not isinstance(release, VerifiedApplicationRelease):
            raise ApplicationInstallationError(
                "a verifier-produced application release is required"
            )
        installation_id = _hex(release.release_set_digest)
        now = self.clock()
        try:
            self.conn.execute(
                """
                INSERT INTO application_installations (
                    installation_id, version, source_commit, source_ref,
                    repository, release_set_digest, manifest_digest,
                    inventory_digest, wheel_digest, source_schema,
                    minimum_readable_schema, maximum_readable_schema,
                    target_schema, platform_qualification_id,
                    release_directory_identity, state, smoke_state,
                    created_by_operation_id, created_at, revision)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        'STAGED', 'PENDING', ?, ?, 1)
                """,
                (
                    installation_id, release.version, release.source_commit,
                    release.source_ref, release.repository, installation_id,
                    _hex(release.manifest_digest),
                    _hex(release.inventory_digest), _hex(release.wheel_digest),
                    release.source_schema, release.minimum_readable_schema,
                    release.maximum_readable_schema, release.target_schema,
                    release.platform_profile, installation_id,
                    created_by_operation_id, now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ApplicationInstallationError(
                "application installation identity already exists or is invalid"
            ) from exc
        return self.get(installation_id)  # type: ignore[return-value]

    def get(self, installation_id: str) -> ApplicationInstallationRow | None:
        row = self.conn.execute(
            f"SELECT {self._COLUMNS} FROM application_installations "
            "WHERE installation_id = ?",
            (_hex(installation_id),),
        ).fetchone()
        return self._row(row)

    def list(self, *, limit: int = 16) -> tuple[ApplicationInstallationRow, ...]:
        if not isinstance(limit, int) or not 1 <= limit <= 64:
            raise ApplicationInstallationError("installation list limit is invalid")
        rows = self.conn.execute(
            f"SELECT {self._COLUMNS} FROM application_installations "
            "ORDER BY created_at DESC, installation_id LIMIT ?",
            (limit,),
        ).fetchall()
        return tuple(self._row(row) for row in rows)  # type: ignore[misc]

    def mark_smoke(
        self,
        installation_id: str,
        *,
        state: SmokeState | str,
        expected_revision: int,
    ) -> ApplicationInstallationRow:
        state = SmokeState(state)
        if state not in {SmokeState.PASSED, SmokeState.FAILED}:
            raise ApplicationInstallationError("smoke must terminate passed or failed")
        cursor = self.conn.execute(
            """
            UPDATE application_installations
               SET smoke_state = ?, last_verified_at = ?, revision = revision + 1
             WHERE installation_id = ? AND revision = ?
               AND smoke_state = 'PENDING'
            """,
            (state.value, self.clock(), _hex(installation_id), expected_revision),
        )
        if cursor.rowcount != 1:
            raise ApplicationInstallationError("installation smoke CAS fence lost")
        return self.get(installation_id)  # type: ignore[return-value]

    def transition(
        self,
        installation_id: str,
        *,
        target: InstallationState | str,
        expected_state: InstallationState | str,
        expected_revision: int,
    ) -> ApplicationInstallationRow:
        target = InstallationState(target)
        expected = InstallationState(expected_state)
        if target not in _INSTALL_TRANSITIONS[expected]:
            raise ApplicationInstallationError(
                f"invalid installation transition {expected.value} -> {target.value}"
            )
        now = self.clock()
        cursor = self.conn.execute(
            """
            UPDATE application_installations
               SET state = ?,
                   published_at = CASE WHEN ? = 'CURRENT'
                                       THEN COALESCE(published_at, ?)
                                       ELSE published_at END,
                   revision = revision + 1
             WHERE installation_id = ? AND state = ? AND revision = ?
            """,
            (
                target.value, target.value, now, _hex(installation_id),
                expected.value, expected_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise ApplicationInstallationError("installation transition CAS fence lost")
        return self.get(installation_id)  # type: ignore[return-value]


class ApplicationInstallationStateRepository:
    def __init__(
        self, conn: sqlite3.Connection, *, clock: Callable[[], str] = utcnow
    ) -> None:
        self.conn = conn
        self.clock = clock

    def get(self) -> ApplicationInstallationStateRow:
        row = self.conn.execute(
            "SELECT current_installation_id, previous_installation_id, "
            "pointer_generation, pending_update_operation_id, recovery_barrier, "
            "last_ack_installation_id, last_ack_process_nonce_digest, "
            "updated_at, revision FROM application_installation_state WHERE id = 1"
        ).fetchone()
        if row is None:
            raise ApplicationInstallationError("installation state singleton missing")
        return ApplicationInstallationStateRow(*row)

    def reserve(
        self, operation_id: str, *, expected_revision: int
    ) -> ApplicationInstallationStateRow:
        if not isinstance(operation_id, str) or not operation_id:
            raise ApplicationInstallationError("operation identity is required")
        current = self.get()
        if current.pending_update_operation_id == operation_id:
            return current
        cursor = self.conn.execute(
            """
            UPDATE application_installation_state
               SET pending_update_operation_id = ?, updated_at = ?,
                   revision = revision + 1
             WHERE id = 1 AND revision = ?
               AND pending_update_operation_id IS NULL
               AND recovery_barrier IS NULL
            """,
            (operation_id, self.clock(), expected_revision),
        )
        if cursor.rowcount != 1:
            raise ApplicationInstallationError("application update reservation lost")
        return self.get()

    def commit_publication(
        self,
        *,
        operation_id: str,
        candidate_installation_id: str,
        prior_current_installation_id: str,
        expected_previous_installation_id: str | None,
        expected_pointer_generation: int,
        acknowledgment_digest: str,
        expected_revision: int,
    ) -> ApplicationInstallationStateRow:
        candidate = _hex(candidate_installation_id)
        prior = _hex(prior_current_installation_id)
        previous = (
            _hex(expected_previous_installation_id)
            if expected_previous_installation_id is not None else None
        )
        ack = _hex(acknowledgment_digest)
        cursor = self.conn.execute(
            """
            UPDATE application_installation_state
               SET current_installation_id = ?,
                   previous_installation_id = ?,
                   pointer_generation = pointer_generation + 1,
                   pending_update_operation_id = NULL,
                   recovery_barrier = NULL,
                   last_ack_installation_id = ?,
                   last_ack_process_nonce_digest = ?,
                   updated_at = ?, revision = revision + 1
             WHERE id = 1 AND revision = ?
               AND current_installation_id = ?
               AND (previous_installation_id = ? OR
                    (previous_installation_id IS NULL AND ? IS NULL))
               AND pointer_generation = ?
               AND pending_update_operation_id = ?
               AND recovery_barrier IS NULL
            """,
            (
                candidate, prior, candidate, ack, self.clock(),
                expected_revision, prior, previous, previous,
                expected_pointer_generation, operation_id,
            ),
        )
        if cursor.rowcount != 1:
            raise ApplicationInstallationError("publication state CAS fence lost")
        return self.get()

    def release_reservation(
        self, operation_id: str, *, expected_revision: int
    ) -> ApplicationInstallationStateRow:
        cursor = self.conn.execute(
            """
            UPDATE application_installation_state
               SET pending_update_operation_id = NULL, updated_at = ?,
                   revision = revision + 1
             WHERE id = 1 AND revision = ?
               AND pending_update_operation_id = ?
               AND recovery_barrier IS NULL
            """,
            (self.clock(), expected_revision, operation_id),
        )
        if cursor.rowcount != 1:
            raise ApplicationInstallationError("reservation release CAS fence lost")
        return self.get()

    def set_recovery_barrier(
        self, code: str, *, operation_id: str, expected_revision: int
    ) -> ApplicationInstallationStateRow:
        if not isinstance(code, str) or not 1 <= len(code) <= 80:
            raise ApplicationInstallationError("recovery barrier code is invalid")
        cursor = self.conn.execute(
            """
            UPDATE application_installation_state
               SET recovery_barrier = ?, updated_at = ?, revision = revision + 1
             WHERE id = 1 AND revision = ?
               AND pending_update_operation_id = ?
            """,
            (code, self.clock(), expected_revision, operation_id),
        )
        if cursor.rowcount != 1:
            raise ApplicationInstallationError("recovery barrier CAS fence lost")
        return self.get()


class ApplicationUpdateImportRepository:
    def __init__(
        self, conn: sqlite3.Connection, *, clock: Callable[[], str] = utcnow
    ) -> None:
        self.conn = conn
        self.clock = clock

    @staticmethod
    def _row(row: sqlite3.Row | None) -> ApplicationUpdateImportRow | None:
        if row is None:
            return None
        values = list(row)
        values[1] = ImportSource(values[1])
        values[4] = ImportState(values[4])
        return ApplicationUpdateImportRow(*values)

    def record_verified(
        self,
        release: VerifiedApplicationRelease,
        *,
        source_class: ImportSource | str,
    ) -> ApplicationUpdateImportRow:
        if not isinstance(release, VerifiedApplicationRelease):
            raise ApplicationInstallationError(
                "a verifier-produced application release is required"
            )
        source = ImportSource(source_class)
        digest = _hex(release.release_set_digest)
        try:
            self.conn.execute(
                """
                INSERT INTO application_update_imports (
                    release_set_digest, source_class, verifier_policy_version,
                    trust_root_id, state, verified_at, revision)
                VALUES (?, ?, ?, ?, 'VERIFIED', ?, 1)
                """,
                (
                    digest, source.value,
                    APPLICATION_UPDATE_VERIFIER_POLICY_VERSION,
                    release.trust_root_id, self.clock(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ApplicationInstallationError(
                "verified release import already exists or is invalid"
            ) from exc
        return self.get(digest)  # type: ignore[return-value]

    def get(self, release_set_digest: str) -> ApplicationUpdateImportRow | None:
        row = self.conn.execute(
            "SELECT release_set_digest, source_class, verifier_policy_version, "
            "trust_root_id, state, verified_at, revision "
            "FROM application_update_imports WHERE release_set_digest = ?",
            (_hex(release_set_digest),),
        ).fetchone()
        return self._row(row)

    def transition(
        self,
        release_set_digest: str,
        *,
        target: ImportState | str,
        expected_revision: int,
    ) -> ApplicationUpdateImportRow:
        target = ImportState(target)
        if target not in {ImportState.CONSUMED, ImportState.QUARANTINED}:
            raise ApplicationInstallationError("invalid import terminal state")
        cursor = self.conn.execute(
            "UPDATE application_update_imports SET state = ?, "
            "revision = revision + 1 WHERE release_set_digest = ? "
            "AND state = 'VERIFIED' AND revision = ?",
            (target.value, _hex(release_set_digest), expected_revision),
        )
        if cursor.rowcount != 1:
            raise ApplicationInstallationError("update import CAS fence lost")
        return self.get(release_set_digest)  # type: ignore[return-value]


__all__ = [
    "ApplicationInstallationError",
    "ApplicationInstallationRepository",
    "ApplicationInstallationRow",
    "ApplicationInstallationStateRepository",
    "ApplicationInstallationStateRow",
    "ApplicationUpdateImportRepository",
    "ApplicationUpdateImportRow",
    "ImportSource",
    "ImportState",
    "InstallationState",
    "SmokeState",
]
