"""EXP-3 migration-012 profile identity, repository, and bound tests."""

from __future__ import annotations

import sqlite3

import pytest

from bc250_llm_mode import db
from bc250_llm_mode.db import initialize, initialize_and_close, open_database
from bc250_llm_mode.repositories import RepositoryConflict
from bc250_llm_mode.workload_profiles import (
    BUILTIN_PROFILES,
    MAX_ACTIVE_CUSTOM_PROFILES,
    ProfileResolutionIdentity,
    WorkloadProfileError,
    WorkloadProfileRepository,
)


NOW = "2026-08-29T18:00:00Z"
CUSTOM = "1" * 32


def _fresh(tmp_path):
    path = tmp_path / "state.db"
    initialize_and_close(path)
    return open_database(path, mode="write")


def _create_v11(path, monkeypatch):
    current_migrations = db.MIGRATIONS
    current_version = db.SCHEMA_VERSION
    monkeypatch.setattr(db, "MIGRATIONS", current_migrations[:-1])
    monkeypatch.setattr(db, "SCHEMA_VERSION", 11)
    conn = open_database(path, mode="migration")
    try:
        assert initialize(conn) == 11
        conn.execute(
            "INSERT INTO runtime_config(id, model_alias, context, slots, profile_id, "
            "extra_json, updated_at) VALUES (1, 'legacy-model', 8192, 2, "
            "'legacy-profile', '{\"keep\":true}', 'old')"
        )
        conn.execute(
            "INSERT INTO known_good_runtime(id, model_alias, context, slots, "
            "profile_id, runtime_json, runtime_fingerprint, "
            "runtime_component_identity, verified_at) VALUES "
            "(1, 'legacy-model', 4096, 1, 'legacy-profile', '{\"x\":1}', "
            "'legacy-runtime-fp', 'component-a', 'old')"
        )
        conn.execute("CREATE TABLE preserved_exp3_marker(value TEXT)")
        conn.execute("INSERT INTO preserved_exp3_marker VALUES ('keep-me')")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(db, "MIGRATIONS", current_migrations)
    monkeypatch.setattr(db, "SCHEMA_VERSION", current_version)


def test_migration_012_preserves_v11_rows_and_seeds_exact_builtins(tmp_path, monkeypatch):
    path = tmp_path / "v11.db"
    _create_v11(path, monkeypatch)
    conn = open_database(path, mode="migration")
    try:
        assert initialize(conn) == 12 == db.SCHEMA_VERSION
        assert conn.execute("SELECT value FROM preserved_exp3_marker").fetchone()[0] == "keep-me"
        runtime = conn.execute("SELECT * FROM runtime_config WHERE id = 1").fetchone()
        assert (runtime["model_alias"], runtime["context"], runtime["slots"], runtime["profile_id"]) == (
            "legacy-model", 8192, 2, "legacy-profile"
        )
        assert runtime["extra_json"] == '{"keep":true}'
        assert runtime["profile_revision"] is None
        assert runtime["profile_fingerprint"] is None
        known = conn.execute("SELECT * FROM known_good_runtime WHERE id = 1").fetchone()
        assert known["runtime_json"] == '{"x":1}'
        assert known["profile_revision"] is None
        assert known["profile_fingerprint"] is None
        rows = WorkloadProfileRepository(conn).list()
        assert tuple(row.profile_id for row in rows) == tuple(
            sorted((item.profile_id for item in BUILTIN_PROFILES), key=lambda profile_id: next(
                item.name.lower() for item in BUILTIN_PROFILES if item.profile_id == profile_id
            ))
        )
        by_id = {row.profile_id: row for row in rows}
        for expected in BUILTIN_PROFILES:
            actual = by_id[expected.profile_id]
            for field, value in expected.__dict__.items():
                assert getattr(actual, field) == value
            assert actual.owner == "builtin"
            assert actual.revision == 1
            assert actual.evidence_class == "ESTIMATED"
    finally:
        conn.close()


def test_migration_012_is_atomic_on_mid_migration_failure(tmp_path, monkeypatch):
    path = tmp_path / "atomic.db"
    _create_v11(path, monkeypatch)
    broken = (
        12,
        "workload-profiles",
        db.MIGRATIONS[-1][2][:-1] + ("INSERT INTO absent_table VALUES (1)",),
    )
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:-1] + (broken,))
    conn = open_database(path, mode="migration")
    try:
        with pytest.raises(sqlite3.OperationalError):
            initialize(conn)
        tables = {row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
        assert "workload_profiles" not in tables
        runtime_columns = {row["name"] for row in conn.execute("PRAGMA table_info(runtime_config)")}
        assert "profile_revision" not in runtime_columns
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 11
    finally:
        conn.close()


def test_repository_create_replace_delete_and_revision_fencing(tmp_path):
    conn = _fresh(tmp_path)
    try:
        profiles = WorkloadProfileRepository(conn, clock=lambda: NOW)
        created = profiles.create_custom(
            profile_id=CUSTOM, name="  My   shared profile ",
            context_per_slot=8192, slots=3, kv_cache_type="q4_0",
            batch_size=512, ubatch_size=128, idle_policy="STOP_AFTER",
            stop_after_minutes=20,
        )
        assert created.name == "My shared profile"
        assert created.revision == 1
        with pytest.raises(RepositoryConflict) as stale:
            profiles.replace_custom(
                CUSTOM, expected_revision=9, name="Changed", context_per_slot=4096,
                slots=2, kv_cache_type="q8_0", batch_size=512, ubatch_size=128,
                flash_attention="auto", optimization_preset_id="balanced",
                thermal_policy="standard", idle_policy="KEEP_LOADED",
                stop_after_minutes=None,
            )
        assert stale.value.code == "PROFILE_REVISION_CONFLICT"
        updated = profiles.replace_custom(
            CUSTOM, expected_revision=1, name="Changed", context_per_slot=4096,
            slots=2, kv_cache_type="q8_0", batch_size=512, ubatch_size=128,
            flash_attention="auto", optimization_preset_id="balanced",
            thermal_policy="standard", idle_policy="KEEP_LOADED",
            stop_after_minutes=None,
        )
        assert updated.revision == 2 and updated.evidence_class == "ESTIMATED"
        deleted = profiles.soft_delete(CUSTOM, expected_revision=2)
        assert deleted.deleted_at == NOW and deleted.revision == 3
        assert profiles.get(CUSTOM, include_deleted=False) is None
        assert len(profiles.list()) == len(BUILTIN_PROFILES)
    finally:
        conn.close()


def test_builtins_are_immutable_and_custom_names_are_case_insensitive(tmp_path):
    conn = _fresh(tmp_path)
    try:
        profiles = WorkloadProfileRepository(conn, clock=lambda: NOW)
        with pytest.raises(RepositoryConflict) as immutable:
            profiles.soft_delete("builtin-cool", expected_revision=1)
        assert immutable.value.code == "PROFILE_BUILTIN_IMMUTABLE"
        profiles.create_custom(
            profile_id=CUSTOM, name="Field Work", context_per_slot=4096, slots=1
        )
        with pytest.raises(RepositoryConflict) as duplicate:
            profiles.create_custom(
                profile_id="2" * 32, name="field work", context_per_slot=4096, slots=1
            )
        assert duplicate.value.code == "PROFILE_CREATE_CONFLICT"
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("patch", "message"),
    (
        ({"context_per_slot": 263000}, "context_per_slot"),
        ({"slots": 9}, "slots"),
        ({"batch_size": 130}, "batch"),
        ({"idle_policy": "STOP_AFTER", "stop_after_minutes": 4}, "stop_after"),
        ({"idle_policy": "KEEP_LOADED", "stop_after_minutes": 10}, "stop_after"),
    ),
)
def test_profile_bounds_fail_before_persistence(tmp_path, patch, message):
    conn = _fresh(tmp_path)
    try:
        kwargs = dict(
            profile_id=CUSTOM, name="Bounded", context_per_slot=4096, slots=1,
            batch_size=512, ubatch_size=128,
        )
        kwargs.update(patch)
        with pytest.raises(WorkloadProfileError, match=message):
            WorkloadProfileRepository(conn, clock=lambda: NOW).create_custom(**kwargs)
        assert conn.execute(
            "SELECT COUNT(*) FROM workload_profiles WHERE owner = 'user'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_active_custom_profile_count_is_bounded(tmp_path):
    conn = _fresh(tmp_path)
    try:
        profiles = WorkloadProfileRepository(conn, clock=lambda: NOW)
        for number in range(MAX_ACTIVE_CUSTOM_PROFILES):
            profiles.create_custom(
                profile_id=f"{number + 1:032x}", name=f"Profile {number}",
                context_per_slot=4096, slots=1,
            )
        with pytest.raises(WorkloadProfileError, match="limit"):
            profiles.create_custom(
                profile_id="f" * 32, name="One too many",
                context_per_slot=4096, slots=1,
            )
    finally:
        conn.close()


def test_resolution_fingerprint_is_canonical_and_covers_every_runtime_field():
    base = ProfileResolutionIdentity(
        profile_id="builtin-interactive", profile_revision=1,
        model_artifact_digest="a" * 64, quant="Q5_K_M",
        context_per_slot=8192, slots=1, kv_cache_type="q8_0",
        batch_size=1024, ubatch_size=256, flash_attention="auto",
        optimization_preset_id="balanced", thermal_policy="standard",
        idle_policy="KEEP_LOADED", stop_after_minutes=None,
    )
    assert base.fingerprint() == base.fingerprint()
    assert len(base.fingerprint()) == 64
    for field, replacement in (
        ("profile_revision", 2), ("model_artifact_digest", "b" * 64),
        ("quant", "Q4_K_M"), ("context_per_slot", 4096), ("slots", 2),
        ("kv_cache_type", "q4_0"), ("batch_size", 512),
        ("ubatch_size", 128), ("flash_attention", "off"),
        ("optimization_preset_id", "cool-quiet"), ("thermal_policy", "cool"),
        ("idle_policy", "STOP_ON_DESKTOP"),
    ):
        assert base.fingerprint() != ProfileResolutionIdentity(
            **{**base.__dict__, field: replacement}
        ).fingerprint()
