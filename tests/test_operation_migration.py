"""Session 5A: migration 003 is atomic, preserves v2 data, and enforces
referential integrity through normal unit-of-work connections."""

from __future__ import annotations

import sqlite3

import pytest

from bc250_llm_mode import db


def _tables(conn):
    return {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _applied(conn):
    if "schema_migrations" not in _tables(conn):
        return set()
    return {int(r["version"]) for r in conn.execute(
        "SELECT version FROM schema_migrations"
    )}


@pytest.fixture()
def v2_conn(tmp_path, monkeypatch):
    """A real file database at schema v2 (pre-operations), like existing installs."""
    path = tmp_path / "v2.db"
    conn = db.open_database(path, mode="migration", journal="delete")
    migrations_all = tuple(db.MIGRATIONS)
    migrations_v2 = tuple(m for m in migrations_all if m[0] <= 2)
    real_schema_version = db.SCHEMA_VERSION
    monkeypatch.setattr(db, "MIGRATIONS", migrations_v2)
    monkeypatch.setattr(db, "SCHEMA_VERSION", 2)
    db.initialize(conn)
    # Hand over with the REAL registry restored: later migrations must be
    # visible (and applicable) inside the test body.
    monkeypatch.setattr(db, "MIGRATIONS", migrations_all)
    monkeypatch.setattr(db, "SCHEMA_VERSION", real_schema_version)
    yield conn
    conn.close()


def test_migration_003_fails_after_first_ddl_leaves_no_v3_trace(v2_conn, monkeypatch):
    """Inject a failure after migration 003's FIRST statement: no operations
    table may survive and no version row may be written."""
    version, name, statements = next(
        m for m in db.MIGRATIONS if m[0] == 3
    )
    injected = (statements[0], "INSERT INTO definitely_missing_table VALUES (1)")

    registry = [
        (v, n, injected if v == 3 else s) for v, n, s in db.MIGRATIONS
    ]
    monkeypatch.setattr(db, "MIGRATIONS", tuple(registry))
    monkeypatch.setattr(db, "SCHEMA_VERSION", 6)

    before_tables = _tables(v2_conn)
    with pytest.raises(sqlite3.OperationalError):
        db.initialize(v2_conn)

    assert _applied(v2_conn) == {1, 2}, "failed 003 must not record its version"
    for table in ("operations", "operation_steps", "operation_events", "operation_leases"):
        assert table not in _tables(v2_conn)
    # Nothing pre-existing disappeared either.
    assert before_tables <= _tables(v2_conn)


def test_fixed_retry_succeeds_and_creates_all_operation_tables(v2_conn):
    assert db.initialize(v2_conn) == db.SCHEMA_VERSION
    tables = _tables(v2_conn)
    for name in ("operations", "operation_steps", "operation_events", "operation_leases"):
        assert name in tables
    assert _applied(v2_conn) == {1, 2, 3, 4, 5, 6}
    # Idempotent re-initialization does not reapply or duplicate.
    assert db.initialize(v2_conn) == db.SCHEMA_VERSION


def test_existing_v2_rows_survive_migration_003(v2_conn):
    v2_conn.execute(
        "INSERT INTO known_good_runtime(id, model_alias, context, slots, verified_at)"
        " VALUES (1, 'lfm25-26b', 8192, 1, '2026-08-23T00:00:00Z')"
    )
    v2_conn.commit()
    assert db.initialize(v2_conn) == db.SCHEMA_VERSION

    row = v2_conn.execute(
        "SELECT model_alias, context FROM known_good_runtime WHERE id = 1"
    ).fetchone()
    assert row["model_alias"] == "lfm25-26b"
    assert row["context"] == 8192


def test_schema_newer_than_supported_is_refused(v2_conn):
    v2_conn.execute(
        "INSERT INTO schema_migrations(version, name, applied_at)"
        " VALUES (99, 'future', 'now')"
    )
    v2_conn.commit()
    with pytest.raises(db.DatabaseTooNew):
        db.initialize(v2_conn)


# --- Referential integrity through normal units of work ---------------------


@pytest.fixture()
def units(tmp_path):
    from bc250_llm_mode.db import initialize_and_close
    from bc250_llm_mode.unit_of_work import UnitOfWorkFactory

    database = tmp_path / "ops.db"
    initialize_and_close(database)
    return UnitOfWorkFactory(database)


def _insert_operation(conn, operation_id="op-1"):
    conn.execute(
        "INSERT INTO operations(id, operation_type, request_version,"
        " request_json, state, created_at, updated_at)"
        " VALUES (?, 'MODEL_ACTIVATE', 1, '{}', 'QUEUED', 't0', 't0')",
        (operation_id,),
    )


def test_orphan_step_rejected_through_uow(units):
    with pytest.raises(sqlite3.IntegrityError):
        with units.begin() as conn:
            conn.execute(
                "INSERT INTO operation_steps(operation_id, step_key, sequence, state)"
                " VALUES ('ghost-op', 's1', 1, 'PENDING')"
            )


def test_orphan_event_rejected_through_uow(units):
    with pytest.raises(sqlite3.IntegrityError):
        with units.begin() as conn:
            conn.execute(
                "INSERT INTO operation_events(operation_id, ts, summary)"
                " VALUES ('ghost-op', 't0', 'orphan')"
            )


def test_orphan_lease_rejected_through_uow(units):
    with pytest.raises(sqlite3.IntegrityError):
        with units.begin() as conn:
            conn.execute(
                "INSERT INTO operation_leases(resource_key, operation_id, owner,"
                " acquired_at, heartbeat_at, expires_at)"
                " VALUES ('runtime-active', 'ghost-op', 'w1', 't0', 't0', 't9')"
            )


def test_step_and_event_cascade_with_own_operation_parent_ref_severed(units):
    """Steps/events cascade ONLY with their own operation; deleting a parent
    operation severs child references (SET NULL) instead of cascading."""
    with units.begin() as conn:
        _insert_operation(conn, "op-parent")
        _insert_operation(conn, "op-child")
        conn.execute(
            "UPDATE operations SET parent_operation_id='op-parent'"
            " WHERE id='op-child'"
        )
        conn.execute(
            "INSERT INTO operation_steps(operation_id, step_key, sequence, state)"
            " VALUES ('op-child', 's1', 1, 'PENDING')"
        )

    # Deleting the PARENT must not take the child (or its history) along.
    with units.begin() as conn:
        conn.execute("DELETE FROM operations WHERE id='op-parent'")

    with units.begin() as conn:
        child = conn.execute(
            "SELECT parent_operation_id FROM operations WHERE id='op-child'"
        ).fetchone()
        assert child is not None
        assert child["parent_operation_id"] is None
        steps = conn.execute(
            "SELECT COUNT(*) FROM operation_steps WHERE operation_id='op-child'"
        ).fetchone()[0]
        assert steps == 1, "child history survives parent deletion"

        # Deleting the CHILD itself cascades its own steps/events.
        conn.execute("DELETE FROM operations WHERE id='op-child'")
        assert conn.execute(
            "SELECT COUNT(*) FROM operation_steps WHERE operation_id='op-child'"
        ).fetchone()[0] == 0

