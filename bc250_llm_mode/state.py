"""Legacy state defaults and read-model helpers.

Since the SQLite cutover the runtime store is ``state.db``; this module
retains only immutable defaults and boot identity. The v1→v5 interpretation
of pre-SQLite JSON payloads is pure and lives in
:mod:`bc250_llm_mode.legacy_schema`. There is intentionally NO writable
runtime JSON persistence here anymore.
"""

from __future__ import annotations

from typing import Any

from .legacy_schema import _current_boot_id, default_state

DEFAULT_STATE: dict[str, Any] = default_state()

__all__ = ["DEFAULT_STATE", "_current_boot_id"]


