"""U1.2 §7.4: migration 005 and the immutable runtime build schema."""

from __future__ import annotations

import json
import sqlite3

import pytest

from bc250_llm_mode.db import (
    SCHEMA_VERSION,
    DatabaseTooNew,
    initialize_and_close,
    open_database,
)
from bc250_llm_mode.runtime_builds import (
    COMPONENT,
    MANIFEST_VERSION,
    RuntimeBuildError,
    canonical_manifest_bytes,
    derive_build_id,
)
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory


@pytest.fixture()
def units(tmp_path):
    database = tmp_path / "state.db"
    initialize_and_close(database)
    return UnitOfWorkFactory(database)


def _manifest(**overrides):
    manifest = {
        "schema_version": MANIFEST_VERSION,
        "component": COMPONENT,
        "upstream_repository": "https://github.com/ggml-org/llama.cpp",
        "requested_ref": "b7598",
        "source_commit": "a" * 40,
        "source_checkout_verified": True,
        "recipe_version": 1,
        "recipe_digest": "b" * 64,
        "cmake_generator": "Ninja",
        "cmake_options": ["-DGGML_VULKAN=ON", "-DCMAKE_BUILD_TYPE=Release"],
        "cmake_targets": ["llama-server", "llama-cli", "llama-quantize"],
        "build_parallelism": {"policy": "bounded", "jobs_cap": 2},
        "container_image_id": "c" * 64,
        "container_image_digest": "d" * 64,
        "toolchain": {
            "cmake": "3.30",
            "ninja": "1.12",
            "cc": "gcc 14",
            "linker": "ld",
            "libc": "glibc 2.39",
        },
        "target_arch": "x86_64",
        "binaries": [
            {
                "path": "build/bin/llama-server",
                "size": 10,
                "mode": "755",
                "sha256": "e" * 64,
                "version_output_digest": "f" * 64,
            },
        ],
        "smoke_contract_version": 1,
    }
    manifest.update(overrides)
    return {key: value for key, value in manifest.items() if value is not None}


def test_fresh_schema_reaches_v5_with_runtime_tables(units):
    with units.begin() as conn:
        applied = [
            row["version"]
            for row in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
        assert applied == [1, 2, 3, 4, 5, 6, 7, 8]
        assert SCHEMA_VERSION == 8
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {
            "runtime_builds",
            "runtime_build_verifications",
            "runtime_trees",
            "runtime_component_state",
        } <= tables
        count = conn.execute("SELECT COUNT(*) AS n FROM runtime_builds").fetchone()
        assert count["n"] == 0  # no legacy backfill without provenance


def _v4_fixture(path, *, with_provenance: bool) -> None:
    """Hand-build a v4 database (identical bytes across calls)."""
    conn = sqlite3.connect(str(path))
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
        CREATE TABLE bench_history (id INTEGER PRIMARY KEY, ts TEXT NOT NULL,
            payload_json TEXT NOT NULL);
        CREATE TABLE autotune_history (id INTEGER PRIMARY KEY,
            payload_json TEXT NOT NULL);
        CREATE TABLE thermal_state (id INTEGER PRIMARY KEY CHECK (id = 1),
            latch_state TEXT NOT NULL, baseline_json TEXT,
            updated_at TEXT NOT NULL);
        CREATE TABLE component_provenance (component TEXT PRIMARY KEY,
            describe TEXT, commit_sha TEXT, recorded_at TEXT NOT NULL);
        CREATE TABLE runtime_observations (key TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL, observed_at TEXT NOT NULL,
            stale INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE legacy_import_extras (key TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL);
        CREATE TABLE known_good_runtime (id INTEGER PRIMARY KEY CHECK (id = 1),
            model_alias TEXT, context INTEGER NOT NULL,
            slots INTEGER NOT NULL DEFAULT 1, profile_id TEXT,
            runtime_json TEXT NOT NULL DEFAULT '{}',
            runtime_fingerprint TEXT, runtime_component_identity TEXT,
            verified_at TEXT NOT NULL);
        INSERT INTO known_good_runtime VALUES (1, 'demo', 8192, 4, NULL,
            '{}', 'fp', 'ci', '2026-01-01T00:00:00Z');
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
        CREATE TABLE model_artifacts (id TEXT PRIMARY KEY,
            content_digest TEXT UNIQUE, byte_size INTEGER CHECK (byte_size >= 0),
            canonical_path TEXT NOT NULL UNIQUE, storage_state TEXT NOT NULL,
            trust_state TEXT NOT NULL, format TEXT, architecture TEXT,
            quantization TEXT, tensor_count INTEGER,
            validator_version INTEGER NOT NULL DEFAULT 1,
            validation_detail_json TEXT NOT NULL DEFAULT '{}',
            source_kind TEXT NOT NULL DEFAULT 'legacy', source_repo TEXT,
            source_revision TEXT, source_filename TEXT, source_digest TEXT,
            catalog_id TEXT, license_id TEXT,
            provenance_json TEXT NOT NULL DEFAULT '{}',
            quarantine_reason_code TEXT, created_at TEXT NOT NULL,
            validated_at TEXT);
        CREATE TABLE operation_storage_reservations (
            operation_id TEXT PRIMARY KEY REFERENCES operations(id) ON DELETE CASCADE,
            filesystem_identity TEXT NOT NULL, required_bytes INTEGER NOT NULL,
            available_bytes INTEGER NOT NULL, reserved_bytes INTEGER NOT NULL,
            reclaimable_owned_bytes INTEGER NOT NULL,
            credited_partial_bytes INTEGER NOT NULL, state TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, released_at TEXT);
        """
    )
    if with_provenance:
        conn.execute(
            "INSERT INTO component_provenance VALUES "
            "('llamacpp', 'b7598', ?, '2026-02-02')",
            ("9" * 40,),
        )
    conn.executescript(
        """
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY,
            name TEXT, applied_at TEXT);
        INSERT INTO schema_migrations VALUES
            (1, 'initial-schema', 'x'), (2, 'known-good-runtime', 'x'),
            (3, 'durable-operations', 'x'), (4, 'managed-model-artifacts', 'x');
        """
    )
    conn.commit()
    conn.close()


def _migrate_v4(path):
    from bc250_llm_mode.db import initialize

    conn = open_database(path, mode="migration")
    try:
        return initialize(conn)
    finally:
        conn.close()


def test_v4_to_v5_without_provenance_inserts_no_backfill(tmp_path):
    fixture = tmp_path / "v4.db"
    _v4_fixture(fixture, with_provenance=False)
    assert _migrate_v4(fixture) == SCHEMA_VERSION
    units = UnitOfWorkFactory(fixture)
    with units.begin() as conn:
        rows = conn.execute("SELECT COUNT(*) AS n FROM runtime_builds").fetchone()
        assert rows["n"] == 0


def test_v4_to_v5_with_legacy_provenance_is_deterministic(tmp_path):
    digests = []
    for name in ("one.db", "two.db"):
        fixture = tmp_path / name
        _v4_fixture(fixture, with_provenance=True)
        assert _migrate_v4(fixture) == SCHEMA_VERSION
        conn = open_database(fixture, mode="read")
        try:
            row = conn.execute(
                "SELECT build_id, component, provenance_class, manifest_json,"
                " manifest_digest, requested_ref FROM runtime_builds"
            ).fetchone()
        finally:
            conn.close()
        assert row["build_id"] == "legacy:llamacpp"
        assert row["component"] == COMPONENT
        assert row["provenance_class"] == "LEGACY_UNVERIFIED"
        assert json.loads(row["manifest_json"])["describe"] == "b7598"
        assert len(row["manifest_digest"]) == 64
        assert row["requested_ref"] == "b7598"
        digests.append((row["manifest_json"], row["manifest_digest"]))
    # Deterministic repeat from identical v4 fixtures.
    assert digests[0] == digests[1]
    # The legacy row never claims an active/rollback tree or component row.
    units = UnitOfWorkFactory(tmp_path / "one.db")
    with units.begin() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM runtime_trees"
        ).fetchone()["n"] == 0
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM runtime_component_state"
        ).fetchone()["n"] == 0
        settings_row = conn.execute(
            "SELECT value_json FROM settings WHERE key = 'llamacpp_history'"
        ).fetchone()
        assert settings_row is None  # old settings untouched, never created


def test_migration_failure_rolls_back_to_complete_v4(tmp_path):
    """A failure inside migration 005 leaves NO partial v5 objects."""
    from bc250_llm_mode import db as db_module

    original = db_module.MIGRATIONS
    version, name, statements = next(
        m for m in original if m[0] == 5
    )
    broken_entry = [version, name, list(statements)]
    broken_entry[2].insert(2, "INSERT INTO runtime_trees VALUES ('x', 'y')")
    broken_entry[2] = tuple(broken_entry[2])
    rebuilt = [
        tuple(broken_entry) if m[0] == 5 else m for m in original
    ]
    db_module.MIGRATIONS = tuple(rebuilt)
    try:
        fixture = tmp_path / "v4.db"
        _v4_fixture(fixture, with_provenance=True)
        with pytest.raises(sqlite3.DatabaseError):
            _migrate_v4(fixture)
        conn = sqlite3.connect(str(fixture))
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            versions = [
                r[0] for r in conn.execute("SELECT version FROM schema_migrations")
            ]
        finally:
            conn.close()
        assert "runtime_builds" not in tables
        assert "runtime_trees" not in tables
        assert versions == [1, 2, 3, 4]
    finally:
        db_module.MIGRATIONS = original


def test_newer_schema_still_refused(tmp_path):
    from bc250_llm_mode.db import initialize

    database = tmp_path / "state.db"
    initialize_and_close(database)
    conn = open_database(database, mode="write")
    try:
        conn.execute("UPDATE schema_migrations SET version = 99 WHERE version = 5")
        conn.commit()
    finally:
        conn.close()
    conn = open_database(database, mode="write")
    try:
        with pytest.raises(DatabaseTooNew):
            initialize(conn)
    finally:
        conn.close()


# -- Constraint coverage -------------------------------------------------------


def test_constraint_rejects_malformed_build_ids_and_generations(units):
    with pytest.raises(sqlite3.IntegrityError):
        with units.begin() as conn:
            conn.execute(
                "INSERT INTO runtime_builds (build_id, component,"
                " manifest_version, manifest_json, manifest_digest,"
                " recipe_version, provenance_class, created_at)"
                " VALUES ('llamacpp:sha256:XYZ', 'llamacpp', 1, '{}', ?, 1,"
                " 'IMMUTABLE_SOURCE', 't')",
                ("0" * 64,),
            )
    with units.begin() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO runtime_component_state (component, generation,"
                " updated_at) VALUES ('llamacpp', 0, 't')"
            )


# -- Identity derivation ---------------------------------------------------------


def test_derive_build_id_excludes_display_refs_and_refuses_mutability():
    base = _manifest()
    build_a, digest_a = derive_build_id(base)
    assert build_a.startswith("llamacpp:sha256:")
    # A mutable display ref never changes the content-derived identity (D1).
    build_b, digest_b = derive_build_id(_manifest(requested_ref="different-tag"))
    assert build_a == build_b
    assert digest_a == digest_b
    # Canonical encoding is order-independent and stable.
    reordered = dict(reversed(list(base.items())))
    reordered.pop("requested_ref")
    base_identity = {k: v for k, v in base.items() if k != "requested_ref"}
    assert canonical_manifest_bytes(reordered) == canonical_manifest_bytes(base_identity)
    # Timestamps / forbidden / secret-like fields are rejected outright.
    with pytest.raises(RuntimeBuildError) as err:
        derive_build_id(_manifest(built_at="2026-01-01T00:00:00Z"))
    assert err.value.code == "MANIFEST_FIELD_FORBIDDEN"
    with pytest.raises(RuntimeBuildError) as err:
        derive_build_id({**base, "api_key_material": "x"})
    assert err.value.code == "MANIFEST_FIELD_FORBIDDEN"


def test_migration_006_worker_locks_constraints(units):
    from bc250_llm_mode.operations.repositories import WorkerLockRepository

    with units.begin() as conn:
        locks = WorkerLockRepository(conn)
        first = locks.acquire(owner="worker-a", ttl_seconds=60)
        assert first["lease_revision"] == 1
        # Active lock refuses a second host.
        import pytest as _pytest

        from bc250_llm_mode.operations.model import OperationConflict

        with _pytest.raises(OperationConflict):
            locks.acquire(owner="worker-b", ttl_seconds=60)
        # Heartbeat bumps revision and extends expiry.
        beaten = locks.heartbeat(owner="worker-a", expected_revision=1,
                                 ttl_seconds=120)
        assert beaten["lease_revision"] == 2
        # Wrong owner/revision cannot heartbeat or release.
        with _pytest.raises(OperationConflict):
            locks.heartbeat(owner="worker-b", expected_revision=2,
                            ttl_seconds=10)
        locks.release(owner="worker-a", expected_revision=2)
        assert locks.get() is None
        # Expired lock takeover increments revision.
        again = locks.acquire(owner="worker-c", ttl_seconds=0)
        assert again["lease_revision"] == 1
