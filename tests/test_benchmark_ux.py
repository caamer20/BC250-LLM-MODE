"""P7 §13.4: benchmark & tuning UX presentation contract (pure).

Pins tested-vs-estimated comparison, attribution, thermal notice, partial/
cancellation semantics, bounded retention, the prompt-content canary, and
"apply winner" as a separate verified operation.
"""

from __future__ import annotations

from bc250_llm_mode.benchmark_ux import (
    apply_winner_notice,
    benchmark_attribution,
    partial_result_semantics,
    result_contains_prompt_content,
    retention_summary,
    tested_vs_estimated as compare_tested_vs_estimated,
    thermal_condition_notice,
)


def test_attribution_carries_identity():
    attr = benchmark_attribution(
        model="tiny", runtime_identity="llamacpp:sha256:abc",
        context=8192, slots=2, temperature=0.3)
    assert attr["model"] == "tiny"
    assert attr["runtime_identity"] == "llamacpp:sha256:abc"
    assert attr["context"] == 8192 and attr["slots"] == 2


def test_tested_vs_estimated():
    r = compare_tested_vs_estimated(50.0, 50.0)
    assert r["verdict"] == "meets-or-exceeds-estimate" and r["ratio"] == 1.0
    assert compare_tested_vs_estimated(40.0, 50.0)["verdict"] == "below-estimate"
    assert compare_tested_vs_estimated(20.0, 50.0)["verdict"] == "well-below-estimate"
    assert compare_tested_vs_estimated(None, 50.0)["verdict"] == "no-tested-result"
    assert compare_tested_vs_estimated(50.0, None)["verdict"] == "no-estimate"
    assert compare_tested_vs_estimated(50.0, 0)["verdict"] == "no-estimate"


def test_thermal_condition_notice():
    assert thermal_condition_notice(False) is None
    notice = thermal_condition_notice(True)
    assert notice and "thermal" in notice.lower()


def test_partial_result_semantics():
    done = partial_result_semantics(cancelled=False, runs_completed=3)
    assert done["usable"] is True and done["cancelled"] is False
    partial = partial_result_semantics(cancelled=True, runs_completed=1)
    assert partial["usable"] is True and "partial" in partial["note"]
    empty = partial_result_semantics(cancelled=True, runs_completed=0)
    assert empty["usable"] is False


def test_apply_winner_is_separate_operation():
    notice = apply_winner_notice()
    assert "SEPARATE" in notice
    assert "never changes" in notice


def test_retention_is_bounded():
    results = [{"i": i} for i in range(30)]
    summary = retention_summary(results)
    assert summary["retained"] == 20
    assert summary["dropped"] == 10
    assert summary["results"][0] == {"i": 10}


def test_prompt_content_canary():
    prompt = "a very distinctive prompt phrase"
    clean = {"model": "tiny", "predicted_per_second": 42.0}
    assert result_contains_prompt_content(clean, prompt) is False
    leaked = {"model": "tiny", "echo": prompt}
    assert result_contains_prompt_content(leaked, prompt) is True
    assert result_contains_prompt_content(clean, None) is False
