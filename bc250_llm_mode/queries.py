"""Repository-native application snapshots for the frontends.

``ApplicationQueryService`` assembles a complete view from repositories and
the injected ``AppPaths`` — it never wraps a whole-state loader so
that deleting the facade in Session 4 is mechanical.

Contract of :class:`ApplicationSnapshot`:

- ``data`` is a DISPOSABLE frontend draft; it is never persisted wholesale.
- It carries ``revision`` (the durable configuration revision it came from).
- Commands take explicit fields and an expected revision; after a command,
  discard this snapshot and query a fresh one.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import BUSY_TIMEOUT_MS
from .paths import AppPaths
from .repositories import (
    AutotuneHistoryRepository,
    BenchHistoryRepository,
    ComponentProvenanceRepository,
    ExtrasRepository,
    KnownGoodRuntimeRepository,
    ModelInstallationsRepository,
    RuntimeConfigRepository,
    SettingsRepository,
    ThermalStateRepository,
)
from .unit_of_work import UnitOfWorkFactory


@dataclass
class ApplicationSnapshot:
    """Disposable frontend draft. Never persisted wholesale."""

    data: dict[str, Any]
    revision: int

    def __getitem__(self, key):
        return self.data[key]

    def get(self, key, default=None):
        return self.data.get(key, default)


class ApplicationQueryService:
    """Assembles application state from repositories + AppPaths."""

    def __init__(self, units: UnitOfWorkFactory, paths: AppPaths) -> None:
        self.units = units
        self.paths = paths

    def snapshot(self) -> ApplicationSnapshot:
        from .state import DEFAULT_STATE, _current_boot_id

        with self._connect() as conn:
            settings = SettingsRepository(conn)
            values = settings.all()
            revision = int(values.pop("__revision", 0))
            models_custom = values.pop("models_dir_custom", None)
            llamacpp_history = values.pop("llamacpp_history", None) or []
            models = ModelInstallationsRepository(conn).list()
            bench = BenchHistoryRepository(conn).list()
            autotune = AutotuneHistoryRepository(conn).list()
            thermal = ThermalStateRepository(conn).get()
            known_good = KnownGoodRuntimeRepository(conn).get()
            runtime = RuntimeConfigRepository(conn).get()
            from .runtime_builds import (
                RuntimeBuildRepository,
                RuntimeComponentRepository,
            )

            component_row = RuntimeComponentRepository(conn).current()
            builds = RuntimeBuildRepository(conn)
            # Quarantined unknown keys are support evidence, never projected
            # to frontends as configuration (§5.5).
            from .repositories import ObservationRepository

            observations = ObservationRepository(conn).list()

        state = deepcopy(DEFAULT_STATE)
        state.update(values)

        # Transient observations: values are surfaced, but each is labeled
        # with its observation timestamp and stale marker so frontends and
        # services know a re-probe is required before trusting them.
        staleness: dict[str, dict[str, Any]] = {}
        for key, record in observations.items():
            state[key] = record["value"]
            staleness[key] = {
                "at": record["observed_at"],
                "stale": record["stale"],
            }
        if staleness:
            state["observation_staleness"] = staleness

        if runtime:
            if runtime.get("context"):
                state["current_ctx"] = runtime["context"]
            if runtime.get("slots"):
                state.setdefault("optimizations", {})["parallel_slots"] = runtime["slots"]
            if runtime.get("model_alias"):
                state["current_model"] = runtime["model_alias"]

        state["installed_models"] = models
        state["bench_history"] = bench
        state["autotune_history"] = autotune
        state["thermal_watchdog_state"] = thermal["latch_state"]
        state["thermal_watchdog_baseline"] = thermal["baseline"]
        if known_good:
            state["known_good_runtime"] = known_good
        if component_row and component_row.get("promoted_build_id"):
            try:
                promoted = builds.require(component_row["promoted_build_id"])
                state["llamacpp_build"] = {
                    "describe":
                        promoted.get("requested_ref")
                        or promoted["build_id"].rsplit(":", 1)[-1][:12],
                    "commit": promoted.get("source_commit"),
                    "build_id": promoted["build_id"],
                    "generation": component_row.get("generation"),
                    "provenance_class": promoted.get("provenance_class"),
                }
            except Exception:  # noqa: BLE001 - snapshot stays best-effort
                pass
        if llamacpp_history:
            state["llamacpp_history"] = llamacpp_history
        if models_custom:
            state["models_dir"] = models_custom
        elif (
            not state.get("models_dir")
            or str(state["models_dir"]) == str(DEFAULT_STATE.get("models_dir"))
        ):
            # An untouched default follows the composed profile; only an
            # explicitly customized location is preserved verbatim.
            state["models_dir"] = str(self.paths.models_dir)

        boot_id = _current_boot_id()
        session_boot = state.get("llm_session_boot_id")
        if (
            state.get("boot_policy") == "desktop"
            and state.get("system_mode") == "llm-session"
            and session_boot
            and boot_id
            and session_boot != boot_id
        ):
            state.update(
                system_mode="desktop",
                llm_mode_done=False,
                llm_session_boot_id=None,
                desktop_reboot_pending=False,
            )

        state["app_dir"] = str(self.paths.app_dir)
        state["logs_dir"] = str(self.paths.logs_dir)
        if not state.get("models_dir"):
            state["models_dir"] = str(self.paths.models_dir)
        state["state_path"] = str(self.paths.database_path)
        state["schema_version"] = 5
        state["revision"] = revision
        return ApplicationSnapshot(state, revision=revision)

    def health(self) -> dict[str, Any]:
        snap = self.snapshot()
        return {
            "revision": snap.revision,
            "model": snap.get("current_model"),
            "context": snap.get("current_ctx"),
            "setup_complete": bool(snap.get("setup_complete")),
            "env_ready": bool(snap.get("env_ready")),
            "thermal_latch": snap.get("thermal_watchdog_state"),
            "recovery_required": bool(snap.get("recovery_required")),
        }

    def _connect(self):
        # Read-only unit: busy timeout carries contention; no write txn.
        return self.units.read()


def open_query_service(database_path: Path, paths: AppPaths) -> ApplicationQueryService:
    return ApplicationQueryService(UnitOfWorkFactory(database_path), paths)


_ = BUSY_TIMEOUT_MS  # imported for parity with db contract docs