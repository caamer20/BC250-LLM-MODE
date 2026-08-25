"""Session 6A §5.6: migration 004 and repository contracts."""

from __future__ import annotations

import sqlite3

import pytest

from bc250_llm_mode.db import SCHEMA_VERSION, initialize_and_close
from bc250_llm_mode.repositories import (
    ModelArtifactRepository,
    ModelInstallationsRepository,
    RepositoryConflict,
    StorageReservationRepository,
)
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory


@pytest.fixture()
def units(tmp_path):
    database = tmp_path / "state.db"
    initialize_and_close(database)
    return UnitOfWorkFactory(database)


def test_fresh_schema_reaches_v4_with_constraints(tmp_path):
    database = tmp_path / "state.db"
    initialize_and_close(database)
    units = UnitOfWorkFactory(database)
    with units.begin() as conn:
        applied = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [r["version"] for r in applied] == [1, 2, 3, 4, 5, 6]
        assert SCHEMA_VERSION == 6
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO model_artifacts (id, canonical_path, "
                "storage_state, trust_state, validator_version, created_at) "
                "VALUES ('x', '/p', 'WAT', 'UNVERIFIED', 1, 't')"
            )


def test_v3_to_v4_backfill_is_deterministic_and_file_free(tmp_path):
    """Build a v3 database by hand, seed installations, then run the ordered
    migrations: backfill creates exactly one legacy artifact per
    installation without reading or hashing any file."""
    v3 = tmp_path / "v3.db"
    conn = sqlite3.connect(v3)
    conn.executescript(
        """
        CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE model_installations (
            id INTEGER PRIMARY KEY, alias TEXT NOT NULL UNIQUE,
            path TEXT NOT NULL, quant TEXT, display_name TEXT,
            sampling_json TEXT NOT NULL DEFAULT '{}',
            provenance TEXT NOT NULL DEFAULT 'legacy-import',
            validation_status TEXT NOT NULL DEFAULT 'unverified',
            imported_at TEXT NOT NULL);
        INSERT INTO model_installations (alias, path, imported_at)
            VALUES ('alpha', '/models/a.gguf', '2026-01-01T00:00:00Z');
        INSERT INTO model_installations (alias, path, imported_at)
            VALUES ('beta', '/models/b.gguf', '2026-01-02T00:00:00Z');
        CREATE TABLE component_provenance (component TEXT PRIMARY KEY,
            describe TEXT, commit_sha TEXT, recorded_at TEXT NOT NULL);
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
        PRAGMA user_version = 3;
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT);
        INSERT INTO schema_migrations VALUES
            (1, 'initial-schema', 'x'),
            (2, 'known-good-runtime', 'x'),
            (3, 'durable-operations', 'x');
        """
    )
    conn.commit()
    conn.close()

    from bc250_llm_mode.db import open_database, initialize

    migration_conn = open_database(v3, mode="migration")
    try:
        initialize(migration_conn)
    finally:
        migration_conn.close()

    units = UnitOfWorkFactory(v3)
    with units.begin() as conn:
        artifacts = ModelArtifactRepository(conn)
        rows = conn.execute(
            "SELECT id, storage_state, trust_state FROM model_artifacts "
            "ORDER BY id"
        ).fetchall()
        assert [r["id"] for r in rows] == ["legacy:1", "legacy:2"]
        assert all(r["storage_state"] == "LEGACY_EXTERNAL" for r in rows)
        links = conn.execute(
            "SELECT alias, artifact_id FROM model_installations ORDER BY alias"
        ).fetchall()
        assert dict(links) == {"alpha": "legacy:1", "beta": "legacy:2"}
        # No file was hashed: no digest recorded for legacy rows.
        assert artifacts.get("legacy:1")["content_digest"] is None


def test_duplicate_digest_two_aliases_one_artifact(units):
    with units.begin() as conn:
        artifacts = ModelArtifactRepository(conn)
        installs = ModelInstallationsRepository(conn)
        digest = "sha256:" + "ab" * 32
        artifact_id = artifacts.record_verified(
            artifact_id="art-1",
            content_digest=digest,
            byte_size=10,
            canonical_path="/managed/ab/abcd.gguf",
            architecture="llama",
            source_kind="local",
        )
        installs.install_alias(
            alias="model-one", artifact_id=artifact_id, quant="Q4_K_M",
            display_name="One",
        )
        installs.install_alias(
            alias="model-two", artifact_id=artifact_id, quant="Q4_K_M",
            display_name="Two",
        )
        found = artifacts.get_by_digest(digest)
        assert found is not None and found["id"] == "art-1"
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM model_installations"
        ).fetchone()["n"]
        assert count == 2


def test_alias_conflict_refused_without_mutation(units):
    with units.begin() as conn:
        artifacts = ModelArtifactRepository(conn)
        installs = ModelInstallationsRepository(conn)
        art_a = artifacts.record_verified(
            artifact_id="art-a", content_digest="sha256:" + "aa" * 32,
            byte_size=1, canonical_path="/m/a.gguf", source_kind="local",
        )
        art_b = artifacts.record_verified(
            artifact_id="art-b", content_digest="sha256:" + "bb" * 32,
            byte_size=2, canonical_path="/m/b.gguf", source_kind="local",
        )
        installs.install_alias(
            alias="dup", artifact_id=art_a,
            quant="Q4_K_M", display_name="A",
        )
        before = conn.execute(
            "SELECT artifact_id FROM model_installations WHERE alias='dup'"
        ).fetchone()["artifact_id"]
        with pytest.raises(RepositoryConflict) as err:
            installs.install_alias(
                alias="dup", artifact_id=art_b,
                quant="Q4_K_M", display_name="B",
            )
        assert err.value.code == "INSTALLATION_ALIAS_CONFLICT"
        after = conn.execute(
            "SELECT artifact_id FROM model_installations WHERE alias='dup'"
        ).fetchone()["artifact_id"]
        assert after == before == art_a


def test_quarantined_artifact_cannot_receive_alias(units):
    with units.begin() as conn:
        artifacts = ModelArtifactRepository(conn)
        installs = ModelInstallationsRepository(conn)
        quarantined = artifacts.record_quarantine(
            artifact_id="q-1",
            content_digest="sha256:" + "cc" * 32,
            byte_size=5,
            canonical_path="/quarantine/cc/cc.gguf",
            reason_code="GGUF_INVALID",
        )
        with pytest.raises(RepositoryConflict):
            installs.install_alias(
                alias="bad", artifact_id=quarantined, quant="Q4_K_M",
                display_name="Bad",
            )
        listed = artifacts.list_quarantined()
        assert [a["id"] for a in listed] == ["q-1"]
        assert listed[0]["quarantine_reason_code"] == "GGUF_INVALID"


def test_reservation_lifecycle_and_validation(units):
    with units.begin() as conn:
        conn.execute(
            "INSERT INTO operations (id, operation_type, request_version, "
            "recovery_policy_version, request_json, state, state_revision, "
            "surface, created_at, updated_at) VALUES (?, 'MODEL_ACQUIRE', 1, "
            "1, '{}', 'QUEUED', 1, 'test', 't', 't')",
            ("op-res-1",),
        )
        reservations = StorageReservationRepository(conn)
        row = reservations.reserve(
            operation_id="op-res-1",
            filesystem_identity="fs-1",
            required_bytes=1000,
            available_bytes=5000,
            reserved_bytes=1000,
        )
        assert row["state"] == "RESERVED"
        grown = reservations.update_growth("op-res-1", reserved_bytes=1500)
        assert grown["reserved_bytes"] == 1500
        reservations.release("op-res-1")
        assert reservations.get("op-res-1")["state"] == "RELEASED"
        with pytest.raises(RepositoryConflict):
            reservations.release("op-res-1")
        with pytest.raises(ValueError):
            reservations.reserve(
                operation_id="op-res-2",
                filesystem_identity="fs-1",
                required_bytes=-1,
                available_bytes=10,
                reserved_bytes=0,
            )


def test_secret_like_provenance_is_rejected(units):
    with units.begin() as conn:
        artifacts = ModelArtifactRepository(conn)
        with pytest.raises(RepositoryConflict):
            artifacts.record_verified(
                artifact_id="leak", content_digest="sha256:" + "dd" * 32,
                byte_size=1, canonical_path="/m/leak.gguf",
                provenance={"api_key": "CANARY"},
            )