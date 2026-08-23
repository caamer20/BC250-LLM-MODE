"""Shared model selection operations for the GUI and both CLIs.

All durable mutations route through ``ModelActivationService`` (typed,
revision-checked, verified rollback). Stores without a database profile
(in-memory test doubles / legacy JSON) take a dry in-memory path that
never performs a whole-state save.
"""

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


def restart_with_rollback(
    store: Any,
    state: dict[str, Any],
    runner: CommandRunner,
    previous: dict[str, Any],
    description: str,
) -> None:
    """Deprecated compatibility wrapper (GUI/CLI callers migrate in Session 3).

    Durable persistence for SQLite profiles happens inside
    ModelActivationService; this wrapper only performs the host
    restart + health verification with in-memory rollback.
    """
    _apply_legacy_or_raise(state, {}, previous, runner, description)


def _read_supplier(store: Any):
    """Read supplier for the composition (``read_model``) or a test double
    exposing the legacy ``load`` seam. Never a whole-state writer."""
    read = getattr(store, "read_model", None)
    return read if read is not None else store.load


def _activation_service(store: Any, runner: CommandRunner) -> Any:
    """ModelActivationService for SQLite profiles; None on legacy stores."""
    database_path = getattr(getattr(store, "paths", None), "database_path", None)
    if database_path is None:
        return None
    from .services import (
        ModelActivationService,
        RuntimeConfigurationService,
        RuntimeController,
    )
    from .unit_of_work import UnitOfWorkFactory

    class _ModuleController(RuntimeController):
        """Delegates to this module's seams so hosts/tests can intercept."""

        def __init__(self, inner_runner: CommandRunner) -> None:
            self.runner = inner_runner

        def restart(self, view: dict[str, Any]) -> None:
            restart_service(view, self.runner)

        def health_check(self, view: dict[str, Any]) -> dict[str, Any]:
            return health_check(view, self.runner)

        def minimal_inference_probe(self, view: dict[str, Any]) -> dict[str, Any]:
            from .server import minimal_inference_probe

            return minimal_inference_probe(view)

    paths = store.paths
    read = _read_supplier(store)
    units = UnitOfWorkFactory(database_path)
    runtime = RuntimeConfigurationService(
        units, app_dir=paths.app_dir, state_supplier=read
    )
    return ModelActivationService(
        units, runtime, _ModuleController(runner), state_supplier=read
    )


def _apply_legacy_or_raise(
    state: dict[str, Any],
    changes: dict[str, Any],
    previous: dict[str, Any],
    runner: CommandRunner,
    description: str,
    *,
    wait_for_health: bool = True,
) -> None:
    """In-memory fallback: fit-checked mutation with dict-level rollback."""
    state.update(changes)
    try:
        restart_service(state, runner)
        if wait_for_health:
            health_check(state, runner)
    except Exception as activation_error:  # noqa: BLE001 - rollback covers all
        state.update(deepcopy(previous))
        runner.emit(f"{description} failed; restoring the previous working server configuration")
        try:
            restart_service(state, runner)
            if wait_for_health:
                health_check(state, runner)
        except Exception as rollback_error:  # noqa: BLE001 - report both
            raise RuntimeError(
                f"{description} failed ({activation_error}); rollback also failed "
                f"({rollback_error}). Inspect the model server log."
            ) from activation_error
        raise RuntimeError(
            f"{description} failed ({activation_error}); the previous working "
            "configuration was restored."
        ) from activation_error


def _service_activation(
    store: Any,
    state: dict[str, Any],
    runner: CommandRunner,
    *,
    model_id: str | None = None,
    context: int | None = None,
    slots: int | None = None,
) -> dict[str, Any] | None:
    """Run the typed activation when a database profile exists."""
    service = _activation_service(store, runner)
    if service is None:
        return None
    from .services import ActivationRequest

    request = ActivationRequest(
        model_id=model_id or str(state.get("current_model")),
        context=context,
        slots=slots,
        requester="cli",
    )
    result = service.activate(request)
    if not result.ok:
        reason = result.detail.get("reason") or result.status
        if result.status in ("REJECTED_INVALID", "REJECTED_THERMAL_LATCH"):
            raise ValueError(reason)
        if result.status == "CONFLICT":
            raise RuntimeError(f"conflict: {reason}")
        raise RuntimeError(f"activation failed ({reason})")
    fresh = store.load()
    state.clear()
    state.update(fresh)
    return result.detail


def switch_model(
    store: Any,
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
    if wait_for_health:
        result = _service_activation(store, state, runner, model_id=model_id)
        if result is not None:
            return state
    state["current_model"] = model_id
    _apply_legacy_or_raise(
        state, {}, previous, runner, f"Switching to {model_id}",
        wait_for_health=wait_for_health,
    )
    if not wait_for_health:
        restart_service(state, runner)
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
    previous = {"current_model": state.get("current_model")}
    prepare_local_model(state, LocalModel.from_dict(model.to_dict()), runner)
    _service_activation(store, state, runner, model_id=local_id)
    _apply_legacy_or_raise(
        state, {}, previous, runner, f"Switching to {model.display_name}"
    )
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
    previous = {"current_ctx": state.get("current_ctx", 8192)}
    result = _service_activation(store, state, runner, context=ctx)
    if result is not None:
        return fit.detail
    state["current_ctx"] = ctx
    _apply_legacy_or_raise(state, {}, previous, runner, f"Changing context to {ctx}")
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
    previous = {"optimizations": deepcopy(state.get("optimizations"))}
    result = _service_activation(store, state, runner, slots=slots)
    if result is not None:
        return fit.detail
    state["optimizations"] = checked
    _apply_legacy_or_raise(state, {}, previous, runner, f"Changing request slots to {slots}")
    return fit.detail
