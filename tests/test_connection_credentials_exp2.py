from __future__ import annotations

import sqlite3

import pytest

from bc250_llm_mode.connection_credentials import (
    ConnectionAccessRepository,
    ConnectionClientRepository,
    ConnectionCredentialError,
)
from bc250_llm_mode.db import SCHEMA_VERSION, initialize, initialize_and_close, open_database
from bc250_llm_mode.repositories import RepositoryConflict


NOW = "2026-08-29T12:00:00Z"
FP1 = "a" * 64
FP2 = "b" * 64
CLIENT = "1" * 32


def _fresh(tmp_path):
    path = tmp_path / "state.db"
    initialize_and_close(path)
    return open_database(path, mode="write")


def _v10(path, *, fingerprint=FP1, revoked=False):
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT);"
        + "".join(
            f"INSERT INTO schema_migrations VALUES ({n}, 'm{n}', 'x');"
            for n in range(1, 11)
        )
        + "CREATE TABLE gateway_credentials (id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL, "
          "scopes TEXT NOT NULL, created_at TEXT NOT NULL, rotated_at TEXT, revoked_at TEXT, "
          "revision INTEGER NOT NULL);"
          "CREATE TABLE runtime_config (id INTEGER PRIMARY KEY, model_alias TEXT, "
          "context INTEGER NOT NULL, slots INTEGER NOT NULL, profile_id TEXT, "
          "extra_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL);"
          "CREATE TABLE known_good_runtime (id INTEGER PRIMARY KEY, model_alias TEXT, "
          "context INTEGER NOT NULL, slots INTEGER NOT NULL, profile_id TEXT, "
          "runtime_json TEXT NOT NULL DEFAULT '{}', runtime_fingerprint TEXT, "
          "runtime_component_identity TEXT, verified_at TEXT NOT NULL);"
          "CREATE TABLE operations (id TEXT PRIMARY KEY);"
          "CREATE TABLE preserved_marker (value TEXT);"
          "INSERT INTO preserved_marker VALUES ('keep-me');"
    )
    conn.execute(
        "INSERT INTO gateway_credentials VALUES (1, ?, ?, 't0', 't1', ?, 7)",
        (fingerprint, "inference:read,inference:stream,models:list", "t2" if revoked else None),
    )
    conn.commit()
    conn.close()


def test_migration_011_preserves_v10_and_imports_legacy_singleton(tmp_path):
    path = tmp_path / "v10.db"
    _v10(path)
    conn = open_database(path, mode="migration")
    try:
        assert initialize(conn) == SCHEMA_VERSION
        assert 11 in {
            row["version"] for row in conn.execute("SELECT version FROM schema_migrations")
        }
        assert conn.execute("SELECT value FROM preserved_marker").fetchone()["value"] == "keep-me"
        row = conn.execute("SELECT * FROM connection_clients").fetchone()
        assert row["client_id"] == "legacy-install"
        assert row["active_fingerprint"] == FP1
        assert row["secret_storage"] == "legacy-singleton"
        assert row["revision"] == 7
        secret = conn.execute("SELECT * FROM connection_client_secrets").fetchone()
        assert secret["state"] == "ACTIVE"
    finally:
        conn.close()


def test_revoked_legacy_stays_revoked_and_invalid_fingerprint_is_not_promoted(tmp_path):
    revoked = tmp_path / "revoked.db"
    _v10(revoked, revoked=True)
    conn = open_database(revoked, mode="migration")
    try:
        initialize(conn)
        assert conn.execute("SELECT revoked_at FROM connection_clients").fetchone()[0] == "t2"
        assert conn.execute("SELECT state FROM connection_client_secrets").fetchone()[0] == "RETIRED"
    finally:
        conn.close()
    invalid = tmp_path / "invalid.db"
    _v10(invalid, fingerprint="deadbeef")
    conn = open_database(invalid, mode="migration")
    try:
        initialize(conn)
        assert conn.execute("SELECT COUNT(*) FROM connection_clients").fetchone()[0] == 0
        assert conn.execute("SELECT fingerprint FROM gateway_credentials").fetchone()[0] == "deadbeef"
    finally:
        conn.close()


def test_migration_011_is_atomic_on_mid_migration_death(tmp_path, monkeypatch):
    from bc250_llm_mode import db

    path = tmp_path / "atomic.db"
    _v10(path)
    migration_011 = next(item for item in db.MIGRATIONS if item[0] == 11)
    broken = (11, "multi-client-credentials", migration_011[2][:-1] + (
        "INSERT INTO absent_table VALUES (1)",
    ))
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:10] + (broken,))
    monkeypatch.setattr(db, "SCHEMA_VERSION", 11)
    conn = open_database(path, mode="migration")
    try:
        with pytest.raises(sqlite3.OperationalError):
            initialize(conn)
        tables = {row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "connection_clients" not in tables
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 10
    finally:
        conn.close()


def test_repository_create_rotate_overlap_revoke_and_cas(tmp_path):
    conn = _fresh(tmp_path)
    try:
        repo = ConnectionClientRepository(conn, clock=lambda: NOW)
        created = repo.create(
            client_id=CLIENT, label="  Cameron's   phone ", client_kind="PocketPal",
            scopes=("models:list", "inference:stream"), fingerprint=FP1,
        )
        assert created.label == "Cameron's phone"
        assert created.scopes == ("models:list", "inference:stream")
        with pytest.raises(RepositoryConflict):
            repo.rotate(CLIENT, expected_revision=99, new_fingerprint=FP2, new_generation=2)
        rotated = repo.rotate(
            CLIENT, expected_revision=1, new_fingerprint=FP2, new_generation=2,
            overlap_expires_at="2026-08-29T12:05:00Z",
        )
        assert rotated.revision == 2 and rotated.active_generation == 2
        auth = repo.authentication_fingerprints(now="2026-08-29T12:01:00Z")
        assert [item.fingerprint for item in auth] == [FP1, FP2]
        auth = repo.authentication_fingerprints(now="2026-08-29T12:06:00Z")
        assert [item.fingerprint for item in auth] == [FP2]
        revoked = repo.revoke(CLIENT, expected_revision=2)
        assert revoked.revoked_at == NOW
        assert repo.authentication_fingerprints(now=NOW) == ()
    finally:
        conn.close()


def test_repository_bounds_labels_scopes_ids_and_active_count(tmp_path):
    conn = _fresh(tmp_path)
    try:
        repo = ConnectionClientRepository(conn, clock=lambda: NOW)
        with pytest.raises(ConnectionCredentialError):
            repo.create(client_id="phone", label="x", client_kind="pocketpal",
                        scopes=("models:list",), fingerprint=FP1)
        with pytest.raises(ConnectionCredentialError):
            repo.create(client_id=CLIENT, label="x", client_kind="browser",
                        scopes=("models:list",), fingerprint=FP1)
        with pytest.raises(ConnectionCredentialError):
            repo.create(client_id=CLIENT, label="x", client_kind="pocketpal",
                        scopes=("admin",), fingerprint=FP1)
    finally:
        conn.close()


def test_disable_all_is_atomic_revision_fenced_and_independent_of_server(tmp_path):
    conn = _fresh(tmp_path)
    try:
        clients = ConnectionClientRepository(conn, clock=lambda: NOW)
        clients.create(client_id=CLIENT, label="Phone", client_kind="pocketpal",
                       scopes=("models:list",), fingerprint=FP1)
        access = ConnectionAccessRepository(conn, clock=lambda: NOW)
        with pytest.raises(RepositoryConflict):
            access.disable_all(expected_revision=9)
        state = access.disable_all(expected_revision=1)
        assert state == {"enabled": False, "disabled_at": NOW, "revision": 2}
        assert clients.get(CLIENT).revoked_at == NOW
        assert clients.authentication_fingerprints(now=NOW) == ()
    finally:
        conn.close()


def test_fresh_schema_has_exp2_tables_and_no_secret_columns(tmp_path):
    conn = _fresh(tmp_path)
    try:
        for table in ("connection_clients", "connection_client_secrets", "connection_access_state"):
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            assert "secret" not in columns
        dumped = "\n".join(conn.iterdump()).lower()
        assert "bearer " not in dumped
    finally:
        conn.close()
