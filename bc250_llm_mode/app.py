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

from .db import initialize_and_close
from .logging_utils import CommandRunner, configure_logging
from .paths import AppPaths


class RepairRequired(RuntimeError):
    """Composition needs repair (failed legacy import); services are absent."""


# Settings keys a frontend may narrow-commit during the transition to typed
# view models (dies in R4). Anything else requires an owning service, which
# keeps commit_settings_changes from being a whole-state escape hatch.
#
# Frozen call-site census (Session 4.1 §3.6) — each remaining site owns
# persistence because its command calls module functions, not services:
#   __main__.py: 1  (llm start/stop/restart/ensure)
#   chat.py:     4  (/llm /webui /serve /boot desktop)
#   gui/app.py:  1  (commit_narrow, the GUI's sole persistence primitive)
# Services that commit internally (activation, host-mode, component,
# openwebui, sharing, model-install) MUST NOT get a caller-side commit.
FRONTEND_COMMIT_KEYS = frozenset({
    "disclaimer_ack", "ack_timestamp", "setup_complete",
    "openwebui_installed", "https_sharing_enabled", "boot_policy",
    "model_search_paths", "selected_model", "selected_quant",
    "selected_source", "selected_local_model", "installed_alias",
    "models_dir",
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
    model_acquisition: Any = None
    runtime_lifecycle: Any = None
    model_remove: Any = None
    model_convert: Any = None
    maintenance: Any = None
    operation_query: Any = None
    operation_commands: Any = None
    gateway: Any = None
    home: Any = None
    doctor: Any = None
    support_bundle: Any = None
    model_library: Any = None
    storage_capacity: Any = None
    platform: Any = None
    desktop_integration: Any = None
    optimizations: Any = None
    model_server: Any = None
    tailscale: Any = None
    logs: Any = None
    operational: bool = False
    repair_reason: str | None = None

    def require_operational(self) -> "Application":
        if not self.operational:
            raise RepairRequired(
                self.repair_reason or "application requires repair"
            )
        return self

    def hub_client(self):
        """Typed hub HTTP client bound to the immutable HF origin (U1.1)."""
        from .hub_source import HubClient

        return HubClient()

    def record_benchmark(self, entry: dict) -> None:
        """Durable benchmark append; frontends never touch repositories."""
        from .repositories import BenchHistoryRepository

        with self.units.begin() as conn:
            BenchHistoryRepository(conn).append(entry, commit=False)

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

        # Host detection is read-only and disposable. Distribution and boot
        # observations are never persisted as durable truth; every process
        # recomputes them before any host mutation is offered.
        from .host_platform import HostPlatformService

        platform = HostPlatformService.detect()

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
                    platform=platform,
                    operational=False,
                    repair_reason=(
                        f"Legacy state migration failed and was not published: {exc}"
                    ),
                )

        # Create/open the database under the production permission and
        # integrity contract, then close deterministically: composition owns
        # no connection; services use short-lived per-command units of work.
        initialize_and_close(resolved.database_path)

        application = cls(
            paths=resolved, logger=logger, platform=platform, operational=True
        )
        cls._wire_services(application)
        return application

    @staticmethod
    def _wire_services(application: "Application") -> None:
        import uuid as _uuid

        from .activation_adapter import ActivationHostAdapter
        from .activation_command import ActivationCommandService
        from .acquisition_adapter import AcquisitionHostAdapter
        from .acquisition_command import ModelAcquisitionCommandService
        from .hub_source import catalog_fingerprint
        from .operations.activation import build_activation_workflow
        from .operations.acquisition import (
            build_acquire_workflow,
            build_import_workflow,
        )
        from .operations.engine import ExecutionEngine
        from .operations.runtime_lifecycle import (
            build_runtime_rollback_workflow,
            build_runtime_update_workflow,
        )
        from .operations.workflow import EnqueueService, WorkflowRegistry
        from .queries import ApplicationQueryService
        from .legacy_import import utcnow
        from .runtime_handoff import RuntimeHandoffRenderer
        from .runtime_lifecycle_adapter import (
            RuntimeLifecycleHostAdapter,
            RuntimeLocations,
        )
        from .runtime_lifecycle_command import RuntimeLifecycleCommandService
        from .runtime_process import RuntimeProcessRunner
        from .services import (
            ComponentLifecycleService,
            HostModeService,
            MaintenanceService,
            OpenWebUIService,
            RuntimeConfigurationService,
            SetupService,
            SharingService,
            ThermalStateService,
        )
        from .unit_of_work import UnitOfWorkFactory

        units = UnitOfWorkFactory(application.paths.database_path)
        application.units = units
        application.query = ApplicationQueryService(units, application.paths)
        # P5 §11.1: the unified, query-only appliance home snapshot.
        from .home import HomeQueryService

        application.home = HomeQueryService(
            units, application.paths, platform=application.platform
        )
        # P5 §11.3: the read-only doctor (stable findings, no mutations).
        from .doctor import DoctorService

        application.doctor = DoctorService(units, application.paths)
        # P5 §11.3: the redacted-by-construction support bundle. Reuses the
        # SAME composed home/doctor services the CLI/GUI consume so the
        # bundle is consistent with every other surface.
        from .support_bundle import SupportBundleService

        application.support_bundle = SupportBundleService(
            units, application.paths,
            home=application.home, doctor=application.doctor,
            platform=application.platform,
        )
        # P6 §12.1: the Model Library read model (query-only).
        from .model_library import ModelLibraryQueryService

        application.model_library = ModelLibraryQueryService(
            units, application.paths
        )
        # P6 §12.3: capacity/dedup report + ranked cleanup (query-only).
        from .storage_capacity import StorageCapacityService

        application.storage_capacity = StorageCapacityService(
            units, application.paths
        )
        from .desktop_integration import DesktopIntegrationService

        application.desktop_integration = DesktopIntegrationService(
            application.paths
        )
        application.setup = SetupService(units)
        application.safety = ThermalStateService(units)
        application.runtime_config = RuntimeConfigurationService(
            units,
            app_dir=application.paths.app_dir,
            state_supplier=lambda: application.read_model(),
        )

        # ONE durable activation path (Session 5C): adapter -> workflow ->
        # engine, composed once; composition starts no worker or host call.
        activation_adapter = ActivationHostAdapter(
            units=units,
            runtime=application.runtime_config,
            renderer=RuntimeHandoffRenderer(application.paths.app_dir),
            state_supplier=lambda: application.read_model(),
            runner_factory=lambda: application.runner(),
        )
        registry = WorkflowRegistry()
        registry.register(build_activation_workflow(activation_adapter))

        # U1.1 §7.3: catalog lookup + policy fingerprint helpers.
        def _catalog_module():
            from . import catalog as catalog_module

            class _Catalog:
                @staticmethod
                def find(model_id: str):
                    return next(
                        (e for e in catalog_module.CATALOG if e.id == model_id),
                        None,
                    )

            return _Catalog

        # U1.1: the same registry registers acquisition/import.
        catalog = _catalog_module()
        acquisition_adapter = AcquisitionHostAdapter(
            application.paths,
            units,
            hub_client=application.hub_client(),
            clock=utcnow,
            catalog_lookup=lambda model_id: catalog.find(model_id),
        )
        registry.register(build_acquire_workflow(acquisition_adapter))
        registry.register(build_import_workflow(acquisition_adapter))

        def _fingerprint_for(model_id: str, quantization: str) -> str:
            entry = _catalog_module().find(model_id)
            if entry is None:
                return ""
            return catalog_fingerprint(entry, quantization)

        # U1.2 §15.2: ONE durable runtime lifecycle path — the same frozen
        # registry, enqueue service, and engine factory as activation and
        # acquisition, with ONE production adapter.
        class _RuntimeServerPort:
            """Typed server seams; commands stay inside ``server.py``."""

            def capture(self, view):
                from .server import capture_service_state

                return capture_service_state(view, application.runner())

            def restart(self, view):
                from .server import restart_service

                restart_service(view, application.runner())

            def stop(self, view):
                from .server import stop_service

                return stop_service(view, application.runner())

            def health(self, view, *, timeout: int = 120):
                from .server import health_check

                return health_check(view, timeout=timeout)

            def inference(self, view, *, timeout: float = 20.0):
                from .server import minimal_inference_probe

                return minimal_inference_probe(view, timeout=timeout)

        def _runtime_locations() -> RuntimeLocations:
            snapshot = application.read_model()
            active_root = str(
                snapshot.get("llama_cpp_path") or "/root/llama.cpp"
            )
            parent = active_root.rsplit("/", 1)[0] or "/root"
            return RuntimeLocations(
                container_name=str(snapshot.get("container_name") or "llm"),
                active_root=active_root,
                managed_root=f"{active_root}-managed",
                sources_root=f"{active_root}-sources",
                runtime_parent=parent,
            )

        def _thermal_ok() -> bool:
            from .repositories import ThermalStateRepository

            try:
                with units.read() as conn:
                    latch = ThermalStateRepository(conn).get().get(
                        "latch_state"
                    )
                    return latch != "stopped"
            except Exception:  # noqa: BLE001 - doubt is unsafe
                return False

        runtime_adapter = RuntimeLifecycleHostAdapter(
            units=units,
            locations=_runtime_locations(),
            process_runner=RuntimeProcessRunner(),
            server_port=_RuntimeServerPort(),
            renderer=RuntimeHandoffRenderer(application.paths.app_dir),
            state_supplier=lambda: application.read_model(),
            clock=utcnow,
            thermal_supplier=_thermal_ok,
        )
        registry.register(build_runtime_update_workflow(runtime_adapter))
        registry.register(build_runtime_rollback_workflow(runtime_adapter))

        # P6 §12.2: ONE durable model-removal path — the same frozen registry,
        # enqueue service, and engine factory, with ONE production adapter.
        from .model_remove_adapter import ModelRemoveHostAdapter
        from .operations.model_remove import build_remove_workflow

        remove_adapter = ModelRemoveHostAdapter(units, application.paths)
        registry.register(build_remove_workflow(remove_adapter))

        # C2 (ADR 006): ONE durable backup/restore path — the same frozen
        # registry, enqueue service, and engine factory, with ONE production
        # host adapter satisfying both BACKUP_CREATE v1 and BACKUP_RESTORE v1.
        from .backup_adapter import BackupHostAdapter
        from .operations.backup import (
            build_backup_create_workflow,
            build_backup_restore_workflow,
        )

        backup_adapter = BackupHostAdapter(units, application.paths)
        registry.register(build_backup_create_workflow(backup_adapter))
        registry.register(build_backup_restore_workflow(backup_adapter))

        # ONE freeze point for ALL eight durable workflows; exactly one
        # enqueue service and one engine factory are ever constructed.
        frozen_registry = registry.freeze()
        enqueue = EnqueueService(
            units,
            frozen_registry,
            clock=utcnow,
            uuid_factory=lambda: str(_uuid.uuid4()),
        )

        def engine_factory() -> ExecutionEngine:
            return ExecutionEngine(
                units,
                frozen_registry,
                clock=utcnow,
                uuid_factory=lambda: str(_uuid.uuid4()),
                worker_id=f"foreground-{_uuid.uuid4().hex[:12]}",
                lease_ttl_seconds=60,
            )

        application.activation = ActivationCommandService(
            units=units, enqueue=enqueue, engine_factory=engine_factory
        )
        application.model_acquisition = ModelAcquisitionCommandService(
            units=units,
            enqueue=enqueue,
            engine_factory=engine_factory,
            fingerprint_for=_fingerprint_for,
        )
        application.runtime_lifecycle = RuntimeLifecycleCommandService(
            units=units, enqueue=enqueue, engine_factory=engine_factory
        )
        # P6 §12.2: durable model removal (dry-run + fenced remove).
        from .model_remove_command import ModelRemoveCommandService

        application.model_remove = ModelRemoveCommandService(
            units=units, enqueue=enqueue, engine_factory=engine_factory
        )
        # C2 (ADR 006): durable backup create/list/verify + restore inspect/
        # start over the same frozen registry and engine factory.
        from .backup_command import BackupCommandService

        application.backup = BackupCommandService(
            units=units,
            enqueue=enqueue,
            engine_factory=engine_factory,
            adapter=backup_adapter,
        )
        # P6 §12.4: model conversion — honestly unavailable in this build.
        from .model_convert_command import ModelConvertCommandService

        application.model_convert = ModelConvertCommandService(units=units)
        # U1.3: exposed read-only for the explicitly-started worker host;
        # composition itself NEVER constructs or starts one.
        application.registry = frozen_registry

        # U1.4: ONE operation control plane over the same frozen registry —
        # read-only queries plus fenced commands. No new engine graph is
        # created: the command service reuses the shared engine factory.
        from .operations.command_service import OperationCommandService
        from .operations.query_service import OperationQueryService

        application.operation_query = OperationQueryService(
            units, clock=utcnow
        )
        application.operation_commands = OperationCommandService(
            units,
            engine_factory=engine_factory,
            enqueue=enqueue,
            worker_profile_dir=application.paths.app_dir,
        )

        application.host_mode = HostModeService(units, application.platform)
        application.component = ComponentLifecycleService(
            units, application.platform
        )
        # ADR 005 D3: durable gateway credential management. Constructed with
        # the ONE UnitOfWorkFactory + profile dir; persists ONLY the fingerprint
        # in the DB and the secret in a 0600 profile file, never in state/argv.
        from .gateway_command import GatewayCredentialService

        application.gateway = GatewayCredentialService(
            units, application.paths.app_dir
        )
        application.openwebui = OpenWebUIService(units, gateway=application.gateway)
        application.sharing = SharingService(units)
        application.maintenance = MaintenanceService(units)
        from .optimization_service import OptimizationService

        application.optimizations = OptimizationService(units)
        from .log_tail import LogTailService
        from .system_services import ModelServerService, TailscaleService

        application.model_server = ModelServerService(units)
        application.tailscale = TailscaleService(units)
        application.logs = LogTailService(application.paths)
