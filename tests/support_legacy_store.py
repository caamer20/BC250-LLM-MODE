"""Test support: the historical writable legacy JSON store.

Production no longer has any writable runtime JSON persistence (Session
4.1 §3.5). This module exists ONLY so tests of the historical store's
behavioral contract (atomic writes, revision bumps, transactions) keep a
faithful reference implementation. It delegates canonicalization to the
pure ``bc250_llm_mode.legacy_schema`` functions.
"""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from bc250_llm_mode.legacy_schema import canonicalize_legacy_state


class LegacyStateStore:
    """The pre-SQLite atomic JSON store, preserved for test reference."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def _locked_write(self, mutator) -> dict[str, Any]:
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "w", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                state = self.load()
                result = mutator(state)
                if result is None:
                    return state
                if not isinstance(result, dict):
                    raise TypeError("transaction mutator must return a dict or None")
                state = result
                state["revision"] = int(state.get("revision", 0)) + 1
                self.save(state)
                return state
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def transaction(self, mutator) -> dict[str, Any]:
        return self._locked_write(mutator)

    def load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return canonicalize_legacy_state({})
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"State file {self.path} is corrupt: {exc}") from exc
        return canonicalize_legacy_state(raw)

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = deepcopy(state)
        fd, temporary = tempfile.mkstemp(prefix=".state-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def update(self, **changes: Any) -> dict[str, Any]:
        state = self.load()
        state.update(changes)
        self.save(state)
        return state
