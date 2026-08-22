"""Opt-in benchmark sweep that picks the fastest safe runtime configuration.

Every candidate is validated and VRAM-fit-checked before the server restarts;
a combo that fails health is rolled back and excluded. The winner stays applied.
"""

from __future__ import annotations

import time
from copy import deepcopy
from typing import Any

from .disclaimer import require_acknowledgment
from .logging_utils import CommandRunner
from .optimize import kv_scale_for_settings, normalized_settings, validate_settings
from .state import StateStore

CANDIDATE_UBATCH = (256, 512)
CANDIDATE_KV = ("q8_0", "q4_0")
CANDIDATE_FLASH_ATTN = ("auto", "on")


def _combo_fits(state: dict[str, Any], settings: dict[str, Any]) -> bool:
    from .catalog import calculate_fit
    from .local_models import installed_fit_entry
    from .optimize import parallel_slots_for_settings

    record = next(
        (item for item in state.get("installed_models", []) if item.get("id") == state.get("current_model")),
        None,
    )
    if record is None:
        raise RuntimeError("Install a model before running the auto-tuner")
    try:
        fit = calculate_fit(
            installed_fit_entry(record),
            str(record["quant"]),
            int(state.get("current_ctx", 8192)),
            kv_scale=kv_scale_for_settings(settings),
            parallel_slots=parallel_slots_for_settings(settings),
        )
    except ValueError:
        # Context above the model's trained limit: this combo cannot run.
        return False
    return fit.verdict != "NO-FIT"



def autotune(
    store: StateStore,
    state: dict[str, Any],
    runner: CommandRunner,
    *,
    repeat: int = 2,
    max_tokens: int = 96,
) -> dict[str, Any]:
    require_acknowledgment(state)
    from .chat import benchmark_repeat
    from .server import restart_and_wait

    base = normalized_settings(state.get("optimizations"))
    original = deepcopy(base)
    previous_settings = deepcopy(base)
    results: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None

    for ubatch in CANDIDATE_UBATCH:
        for kv in CANDIDATE_KV:
            for flash in CANDIDATE_FLASH_ATTN:
                candidate = validate_settings(
                    dict(base, ubatch_size=ubatch, kv_cache_type=kv, flash_attention=flash)
                )
                label = f"ubatch={ubatch} kv={kv} fa={flash}"
                try:
                    if not _combo_fits(state, candidate):
                        runner.emit(f"autotune: skip {label} — projected NO-FIT")
                        continue
                except RuntimeError:
                    raise
                runner.emit(f"autotune: testing {label}")
                state["optimizations"] = candidate
                store.save(state)
                try:
                    restart_and_wait(state, runner)
                    speed_result = benchmark_repeat(
                        state, max_tokens=max_tokens, repeat=repeat
                    )
                except Exception as combo_error:  # noqa: BLE001 - every failure must roll back
                    runner.emit(f"autotune: {label} failed ({combo_error}); reverting to last working combo")
                    state["optimizations"] = previous_settings
                    store.save(state)
                    restart_and_wait(state, runner)
                    continue
                speed = speed_result.get("predicted_per_second_median") or 0.0
                entry = {
                    "ubatch_size": ubatch,
                    "kv_cache_type": kv,
                    "flash_attention": flash,
                    "predicted_per_second_median": speed,
                    **{
                        key: speed_result.get(key)
                        for key in ("predicted_per_second_min", "predicted_per_second_max")
                    },
                }
                results.append(entry)
                runner.emit(f"autotune: {label} -> {speed:.1f} tok/s median")
                if best is None or speed > float(best["predicted_per_second_median"]):
                    best = entry
                previous_settings = deepcopy(candidate)

    if best is not None:
        state["optimizations"] = validate_settings(
            dict(
                base,
                ubatch_size=int(best["ubatch_size"]),
                kv_cache_type=str(best["kv_cache_type"]),
                flash_attention=str(best["flash_attention"]),
            )
        )
        runner.emit(
            f"autotune: winner {best['ubatch_size']}/{best['kv_cache_type']}/{best['flash_attention']} "
            f"at {float(best['predicted_per_second_median']):.1f} tok/s; applying and restarting"
        )
        state["optimizations_applied"] = True
    else:
        state["optimizations"] = original
        runner.emit("autotune: no candidate completed successfully; original settings restored")
    # Narrow history persistence: capped repository appends on a dedicated
    # per-command connection (SQLite), a history-only transaction (legacy
    # JSON), or direct dict mutation (in-memory doubles). Never a save.
    paths = getattr(store, "paths", None)
    if paths is not None:
        from .repositories import AutotuneHistoryRepository
        from .unit_of_work import UnitOfWorkFactory

        with UnitOfWorkFactory(paths.database_path).begin() as conn:
            repo = AutotuneHistoryRepository(conn)
            for entry in results:
                entry.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S"))
                entry.setdefault("model", state.get("current_model"))
                entry.setdefault("context", state.get("current_ctx"))
                repo.append(entry, commit=False)
    elif hasattr(store, "transaction"):
        def mutate(current: dict[str, Any]) -> dict[str, Any]:
            history = [
                item for item in (current.get("autotune_history") or [])
                if isinstance(item, dict)
            ]
            stamped = [dict(entry, timestamp=time.strftime("%Y-%m-%dT%H:%M:%S")) for entry in results]
            current["autotune_history"] = ([*history, *stamped])[-40:]
            return current

        store.transaction(mutate)
    else:
        # In-memory stores (test doubles): mutate the dict directly.
        history = [
            item for item in (state.get("autotune_history") or [])
            if isinstance(item, dict)
        ]
        state["autotune_history"] = ([*history, *results])[-40:]
    restart_and_wait(state, runner)
    return {"winner": best, "results": results}
