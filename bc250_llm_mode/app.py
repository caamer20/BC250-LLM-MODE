"""Application composition root.

Production rule: ``AppPaths`` is constructed once (from the installation
profile or a test temporary directory), validated here, and injected into
every store/service/frontend. No module may fall back to ``Path.home()``
after composition.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .logging_utils import CommandRunner, configure_logging
from .paths import AppPaths
from .state import StateStore


@dataclass
class Application:
    """Everything the CLI/GUI/chat frontends need, built once.

    ``store`` is ``None`` only in repair mode: a legacy-state migration
    failed and no database was published. ``repair_reason`` carries the
    failure; normal operation is blocked until ``repair-retry`` succeeds.
    """

    paths: AppPaths
    store: "StateStore | CompatStateStore | None"
    logger: logging.Logger
    repair_reason: str | None = None

    @property
    def operational(self) -> bool:
        return self.store is not None

    @classmethod
    def compose(cls, paths: AppPaths | None = None) -> "Application":
        """Compose with SQLite as the source of truth (ADR 001 cutover).

        One-time migration: if no database exists and a legacy ``state.json``
        is present, it is imported into a staged database and published
        atomically before anything else runs. The JSON is retained as a
        read-only backup.
        """
        resolved = paths or AppPaths.for_home()
        resolved.validate()
        resolved.ensure_directories()
        logger = configure_logging(resolved.logs_dir)

        from .compat_state import CompatStateStore
        from .legacy_import import LegacyImportError, LegacyImporter

        if not resolved.database_path.exists() and resolved.legacy_state_path.exists():
            runner = CommandRunner(logger)
            try:
                LegacyImporter(resolved, runner).import_legacy()
            except LegacyImportError as exc:
                # Repair mode: publish nothing, serve nothing from empty
                # state. The JSON backup stays untouched on disk.
                logger.error("Legacy state import failed: %s", exc)
                return cls(
                    paths=resolved,
                    store=None,
                    logger=logger,
                    repair_reason=(
                        f"Legacy state migration failed and was not published: {exc}"
                    ),
                )

        store = CompatStateStore(resolved)
        return cls(paths=resolved, store=store, logger=logger)

    def runner(self, callback=None) -> CommandRunner:
        return CommandRunner(self.logger, callback)

    def apply_to_state(self, state: dict) -> dict:
        """Derive every path field on a freshly loaded state from this profile."""
        state["app_dir"] = str(self.paths.app_dir)
        state["models_dir"] = str(self.paths.models_dir)
        state["logs_dir"] = str(self.paths.logs_dir)
        return state


def load_state_with_paths(store: StateStore, paths: AppPaths) -> dict:
    """Load state and normalize installation-identity paths onto the profile.

    ``app_dir``/``logs_dir`` are installation identity and always follow the
    composed profile, so a moved installation cannot keep pointing at a dead
    home. ``models_dir`` preserves an explicitly customized location; only an
    untouched default is redirected to the profile.
    """
    from .constants import DEFAULT_MODELS_DIR
    from .state import DEFAULT_STATE

    state = store.load()
    state["app_dir"] = str(paths.app_dir)
    state["logs_dir"] = str(paths.logs_dir)

    persisted_models = state.get("models_dir")
    untouched_default = not persisted_models or (
        str(Path(str(persisted_models)).expanduser())
        == str(Path(DEFAULT_STATE["models_dir"]).expanduser())
        or str(persisted_models) == str(DEFAULT_MODELS_DIR)
    )
    if untouched_default:
        state["models_dir"] = str(paths.models_dir)
    return state
