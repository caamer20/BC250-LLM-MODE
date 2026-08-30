"""P8 §14.5: upgrade matrix — schema upgrades preserve the database, managed
artifacts, and runtime lineage.

Hand-builds a v8 database (operations + model_installations + known-good
runtime lineage + gateway credentials) and proves the ordered migration to the
current schema (v9, model_library_meta) preserves every durable row and adds
the new table without data loss.
"""

from __future__ import annotations

import sqlite3

from bc250_llm_mode.db import SCHEMA_VERSION, initialize_and_close


def test_v8_database_upgrades_to_v9_preserving_artifacts_and_lineage(tmp_path):
    from bc250_llm_mode.db import initialize, open_database

    assert SCHEMA_VERSION >= 9

    v8 = tmp_path / "v8.db"
    conn = sqlite3.connect(v8)
    conn.executescript(
        """
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY,
            name TEXT, applied_at TEXT);
        INSERT INTO schema_migrations VALUES
            (1,'initial-schema','x'),(2,'known-good-runtime','x'),
            (3,'durable-operations','x'),(4,'managed-model-artifacts','x'),
            (5,'immutable-runtime-lifecycle','x'),
            (6,'explicit-worker-lifecycle','x'),
            (7,'operation-dismissal','x'),
            (8,'gateway-credentials','x');
        CREATE TABLE operations (id TEXT PRIMARY KEY,
            operation_type TEXT NOT NULL, state TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            dismissed_at TEXT);
        CREATE TABLE runtime_config (id INTEGER PRIMARY KEY CHECK (id = 1),
            model_alias TEXT, context INTEGER NOT NULL,
            slots INTEGER NOT NULL DEFAULT 1, profile_id TEXT,
            extra_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL);
        INSERT INTO operations (id, operation_type, state, created_at,
            updated_at) VALUES ('op-old', 'MODEL_ACTIVATE', 'SUCCEEDED',
            't0', 't1');
        CREATE TABLE model_installations (
            id INTEGER PRIMARY KEY, alias TEXT NOT NULL UNIQUE,
            path TEXT NOT NULL, imported_at TEXT NOT NULL);
        INSERT INTO model_installations (id, alias, path, imported_at)
            VALUES (1, 'tiny', '/models/tiny.gguf', 't0');
        CREATE TABLE model_artifacts (
            id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE,
            size_bytes INTEGER NOT NULL, storage_state TEXT NOT NULL
            DEFAULT 'INSTALLED');
        INSERT INTO model_artifacts (id, sha256, size_bytes)
            VALUES (1, 'abc123', 1024);
        CREATE TABLE known_good_runtime (id INTEGER PRIMARY KEY CHECK (id = 1),
            settings_revision INTEGER, recorded_at TEXT);
        INSERT INTO known_good_runtime (id, settings_revision, recorded_at)
            VALUES (1, 5, 't0');
        CREATE TABLE gateway_credentials (id INTEGER PRIMARY KEY CHECK (id = 1),
            fingerprint TEXT NOT NULL, scopes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, rotated_at TEXT, revoked_at TEXT,
            revision INTEGER NOT NULL DEFAULT 1);
        INSERT INTO gateway_credentials (id, fingerprint, created_at)
            VALUES (1, 'deadbeef', 't0');
        PRAGMA user_version = 8;
        """
    )
    conn.commit()
    conn.close()

    migration_conn = open_database(v8, mode="migration")
    try:
        assert initialize(migration_conn) == SCHEMA_VERSION
    finally:
        migration_conn.close()

    # Verify preservation on a fresh read connection.
    read = sqlite3.connect(v8)
    read.row_factory = sqlite3.Row
    try:
        tables = {
            r["name"]
            for r in read.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "model_library_meta" in tables

        op = read.execute(
            "SELECT id, state FROM operations WHERE id='op-old'").fetchone()
        assert op is not None and op["state"] == "SUCCEEDED"

        inst = read.execute(
            "SELECT alias FROM model_installations").fetchone()
        assert inst["alias"] == "tiny"

        artifact = read.execute(
            "SELECT sha256, size_bytes FROM model_artifacts").fetchone()
        assert artifact["sha256"] == "abc123" and artifact["size_bytes"] == 1024

        lineage = read.execute(
            "SELECT settings_revision FROM known_good_runtime").fetchone()
        assert lineage["settings_revision"] == 5

        cred = read.execute(
            "SELECT fingerprint FROM gateway_credentials").fetchone()
        assert cred["fingerprint"] == "deadbeef"

        applied = [
            r[0] for r in read.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
        assert applied == list(range(1, SCHEMA_VERSION + 1))
    finally:
        read.close()


def test_fresh_install_reaches_current_schema(tmp_path):
    """§14.5 fresh-install at current: initialize_and_close lands on
    SCHEMA_VERSION with every migration recorded."""
    database = tmp_path / "fresh.db"
    initialize_and_close(database)
    read = sqlite3.connect(database)
    try:
        applied = [
            r[0] for r in read.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
        assert applied == list(range(1, SCHEMA_VERSION + 1))
    finally:
        read.close()
