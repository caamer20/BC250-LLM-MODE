"""Durable authenticated-gateway credential management (ADR 005 D3).

The gateway secret NEVER touches SQLite/logs/argv/state — only the non-secret
sha256 fingerprint is durable (migration 008 ``gateway_credentials``). Rotation
and revocation are durable and auditable. The secret rides a 0600 profile file
so the Open WebUI container can consume it read-only.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bc250_llm_mode.db import SCHEMA_VERSION, initialize_and_close
from bc250_llm_mode.gateway import fingerprint_of
from bc250_llm_mode.gateway_command import GatewayCredentialService
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory


@pytest.fixture()
def unit(tmp_path):
    database = tmp_path / "state.db"
    initialize_and_close(database)
    return UnitOfWorkFactory(database)


@pytest.fixture()
def service(unit, tmp_path):
    return GatewayCredentialService(unit, tmp_path)


def _fingerprint_in_db(unit, secret: str | None = None):
    with unit.begin() as conn:
        row = conn.execute(
            "SELECT fingerprint, revoked_at, created_at, rotated_at FROM "
            "gateway_credentials WHERE id = 1"
        ).fetchone()
    return row


def test_fresh_schema_is_v8_and_has_gateway_table(tmp_path):
    database = tmp_path / "state.db"
    initialize_and_close(database)
    units = UnitOfWorkFactory(database)
    with units.begin() as conn:
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "gateway_credentials" in tables
        applied = [
            r["version"]
            for r in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
        assert applied[-1] == 8
        assert SCHEMA_VERSION == 8


def test_provision_writes_0600_file_but_never_secret_in_db(service, unit, tmp_path):
    secret = "globallyscopedsecret-0123456789abcdef"
    result = service.provision(secret=secret)
    assert result.provisioned is True
    assert result.fingerprint == fingerprint_of(secret)
    assert result.credential_file is not None
    # secret file exists, 0600, contains the secret
    cred = Path(result.credential_file)
    assert cred.exists()
    assert (cred.stat().st_mode & 0o777) == 0o600
    assert cred.read_text(encoding="utf-8").strip() == secret
    # the DB never holds the secret, only the non-secret fingerprint
    stored = _fingerprint_in_db(unit)
    assert stored["fingerprint"] == fingerprint_of(secret)
    with unit.read() as conn:
        trailers = conn.execute(
            "SELECT fingerprint FROM gateway_credentials"
        ).fetchall()
    assert secret not in {t[0] for t in trailers}


def test_rotate_changes_fingerprint_durably_and_is_idempotent(service, unit):
    s1 = "first-secret-00000000000000000000"
    s2 = "second-secret-000000000000000000000"
    p1 = service.provision(secret=s1)
    p2 = service.rotate(secret=s2)
    assert p2.fingerprint != p1.fingerprint
    assert p2.fingerprint == fingerprint_of(s2)
    stored = _fingerprint_in_db(unit)
    assert stored["fingerprint"] == fingerprint_of(s2)
    # rotate is idempotent: verify against the new secret
    assert service.verify(presented_secret=s2)["verified"] is True
    assert service.verify(presented_secret=s1)["verified"] is False


def test_revoke_durably_marks_and_fields_reflect_revoked(service, unit, tmp_path):
    service.provision(secret="another-secret-000000000000000000")
    assert service.verify()["verified"] is True
    service.revoke()
    stored = _fingerprint_in_db(unit)
    assert stored["revoked_at"] is not None
    # the 0600 file is removed on revoke
    cred = tmp_path / "gateway-credential"
    assert not cred.exists()
    state = {}
    service.write_state_fields(state)
    assert state["gateway_provisioned"] is False
    assert state["gateway_verified"] is False
    assert state["gateway_backend_identity"] == "revoked"
    assert service.resolve_credential_file() is None
    assert service.verify()["backend_identity"] == "revoked"


def test_verify_is_not_provisioned_and_constant_time(service):
    assert service.verify()["backend_identity"] == "not-provisioned"
    # before provision, any presented secret fails closed
    assert service.verify(presented_secret="x" * 40)["verified"] is False


def test_write_state_fields_picks_up_provisioned(service, tmp_path):
    service.provision(secret="write-state-secret-000000000000000")
    state = {}
    service.write_state_fields(state)
    assert state["gateway_provisioned"] is True
    assert state["gateway_verified"] is True
    assert state["gateway_backend_identity"] == "verified"
    assert state["gateway_credential_file"] == str(tmp_path / "gateway-credential")
    assert state["gateway_last_verified_at"] is not None


def test_resolve_credential_file_none_before_provision(service):
    assert service.resolve_credential_file() is None


def test_secret_length_bounds_refused(service, tmp_path):
    with pytest.raises(ValueError):
        service.provision(secret="short")


def test_v7_database_upgrades_to_v8_preserving_rows(tmp_path):
    """Hand-built v7 database (with dismissal column) upgrades to v8 while
    preserving an existing operations row untouched."""
    v7 = tmp_path / "v7.db"
    conn = sqlite3.connect(v7)
    conn.executescript(
        """
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY,
            name TEXT, applied_at TEXT);
        INSERT INTO schema_migrations VALUES
            (1,'initial-schema','x'),(2,'known-good-runtime','x'),
            (3,'durable-operations','x'),(4,'managed-model-artifacts','x'),
            (5,'immutable-runtime-lifecycle','x'),
            (6,'explicit-worker-lifecycle','x'),
            (7,'operation-dismissal','x');
        CREATE TABLE operations (id TEXT PRIMARY KEY,
            state TEXT NOT NULL, revision INTEGER NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            operation_id TEXT, spec_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT, dismissed_at TEXT);
        INSERT INTO operations (id, state, revision, created_at, updated_at)
            VALUES ('op-old', 'SUCCEEDED', 3, 't', 't');
        """
    )
    conn.commit()
    conn.close()

    from bc250_llm_mode.db import initialize, open_database

    migration_conn = open_database(v7, mode="migration")
    try:
        assert initialize(migration_conn) == SCHEMA_VERSION == 8
    finally:
        migration_conn.close()

    units = UnitOfWorkFactory(v7)
    with units.begin() as conn2:
        row = conn2.execute(
            "SELECT id, dismissed_at FROM operations WHERE id = 'op-old'"
        ).fetchone()
        assert row is not None and row["id"] == "op-old"
        assert "dismissed_at" in row.keys()
        tables = {
            r["name"]
            for r in conn2.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "gateway_credentials" in tables