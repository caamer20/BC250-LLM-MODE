"""Shared model selection operations for the GUI and both CLIs.

All durable mutations route through the composed durable activation
command (``store.activation.activate``, Session 5C). There is no second
path: success is reported only after health, bounded inference, and
known-good promotion; rollback honestly reports the prior model.
"""

from __future__ import annotations

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


def _activation(store: Any) -> Any:
    """The composed ``Application.activation`` command service."""
    service = getattr(store, "activation", None)
    if service is None:
        raise RuntimeError(
            "Activation requires the composed application; no fallback "
            "path exists."
        )
    return store.require_operational().activation


def _requested_by(runner: CommandRunner) -> str:
    return str(getattr(runner, "surface", "cli") or "cli")


def _run_activation(
    store: Any,
    runner: CommandRunner,
    payload: dict[str, Any],
    description: str,
) -> dict[str, Any]:
    """ONE durable activation path; maps the typed terminal honestly."""
    outcome = _activation(store).activate(payload)
    if outcome.status == "BUSY":
        raise RuntimeError(
            f"{description}: an activation is already running; try again "
            "when it finishes."
        )
    if outcome.status == "RECOVERY_REQUIRED":
        raise RuntimeError(
            f"{description}: repair is required (operation "
            f"{outcome.operation_id}). The candidate is NOT confirmed "
            "running."
        )
    if outcome.status == "FAILED_SAFE":
        raise RuntimeError(
            f"{description} was rejected before any change "
            f"({outcome.detail.get('error_code') or 'validation'})."
        )
    if outcome.status == "FAILED_ROLLED_BACK":
        runner.emit(
            f"{description} failed; the previous working configuration "
            "was restored and verified."
        )
        return {"rolled_back": True, "operation_id": outcome.operation_id}
    if not outcome.ok:
        raise RuntimeError(f"{description} failed: {outcome.status}")
    return {"succeeded": True, "operation_id": outcome.operation_id}


def switch_model(
    store: Any,
    state: dict[str, Any],
    model_id: str,
    runner: CommandRunner,
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

    _run_activation(
        store,
        runner,
        {
            "model_alias": model_id,
            "requested_by": _requested_by(runner),
        },
        f"Switching to {model_id}",
    )
    state.clear()
    state.update(store.read_model())
    return state


def register_and_switch_local(
    store: Any,
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
    prepare_local_model(state, LocalModel.from_dict(model.to_dict()), runner)
    _run_activation(
        store,
        runner,
        {
            "model_alias": local_id,
            "requested_by": _requested_by(runner),
        },
        f"Switching to {model.display_name}",
    )
    state.clear()
    state.update(store.read_model())
    return state


def change_context(
    store: Any,
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
    _run_activation(
        store,
        runner,
        {
            "model_alias": state.get("current_model"),
            "context_per_slot": ctx,
            "requested_by": _requested_by(runner),
        },
        f"Changing context to {ctx}",
    )
    state.clear()
    state.update(store.read_model())
    return fit.detail


def change_parallel_slots(
    store: Any,
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
    _run_activation(
        store,
        runner,
        {
            "model_alias": state.get("current_model"),
            "parallel_slots": slots,
            "requested_by": _requested_by(runner),
        },
        f"Changing request slots to {slots}",
    )
    state.clear()
    state.update(store.read_model())
    return fit.detail
