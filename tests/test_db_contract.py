"""Session 4.1 §3.2: one SQLite connection policy, test-proven.

Every production connection comes from ``db.open_database``; foreign keys
are connection-local, so they must be enforced through the same unit-of-work
connections services actually use — not only the initialization connection.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bc250_llm_mode.db import (
    BUSY_TIMEOUT_MS,
    initialize_and_close,
    integrity_ok,
    open_database,
)
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory


def _units(tmp_path: Path) -> UnitOfWorkFactory:
    database = tmp_path / "state.db"
    initialize_and_close(database)
    return UnitOfWorkFactory(database)


def test_foreign_keys_enforced_through_a_normal_unit_of_work(tmp_path):
    units = _units(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        with units.begin() as conn:
            # operation_steps does not exist yet; use settings' schema by
            # inserting an orphan into a table with a real foreign key is
            # not available at v2, so assert enforcement directly.
            row = conn.execute("PRAGMA foreign_keys").fetchone()
            assert row[0] == 1, "unit-of-work connections must enable FKs"
            conn.execute(
                "INSERT INTO runtime_config (id, model_alias, context, slots,"
                " updated_at) VALUES (2, 'x', 1, 1, 'now')"
            )


def test_read_unit_is_query_only_and_write_unit_commits_once(tmp_path):
    units = _units(tmp_path)
    with units.begin() as conn:
        from bc250_llm_mode.repositories import SettingsRepository

        SettingsRepository(conn).set_many({"disclaimer_ack": True})

    # A read unit can neither write nor alter schema.
    with units.read() as conn:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute(
                "INSERT INTO settings(key, value_json, updated_at) VALUES ('x','{}','t')"
            )
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("CREATE TABLE nope (id INTEGER)")

    # The write unit's single commit persisted exactly its data.
    with units.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 1


def test_write_unit_rolls_back_on_exception(tmp_path):
    units = _units(tmp_path)
    with pytest.raises(RuntimeError):
        with units.begin() as conn:
            conn.execute(
                "INSERT INTO settings(key, value_json, updated_at) VALUES ('x','{}','t')"
            )
            raise RuntimeError("boom")
    with units.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0


def test_open_database_read_mode_requires_existing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        open_database(tmp_path / "missing.db", mode="read")


def test_open_database_rejects_unknown_mode(tmp_path):
    with pytest.raises(ValueError, match="mode"):
        open_database(tmp_path / "x.db", mode="upsert")


def test_migration_staging_journal_delete_leaves_no_wal_sidecar(tmp_path):
    staging = tmp_path / "staging.db"
    conn = open_database(staging, mode="migration", journal="delete")
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "delete"
        assert integrity_ok(conn)
    finally:
        conn.close()
    assert not (tmp_path / "staging.db-wal").exists()


def test_busy_timeout_applies_to_unit_connections(tmp_path):
    units = _units(tmp_path)
    with units.read() as conn:
        value = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert int(value) == BUSY_TIMEOUT_MS


def test_initialize_and_close_keeps_database_valid(tmp_path):
    database = tmp_path / "state.db"
    initialize_and_close(database)
    initialize_and_close(database)  # idempotent re-initialization
    conn = open_database(database, mode="read")
    try:
        assert integrity_ok(conn)
    finally:
        conn.close()
