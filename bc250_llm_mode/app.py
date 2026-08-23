"""Application composition root.

Production rule: ``AppPaths`` is constructed once (from the installation
profile or a test temporary directory), validated here, and injected into
every service/frontend. No module may fall back to ``Path.home()`` after
composition.

There is intentionally NO generic store/load/save/transaction on the
composition. The SQLite database is reached only through the unit-of-work
factory, repositories, the query layer, and the typed domain services.
Frontends read disposable snapshots and mutate only through services.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .db import initialize_file
from .logging_utils import CommandRunner, configure_logging
from .paths import AppPaths


class RepairRequired(RuntimeError):
    """Composition needs repair (failed legacy import); services are absent."""


# Settings keys a frontend may narrow-commit during the transition to typed
# view models (dies in Phase C). Anything else requires an owning service,
# which keeps commit_settings_changes from being a whole-state escape hatch.
FRONTEND_COMMIT_KEYS = frozenset({
    "disclaimer_ack", "ack_timestamp", "setup_complete",
    "current_model", "current_ctx", "optimizations",
    "openwebui_installed", "https_sharing_enabled", "boot_policy",
    "model_search_paths", "selected_model", "selected_quant",
    "selected_source", "selected_local_model", "download_dir", "models_dir",
})


@dataclass
class Application:
    """Composition root: paths, unit-of-work, query layer, domain services.

    ``operational`` is False only in repair mode; every frontend entry
    point calls :meth:`require_operational` so half-wired services cannot
    be dereferenced.
    """

    paths: AppPaths
    logger: logging.Logger
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
    operational: bool = False
    repair_reason: str | None = None

    def require_operational(self) -> "Application":
        if not self.operational:
            raise RepairRequired(
                self.repair_reason or "application requires repair"
            )
        return self

    def runner(self, callback=None) -> CommandRunner:
        return CommandRunner(self.logger, callback)

    def read_model(self) -> dict[str, Any]:
        """Assembled frontend read model (disposable draft, never persisted
        wholesale)."""
        return self.query.snapshot().data

    def commit_settings_changes(self, before: dict, after: dict) -> int:
        """Transitional narrow settings commit, restricted to
        ``FRONTEND_COMMIT_KEYS``. One unit of work, one revision bump."""
        from .services import persist_state_diff

        return persist_state_diff(
            self.units, before, after, allowed_keys=FRONTEND_COMMIT_KEYS
        )

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
        print("No supported terminal launcher was found. Run:\\n" + " ".join(command))

    @classmethod
    def compose(cls, paths: AppPaths | None = None) -> "Application":
        """Compose an operational application (or a repair result).

        Flow (R2 exit plan 5.3): validate paths -> enforce directory
        permissions -> configure logging -> one-time legacy import -> open
        and run ordered migrations -> build unit-of-work/query/services ->
        return. No compatibility facade is ever instantiated.
        """
        resolved = paths or AppPaths.for_home()
        resolved.validate()
        resolved.ensure_directories()
        logger = configure_logging(resolved.logs_dir)

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
                    logger=logger,
                    operational=False,
                    repair_reason=(
                        f"Legacy state migration failed and was not published: {exc}"
                    ),
                )

        # Create/open the database with the production permission and
        # integrity contract, then close: services use short-lived
        # per-command connections through the unit-of-work factory.
        initialize_file(resolved.database_path)

        application = cls(paths=resolved, logger=logger, operational=True)
        cls._wire_services(application)
        return application

    @staticmethod
    def _wire_services(application: "Application") -> None:
        from .queries import ApplicationQueryService
        from .services import (
            ComponentLifecycleService,
            HostModeService,
            MaintenanceService,
            ModelActivationService,
            ModelInstallationService,
            OpenWebUIService,
            RuntimeConfigurationService,
            RuntimeController,
            SetupService,
            SharingService,
            ThermalStateService,
        )
        from .unit_of_work import UnitOfWorkFactory

        units = UnitOfWorkFactory(application.paths.database_path)
        application.units = units
        application.query = ApplicationQueryService(units, application.paths)
        application.setup = SetupService(units)
        application.safety = ThermalStateService(units)
        application.runtime_config = RuntimeConfigurationService(
            units,
            app_dir=application.paths.app_dir,
            state_supplier=lambda: application.read_model(),
        )

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

        application.activation = ModelActivationService(
            units, application.runtime_config, _AppController(),
            state_supplier=lambda: application.read_model(),
        )
        application.host_mode = HostModeService(units)
        application.component = ComponentLifecycleService(units)
        application.openwebui = OpenWebUIService(units)
        application.sharing = SharingService(units)
        application.model_install = ModelInstallationService(units)
        application.maintenance = MaintenanceService(units)
