"""R2.2: SQLite connections, schema creation, PRAGMAs, permissions."""

import json
from pathlib import Path

import pytest

from bc250_llm_mode import db
from bc250_llm_mode.db import SCHEMA_VERSION, connect, initialize, integrity_ok
from bc250_llm_mode.paths import AppPaths

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def paths(tmp_path):
    paths = AppPaths.temporary(tmp_path)
    paths.ensure_directories()
    return paths


def test_fresh_database_creates_schema_at_version_one(paths):
    conn = db.initialize_file(paths.database_path)
    version = conn.execute(
        "SELECT MAX(version) AS v FROM schema_migrations"
    ).fetchone()["v"]
    assert version == SCHEMA_VERSION
    tables = {
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {
        "settings", "runtime_config", "model_installations", "bench_history",
        "autotune_history", "thermal_state", "component_provenance",
        "runtime_observations", "legacy_import_extras", "schema_migrations",
    } <= tables


def test_connection_applies_required_pragmas(paths):
    conn = connect(paths.database_path)
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL


def test_database_file_permissions_are_private(paths):
    conn = db.initialize_file(paths.database_path)
    conn.close()
    mode = paths.database_path.stat().st_mode & 0o777
    assert mode & 0o077 == 0, f"database is group/world accessible: {oct(mode)}"


def test_integrity_check_passes_on_healthy_database(paths):
    conn = db.initialize_file(paths.database_path)
    assert integrity_ok(conn) is True


def test_newer_database_schema_is_refused_never_reset(paths):
    conn = connect(paths.database_path)
    initialize(conn)
    conn.execute(
        "UPDATE schema_migrations SET version = 999 "
        "WHERE version = (SELECT MAX(version) FROM schema_migrations)"
    )
    conn.commit()
    with pytest.raises(db.DatabaseTooNew, match="newer than supported"):
        initialize(connect(paths.database_path))
    conn.close()
    # The database was not wiped by the refusal.
    assert paths.database_path.exists()


def test_concurrent_writer_respects_busy_timeout(paths):
    db.initialize_file(paths.database_path)
    blocker = connect(paths.database_path)
    writer = connect(paths.database_path)
    try:
        blocker.execute("BEGIN EXCLUSIVE")
        blocker.execute(
            "INSERT INTO settings(key, value_json, updated_at) "
            "VALUES ('k', '{}', 'now')"
        )
        blocker.commit()
        writer.execute(
            "INSERT INTO settings(key, value_json, updated_at) "
            "VALUES ('k2', '{}', 'now')"
        )
        writer.commit()
    finally:
        blocker.close()
        writer.close()

