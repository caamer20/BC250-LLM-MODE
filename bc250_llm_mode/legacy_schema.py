"""Pure legacy state canonicalization (v1 → v5).

Session 4.1 §3.5: the interpretation of pre-SQLite ``state.json`` payloads
lives here as pure functions — defaults plus ordered schema migrations plus
boot reconciliation. No file I/O, no staging JSON, no writable store: the
importer feeds a parsed dictionary in and receives a canonical dictionary
back.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .constants import (
    DEFAULT_APP_DIR,
    DEFAULT_CONTAINER,
    DEFAULT_CTX,
    DEFAULT_LOGS_DIR,
    DEFAULT_MODELS_DIR,
    DEFAULT_PORT,
    DEFAULT_SERVICE,
)
from .optimize import DEFAULT_OPTIMIZATIONS


def _current_boot_id() -> str | None:
    try:
        from pathlib import Path

        return Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="utf-8"
        ).strip() or None
    except OSError:
        return None


def default_state() -> dict[str, Any]:
    """Fresh v5 defaults (deep copy; callers may mutate freely)."""
    return {
        "schema_version": 5,
        "disclaimer_ack": False,
        "ack_timestamp": None,
        "setup_phase": 0,
        "setup_complete": False,
        "llm_mode_done": False,
        "system_mode": "unconfigured",
        "boot_policy": "desktop",
        "desktop_on_reboot": True,
        "llm_autostart": False,
        "https_sharing_enabled": False,
        "https_webui_port": 8443,
        "https_api_port": 10000,
        "https_webui_url": None,
        "https_api_base_url": None,
        "llm_session_boot_id": None,
        "desktop_reboot_pending": False,
        "reboot_required": False,
        "pending_karg_mode": None,
        "env_ready": False,
        "installed_models": [],
        "current_model": None,
        "selected_source": "catalog",
        "selected_local_model": None,
        "current_ctx": DEFAULT_CTX,
        "server_port": DEFAULT_PORT,
        "container_name": DEFAULT_CONTAINER,
        "service_name": DEFAULT_SERVICE,
        "app_dir": str(DEFAULT_APP_DIR),
        "models_dir": str(DEFAULT_MODELS_DIR),
        "model_search_paths": [],
        "logs_dir": str(DEFAULT_LOGS_DIR),
        "llama_cpp_path": "/root/llama.cpp",
        "venv_path": "/root/.venvs/hf",
        "optimizations": deepcopy(DEFAULT_OPTIMIZATIONS),
        "bench_history": [],
        "autotune_history": [],
        "thermal_watchdog_state": "nominal",
        "llamacpp_build": None,
        "llamacpp_history": [],
        "revision": 0,
    }


def canonicalize_legacy_state(
    raw: dict[str, Any],
    *,
    boot_id: str | None = None,
) -> dict[str, Any]:
    """Canonicalize a raw legacy payload to the v5 shape. Pure.

    Applies the frozen v1→v5 migration ladder exactly as the historical
    writable store did, then reconciles stale llm-session markers against
    ``boot_id`` (defaults to the current boot).
    """
    if not isinstance(raw, dict):
        raise ValueError("legacy state must be a JSON object")
    state = default_state()
    state.update(raw)
    old_schema = int(raw.get("schema_version", 1))
    if old_schema < 2:
        old_phase = int(state.get("setup_phase", 0))
        if old_phase >= 6:
            state["setup_phase"] = old_phase + 1
    if old_schema < 3:
        state.update(
            schema_version=3,
            boot_policy="desktop",
            desktop_on_reboot=True,
            llm_autostart=False,
        )
        if state.get("system_mode") == "llm":
            state["system_mode"] = "llm-session"
            state["llm_session_boot_id"] = boot_id or _current_boot_id()
    if old_schema < 4:
        settings = dict(state.get("optimizations") or {})
        if "gpu_tuning_enabled" not in settings:
            settings["gpu_tuning_enabled"] = bool(settings.pop("gpu_enabled", False))
        state.update(schema_version=4, optimizations=settings)
    if old_schema < 5:
        # v5 declares the runtime-telemetry and llama.cpp build keys.
        state.update(
            schema_version=5,
            bench_history=[
                item for item in (state.get("bench_history") or []) if isinstance(item, dict)
            ],
            autotune_history=[
                item for item in (state.get("autotune_history") or []) if isinstance(item, dict)
            ],
            thermal_watchdog_state=state.get("thermal_watchdog_state") or "nominal",
            llamacpp_build=state.get("llamacpp_build") or None,
            llamacpp_history=[
                item for item in (state.get("llamacpp_history") or []) if isinstance(item, dict)
            ],
        )
    session_boot = state.get("llm_session_boot_id")
    current_boot = boot_id or _current_boot_id()
    if (
        state.get("boot_policy") == "desktop"
        and state.get("system_mode") == "llm-session"
        and session_boot
        and current_boot
        and session_boot != current_boot
    ):
        state.update(
            system_mode="desktop",
            llm_mode_done=False,
            llm_session_boot_id=None,
            desktop_reboot_pending=False,
        )
    return state
