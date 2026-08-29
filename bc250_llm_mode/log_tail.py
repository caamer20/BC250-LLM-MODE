"""Typed, bounded, read-only access to application-owned logs."""

from __future__ import annotations

from pathlib import Path

MAX_LOG_LINES = 2000
MAX_LOG_BYTES = 2 * 1024 * 1024


class LogTailService:
    def __init__(self, paths) -> None:
        self._sources = {
            "setup": Path(paths.logs_dir) / "setup.log",
            "server": Path(paths.logs_dir) / "llama-server.log",
            "worker": Path(paths.logs_dir) / "worker.log",
        }

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(self._sources)

    def tail(self, source: str, *, lines: int = 200) -> tuple[str, ...]:
        if source not in self._sources:
            raise ValueError(f"unknown log source {source!r}")
        if not isinstance(lines, int) or not 1 <= lines <= MAX_LOG_LINES:
            raise ValueError(f"lines must be within 1..{MAX_LOG_LINES}")
        path = self._sources[source]
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                handle.seek(max(0, size - MAX_LOG_BYTES))
                data = handle.read(MAX_LOG_BYTES)
        except OSError:
            return ()
        text = data.decode("utf-8", errors="replace")
        return tuple(text.splitlines()[-lines:])
