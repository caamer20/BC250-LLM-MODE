"""P7 §13.4: benchmark & tuning UX presentation contract (pure, no I/O).

Headless-testable semantics for benchmark/tune results: tested-vs-estimated
comparison, model/runtime/config attribution, thermal conditions, cancellation
and partial-result semantics, bounded retention, the prompt-content canary, and
"apply winner" as a SEPARATE verified runtime/configuration operation.
"""

from __future__ import annotations

from typing import Any

BENCHMARK_UX_SCHEMA_VERSION = 1

_MAX_RETAINED_RESULTS = 20


def benchmark_attribution(
    *,
    model: str | None,
    runtime_identity: str | None = None,
    context: int | None = None,
    slots: int | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    """Model/runtime/config identity a benchmark result is attributable to."""
    return {
        "model": model,
        "runtime_identity": runtime_identity,
        "context": context,
        "slots": slots,
        "temperature": temperature,
    }


def tested_vs_estimated(
    tested_tokens_per_second: float | None,
    estimated_tokens_per_second: float | None,
) -> dict[str, Any]:
    """Compare a measured result against an estimate (None-safe)."""
    if tested_tokens_per_second is None:
        return {"tested": None, "estimated": estimated_tokens_per_second,
                "ratio": None, "verdict": "no-tested-result"}
    if estimated_tokens_per_second in (None, 0):
        return {"tested": tested_tokens_per_second,
                "estimated": estimated_tokens_per_second,
                "ratio": None, "verdict": "no-estimate"}
    ratio = tested_tokens_per_second / estimated_tokens_per_second
    if ratio >= 0.9:
        verdict = "meets-or-exceeds-estimate"
    elif ratio >= 0.7:
        verdict = "below-estimate"
    else:
        verdict = "well-below-estimate"
    return {"tested": tested_tokens_per_second,
            "estimated": estimated_tokens_per_second,
            "ratio": round(ratio, 3), "verdict": verdict}


def thermal_condition_notice(thermal_latched: bool) -> str | None:
    if not thermal_latched:
        return None
    return (
        "The thermal latch is engaged; benchmark/tune results are not "
        "representative until the latch is reset at a safe temperature."
    )


def partial_result_semantics(cancelled: bool, runs_completed: int) -> dict[str, Any]:
    """Cancellation/partial-result semantics for a repeated benchmark."""
    return {
        "cancelled": cancelled,
        "runs_completed": runs_completed,
        "usable": runs_completed > 0,
        "note": (
            "partial result from cancelled run" if cancelled
            else "run completed"
        ),
    }


def apply_winner_notice() -> str:
    return (
        "Applying the winning configuration is a SEPARATE verified "
        "runtime/configuration operation. Benchmarking alone never changes "
        "the active configuration."
    )


def retention_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Bounded retention: only the most recent N results are kept."""
    retained = results[-_MAX_RETAINED_RESULTS:]
    return {
        "retained": len(retained),
        "dropped": max(0, len(results) - _MAX_RETAINED_RESULTS),
        "max_retained": _MAX_RETAINED_RESULTS,
        "results": retained,
    }


def result_contains_prompt_content(
    result: dict[str, Any], prompt: str | None
) -> bool:
    """Prompt-content canary: a stored benchmark result must never contain the
    prompt text (P7 §13.4 / exit gate)."""
    if not prompt:
        return False
    return prompt in str(result)
