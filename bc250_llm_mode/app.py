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
from .services import RuntimeController
from .paths import AppPaths
from .state import StateStore


@dataclass
class Application:
    """Composition root: paths, query layer, and every domain service.

    Frontends (GUI/CLI/chat) receive THIS object — never the compatibility
    store. The store stays private during Session 3 and disappears in
    Session 4.
    """

    paths: AppPaths
    store: Any
    logger: Any
    units: Any = None
    query: Any = None
    setup: Any = None
    safety: Any = None
    runtime_config: Any = None
    activation: Any = None
    host_mode: Any = None
    component: Any = None
    openwebui: Any = None
    sharing: Any = None
    model_install: Any = None
    maintenance: Any = None
    repair_reason: str | None = None

    @property
    def operational(self) -> bool:
        return self.store is not None

    def runner(self):
        from .logging_utils import CommandRunner

        return CommandRunner(self.logger)

    def persist_state_changes(self, before: dict, after: dict) -> int:
        from .services import persist_state_diff

        return persist_state_diff(self.units, before, after)

    def open_chat_terminal(self) -> None:
        """Host adapter: launch a terminal running the chat REPL."""
        import shutil
        import subprocess
        import sys

        command = [sys.executable, "-m", "bc250_llm_mode", "chat"]
        terminals = (
            ("konsole", ["konsole", "-e", *command]),
            ("gnome-terminal", ["gnome-terminal", "--", *command]),
            ("x-terminal-emulator", ["x-terminal-emulator", "-e", *command]),
        )
        for executable, argv in terminals:
            if shutil.which(executable):
                subprocess.Popen(argv)
                return

    @classmethod
    def wrap(cls, store) -> "Application":
        """Transitional helper for legacy stores (tests / --state mode)."""
        database_path = getattr(store, "path", None)
        app_dir = database_path.parent if database_path else Path.cwd()
        paths = AppPaths.from_app_dir(app_dir)
        application = cls.__new__(cls)
        application.paths = paths
        application.store = store
        application.logger = logging.getLogger("bc250.wrap")
        application.repair_reason = None
        cls._wire_services(application)
        return application

    @staticmethod
    def _wire_services(application) -> None:
        from .queries import ApplicationQueryService
        from .services import (
            HostModeService,
            MaintenanceService,
            ModelInstallationService,
            OpenWebUIService,
            SetupService,
            SharingService,
        )
        from .unit_of_work import UnitOfWorkFactory

        database_path = getattr(
            getattr(application, "paths", None), "database_path", None
        )
        sqlite_backed = (
            database_path is not None and Path(database_path).exists()
        )
        application.units = UnitOfWorkFactory(database_path) if sqlite_backed else None
        units = application.units

        application.query = (
            ApplicationQueryService(units, application.paths)
            if units is not None else None
        )
        application.setup = SetupService(units) if units is not None else None

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
        application = cls.__new__(cls)
        application.paths = resolved
        application.store = store
        application.logger = logger
        application.repair_reason = None
        cls._wire_services(application)

        from .queries import ApplicationQueryService  # noqa: F401
        from .services import (
            ComponentLifecycleService,
            HostModeService,
            MaintenanceService,
            ModelActivationService,
            ModelInstallationService,
            OpenWebUIService,
            RuntimeConfigurationService,
            SharingService,
            ThermalStateService,
        )
        from .unit_of_work import UnitOfWorkFactory

        units = UnitOfWorkFactory(resolved.database_path)
        application.units = units
        application.query = ApplicationQueryService(units, resolved)

        class _AppController(RuntimeController):
            def restart(self, view):
                from .server import restart_service

                restart_service(view, application.runner())

            def health_check(self, view):
                from .server import health_check

                return health_check(view, application.runner())

            def minimal_inference_probe(self, view):
                from .server import minimal_inference_probe

                return minimal_inference_probe(view)

        controller = _AppController()
        application.activation = ModelActivationService(
            units, application.runtime_config, controller,
            state_supplier=lambda: application.store.load(),
        )
        application.host_mode = HostModeService(units)
        application.component = ComponentLifecycleService(units)
        application.openwebui = OpenWebUIService(units)
        application.sharing = SharingService(units)
        application.model_install = ModelInstallationService(units)
        application.maintenance = MaintenanceService(units)
        return application

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
