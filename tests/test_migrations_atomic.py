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

    next_version = db.SCHEMA_VERSION + 1
    failing = (
        next_version,
        "broken-migration",
        (
            "CREATE TABLE good_table (id INTEGER PRIMARY KEY)",
            "INSERT INTO definitely_missing_table VALUES (1)",
        ),
    )
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS + (failing,))
    monkeypatch.setattr(db, "SCHEMA_VERSION", next_version)

    with pytest.raises(sqlite3.OperationalError):
        db.initialize(fresh_conn)

    assert "good_table" not in _tables(fresh_conn), "partial DDL must roll back"
    applied = {int(r["version"]) for r in fresh_conn.execute(
        "SELECT version FROM schema_migrations"
    )}
    assert next_version not in applied, "failed migration must not record its version"

    # Retry after the migration is fixed succeeds cleanly.
    fixed = (
        next_version,
        "fixed-migration",
        ("CREATE TABLE good_table (id INTEGER PRIMARY KEY)",),
    )
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:-1] + (fixed,))
    assert db.initialize(fresh_conn) >= next_version
    assert "good_table" in _tables(fresh_conn)
    applied = {int(r["version"]) for r in fresh_conn.execute(
        "SELECT version FROM schema_migrations"
    )}
    assert next_version in applied


def test_initialize_is_idempotent_across_connections(tmp_path):
    path = tmp_path / "again.db"
    conn_a = db.connect(path)
    db.initialize(conn_a)
    conn_b = db.connect(path)
    assert db.initialize(conn_b) == db.SCHEMA_VERSION
    conn_a.close()
    conn_b.close()


# --- Registry contract (R2 exit plan §3.1) ---------------------------------


def test_registry_declared_ascending_and_contiguous():
    versions = [version for version, _name, _stmts in db.MIGRATIONS]
    assert versions == sorted(versions)
    assert versions == list(range(1, len(versions) + 1))
    assert db.SCHEMA_VERSION == max(versions)


def test_later_migration_depends_on_earlier_one(fresh_conn, monkeypatch):
    """Ordering is behavioral: a later migration's statement references a
    table created by the immediately preceding migration, and runs only
    because numeric order guarantees that predecessor exists."""
    dependent = (
        db.SCHEMA_VERSION + 1,
        "depends-on-previous",
        (
            "INSERT INTO known_good_runtime(id, model_alias, context, slots, "
            "verified_at) VALUES (1, 'probe', 8192, 1, 'now')",
        ),
    )
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS + (dependent,))
    monkeypatch.setattr(db, "SCHEMA_VERSION", db.SCHEMA_VERSION + 1)

    assert db.initialize(fresh_conn) == db.SCHEMA_VERSION
    row = fresh_conn.execute(
        "SELECT model_alias FROM known_good_runtime WHERE id = 1"
    ).fetchone()
    assert row["model_alias"] == "probe"


@pytest.mark.parametrize(
    "registry, schema_version",
    [
        ((2, "b", ("SELECT 1",)), 2),                      # gap: missing 1
        (
            (1, "a", ("SELECT 1",)),
            (1, "a", ("SELECT 1",)),                        # duplicate
        ),
        ((0, "zero", ("SELECT 1",)), 1),                    # non-positive
        ((1, "a", ("SELECT 1",)), (2, "b", ("SELECT 1",))),  # wrong SCHEMA_VERSION (declared desc)
    ],
)
def test_invalid_registries_rejected(fresh_conn, monkeypatch, registry, schema_version):
    if isinstance(registry[0], tuple):
        migrations = registry
    else:
        migrations = (registry,)
    monkeypatch.setattr(db, "MIGRATIONS", migrations)
    with pytest.raises(db.MigrationRegistryError):
        db.validate_registry(db.MIGRATIONS, schema_version)


def test_reordered_declaration_still_executes_numerically(fresh_conn, monkeypatch):
    """Even if declarations are accidentally reversed, execution order is
    numeric — the dependent statement must still succeed."""
    next_version = db.SCHEMA_VERSION + 1
    after_next = next_version + 1
    first = (next_version, "creates-probe-table", ("CREATE TABLE probe_t (v INTEGER)",))
    second = (after_next, "uses-probe-table", ("INSERT INTO probe_t VALUES (7)",))
    monkeypatch.setattr(
        db, "MIGRATIONS", db.MIGRATIONS + (second, first)
    )
    monkeypatch.setattr(db, "SCHEMA_VERSION", after_next)

    assert db.initialize(fresh_conn) == after_next
    assert fresh_conn.execute("SELECT v FROM probe_t").fetchone()["v"] == 7