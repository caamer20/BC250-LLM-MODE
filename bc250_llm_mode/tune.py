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
    store: Any,
    state: dict[str, Any],
    runner: CommandRunner,
    *,
    repeat: int = 2,
    max_tokens: int = 96,
    runtime_service=None,
) -> dict[str, Any]:
    require_acknowledgment(state)
    from .chat import benchmark_repeat
    from .server import restart_and_wait

    def _default_runtime_service():
        database_path = getattr(getattr(store, "paths", None), "database_path", None)
        if database_path is None:
            return None
        from .services import RuntimeConfigurationService
        from .unit_of_work import UnitOfWorkFactory

        paths = store.paths
        read = getattr(store, "read_model", None) or store.load
        return RuntimeConfigurationService(
            UnitOfWorkFactory(database_path),
            app_dir=paths.app_dir,
            state_supplier=read,
        )

    runtime = runtime_service if runtime_service is not None else _default_runtime_service()
    base = normalized_settings(state.get("optimizations"))
    original = deepcopy(base)
    previous_settings = deepcopy(base)
    results: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    revision = runtime.current()["revision"] if runtime is not None else None

    def _apply_candidate(candidate: dict[str, Any]) -> None:
        """Commit a trial configuration (typed service or in-memory only)."""
        nonlocal revision
        if runtime is not None:
            applied = runtime.apply(
                {"optimizations_patch": candidate}, expected_revision=revision
            )
            revision = applied.revision
            state["optimizations"] = applied.resolved["optimizations"]
        else:
            state["optimizations"] = candidate

    def _restore_original() -> None:
        nonlocal revision
        if runtime is not None:
            restored = runtime.restore(original, expected_revision=revision)
            revision = restored.revision
            state["optimizations"] = restored.resolved["optimizations"]
        else:
            state["optimizations"] = deepcopy(original)

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
                _apply_candidate(candidate)
                try:
                    restart_and_wait(state, runner)
                    speed_result = benchmark_repeat(
                        state, max_tokens=max_tokens, repeat=repeat
                    )
                except Exception as combo_error:  # noqa: BLE001 - every failure must roll back
                    runner.emit(f"autotune: {label} failed ({combo_error}); reverting to last working combo")
                    _restore_original()
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
        winner_candidate = validate_settings(
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
        try:
            _apply_candidate(winner_candidate)
            state["optimizations_applied"] = True
            restart_and_wait(state, runner)
            # A winner becomes known-good only after health verification.
            if runtime is not None:
                from .runtime_handoff import runtime_fingerprint

                runtime.promote_known_good(
                    component_identity=(state.get("llamacpp_build") or {}).get("describe")
                    if isinstance(state.get("llamacpp_build"), dict)
                    else None
                )
        except Exception:
            runner.emit("autotune: winner verification failed; restoring original configuration")
            _restore_original()
            restart_and_wait(state, runner)
            raise
    else:
        state["optimizations"] = original
        runner.emit("autotune: no candidate completed successfully; original settings restored")
    # Narrow history persistence: capped repository appends on a dedicated
    # per-command connection (SQLite); handles without a database (in-memory
    # doubles) mutate only the draft. Never a whole-state write.
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
    else:
        history = [
            item for item in (state.get("autotune_history") or [])
            if isinstance(item, dict)
        ]
        state["autotune_history"] = ([*history, *results])[-40:]
    restart_and_wait(state, runner)
    return {"winner": best, "results": results}
