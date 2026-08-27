"""Migration 007 (U1.4 §7.3): durable operation dismissal flag.

Dismiss must be a real durable column — hiding a terminal operation from
default views without deleting any audit history — and the ordered
migration must carry existing rows across untouched.
"""

from __future__ import annotations

import sqlite3

from bc250_llm_mode.db import SCHEMA_VERSION, initialize_and_close
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory


def test_fresh_schema_has_dismissal_column_and_partial_index(tmp_path):
    database = tmp_path / "state.db"
    initialize_and_close(database)
    units = UnitOfWorkFactory(database)
    with units.begin() as conn:
        columns = {
            r["name"]
            for r in conn.execute("PRAGMA table_info(operations)").fetchall()
        }
        assert "dismissed_at" in columns
        indexes = {
            r["name"]
            for r in conn.execute("PRAGMA index_list(operations)").fetchall()
        }
        assert "idx_operations_default_view" in indexes


def test_v6_database_upgrades_to_v7_preserving_rows(tmp_path):
    """Hand-build a v6 database with one operation row; the ordered
    migration adds dismissal without touching audit data."""
    v6 = tmp_path / "v6.db"
    conn = sqlite3.connect(v6)
    conn.executescript(
        """
        CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE runtime_config (id INTEGER PRIMARY KEY CHECK (id = 1),
            model_alias TEXT, context INTEGER NOT NULL,
            slots INTEGER NOT NULL DEFAULT 1, profile_id TEXT,
            extra_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL);
        CREATE TABLE model_installations (
            id INTEGER PRIMARY KEY, alias TEXT NOT NULL UNIQUE,
            path TEXT NOT NULL, quant TEXT, display_name TEXT,
            sampling_json TEXT NOT NULL DEFAULT '{}',
            provenance TEXT NOT NULL DEFAULT 'legacy-import',
            validation_status TEXT NOT NULL DEFAULT 'unverified',
            imported_at TEXT NOT NULL);
        CREATE TABLE component_provenance (component TEXT PRIMARY KEY,
            describe TEXT, commit_sha TEXT, recorded_at TEXT);
        CREATE TABLE known_good_runtime (id INTEGER PRIMARY KEY CHECK (id = 1),
            settings_revision INTEGER, recorded_at TEXT);
        CREATE TABLE operations (
            id TEXT PRIMARY KEY, operation_type TEXT NOT NULL,
            request_version INTEGER NOT NULL,
            recovery_policy_version INTEGER NOT NULL DEFAULT 1,
            request_json TEXT NOT NULL, state TEXT NOT NULL,
            state_revision INTEGER NOT NULL DEFAULT 1, progress_phase TEXT,
            progress_current INTEGER NOT NULL DEFAULT 0,
            progress_total INTEGER, progress_unit TEXT,
            progress_summary TEXT, surface TEXT NOT NULL DEFAULT 'unknown',
            cancel_requested_at TEXT, result_code TEXT, result_detail TEXT,
            error_code TEXT, error_detail TEXT, parent_operation_id TEXT,
            created_at TEXT NOT NULL, started_at TEXT, updated_at TEXT NOT NULL,
            finished_at TEXT);
        INSERT INTO operations (id, operation_type, request_version,
            request_json, state, surface, created_at, updated_at)
        VALUES ('op-old', 'MODEL_ACTIVATE', 1, '{"desired_value":"v1"}',
                'SUCCEEDED', 'test', 't0', 't1');
        CREATE TABLE operation_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_id TEXT NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
            step_key TEXT NOT NULL, sequence INTEGER NOT NULL,
            implementation_version INTEGER NOT NULL DEFAULT 1,
            state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            input_json TEXT, output_json TEXT, external_effect_id TEXT,
            failure_code TEXT, failure_detail TEXT, started_at TEXT,
            checkpointed_at TEXT, finished_at TEXT,
            UNIQUE (operation_id, step_key), UNIQUE (operation_id, sequence));
        CREATE TABLE operation_events (
            cursor INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_id TEXT NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
            ts TEXT NOT NULL, level TEXT NOT NULL DEFAULT 'info', code TEXT,
            summary TEXT NOT NULL, detail_json TEXT, progress_json TEXT);
        CREATE TABLE operation_leases (
            resource_key TEXT PRIMARY KEY,
            operation_id TEXT NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
            owner TEXT NOT NULL, lease_revision INTEGER NOT NULL DEFAULT 1,
            acquired_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL,
            expires_at TEXT NOT NULL);
        PRAGMA user_version = 6;
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT);
        INSERT INTO schema_migrations VALUES
            (1, 'initial-schema', 'x'), (2, 'known-good-runtime', 'x'),
            (3, 'durable-operations', 'x'), (4, 'managed-artifacts', 'x'),
            (5, 'immutable-runtime-builds', 'x'),
            (6, 'explicit-worker-lifecycle', 'x');
        """
    )
    # Minimal remaining v5/v6 tables so later migrations succeed.
    conn.executescript(
        """
        CREATE TABLE runtime_builds (build_id TEXT PRIMARY KEY, manifest_json
            TEXT NOT NULL, manifest_digest TEXT NOT NULL, created_at TEXT);
        CREATE TABLE runtime_build_verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT, build_id TEXT NOT NULL,
            kind TEXT NOT NULL, evidence_json TEXT, recorded_at TEXT);
        CREATE TABLE runtime_trees (tree_id TEXT PRIMARY KEY, build_id TEXT,
            container_profile TEXT, locator TEXT, manifest_digest TEXT,
            server_binary_digest TEXT, state TEXT, created_at TEXT);
        CREATE TABLE runtime_component_state (id INTEGER PRIMARY KEY CHECK
            (id = 1), generation INTEGER NOT NULL, promoted_build_id TEXT,
            rollback_build_id TEXT, updated_at TEXT);
        CREATE TABLE worker_locks (token TEXT PRIMARY KEY
            CHECK (token = 'worker-host'), owner TEXT NOT NULL,
            lease_revision INTEGER NOT NULL DEFAULT 1, acquired_at TEXT NOT
            NULL, heartbeat_at TEXT NOT NULL, expires_at TEXT NOT NULL);
        """
    )
    conn.commit()
    conn.close()

    from bc250_llm_mode.db import initialize, open_database

    migration_conn = open_database(v6, mode="migration")
    try:
        assert initialize(migration_conn) == SCHEMA_VERSION == 8
    finally:
        migration_conn.close()

    units = UnitOfWorkFactory(v6)
    with units.begin() as conn2:
        row = conn2.execute(
            "SELECT id, dismissed_at FROM operations WHERE id = 'op-old'"
        ).fetchone()
        assert row["id"] == "op-old"
        assert row["dismissed_at"] is None
