"""Durable authenticated-gateway credential management (ADR 005 D3 §10.3).

The gateway secret NEVER touches SQLite/logs/argv/state. Only the NON-SECRET
sha256 fingerprint is durable (migration 008 ``gateway_credentials`` singleton
row), which makes rotation/revocation auditable and durable. The secret itself
rides a 0600 profile file so the Open WebUI container can mount it read-only;
the pinned provider adapter reads that mount internally and never places the
key in environment or argv. The same boundary routes remote/container traffic
through the gateway.

Data model (view fields this service refreshes):
    gateway_provisioned   - a credential has been provisioned (not revoked)
    gateway_verified      - the provisioned credential is present + valid
    gateway_backend_identity - verified | revoked | not-provisioned
    gateway_last_verified_at - ISO timestamp of last durable verify
    gateway_credential_file  - absolute path to the 0600 secret file (or None)
"""

from __future__ import annotations

import hmac
import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .gateway import MAX_SECRET_LEN, fingerprint_of
from .unit_of_work import UnitOfWorkFactory

# Singleton row id for the durable fingerprint record (mirrors
# runtime_component_state/worker_locks).
_ROW_ID = 1
_ROW_KEY = "gateway_credentials"
_SECRET_FILENAME = "gateway-credential"
_UNSET = object()
DEFAULT_SCOPES = "inference:read,inference:stream,models:list"


@dataclass(frozen=True)
class GatewayProvision:
    """Redacted result returned to callers (never contains the secret)."""

    provisioned: bool
    fingerprint: str  # full sha256 hex of the secret (non-secret)
    credential_file: str | None
    scopes: str


class GatewayCredentialService:
    """Durable gateway-credential lifecycle backed by SQLite + a 0600 file.

    ``secret_file_dir`` is the app profile dir; the secret is written to
    ``<dir>/gateway-credential`` (0600, atomic replace). All durable truth is
    in SQLite via ``units``; the view-layer ``write_state_fields`` refreshes
    the compose snapshot without persisting the secret.
    """

    def __init__(
        self,
        units: UnitOfWorkFactory,
        secret_file_dir: str | Path,
        *,
        clock=None,
    ) -> None:
        self._units = units
        self._secret_file = Path(secret_file_dir) / _SECRET_FILENAME
        self._clock = clock or (lambda: None)

    # --- durable file helpers ---------------------------------------------

    def _secret_path(self) -> Path:
        return self._secret_file

    def _read_secret(self) -> str | None:
        try:
            raw = self._secret_file.read_bytes().strip()
        except OSError:
            return None
        if not raw or len(raw) > MAX_SECRET_LEN:
            return None
        return raw.decode("utf-8", errors="strict")

    def _write_secret(self, secret: str) -> None:
        if not (8 <= len(secret) <= MAX_SECRET_LEN):
            raise ValueError("gateway secret length out of bounds")
        # 0600 unique temp + atomic replace + parent fsync (like write_receipt);
        # never leaves a partially-written secret, never in argv/labels/logs.
        self._secret_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=self._secret_file.stem + "-",
            suffix=".tmp-secret",
            dir=str(self._secret_file.parent),
        )
        tmp = Path(tmp_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(secret.encode("utf-8"))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._secret_file)
            self._fsync_dir(self._secret_file.parent)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    # --- durable row read/write -------------------------------------------

    def _row(self) -> dict[str, Any] | None:
        with self._units.begin() as conn:
            cur = conn.execute(
                f'SELECT * FROM {_ROW_KEY} WHERE id = ?', (_ROW_ID,)
            ).fetchone()
            return dict(cur) if cur else None

    def _upsert(self, *, fingerprint: str, scopes: str, rotated: bool) -> None:
        import datetime

        now = (self._clock() or datetime.datetime.now(datetime.timezone.utc))
        ts = now.isoformat()
        with self._units.begin() as conn:
            cur = conn.execute(
                f'SELECT revision FROM {_ROW_KEY} WHERE id = ?', (_ROW_ID,)
            ).fetchone()
            if cur is None:
                conn.execute(
                    f"INSERT INTO {_ROW_KEY} (id, fingerprint, scopes, "
                    "created_at, revision) VALUES (?, ?, ?, ?, 1)",
                    (_ROW_ID, fingerprint, scopes, ts),
                )
            else:
                col = "rotated_at" if rotated else "created_at"
                conn.execute(
                    f"UPDATE {_ROW_KEY} SET fingerprint = ?, scopes = ?, "
                    f"{col} = ?, revoked_at = NULL, "
                    "revision = revision + 1 WHERE id = ?",
                    (fingerprint, scopes, ts, _ROW_ID),
                )

    # --- public API --------------------------------------------------------

    def provision(self, *, secret: str | None = None) -> GatewayProvision:
        """Provision a scoped credential: write secret to 0600 file, persist
        ONLY the fingerprint, return a redacted record. Idempotent re-provision
        is allowed (rotates to the new secret)."""
        new_secret = secret if secret is not None else secrets.token_urlsafe(48)
        if not (8 <= len(new_secret) <= MAX_SECRET_LEN):
            raise ValueError("gateway secret length out of bounds")
        self._write_secret(new_secret)
        self._upsert(
            fingerprint=fingerprint_of(new_secret),
            scopes=DEFAULT_SCOPES,
            rotated=True,
        )
        return GatewayProvision(
            provisioned=True,
            fingerprint=fingerprint_of(new_secret),
            credential_file=str(self._secret_path()),
            scopes=DEFAULT_SCOPES,
        )

    def rotate(self, *, secret: str | None = None) -> GatewayProvision:
        """Rotate to a fresh credential; durable fingerprint replacement."""
        return self.provision(secret=secret)

    def revoke(self) -> dict[str, Any]:
        """Revoke durably: mark revoked_at and clear the 0600 file."""
        import datetime

        now = (self._clock() or datetime.datetime.now(datetime.timezone.utc))
        with self._units.begin() as conn:
            cur = conn.execute(
                f'SELECT revision FROM {_ROW_KEY} WHERE id = ?', (_ROW_ID,)
            ).fetchone()
            if cur is not None:
                conn.execute(
                    f"UPDATE {_ROW_KEY} SET revoked_at = ?, "
                    "revision = revision + 1 WHERE id = ?",
                    (now.isoformat(), _ROW_ID),
                )
        # Remove the 0600 secret file so nothing can present it.
        if self._secret_file.exists():
            try:
                os.remove(self._secret_file)
            except OSError:
                pass
        return {"revoked": True}

    def verify(self, *, presented_secret: str | None = None) -> dict[str, Any]:
        """Constant-time verify of a presented secret against the durable
        fingerprint. Returns backend_identity + last_verified_at."""
        import datetime

        now = (self._clock() or datetime.datetime.now(datetime.timezone.utc))
        row = self._row() or {}
        if not row:
            return {
                "verified": False,
                "backend_identity": "not-provisioned",
                "last_verified_at": None,
            }
        if row.get("revoked_at"):
            return {
                "verified": False,
                "backend_identity": "revoked",
                "last_verified_at": row.get("revoked_at"),
            }
        expected = row.get("fingerprint", "")
        presented = presented_secret if presented_secret is not None else self._read_secret()
        valid = bool(presented) and hmac.compare_digest(
            fingerprint_of(presented), expected
        )
        if valid:
            return {
                "verified": True,
                "backend_identity": "verified",
                "last_verified_at": now.isoformat(),
            }
        return {
            "verified": False,
            "backend_identity": "invalid",
            "last_verified_at": None,
        }

    def resolve_credential_file(self) -> str | None:
        """Absolute path to the 0600 secret file, or None if not usable."""
        row = self._row() or {}
        if not row or row.get("revoked_at"):
            return None
        if not self._secret_file.exists():
            return None
        return str(self._secret_path())

    def write_state_fields(self, state: dict[str, Any]) -> dict[str, Any]:
        """Refresh view-level gateway_* fields from durable truth."""
        row = self._row() or {}
        provisioned = bool(row) and not row.get("revoked_at")
        file_ok = self._secret_file.exists() if provisioned else False
        verified = provisioned and file_ok
        identity = (
            "revoked" if row.get("revoked_at")
            else ("verified" if verified else "not-provisioned")
        )
        state["gateway_provisioned"] = provisioned
        state["gateway_verified"] = verified
        state["gateway_backend_identity"] = identity
        state["gateway_last_verified_at"] = row.get("rotated_at") or row.get("created_at")
        state["gateway_credential_file"] = (
            str(self._secret_path()) if verified else None
        )
        return state


def provision_state(
    service: GatewayCredentialService, state: dict[str, Any]
) -> dict[str, Any]:
    return service.write_state_fields(state)
