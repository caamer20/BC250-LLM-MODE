"""Temporary compatibility state store backed by SQLite (ADR 001, step 6).

Exposes the exact ``StateStore`` interface (``load``/``save``/``transaction``)
so unmigrated GUI/CLI/chat call sites keep working after the composition root
switches to SQLite. Derived paths are recomputed from the injected
``AppPaths`` and never persisted; saves use optimistic revision checks.

Removal criteria: this module is deleted once no production module performs
whole-state saves (guard test drives the count toward zero).
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any

from .db import connect, initialize
from .legacy_import import utcnow
from .paths import AppPaths
from .repositories import (
    AutotuneHistoryRepository,
    BenchHistoryRepository,
    ComponentProvenanceRepository,
    ExtrasRepository,
    ModelInstallationsRepository,
    RuntimeConfigRepository,
    SettingsRepository,
    ThermalStateRepository,
)

# Keys handled by dedicated tables or recomputed from AppPaths. Everything
# else round-trips through `settings` for lossless compatibility.
TYPED_KEYS = frozenset({
    "installed_models", "bench_history", "autotune_history",
    "thermal_watchdog_state", "thermal_watchdog_baseline", "llamacpp_build",
})
DERIVED_KEYS = frozenset({"app_dir", "logs_dir", "state_path"})
RUNTIME_CONFIG_KEYS = frozenset({"current_ctx", "current_model"})


class StaleStateError(RuntimeError):
    """The state changed externally since it was last read."""


class CompatStateStore:
    """SQLite-backed drop-in replacement for StateStore during migration."""

    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths
        self.paths.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = connect(paths.database_path)
        initialize(self.conn)
        self.settings = SettingsRepository(self.conn)
        self.runtime = RuntimeConfigRepository(self.conn)
        self.models = ModelInstallationsRepository(self.conn)
        self.bench = BenchHistoryRepository(self.conn)
        self.autotune = AutotuneHistoryRepository(self.conn)
        self.thermal = ThermalStateRepository(self.conn)
        self.provenance = ComponentProvenanceRepository(self.conn)
        self.extras = ExtrasRepository(self.conn)
        self._known_revision = self.settings.revision()

    @property
    def path(self) -> Path:
        return self.paths.database_path

    @property
    def lock_path(self) -> Path:
        return self.paths.database_path.with_suffix(".txn-lock")

    def load(self) -> dict[str, Any]:
        from copy import deepcopy

        from .state import DEFAULT_STATE, _current_boot_id

        state = deepcopy(DEFAULT_STATE)
        settings = self.settings.all()
        revision = int(settings.pop(SettingsRepository.REVISION_KEY, 0))

        models_custom = settings.pop("models_dir_custom", None)
        llamacpp_history = settings.pop("llamacpp_history", None)
        for key, value in settings.items():
            state[key] = value

        runtime = self.runtime.get()
        if runtime:
            if runtime.get("context"):
                state["current_ctx"] = runtime["context"]
            if runtime.get("slots"):
                state.setdefault("optimizations", {})["parallel_slots"] = runtime["slots"]
            if runtime.get("model_alias"):
                state["current_model"] = runtime["model_alias"]

        state["installed_models"] = self.models.list()
        state["bench_history"] = self.bench.list()
        state["autotune_history"] = self.autotune.list()

        thermal = self.thermal.get()
        state["thermal_watchdog_state"] = thermal["latch_state"]
        state["thermal_watchdog_baseline"] = thermal["baseline"]

        build = self.provenance.get_component("llamacpp")
        if build:
            state["llamacpp_build"] = {
                "describe": build["describe"],
                "commit": build["commit_sha"],
                "recorded": build["recorded"],
            }
        if llamacpp_history is not None:
            state["llamacpp_history"] = llamacpp_history

        for key, value in self.extras.all().items():
            state[key] = value

        if models_custom:
            state["models_dir"] = models_custom

        # Installation-identity paths always come from the composed profile.
        state["app_dir"] = str(self.paths.app_dir)
        state["logs_dir"] = str(self.paths.logs_dir)
        state["models_dir"] = state.get("models_dir") or str(self.paths.models_dir)
        state["state_path"] = str(self.paths.database_path)

        state["schema_version"] = 5
        state["revision"] = revision
        self._known_revision = revision
        self._reconcile_boot_observation(state)
        return state

    def _reconcile_boot_observation(self, state: dict[str, Any]) -> None:
        from .state import _current_boot_id

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

    def save(self, state: dict[str, Any]) -> None:
        """Optimistic whole-state write into typed storage + handoff refresh."""
        current = self.settings.revision()
        known = getattr(self, "_known_revision", current)
        if known != current:
            raise StaleStateError(
                f"State changed externally; reload before saving "
                f"(known revision {known}, current {current})."
            )
        self._write_state(state)
        self._known_revision = current + 1

    def _write_state(self, state: dict[str, Any]) -> None:
        managed: dict[str, Any] = {}
        for key, value in state.items():
            if key in TYPED_KEYS or key in DERIVED_KEYS or key in RUNTIME_CONFIG_KEYS:
                continue
            if key.startswith("__") or key == "revision":
                continue
            managed[key] = value
        if state.get("models_dir") and str(state["models_dir"]) != str(
            self.paths.models_dir
        ):
            managed["models_dir_custom"] = state["models_dir"]
        managed["llamacpp_history"] = state.get("llamacpp_history") or []

        self.settings.set_many(managed)
        self.runtime.update(
            model_alias=state.get("current_model"),
            context=int(state.get("current_ctx", 8192)),
            slots=int((state.get("optimizations") or {}).get("parallel_slots", 4)),
        )
        self.models.replace_all(state.get("installed_models") or [])
        self.bench.replace_all(state.get("bench_history") or [])
        self.autotune.replace_all(state.get("autotune_history") or [])
        self.thermal.set(
            str(state.get("thermal_watchdog_state") or "nominal"),
            state.get("thermal_watchdog_baseline"),
        )
        build = state.get("llamacpp_build")
        if isinstance(build, dict) and build.get("describe"):
            self.provenance.set_component(
                "llamacpp", str(build.get("describe")), build.get("commit")
            )
        new_revision = self.settings.revision() + 1
        self.settings.set_revision(new_revision)
        self._known_revision = new_revision
        self.conn.commit()
        self._write_runtime_handoff(state)

    def transaction(self, mutator):
        """Locked read-modify-write (lost-update safe) with revision bump."""
        import fcntl

        lock = self.lock_path
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "w", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                state = self.load()
                result = mutator(state)
                if result is None:
                    return state
                self.save(state)
                return self.load()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _write_runtime_handoff(self, state: dict[str, Any]) -> None:
        """Render the launcher-consumption snapshot (regenerated every save).

        This is a rendered artifact of committed state — the server reads it
        at daemon start — not authoritative dual-write state.
        """
        opts = state.get("optimizations") or {}
        effective = opts if opts.get("runtime_enabled", True) else {}
        current_id = state.get("current_model")
        model_path = ""
        alias = str(current_id or "local")
        sampling = {}
        for record in state.get("installed_models") or []:
            if record.get("id") == current_id:
                model_path = str(record.get("path", ""))
                alias = str(
                    record.get("display_name") or alias
                ).replace("\n", " ").strip()
                sampling = {
                    k: record[k]
                    for k in ("temperature", "top_p", "top_k", "min_p")
                    if k in record
                }
                break
        slots = int(effective.get("parallel_slots", 4))
        payload = {
            "llama_cpp_path": str(state.get("llama_cpp_path", "/root/llama.cpp")),
            "model_path": model_path,
            "alias": alias,
            "ctx_total": int(state.get("current_ctx", 8192)) * slots,
            "port": int(state.get("server_port", 8080)),
            "flash_attention": effective.get("flash_attention", "auto"),
            "batch_size": int(effective.get("batch_size", 2048)),
            "ubatch_size": int(effective.get("ubatch_size", 512)),
            "kv_cache_type": effective.get("kv_cache_type", "q8_0"),
            "parallel_slots": slots,
            "threads": effective.get("threads"),
            "fast_sync": bool(effective.get("fast_sync")),
        }
        payload.update(sampling)
        handoff = self.paths.app_dir / "runtime-handoff.json"
        tmp = handoff.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, handoff)
        os.chmod(handoff, 0o600)