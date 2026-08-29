"""Typed migration-011 repositories for independently revocable clients."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Callable, Iterable

from .legacy_import import utcnow
from .repositories import RepositoryConflict


CLIENT_KINDS = frozenset({"openwebui", "pocketpal", "openai", "curl", "sse"})
CLIENT_SCOPES = ("models:list", "inference:read", "inference:stream")
ENDPOINT_CLASSES = frozenset({"models", "chat", "stream"})
MAX_ACTIVE_CLIENTS = 32
_CLIENT_ID = re.compile(r"[0-9a-f]{32}\Z")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")


class ConnectionCredentialError(ValueError):
    """Stable validation failure before any credential metadata is written."""


@dataclass(frozen=True)
class ConnectionClientRecord:
    client_id: str
    label: str
    client_kind: str
    scopes: tuple[str, ...]
    active_fingerprint: str
    active_generation: int
    secret_storage: str
    created_at: str
    rotated_at: str | None
    revoked_at: str | None
    last_used_at: str | None
    last_endpoint_class: str | None
    revision: int

    def to_dict(self) -> dict:
        value = asdict(self)
        value["scopes"] = list(self.scopes)
        return value


@dataclass(frozen=True)
class AuthenticationFingerprint:
    client_id: str
    scopes: tuple[str, ...]
    fingerprint: str
    generation: int
    state: str
    overlap_expires_at: str | None


def normalize_label(value: str) -> str:
    label = " ".join(str(value).strip().split())
    if not (1 <= len(label) <= 80):
        raise ConnectionCredentialError("client label must be 1-80 characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in label):
        raise ConnectionCredentialError("client label contains control characters")
    return label


def normalize_kind(value: str) -> str:
    kind = str(value).strip().lower()
    if kind not in CLIENT_KINDS:
        raise ConnectionCredentialError("unknown client kind")
    return kind


def normalize_scopes(values: Iterable[str]) -> tuple[str, ...]:
    requested = {str(value).strip() for value in values}
    if not requested or not requested <= set(CLIENT_SCOPES):
        raise ConnectionCredentialError("unknown or empty client scope set")
    return tuple(scope for scope in CLIENT_SCOPES if scope in requested)


def validate_client_id(value: str, *, allow_legacy: bool = False) -> str:
    client_id = str(value)
    if allow_legacy and client_id == "legacy-install":
        return client_id
    if not _CLIENT_ID.fullmatch(client_id):
        raise ConnectionCredentialError("client id must be generated lowercase UUID hex")
    return client_id


def validate_fingerprint(value: str) -> str:
    fingerprint = str(value)
    if not _FINGERPRINT.fullmatch(fingerprint):
        raise ConnectionCredentialError("credential fingerprint must be sha256 hex")
    return fingerprint


class ConnectionClientRepository:
    """Revision-fenced client metadata and fingerprint generations."""

    def __init__(self, conn, *, clock: Callable[[], str] = utcnow) -> None:
        self.conn = conn
        self._clock = clock

    @staticmethod
    def _record(row) -> ConnectionClientRecord:
        return ConnectionClientRecord(
            client_id=row["client_id"], label=row["label"],
            client_kind=row["client_kind"],
            scopes=tuple(row["scopes"].split(",")),
            active_fingerprint=row["active_fingerprint"],
            active_generation=int(row["active_generation"]),
            secret_storage=row["secret_storage"], created_at=row["created_at"],
            rotated_at=row["rotated_at"], revoked_at=row["revoked_at"],
            last_used_at=row["last_used_at"],
            last_endpoint_class=row["last_endpoint_class"],
            revision=int(row["revision"]),
        )

    def get(self, client_id: str) -> ConnectionClientRecord | None:
        row = self.conn.execute(
            "SELECT * FROM connection_clients WHERE client_id = ?", (client_id,)
        ).fetchone()
        return self._record(row) if row else None

    def list(self, *, include_revoked: bool = True) -> tuple[ConnectionClientRecord, ...]:
        where = "" if include_revoked else "WHERE revoked_at IS NULL"
        rows = self.conn.execute(
            f"SELECT * FROM connection_clients {where} "
            "ORDER BY lower(label), client_id"
        ).fetchall()
        return tuple(self._record(row) for row in rows)

    def create(
        self, *, client_id: str, label: str, client_kind: str,
        scopes: Iterable[str], fingerprint: str, generation: int = 1,
        secret_storage: str = "managed",
    ) -> ConnectionClientRecord:
        client_id = validate_client_id(client_id)
        label = normalize_label(label)
        kind = normalize_kind(client_kind)
        normalized_scopes = normalize_scopes(scopes)
        fingerprint = validate_fingerprint(fingerprint)
        if generation != 1 or secret_storage != "managed":
            raise ConnectionCredentialError("new clients require managed generation 1")
        active = self.conn.execute(
            "SELECT COUNT(*) AS n FROM connection_clients WHERE revoked_at IS NULL"
        ).fetchone()["n"]
        if int(active) >= MAX_ACTIVE_CLIENTS:
            raise ConnectionCredentialError("active client limit reached")
        if any(
            item.label.casefold() == label.casefold()
            for item in self.list(include_revoked=False)
        ):
            raise ConnectionCredentialError("active client label already exists")
        now = self._clock()
        try:
            self.conn.execute(
                "INSERT INTO connection_clients (client_id, label, client_kind, "
                "scopes, active_fingerprint, active_generation, secret_storage, "
                "created_at, revision) VALUES (?, ?, ?, ?, ?, 1, 'managed', ?, 1)",
                (client_id, label, kind, ",".join(normalized_scopes), fingerprint, now),
            )
            self.conn.execute(
                "INSERT INTO connection_client_secrets (client_id, generation, "
                "fingerprint, state, created_at) VALUES (?, 1, ?, 'ACTIVE', ?)",
                (client_id, fingerprint, now),
            )
        except Exception as exc:
            raise RepositoryConflict("CLIENT_CREATE_CONFLICT", "client identity or label conflicts") from exc
        result = self.get(client_id)
        assert result is not None
        return result

    def rotate(
        self, client_id: str, *, expected_revision: int,
        new_fingerprint: str, new_generation: int,
        overlap_expires_at: str | None = None,
    ) -> ConnectionClientRecord:
        new_fingerprint = validate_fingerprint(new_fingerprint)
        current = self.get(client_id)
        if current is None:
            raise RepositoryConflict("CLIENT_NOT_FOUND", "client does not exist")
        if current.revoked_at is not None:
            raise RepositoryConflict("CLIENT_REVOKED", "revoked client cannot rotate")
        if current.revision != expected_revision:
            raise RepositoryConflict("CLIENT_REVISION_CONFLICT", "stale client revision")
        if new_generation != current.active_generation + 1:
            raise ConnectionCredentialError("credential generation must increment exactly once")
        now = self._clock()
        prior_state = "OVERLAP" if overlap_expires_at else "RETIRED"
        self.conn.execute(
            "UPDATE connection_client_secrets SET state = ?, "
            "overlap_expires_at = ?, retired_at = ? "
            "WHERE client_id = ? AND generation = ? AND state = 'ACTIVE'",
            (prior_state, overlap_expires_at, None if overlap_expires_at else now,
             client_id, current.active_generation),
        )
        try:
            self.conn.execute(
                "INSERT INTO connection_client_secrets (client_id, generation, "
                "fingerprint, state, created_at) VALUES (?, ?, ?, 'ACTIVE', ?)",
                (client_id, new_generation, new_fingerprint, now),
            )
        except Exception as exc:
            raise RepositoryConflict("CLIENT_ROTATE_CONFLICT", "credential generation conflicts") from exc
        cursor = self.conn.execute(
            "UPDATE connection_clients SET active_fingerprint = ?, "
            "active_generation = ?, rotated_at = ?, revision = revision + 1 "
            "WHERE client_id = ? AND revision = ? AND revoked_at IS NULL",
            (new_fingerprint, new_generation, now, client_id, expected_revision),
        )
        if cursor.rowcount != 1:
            raise RepositoryConflict("CLIENT_REVISION_CONFLICT", "stale client revision")
        result = self.get(client_id)
        assert result is not None
        return result

    def revoke(self, client_id: str, *, expected_revision: int) -> ConnectionClientRecord:
        current = self.get(client_id)
        if current is None:
            raise RepositoryConflict("CLIENT_NOT_FOUND", "client does not exist")
        if current.revision != expected_revision:
            raise RepositoryConflict("CLIENT_REVISION_CONFLICT", "stale client revision")
        now = self._clock()
        cursor = self.conn.execute(
            "UPDATE connection_clients SET revoked_at = COALESCE(revoked_at, ?), "
            "revision = revision + 1 WHERE client_id = ? AND revision = ?",
            (now, client_id, expected_revision),
        )
        if cursor.rowcount != 1:
            raise RepositoryConflict("CLIENT_REVISION_CONFLICT", "stale client revision")
        self.conn.execute(
            "UPDATE connection_client_secrets SET state = 'RETIRED', "
            "retired_at = COALESCE(retired_at, ?), overlap_expires_at = NULL "
            "WHERE client_id = ? AND state != 'RETIRED'", (now, client_id),
        )
        result = self.get(client_id)
        assert result is not None
        return result

    def record_use(
        self, client_id: str, *, endpoint_class: str,
        expected_revision: int,
    ) -> ConnectionClientRecord:
        if endpoint_class not in ENDPOINT_CLASSES:
            raise ConnectionCredentialError("unknown endpoint class")
        cursor = self.conn.execute(
            "UPDATE connection_clients SET last_used_at = ?, "
            "last_endpoint_class = ?, revision = revision + 1 "
            "WHERE client_id = ? AND revision = ? AND revoked_at IS NULL",
            (self._clock(), endpoint_class, client_id, expected_revision),
        )
        if cursor.rowcount != 1:
            raise RepositoryConflict("CLIENT_REVISION_CONFLICT", "stale client revision")
        result = self.get(client_id)
        assert result is not None
        return result

    def authentication_fingerprints(self, *, now: str) -> tuple[AuthenticationFingerprint, ...]:
        rows = self.conn.execute(
            "SELECT c.client_id, c.scopes, s.fingerprint, s.generation, "
            "s.state, s.overlap_expires_at FROM connection_clients c "
            "JOIN connection_client_secrets s ON s.client_id = c.client_id "
            "JOIN connection_access_state a ON a.id = 1 "
            "WHERE a.enabled = 1 AND c.revoked_at IS NULL AND "
            "(s.state = 'ACTIVE' OR (s.state = 'OVERLAP' AND "
            "s.overlap_expires_at > ?)) ORDER BY c.client_id, s.generation",
            (now,),
        ).fetchall()
        return tuple(AuthenticationFingerprint(
            client_id=row["client_id"], scopes=tuple(row["scopes"].split(",")),
            fingerprint=row["fingerprint"], generation=int(row["generation"]),
            state=row["state"], overlap_expires_at=row["overlap_expires_at"],
        ) for row in rows)


class ConnectionAccessRepository:
    def __init__(self, conn, *, clock: Callable[[], str] = utcnow) -> None:
        self.conn = conn
        self._clock = clock

    def get(self) -> dict:
        row = self.conn.execute(
            "SELECT enabled, disabled_at, revision FROM connection_access_state WHERE id = 1"
        ).fetchone()
        return {"enabled": bool(row["enabled"]), "disabled_at": row["disabled_at"],
                "revision": int(row["revision"])}

    def disable_all(self, *, expected_revision: int) -> dict:
        now = self._clock()
        cursor = self.conn.execute(
            "UPDATE connection_access_state SET enabled = 0, disabled_at = ?, "
            "revision = revision + 1 WHERE id = 1 AND revision = ?",
            (now, expected_revision),
        )
        if cursor.rowcount != 1:
            raise RepositoryConflict("ACCESS_REVISION_CONFLICT", "stale access revision")
        self.conn.execute(
            "UPDATE connection_clients SET revoked_at = COALESCE(revoked_at, ?), "
            "revision = revision + 1 WHERE revoked_at IS NULL", (now,),
        )
        self.conn.execute(
            "UPDATE connection_client_secrets SET state = 'RETIRED', "
            "retired_at = COALESCE(retired_at, ?), overlap_expires_at = NULL "
            "WHERE state != 'RETIRED'", (now,),
        )
        return self.get()

    def enable(self, *, expected_revision: int) -> dict:
        cursor = self.conn.execute(
            "UPDATE connection_access_state SET enabled = 1, disabled_at = NULL, "
            "revision = revision + 1 WHERE id = 1 AND revision = ?",
            (expected_revision,),
        )
        if cursor.rowcount != 1:
            raise RepositoryConflict("ACCESS_REVISION_CONFLICT", "stale access revision")
        return self.get()
