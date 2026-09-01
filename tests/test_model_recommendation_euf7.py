import datetime as dt
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).parent))
from _gui_stubs import install  # noqa: E402

install()

from bc250_llm_mode.gui.models_page import (  # noqa: E402
    build_model_items,
    filter_model_items,
    model_action,
)
from bc250_llm_mode.model_recommendation import (  # noqa: E402
    MODEL_MEASUREMENT_FRESH_SECONDS,
    ModelRecommendationPolicy,
    RecommendationCandidate,
    evidence_is_fresh,
    fit_label,
)


NOW = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)


def _candidate(key, **changes):
    values = {
        "standard_layout": True,
        "immutable_identity": True,
        "fit_verdict": "FITS",
        "support_tier": "supported",
        "architecture_compatible": True,
        "inference_verified": False,
        "measured_local": False,
        "installed": False,
        "active": False,
    }
    values.update(changes)
    return RecommendationCandidate(key=key, **values)


def _installed(**changes):
    values = {
        "alias": "qwen38-9b",
        "display_name": "Qwen3.8 9B",
        "catalog_id": "qwen38-9b",
        "architecture": "qwen35",
        "byte_size": 6 * 1024**3,
        "active": True,
        "known_good": True,
        "trust_state": "VERIFIED",
        "validation_status": "verified",
        "fit_verdict": "FITS",
        "fit_detail": "comfortable fit",
        "source_repo": "empero-ai/Qwen3.8-9B-Distill-GGUF",
        "quant": "Q5_K_M",
        "deletion_eligible": False,
        "deletion_blockers": ("active-model",),
        "format": "gguf",
        "content_digest": "a" * 64,
        "benchmark_summary": {"tokens_per_second": 31.25},
        "last_verified_inference_at": "2026-08-31T00:00:00+00:00",
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_policy_uses_objective_lexicographic_evidence_and_excludes_unsafe_rows():
    decisions = ModelRecommendationPolicy().evaluate((
        _candidate("remote", immutable_identity=False),
        _candidate(
            "installed", installed=True, active=True,
            inference_verified=True, measured_local=True),
        _candidate("tight", fit_verdict="TIGHT"),
        _candidate("preview", support_tier="preview"),
        _candidate("unsafe", standard_layout=False),
        _candidate("no-fit", fit_verdict="NO-FIT"),
    ))
    assert decisions["installed"].rank == 1
    assert decisions["installed"].label == "Recommended for this BC250"
    assert decisions["remote"].eligible is True
    assert decisions["preview"].eligible is False
    assert decisions["unsafe"].eligible is False
    assert decisions["no-fit"].eligible is False
    assert "quality" not in " ".join(decisions["installed"].reasons).lower()


def test_remote_top_candidate_is_labeled_to_install_not_hardware_verified():
    decision = ModelRecommendationPolicy().evaluate((
        _candidate("remote", immutable_identity=False),
    ))["remote"]
    assert decision.label == "Recommended to install"
    assert "identity will be resolved" in " ".join(decision.reasons)


def test_measurement_freshness_and_fit_copy_are_closed():
    assert MODEL_MEASUREMENT_FRESH_SECONDS == 30 * 24 * 60 * 60
    assert evidence_is_fresh("2026-08-31T00:00:00Z", now=NOW)
    assert not evidence_is_fresh("2026-01-01T00:00:00Z", now=NOW)
    assert not evidence_is_fresh("not-a-time", now=NOW)
    assert [fit_label(value) for value in ("FITS", "TIGHT", "NO-FIT", None)] == [
        "Comfortable", "Tight", "Does not fit", "Not evaluated"]


def test_exact_qwen_artifact_gets_truthful_local_recommendation_and_measurement():
    items = build_model_items(
        [_installed()], context=8192, slots=1,
        inference_verified=True, now=NOW,
    )
    qwen = next(item for item in items if item.alias == "qwen38-9b")
    assert qwen.standard_layout and qwen.immutable_identity
    assert qwen.recommendation_label == "Recommended for this BC250"
    assert qwen.measurement_summary == "Measured locally: speed 31.2 tok/s"
    assert model_action(qwen).label == "Open Chat"

    stale = build_model_items(
        [_installed(last_verified_inference_at="2026-01-01T00:00:00Z")],
        context=8192, slots=1, inference_verified=False, now=NOW,
    )
    stale_qwen = next(item for item in stale if item.alias == "qwen38-9b")
    assert stale_qwen.measurement_summary == "Not measured on this machine"

    unidentified = build_model_items(
        [_installed(content_digest=None)], context=8192, slots=1,
        inference_verified=True, now=NOW,
    )
    unidentified_qwen = next(
        item for item in unidentified if item.alias == "qwen38-9b")
    assert unidentified_qwen.recommended is False
    assert unidentified_qwen.recommendation_label is None


def test_actions_are_start_switch_chat_or_explain_and_recommended_order_is_bounded():
    start = build_model_items(
        [_installed(active=False)], context=8192, slots=1, now=NOW)[0]
    assert model_action(start).label == "Start and Chat"
    stopped_selected = build_model_items(
        [_installed(active=True)], context=8192, slots=1,
        inference_verified=False, model_running=False, now=NOW,
    )[0]
    assert stopped_selected.state == "INSTALLED"
    assert model_action(stopped_selected).label == "Start and Chat"
    switch_items = build_model_items(
        [
            _installed(),
            _installed(
                alias="qwen35-9b", display_name="Qwen3.5 9B",
                catalog_id="qwen35-9b", active=False,
            ),
        ],
        context=8192, slots=1, inference_verified=True,
        model_running=True, now=NOW,
    )
    switch = next(item for item in switch_items if item.alias == "qwen35-9b")
    assert model_action(switch).label == "Switch and Chat"
    no_fit = build_model_items(
        [_installed(active=False, fit_verdict="NO-FIT")],
        context=8192, slots=1, now=NOW)[0]
    assert model_action(no_fit).label == "View why it cannot start"
    recommendations = filter_model_items(
        build_model_items([], context=8192, slots=1, now=NOW),
        category="Recommended",
    )
    assert recommendations
    assert len(recommendations) <= 100
    assert recommendations[0].recommendation_rank == 1

    source = Path("bc250_llm_mode/gui/models_page.py").read_text(encoding="utf-8")
    assert 'self.shell.navigate(Route.CHAT)' in source
