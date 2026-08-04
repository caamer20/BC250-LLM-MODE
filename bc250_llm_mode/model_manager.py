"""Shared model selection operations for the GUI and both CLIs."""

from __future__ import annotations

from typing import Any

from .catalog import calculate_fit
from .local_models import LocalModel, discover_local_models, fit_entry_for_local, installed_fit_entry
from .logging_utils import CommandRunner
from .optimize import kv_scale_for_settings
from .prepare import prepare_local_model
from .server import health_check, restart_service
from .state import StateStore


def switch_model(
    store: StateStore,
    state: dict[str, Any],
    model_id: str,
    runner: CommandRunner,
    *,
    wait_for_health: bool = True,
) -> dict[str, Any]:
    record = next(
        (item for item in state.get("installed_models", []) if item.get("id") == model_id), None
    )
    if record is None:
        raise ValueError(f"Model is not installed: {model_id}")
    fit = calculate_fit(
        installed_fit_entry(record),
        str(record["quant"]),
        int(state.get("current_ctx", 8192)),
        kv_scale=kv_scale_for_settings(state.get("optimizations")),
    )
    if fit.verdict == "NO-FIT":
        raise ValueError(fit.detail)
    state["current_model"] = model_id
    store.save(state)
    restart_service(state, runner)
    if wait_for_health:
        health_check(state, runner)
    return state


def register_and_switch_local(
    store: StateStore,
    state: dict[str, Any],
    local_id: str,
    runner: CommandRunner,
) -> dict[str, Any]:
    discovery = discover_local_models(state)
    model = next((item for item in discovery.models if item.id == local_id), None)
    if model is None:
        raise ValueError(f"Discovered model is no longer available: {local_id}")
    fit = calculate_fit(
        fit_entry_for_local(model),
        model.quant,
        int(state.get("current_ctx", 8192)),
        kv_scale=kv_scale_for_settings(state.get("optimizations")),
    )
    if fit.verdict == "NO-FIT":
        raise ValueError(fit.detail)
    prepare_local_model(state, LocalModel.from_dict(model.to_dict()), runner)
    store.save(state)
    restart_service(state, runner)
    health_check(state, runner)
    return state


def change_context(
    store: StateStore,
    state: dict[str, Any],
    ctx: int,
    runner: CommandRunner,
) -> str:
    if ctx < 512 or ctx > 262144:
        raise ValueError("context must be from 512 to 262144")
    record = next(
        (item for item in state.get("installed_models", []) if item.get("id") == state.get("current_model")),
        None,
    )
    if record is None:
        raise ValueError("No current installed model is selected")
    fit = calculate_fit(
        installed_fit_entry(record),
        str(record["quant"]),
        ctx,
        kv_scale=kv_scale_for_settings(state.get("optimizations")),
    )
    if fit.verdict == "NO-FIT":
        raise ValueError(fit.detail)
    state["current_ctx"] = ctx
    store.save(state)
    restart_service(state, runner)
    health_check(state, runner)
    return fit.detail
