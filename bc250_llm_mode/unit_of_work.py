"""Per-command unit-of-work boundary (Road to 1.0, session A3-A5).

Services must NOT share the compatibility facade's long-lived connection:
``check_same_thread=False`` plus an application lock is transitional, and
the final architecture is one short-lived connection per command.

``UnitOfWorkFactory.begin()`` therefore:

1. opens one SQLite connection (busy timeout carries cross-process
   contention);
2. starts ``BEGIN IMMEDIATE`` for write units (read-only units skip the
   write transaction entirely);
3. lets the caller construct repositories bound to that connection;
4. commits exactly once on success;
5. rolls back on every exception;
6. closes the connection deterministically.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .db import open_database


class UnitOfWorkFactory:
    """Opens isolated per-command connections against one database file."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def _connect(self, *, mode: str = "write") -> sqlite3.Connection:
        # One shared policy with initialization/staging: foreign keys ON,
        # bounded busy timeout, WAL+FULL for writers, query_only for readers.
        return open_database(self.database_path, mode=mode)

    @contextmanager
    def begin(self, *, write: bool = True):
        """Yield a connection inside one unit of work."""
        conn = self._connect(mode="write" if write else "read")
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                if write and conn.in_transaction:
                    conn.rollback()
                raise
            else:
                if write and conn.in_transaction:
                    conn.commit()
        finally:
            conn.close()

    def read(self):
        """Read-only unit: no write transaction, no commit."""
        return self.begin(write=False)