"""SQLite foundation: connections, PRAGMAs, migrations, integrity checks.

Contract (docs/adr/001-sqlite-cutover.md):
- state.db is the sole source of truth after cutover; JSON is a read-only
  import source.
- Every connection enables foreign keys, a bounded busy timeout, WAL where
  supported, and an explicit synchronous policy.
- Schema versions are tracked in ``schema_migrations``; a database newer than
  the supported version is refused, never reset.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 5000

# (version, name, statements). Each migration runs inside one explicit
# transaction: every statement is executed individually (never
# executescript(), which commits pending work first), and the
# schema_migrations row is written by the same transaction. A failure
# mid-migration rolls back the partial schema AND the version row.
MIGRATIONS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (
        1,
        "initial-schema",
        (
            """
            CREATE TABLE settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0
            )
            """,
            """
            CREATE TABLE runtime_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                model_alias TEXT,
                context INTEGER NOT NULL,
                slots INTEGER NOT NULL DEFAULT 1,
                profile_id TEXT,
                extra_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE model_installations (
                id INTEGER PRIMARY KEY,
                alias TEXT NOT NULL UNIQUE,
                path TEXT NOT NULL,
                quant TEXT,
                display_name TEXT,
                sampling_json TEXT NOT NULL DEFAULT '{}',
                provenance TEXT NOT NULL DEFAULT 'legacy-import',
                validation_status TEXT NOT NULL DEFAULT 'unverified',
                imported_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE bench_history (
                id INTEGER PRIMARY KEY,
                ts TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE autotune_history (
                id INTEGER PRIMARY KEY,
                payload_json TEXT NOT NULL
            )
            """,
            # The thermal latch is safety-authoritative: it survives migration
            # and reboot and is never marked stale.
            """
            CREATE TABLE thermal_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                latch_state TEXT NOT NULL,
                baseline_json TEXT,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE component_provenance (
                component TEXT PRIMARY KEY,
                describe TEXT,
                commit_sha TEXT,
                recorded_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE runtime_observations (
                key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                stale INTEGER NOT NULL DEFAULT 1
            )
            """,
            """
            CREATE TABLE legacy_import_extras (
                key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            )
            """,
        ),
    ),
)


class DatabaseTooNew(RuntimeError):
    """The on-disk schema is newer than this application supports."""


def connect(database_path: str | Path) -> sqlite3.Connection:
    """Open a connection with the production PRAGMA contract applied.

    Every PRAGMA result is fully consumed: an unconsumed statement keeps a
    read transaction open and would block WAL checkpoints later.
    """
    # check_same_thread=False: the compatibility facade and watchdogs use
    # worker threads. Safety comes from the file-lock + busy_timeout write
    # discipline, not from sqlite's thread affinity.
    path = Path(database_path)
    existed_before = path.exists()
    conn = sqlite3.connect(
        str(path),
        timeout=BUSY_TIMEOUT_MS / 1000.0,
        check_same_thread=False,
    )
    if not existed_before:
        # A freshly created database holds the entire durable state; keep
        # it private from the first moment (WAL sidecars inherit umask).
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    conn.row_factory = sqlite3.Row
    for pragma in (
        "PRAGMA foreign_keys=ON",
        f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}",
        "PRAGMA journal_mode=WAL",
        "PRAGMA synchronous=FULL",
    ):
        conn.execute(pragma).fetchall()
    return conn


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchall()
    if not rows:
        return set()
    return {int(r["version"]) for r in conn.execute("SELECT version FROM schema_migrations")}


def initialize(conn: sqlite3.Connection) -> int:
    """Apply pending migrations; returns the resulting schema version."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    applied = _applied_versions(conn)
    if applied:
        newest = max(applied)
        if newest > SCHEMA_VERSION:
            raise DatabaseTooNew(
                f"Database schema v{newest} is newer than supported v{SCHEMA_VERSION}; "
                "refusing to open (repair mode required)."
            )
    for version, name, statements in MIGRATIONS:
        if version in applied:
            continue
        # Explicit transaction: sqlite supports transactional DDL, so a
        # failure rolls back every earlier statement AND keeps the
        # schema_migrations row from ever being written. (executescript()
        # cannot provide this: it commits pending work before running.)
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in statements:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) "
                "VALUES (?, ?, datetime('now'))",
                (version, name),
            )
        except BaseException:
            conn.rollback()
            raise
        conn.commit()
    applied_now = _applied_versions(conn)
    return max(applied_now) if applied_now else 0


def initialize_file(database_path: str | Path) -> sqlite3.Connection:
    """Create/initialize a database file with production permissions."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    initialize(conn)
    Path(path).chmod(0o600)
    return conn


def integrity_ok(conn: sqlite3.Connection) -> bool:
    # fetchall() is deliberate: an unconsumed cursor holds a read
    # transaction open, which would block WAL checkpoints.
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    if [str(row[0]).lower() for row in rows] != ["ok"]:
        return False
    return not conn.execute("PRAGMA foreign_key_check").fetchall()
