"""Explicit application paths.

Production rule: paths are constructed once from an installation profile and
injected into stores/services — never computed from ``Path.home()`` at module
import time, and never silently created under root's home when setup ran as
the desktop user (or vice versa).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_DIR_NAME = ".bc250-llm-mode"


@dataclass(frozen=True)
class AppPaths:
    """Validated filesystem layout for one installation profile."""

    app_dir: Path
    state_path: Path
    models_dir: Path
    logs_dir: Path
    conversations_dir: Path
    backups_dir: Path
    staging_dir: Path
    database_path: Path
    migration_lock_path: Path
    migration_receipts_dir: Path

    @classmethod
    def for_home(cls, home: str | Path | None = None) -> "AppPaths":
        """Default per-user layout. ``home`` is injectable for tests."""
        base = Path(home).expanduser() if home else Path(os.path.expanduser("~"))
        return cls.from_app_dir(base / APP_DIR_NAME)

    @classmethod
    def from_app_dir(cls, app_dir: str | Path) -> "AppPaths":
        app = Path(app_dir).expanduser()
        return cls(
            app_dir=app,
            # state.json is the LEGACY import source; SQLite (database_path)
            # becomes the sole source of truth after the 0.9 cutover.
            state_path=app / "state.json",
            models_dir=app / "models",
            logs_dir=app / "logs",
            conversations_dir=app / "conversations",
            backups_dir=app / "backups",
            staging_dir=app / "staging",
            database_path=app / "state.db",
            migration_lock_path=app / "state.db.import-lock",
            migration_receipts_dir=app / "migration-receipts",
        )

    @property
    def legacy_state_path(self) -> Path:
        """Explicit alias: the read-only JSON import source."""
        return self.state_path

    @classmethod
    def temporary(cls, tmp_path: str | Path) -> "AppPaths":
        """Isolated layout for tests — nothing touches the developer's home."""
        return cls.from_app_dir(Path(tmp_path) / APP_DIR_NAME)

    def ensure_directories(self) -> None:
        for directory in (
            self.app_dir, self.models_dir, self.logs_dir,
            self.conversations_dir, self.backups_dir, self.staging_dir,
            self.migration_receipts_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def validate(self) -> None:
        """Reject symlink swaps on directories the app owns."""
        for directory in (self.app_dir, self.models_dir, self.logs_dir):
            if directory.exists() and directory.is_symlink():
                raise ValueError(f"Refusing symlinked application path: {directory}")
