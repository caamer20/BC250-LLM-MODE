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
    maintenance_snapshot: Any = None
    maintenance_checks: Any = None
    operation_query: Any = None
    operation_commands: Any = None
    gateway: Any = None
    connections: Any = None
    connection_credentials: Any = None
    connection_probes: Any = None
    workload_profiles: Any = None
    workload_profile_commands: Any = None
    performance_coach: Any = None
    calibration: Any = None
    idle_policy: Any = None
    notification_preferences: Any = None
    notifications: Any = None
    operation_notifications: Any = None
    thermal_notifications: Any = None
    maintenance_notifications: Any = None
    repair_commands: Any = None
    home: Any = None
    doctor: Any = None
    support_bundle: Any = None
    model_library: Any = None
    storage_capacity: Any = None
    storage_cleanup: Any = None
    undo: Any = None
    application_update: Any = None
    application_update_store: Any = None
    application_update_commands: Any = None
    application_update_host: Any = None
    platform: Any = None
    desktop_integration: Any = None
    optimizations: Any = None
    model_server: Any = None
    tailscale: Any = None
    logs: Any = None
    chat_sessions: Any = None
    conversations: Any = None
    chat_observation: Any = None
    preferences: Any = None
    privacy: Any = None
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
            UserPreferencesService,
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
        # EXP-6 begins fail-closed: status/check/preview share one typed query
        # service, but production has no signing root or eligible channel yet.
        # Merely composing the app never contacts a network endpoint.
        import shutil as _shutil

        from . import __version__ as _application_version
        from .application_update import (
            ApplicationReleaseVerifier,
            ApplicationUpdateQueryService,
            EXPECTED_RELEASE_REPOSITORY,
            InstalledApplication,
        )
        from .application_bundle import OfflineApplicationBundleStore
        from .application_installation import (
            ApplicationInstallationRepository,
            ApplicationInstallationStateRepository,
        )
        from .db import SCHEMA_VERSION as _DATABASE_SCHEMA_VERSION

        _update_verifier = ApplicationReleaseVerifier(
            expected_repository=EXPECTED_RELEASE_REPOSITORY
        )

        def _installed_application():
            with units.read() as conn:
                installation_state = ApplicationInstallationStateRepository(conn).get()
                installation = (
                    ApplicationInstallationRepository(conn).get(
                        installation_state.current_installation_id
                    ) if installation_state.current_installation_id else None
                )
            return InstalledApplication(
                version=(installation.version if installation else _application_version),
                source_commit=(installation.source_commit if installation else None),
                release_set_digest=(
                    installation.release_set_digest if installation else None
                ),
                slot_id=(
                    installation.release_directory_identity if installation else None
                ),
                database_schema=_DATABASE_SCHEMA_VERSION,
                channel_id="verified-offline",
                current_installation_id=installation_state.current_installation_id,
                previous_installation_id=installation_state.previous_installation_id,
                pointer_generation=installation_state.pointer_generation,
                pending_operation_id=installation_state.pending_update_operation_id,
                recovery_barrier=installation_state.recovery_barrier,
                revision=installation_state.revision,
            )

        application.application_update_store = OfflineApplicationBundleStore(
            application.paths,
            units,
            _update_verifier,
            platform_profile=application.platform.profile.family.value,
            installed_schema=lambda: _DATABASE_SCHEMA_VERSION,
        )
        application.application_update = ApplicationUpdateQueryService(
            installed=_installed_application,
            verifier=_update_verifier,
            platform_profile=application.platform.profile.family.value,
            channel=application.application_update_store,
            free_bytes=lambda: _shutil.disk_usage(application.paths.app_dir).free,
        )
        from .maintenance_center import MaintenanceCenterQueryService

        application.maintenance_snapshot = MaintenanceCenterQueryService(
            units,
            application.paths.models_dir,
            platform=application.platform,
        )
        from .notifications import (
            DesktopNotificationAdapter,
            NotificationCoordinator,
            NotificationPreferenceService,
        )

        notification_adapter = DesktopNotificationAdapter()
        application.notification_preferences = NotificationPreferenceService(units)
        application.notifications = NotificationCoordinator(
            units, adapter=notification_adapter
        )
        from .notification_producers import (
            MaintenanceNotificationProducer,
            OperationNotificationProducer,
            ThermalNotificationProducer,
        )

        application.operation_notifications = OperationNotificationProducer(
            units, application.notifications
        )
        application.thermal_notifications = ThermalNotificationProducer(
            application.notifications
        )
        application.maintenance_notifications = MaintenanceNotificationProducer(
            application.notifications
        )
        # P5 §11.3 + EXP-4: the redacted-by-construction support bundle
        # consumes the same home/doctor/notification status as every UI.
        from .support_bundle import SupportBundleService

        application.support_bundle = SupportBundleService(
            units, application.paths,
            home=application.home, doctor=application.doctor,
            platform=application.platform,
            notifications=application.notifications,
        )
        from .privacy_center import PrivacyCenterQueryService

        application.privacy = PrivacyCenterQueryService(application.paths)
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
        from .workload_profiles import WorkloadProfileQueryService

        application.workload_profiles = WorkloadProfileQueryService(units)
        from .performance_coach import PerformanceCoachService

        application.performance_coach = PerformanceCoachService(
            units, profiles=application.workload_profiles
        )
        # GUI-6: terminal and native chat share one bounded transport,
        # conversation store, and readiness observation policy.
        from .chat_service import ChatObservationService, ChatSessionService
        from .conversation_service import ConversationService

        def _chat_live_server(state):
            from .server import local_server_readiness

            return local_server_readiness(state)

        def _chat_thermal_ok() -> bool:
            try:
                snapshot = application.home.snapshot().to_dict()
                thermal = (snapshot.get("cards") or {}).get("thermal") or {}
                health = thermal.get("health") or {}
                status = str(
                    health.get("effective_state") or health.get("state") or "UNVERIFIED"
                )
                return status not in {
                    "BLOCKED", "RECOVERY_REQUIRED", "REPAIR_REQUIRED"
                }
            except Exception:
                return False

        application.chat_sessions = ChatSessionService(
            thermal_ok=_chat_thermal_ok,
            active_model=lambda: (
                application.runtime_config.current().get("model_alias")
                or application.read_model().get("current_model")
            ),
        )
        application.conversations = ConversationService(
            application.paths.conversations_dir
        )
        application.chat_observation = ChatObservationService(
            state_supplier=lambda: application.read_model(),
            home=application.home,
            runtime=application.runtime_config,
            live_server=_chat_live_server,
        )

        # ONE durable activation path (Session 5C): adapter -> workflow ->
        # engine, composed once; composition starts no worker or host call.
        activation_adapter = ActivationHostAdapter(
            units=units,
            runtime=application.runtime_config,
            renderer=RuntimeHandoffRenderer(application.paths.app_dir),
            state_supplier=lambda: application.read_model(),
            runner_factory=lambda: application.runner(),
            profile_query=application.workload_profiles,
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
                        (
                            e for e in catalog_module.ADVERTISED_CATALOG
                            if e.id == model_id
                        ),
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

        # EXP-3: durable, fixed-prompt calibration owns both runtime-active
        # and runtime-benchmark. It checkpoints every candidate and restores
        # the exact baseline before proposing (never applying) a winner.
        from .calibration_adapter import CalibrationHostAdapter
        from .operations.calibration import build_calibration_workflow

        calibration_adapter = CalibrationHostAdapter(
            units=units,
            profiles=application.workload_profiles,
            runtime=application.runtime_config,
            app_dir=application.paths.app_dir,
            state_supplier=lambda: application.read_model(),
            runner_factory=lambda: application.runner(),
            clock=utcnow,
        )
        registry.register(build_calibration_workflow(calibration_adapter))

        # EXP-5 / ADR 012: durable quarantine, exact restore, and expired
        # purge share the ONE registry and the model-storage lease.
        from .operations.storage_cleanup import build_storage_cleanup_workflow
        from .storage_cleanup_adapter import StorageCleanupHostAdapter

        cleanup_adapter = StorageCleanupHostAdapter(
            units, application.paths, clock=utcnow
        )
        registry.register(build_storage_cleanup_workflow(cleanup_adapter))

        # EXP-6 / ADR 013: durable self-update is registered now so any future
        # row has an exact recovery definition. Production release trust and
        # post-update execution remain fail-closed until the next boundary
        # composes a verified bundle source and command surface.
        from .application_bundle import OfflineReleaseResolver
        from .application_post_update import ApplicationPostUpdateProcessPort
        from .application_update_adapter import ApplicationUpdateHostAdapter
        from .operations.application_update import build_application_update_workflow

        application.application_update_host = ApplicationUpdateHostAdapter(
            units,
            application.paths,
            resolver=OfflineReleaseResolver(application.application_update_store),
            backup_supplier=lambda: getattr(application, "backup", None),
            post_update=ApplicationPostUpdateProcessPort(
                application.paths,
                units,
                backup_supplier=lambda: getattr(application, "backup", None),
            ),
        )
        registry.register(build_application_update_workflow(
            application.application_update_host
        ))

        # ONE freeze point for ALL durable workflows; exactly one
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
                terminal_observer=application.operation_notifications.after_execution,
            )

        application.activation = ActivationCommandService(
            units=units, enqueue=enqueue, engine_factory=engine_factory
        )
        from .workload_profiles import WorkloadProfileCommandService

        application.workload_profile_commands = WorkloadProfileCommandService(
            units,
            query=application.workload_profiles,
            activation=application.activation,
            id_provider=lambda: _uuid.uuid4().hex,
        )
        from .calibration_command import CalibrationCommandService

        application.calibration = CalibrationCommandService(
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
        from .storage_cleanup_command import StorageCleanupCommandService

        application.storage_cleanup = StorageCleanupCommandService(
            units=units,
            enqueue=enqueue,
            engine_factory=engine_factory,
            adapter=cleanup_adapter,
            clock=utcnow,
        )
        from .undo import UndoService

        application.undo = UndoService(
            units=units,
            cleanup=application.storage_cleanup,
            adapter=cleanup_adapter,
            clock=utcnow,
        )
        from .application_update_command import ApplicationUpdateCommandService

        application.application_update_commands = ApplicationUpdateCommandService(
            units=units,
            query=application.application_update,
            store=application.application_update_store,
            enqueue=enqueue,
            engine_factory=engine_factory,
            now=utcnow,
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
        from .connection_setup import ConnectionCredentialCommandService

        application.gateway = GatewayCredentialService(
            units, application.paths.app_dir
        )
        application.connection_credentials = ConnectionCredentialCommandService(
            units, application.paths.app_dir
        )
        application.openwebui = OpenWebUIService(
            units, gateway=application.gateway,
            connection_credentials=application.connection_credentials)
        application.sharing = SharingService(
            units, gateway=application.gateway,
            connection_credentials=application.connection_credentials)
        application.preferences = UserPreferencesService(units)
        application.maintenance = MaintenanceService(units)
        from .optimization_service import OptimizationService

        application.optimizations = OptimizationService(units)
        from .log_tail import LogTailService
        from .system_services import ModelServerService, TailscaleService

        application.model_server = ModelServerService(units)
        application.tailscale = TailscaleService(units)
        application.logs = LogTailService(application.paths)
        from .idle_policy import IdlePolicyService

        application.idle_policy = IdlePolicyService(
            units,
            server_active=lambda: bool(
                application.model_server.status(
                    application.read_model(), application.runner()
                ).get("active")
            ),
            stop_server=lambda: application.model_server.stop(
                application.read_model(), application.runner()
            ),
            now=utcnow,
        )
        # EXP-2: one multi-client command boundary, one bounded probe service,
        # and one mutation-free connection snapshot shared by GUI and CLI.
        from .connection_setup import (
            ConnectionProbeService,
            ConnectionSetupQueryService,
        )

        application.connection_probes = ConnectionProbeService(
            units, application.connection_credentials
        )
        application.connections = ConnectionSetupQueryService(
            units,
            application.connection_credentials,
            state_supplier=lambda: application.read_model(),
            runner_factory=lambda: application.runner(),
            model_server=application.model_server,
            openwebui=application.openwebui,
            tailscale=application.tailscale,
            sharing=application.sharing,
        )
        from .appliance_readiness import ApplianceReadinessQueryService

        application.readiness = ApplianceReadinessQueryService(
            home=application.home,
            connections=application.connections,
        )
        application.doctor.attach_readiness_source(application.readiness)
        application.support_bundle.attach_readiness_source(application.readiness)
        from .maintenance_checks import MaintenanceCheckService
        from .thermals import read_gpu_temperature

        def _maintenance_services() -> dict[str, bool]:
            state = application.read_model()
            runner = application.runner()

            def active(call) -> bool:
                try:
                    value = call()
                except Exception:
                    return False
                if not isinstance(value, dict):
                    return False
                return bool(
                    value.get("active")
                    or value.get("running")
                    or value.get("connected")
                    or value.get("enabled")
                )

            return {
                "model_server": active(
                    lambda: application.model_server.status(state, runner)
                ),
                "openwebui": active(
                    lambda: application.openwebui.status(state, runner)
                ),
                "tailscale": active(lambda: application.tailscale.status(runner)),
                "sharing": active(
                    lambda: application.sharing.status(state, runner)
                ),
            }

        application.maintenance_checks = MaintenanceCheckService(
            units,
            snapshot=application.maintenance_snapshot,
            doctor=application.doctor,
            storage=application.storage_capacity,
            connection_observer=application.connections.snapshot,
            services_observer=_maintenance_services,
            thermal_reader=read_gpu_temperature,
            notification_observer=application.maintenance_notifications.after_check,
        )
        from .repair_adapter import build_application_repair_bindings
        from .repair_commands import RepairCommandService

        application.repair_commands = RepairCommandService(
            build_application_repair_bindings(application), clock=utcnow
        )
