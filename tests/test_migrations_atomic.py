"""R2 hardening P0-3: migrations are atomic — a mid-migration failure must
leave no partial schema and no recorded version, and a retry must succeed."""

from __future__ import annotations

import sqlite3

import pytest

from bc250_llm_mode import db


@pytest.fixture()
def fresh_conn(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "migration-test.db"))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def _tables(conn):
    return {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def test_failed_migration_leaves_no_partial_schema(fresh_conn, monkeypatch):
    db.initialize(fresh_conn)  # baseline v1

    failing = (
        2,
        "broken-migration",
        (
            "CREATE TABLE good_table (id INTEGER PRIMARY KEY)",
            "INSERT INTO definitely_missing_table VALUES (1)",
        ),
    )
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS + (failing,))

    with pytest.raises(sqlite3.OperationalError):
        db.initialize(fresh_conn)

    assert "good_table" not in _tables(fresh_conn), "partial DDL must roll back"
    applied = {int(r["version"]) for r in fresh_conn.execute(
        "SELECT version FROM schema_migrations"
    )}
    assert 2 not in applied, "failed migration must not record its version"

    # Retry after the migration is fixed succeeds cleanly.
    fixed = (2, "fixed-migration", ("CREATE TABLE good_table (id INTEGER PRIMARY KEY)",))
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:-1] + (fixed,))
    assert db.initialize(fresh_conn) >= 2
    assert "good_table" in _tables(fresh_conn)
    applied = {int(r["version"]) for r in fresh_conn.execute(
        "SELECT version FROM schema_migrations"
    )}
    assert 2 in applied


def test_initialize_is_idempotent_across_connections(tmp_path):
    path = tmp_path / "again.db"
    conn_a = db.connect(path)
    db.initialize(conn_a)
    conn_b = db.connect(path)
    assert db.initialize(conn_b) == db.SCHEMA_VERSION
    conn_a.close()
    conn_b.close()