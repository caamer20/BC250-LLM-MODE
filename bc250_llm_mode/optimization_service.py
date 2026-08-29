"""Typed optimization boundary for frontends and host effects."""

from __future__ import annotations

from typing import Any

from .catalog import calculate_fit
from .local_models import selected_fit_entry
from .optimize import (
    DEFAULT_OPTIMIZATIONS,
    TRIMMABLE_SERVICES,
    apply_optimizations,
    kv_scale_for_settings,
    normalized_settings,
    validate_settings,
)
from .services import persist_state_diff


def validate_selection_settings(
    state: dict[str, Any], values: dict[str, Any]
) -> dict[str, Any]:
    checked = validate_settings(values)
    model = selected_fit_entry(state)
    fit = calculate_fit(
        model,
        str(state["selected_quant"]),
        int(state["current_ctx"]),
        kv_scale=kv_scale_for_settings(checked),
        parallel_slots=int(checked["parallel_slots"]),
    )
    if fit.verdict == "NO-FIT":
        raise ValueError(f"Selected runtime settings do not fit: {fit.detail}")
    return checked


class OptimizationService:
    """Validate, preview, and apply the closed optimization vocabulary."""

    defaults = DEFAULT_OPTIMIZATIONS
    trimmable_services = TRIMMABLE_SERVICES

    def __init__(self, units) -> None:
        self._units = units

    @staticmethod
    def normalized(value=None) -> dict[str, Any]:
        return normalized_settings(value)

    @staticmethod
    def validate_selection(
        state: dict[str, Any], values: dict[str, Any]
    ) -> dict[str, Any]:
        return validate_selection_settings(state, values)

    def apply(self, view, settings, runner) -> dict[str, Any]:
        before = dict(view)
        result = apply_optimizations(view, settings, runner)
        persist_state_diff(self._units, before, view)
        return result
