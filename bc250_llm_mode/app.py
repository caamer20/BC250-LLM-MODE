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
    """Everything the CLI/GUI/chat frontends need, built once."""

    paths: AppPaths
    store: StateStore
    logger: logging.Logger

    @classmethod
    def compose(cls, paths: AppPaths | None = None) -> "Application":
        resolved = paths or AppPaths.for_home()
        resolved.validate()
        resolved.ensure_directories()
        logger = configure_logging(resolved.logs_dir)
        store = StateStore(resolved.state_path)
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
