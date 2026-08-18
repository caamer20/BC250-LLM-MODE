"""Shared model selection operations for the GUI and both CLIs."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .catalog import calculate_fit
from .local_models import LocalModel, discover_local_models, fit_entry_for_local, installed_fit_entry
from .logging_utils import CommandRunner
from .optimize import (
    kv_scale_for_settings,
    normalized_settings,
    parallel_slots_for_settings,
    validate_settings,
)
from .prepare import prepare_local_model
from .server import health_check, restart_service, stop_service
from .state import StateStore


def restart_with_rollback(
    store: StateStore,
    state: dict[str, Any],
    runner: CommandRunner,
    previous: dict[str, Any],
    description: str,
) -> None:
    """Restart and health-check an activation, restoring the last working state on failure."""
    store.save(state)
    try:
        restart_service(state, runner)
        health_check(state, runner)
    except Exception as activation_error:  # noqa: BLE001 - rollback must cover every launch failure
        state.update(deepcopy(previous))
        store.save(state)
        runner.emit(f"{description} failed; restoring the previous working server configuration")
        try:
            if state.get("current_model"):
                restart_service(state, runner)
                health_check(state, runner)
            else:
                stop_service(state, runner)
        except Exception as rollback_error:  # noqa: BLE001 - report both failures to the caller
            raise RuntimeError(
                f"{description} failed ({activation_error}); rollback also failed ({rollback_error}). "
                "Inspect the model server log."
            ) from activation_error
        raise RuntimeError(
            f"{description} failed ({activation_error}); the previous working configuration was restored."
        ) from activation_error


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
        parallel_slots=parallel_slots_for_settings(state.get("optimizations")),
    )
    if fit.verdict == "NO-FIT":
        raise ValueError(fit.detail)
    previous = {"current_model": state.get("current_model")}
    state["current_model"] = model_id
    if wait_for_health:
        restart_with_rollback(store, state, runner, previous, f"Switching to {model_id}")
    else:
        store.save(state)
        restart_service(state, runner)
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
        parallel_slots=parallel_slots_for_settings(state.get("optimizations")),
    )
    if fit.verdict == "NO-FIT":
        raise ValueError(fit.detail)
    previous = {"current_model": state.get("current_model")}
    prepare_local_model(state, LocalModel.from_dict(model.to_dict()), runner)
    restart_with_rollback(store, state, runner, previous, f"Switching to {model.display_name}")
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
        parallel_slots=parallel_slots_for_settings(state.get("optimizations")),
    )
    if fit.verdict == "NO-FIT":
        raise ValueError(fit.detail)
    previous = {"current_ctx": state.get("current_ctx", 8192)}
    state["current_ctx"] = ctx
    restart_with_rollback(store, state, runner, previous, f"Changing context to {ctx}")
    return fit.detail


def change_parallel_slots(
    store: StateStore,
    state: dict[str, Any],
    slots: int,
    runner: CommandRunner,
) -> str:
    if not 1 <= slots <= 8:
        raise ValueError("slots must be from 1 to 8")
    record = next(
        (item for item in state.get("installed_models", []) if item.get("id") == state.get("current_model")),
        None,
    )
    if record is None:
        raise ValueError("No current installed model is selected")
    settings = normalized_settings(state.get("optimizations"))
    settings["parallel_slots"] = slots
    checked = validate_settings(settings)
    fit = calculate_fit(
        installed_fit_entry(record),
        str(record["quant"]),
        int(state.get("current_ctx", 8192)),
        kv_scale=kv_scale_for_settings(checked),
        parallel_slots=slots,
    )
    if fit.verdict == "NO-FIT":
        raise ValueError(fit.detail)
    previous = {"optimizations": deepcopy(state.get("optimizations"))}
    state["optimizations"] = checked
    restart_with_rollback(store, state, runner, previous, f"Changing request slots to {slots}")
    return fit.detail
