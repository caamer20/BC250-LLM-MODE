"""C2 §C2.2 (V1_0_RELEASE_CLOSURE plan): migration 010 + backup repositories.

Backup tables remain present at the current schema; a v9 -> v10+ upgrade preserves every
durable row, newer-schema refusal, and the revision-fenced (CAS) backup/restore
repositories with fail-closed refusal of malformed digests/states.
"""

from __future__ import annotations

import sqlite3

import pytest

from bc250_llm_mode.backup_lifecycle import (
    BackupRepositoryError,
    BackupSetRepository,
    RestoreAttemptRepository,
)
from bc250_llm_mode.db import (
    SCHEMA_VERSION,
    DatabaseTooNew,
    initialize,
    initialize_and_close,
    open_database,
)

_SHA = "a" * 64


def _applied_versions(db_path) -> list[int]:
    read = sqlite3.connect(db_path)
    try:
        return [r[0] for r in read.execute(
            "SELECT version FROM schema_migrations ORDER BY version")]
    finally:
        read.close()


def test_fresh_install_reaches_current_schema_with_backup_tables(tmp_path):
    database = tmp_path / "fresh.db"
    initialize_and_close(database)
    assert _applied_versions(database) == list(range(1, SCHEMA_VERSION + 1))
    read = sqlite3.connect(database)
    try:
        tables = {r[0] for r in read.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "backup_sets" in tables
        assert "restore_attempts" in tables
    finally:
        read.close()


def test_v9_database_upgrades_to_v10_preserving_rows(tmp_path):
    assert SCHEMA_VERSION >= 10
    v9 = tmp_path / "v9.db"
    conn = sqlite3.connect(v9)
    conn.executescript(
        """
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY,
            name TEXT, applied_at TEXT);
        INSERT INTO schema_migrations VALUES
            (1,'initial-schema','x'),(2,'known-good-runtime','x'),
            (3,'durable-operations','x'),(4,'managed-model-artifacts','x'),
            (5,'immutable-runtime-lifecycle','x'),
            (6,'explicit-worker-lifecycle','x'),
            (7,'operation-dismissal','x'),(8,'gateway-credential','x'),
            (9,'model-library-meta','x');
        CREATE TABLE operations (id TEXT PRIMARY KEY,
            operation_type TEXT NOT NULL, state TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            dismissed_at TEXT);
        INSERT INTO operations (id, operation_type, state, created_at,
            updated_at) VALUES ('op-9', 'MODEL_IMPORT', 'SUCCEEDED',
            't0', 't1');
        CREATE TABLE model_installations (
            id INTEGER PRIMARY KEY, alias TEXT NOT NULL UNIQUE,
            path TEXT NOT NULL, imported_at TEXT NOT NULL);
        INSERT INTO model_installations (id, alias, path, imported_at)
            VALUES (1, 'tiny', '/models/tiny.gguf', 't0');
        CREATE TABLE model_library_meta (alias TEXT PRIMARY KEY,
            pinned INTEGER NOT NULL DEFAULT 0, last_used_at TEXT,
            last_verified_inference_at TEXT,
            benchmark_summary_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL);
        INSERT INTO model_library_meta (alias, pinned, updated_at)
            VALUES ('tiny', 1, 't0');
        CREATE TABLE gateway_credentials (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                fingerprint TEXT NOT NULL, scopes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, rotated_at TEXT, revoked_at TEXT,
                revision INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE runtime_config (id INTEGER PRIMARY KEY CHECK (id = 1),
            model_alias TEXT, context INTEGER NOT NULL,
            slots INTEGER NOT NULL DEFAULT 1, profile_id TEXT,
            extra_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL);
        CREATE TABLE known_good_runtime (id INTEGER PRIMARY KEY CHECK (id = 1),
            model_alias TEXT, context INTEGER NOT NULL,
            slots INTEGER NOT NULL DEFAULT 1, profile_id TEXT,
            runtime_json TEXT NOT NULL DEFAULT '{}',
            runtime_fingerprint TEXT, runtime_component_identity TEXT,
            verified_at TEXT NOT NULL);
        PRAGMA user_version = 9;
        """
    )
    conn.commit()
    conn.close()

    migration_conn = open_database(v9, mode="migration")
    try:
        assert initialize(migration_conn) == SCHEMA_VERSION
    finally:
        migration_conn.close()

    read = sqlite3.connect(v9)
    read.row_factory = sqlite3.Row
    try:
        tables = {r["name"] for r in read.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "backup_sets" in tables and "restore_attempts" in tables
        op = read.execute(
            "SELECT state FROM operations WHERE id='op-9'").fetchone()
        assert op["state"] == "SUCCEEDED"
        meta = read.execute(
            "SELECT pinned FROM model_library_meta WHERE alias='tiny'").fetchone()
        assert meta["pinned"] == 1
        assert _applied_versions(v9) == list(range(1, SCHEMA_VERSION + 1))
    finally:
        read.close()


def test_newer_schema_is_refused(tmp_path):
    newer = tmp_path / "v99.db"
    conn = sqlite3.connect(newer)
    conn.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
        "name TEXT, applied_at TEXT)")
    conn.execute(
        "INSERT INTO schema_migrations VALUES (99, 'from-the-future', 'x')")
    conn.commit()
    conn.close()

    migration_conn = open_database(newer, mode="migration")
    try:
        with pytest.raises(DatabaseTooNew):
            initialize(migration_conn)
    finally:
        migration_conn.close()


# --- repositories ----------------------------------------------------------

def _conn(tmp_path):
    database = tmp_path / "repo.db"
    initialize_and_close(database)
    return open_database(database, mode="write")


def test_backup_set_repository_insert_get_list_verify(tmp_path):
    conn = _conn(tmp_path)
    try:
        repo = BackupSetRepository(conn)
        row = repo.insert(backup_id="bk-1", manifest_digest=_SHA,
                          storage_path_label="backups/bk-1.tar",
                          created_by_operation_id=None, bytes_total=123)
        assert row.verification_state == "pending" and row.revision == 1
        assert repo.get("bk-1").manifest_digest == _SHA
        updated = repo.mark_verification(
            "bk-1", state="verified", expected_revision=1)
        assert updated.verification_state == "verified"
        assert updated.revision == 2 and updated.verified_at
        assert [r.backup_id for r in repo.list()] == ["bk-1"]
        conn.commit()
    finally:
        conn.close()


def test_backup_set_repository_rejects_bad_input_and_cas(tmp_path):
    conn = _conn(tmp_path)
    try:
        repo = BackupSetRepository(conn)
        with pytest.raises(BackupRepositoryError):
            repo.insert(backup_id="bk-x", manifest_digest="nothex",
                        storage_path_label="x")
        with pytest.raises(BackupRepositoryError):
            repo.insert(backup_id="bk-y", manifest_digest=_SHA,
                        storage_path_label="x", encryption_mode="rot13")
        repo.insert(backup_id="bk-1", manifest_digest=_SHA,
                    storage_path_label="x")
        with pytest.raises(BackupRepositoryError):
            repo.mark_verification("bk-1", state="verified",
                                   expected_revision=99)  # wrong fence
        with pytest.raises(BackupRepositoryError):
            repo.mark_verification("bk-1", state="bogus",
                                   expected_revision=1)
        conn.rollback()
    finally:
        conn.close()


def test_restore_attempt_repository_lifecycle_and_cas(tmp_path):
    conn = _conn(tmp_path)
    try:
        repo = RestoreAttemptRepository(conn)
        row = repo.insert(restore_id="rs-1", source_manifest_digest=_SHA)
        assert row.publish_state == "pending" and row.revision == 1

        row = repo.set_identities(
            "rs-1", expected_revision=1, staging_identity="staging-1",
            prior_profile_identity="prior-1",
            candidate_profile_identity="candidate-1")
        assert row.staging_identity == "staging-1" and row.revision == 2

        row = repo.set_state("rs-1", expected_revision=2,
                             publish_state="published",
                             post_verify_state="passed",
                             rollback_state="not_needed")
        assert row.publish_state == "published"
        assert row.post_verify_state == "passed"
        assert row.revision == 3

        with pytest.raises(BackupRepositoryError):
            repo.set_state("rs-1", expected_revision=1,  # stale fence
                           publish_state="staged")
        with pytest.raises(BackupRepositoryError):
            repo.set_state("rs-1", expected_revision=3,
                           publish_state="not-a-state")
        with pytest.raises(BackupRepositoryError):
            repo.insert(restore_id="rs-bad",
                        source_manifest_digest="short")
        conn.rollback()
    finally:
        conn.close()
