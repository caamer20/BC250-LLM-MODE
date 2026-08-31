import pytest

from bc250_llm_mode.catalog import (
    ADVERTISED_CATALOG,
    CATALOG,
    advertised_model_by_id,
    best_quant,
    calculate_fit,
    catalog_rows,
    is_forbidden_artifact,
    model_by_id,
    recommend_models,
    search_catalog,
    validate_artifact,
)


def test_qwen35_32k_kv_and_fit():
    result = calculate_fit(model_by_id("qwen35-9b"), "Q5_K_M", 32768)
    assert result.kv_gib == pytest.approx(2.25)
    assert result.required_gib == pytest.approx(9.52)
    assert result.verdict == "FITS"


def test_q4_kv_projection_halves_context_cache():
    model = model_by_id("qwen35-9b")
    q8 = calculate_fit(model, "Q5_K_M", 32768)
    q4 = calculate_fit(model, "Q5_K_M", 32768, kv_scale=0.5)
    assert q4.kv_gib == pytest.approx(q8.kv_gib / 2)


def test_tight_and_no_fit_boundaries():
    model = model_by_id("qwen3-14b")
    assert calculate_fit(model, "Q4_K_M", 32768).verdict == "TIGHT"
    assert calculate_fit(model, "Q4_K_M", 131072).verdict == "NO-FIT"


@pytest.mark.parametrize("name", ["thing-MAX.gguf", "IMATRIX-MAX-Q5.gguf", "weights_fused.gguf"])
def test_forbidden_artifacts(name):
    assert is_forbidden_artifact(name)
    with pytest.raises(ValueError):
        validate_artifact("some/repo", name)


def test_standard_artifact_allowed():
    validate_artifact("bartowski/Qwen", "Qwen-Q5_K_M.gguf")


def test_catalog_ids_are_unique_and_all_downloads_are_standard_layouts():
    assert len(CATALOG) == 40
    assert len({model.id for model in CATALOG}) == len(CATALOG)
    for model in CATALOG:
        for pattern in model.allow_globs.values():
            validate_artifact(model.repo, pattern)


def test_direct_catalog_downloads_use_literal_safe_remote_filenames():
    for model in CATALOG:
        if model.conversion:
            continue
        assert all("*" not in filename for filename in model.allow_globs.values()), model.id
        assert all(filename.lower().endswith(".gguf") for filename in model.allow_globs.values())


def test_conversion_only_sources_are_not_advertised_as_runnable_models():
    hidden = {"qwen38-9b-distill", "defiant-fable-9b"}

    assert len(ADVERTISED_CATALOG) == 38
    assert hidden == {model.id for model in CATALOG if model.conversion}
    assert hidden.isdisjoint(model.id for model in ADVERTISED_CATALOG)
    assert hidden.isdisjoint(model.id for model in search_catalog(""))
    assert hidden.isdisjoint(row["id"] for row in catalog_rows(""))
    assert hidden.isdisjoint(
        model.id for model, _quant, _fit in recommend_models(8192)
    )
    for model_id in hidden:
        with pytest.raises(KeyError, match="requires local conversion"):
            advertised_model_by_id(model_id)
        # The private identity remains available only to recognize an already
        # converted local GGUF without misclassifying it as another model.
        assert model_by_id(model_id).conversion is True


@pytest.mark.parametrize(
    ("model_id", "quant", "ctx", "verdict"),
    [
        ("llama32-3b-instruct", "Q8_0", 32768, "FITS"),
        ("llama31-8b-instruct", "Q5_K_M", 32768, "FITS"),
        ("qwen25-coder-7b", "Q6_K", 32768, "FITS"),
        ("deepseek-r1-qwen-7b", "Q6_K", 32768, "FITS"),
        ("mistral-nemo-12b", "Q5_K_M", 32768, "TIGHT"),
        ("phi4-14b", "Q4_K_M", 8192, "FITS"),
        ("phi4-14b", "Q4_K_M", 16384, "TIGHT"),
        ("phi4-14b", "Q4_K_M", 32768, "NO-FIT"),
    ],
)
def test_expanded_catalog_common_context_fits(model_id, quant, ctx, verdict):
    assert calculate_fit(model_by_id(model_id), quant, ctx).verdict == verdict


@pytest.mark.parametrize(
    ("model_id", "quant", "ctx", "required"),
    [
        ("lfm25-26b", "Q5_K_M", 128000, 3.783),
        ("lfm25-12b-instruct", "Q5_K_M", 128000, 2.517),
    ],
)
def test_lfm25_high_context_models_fit_comfortably(model_id, quant, ctx, required):
    result = calculate_fit(model_by_id(model_id), quant, ctx)
    assert result.required_gib == pytest.approx(required, abs=0.002)
    assert result.verdict == "FITS"


def test_lfm25_128k_fits_four_concurrent_slots():
    result = calculate_fit(
        model_by_id("lfm25-26b"), "Q5_K_M", 128000, parallel_slots=4
    )
    assert result.required_gib == pytest.approx(6.71, abs=0.01)
    assert result.verdict == "FITS"


def test_lfm25_rejects_context_above_trained_limit():
    with pytest.raises(ValueError, match="at most 128000"):
        calculate_fit(model_by_id("lfm25-26b"), "Q5_K_M", 128001)


def test_qwen38_uses_exact_standard_ggufs_and_bc250_fit_math():
    model = model_by_id("qwen38-9b")
    assert model.repo == "empero-ai/Qwen3.8-9B-Distill-GGUF"
    assert model.source_repo == "empero-ai/Qwen3.8-9B-Distill"
    assert model.allow_globs["Q4_K_M"] == "Qwen3.8-9B-Q4_K_M.gguf"
    assert model.true_block_count == 32
    assert model.max_context_tokens == 262144
    assert model.temperature == 0.6
    assert model.top_p == 0.95
    assert model.top_k == 20
    assert calculate_fit(model, "Q4_K_M", 16384, parallel_slots=4).verdict == "TIGHT"
    q5 = calculate_fit(model, "Q5_K_M", 8192, parallel_slots=4)
    assert q5.required_gib == pytest.approx(9.436, abs=0.002)
    assert q5.verdict == "FITS"


def test_qwen38_rejects_context_above_trained_limit():
    with pytest.raises(ValueError, match="at most 262144"):
        calculate_fit(model_by_id("qwen38-9b"), "Q4_K_M", 262145)


def test_expanded_2026_catalog_common_fits():
    assert calculate_fit(model_by_id("qwen3-4b-instruct"), "Q8_0", 32768).verdict == "FITS"
    assert calculate_fit(model_by_id("qwen25-3b-instruct"), "Q8_0", 32768).verdict == "FITS"
    assert calculate_fit(model_by_id("mistral-7b-instruct-v03"), "Q5_K_M", 32768).verdict == "FITS"
    gemma = calculate_fit(model_by_id("gemma-2-9b-it"), "Q4_K_M", 8192)
    assert gemma.verdict == "FITS"
    assert gemma.required_gib == pytest.approx(5.44 + 2.625 + 1.0, abs=0.01)
    assert calculate_fit(model_by_id("llama32-1b-instruct"), "Q8_0", 131072).verdict == "FITS"


def test_new_models_reject_context_above_trained_limits():
    with pytest.raises(ValueError, match="at most 262144"):
        calculate_fit(model_by_id("qwen3-4b-instruct"), "Q4_K_M", 262145)
    with pytest.raises(ValueError, match="at most 8192"):
        calculate_fit(model_by_id("gemma-2-9b-it"), "Q4_K_M", 8193)
    with pytest.raises(ValueError, match="at most 32768"):
        calculate_fit(model_by_id("mistral-7b-instruct-v03"), "Q4_K_M", 32769)


def test_search_catalog_matches_tags_names_and_ids():
    assert search_catalog("") == ADVERTISED_CATALOG
    reasoning_ids = {model.id for model in search_catalog("reasoning")}
    assert {"phi4-14b", "deepseek-r1-llama-8b", "deepseek-r1-qwen-7b"} <= reasoning_ids
    assert {model.id for model in search_catalog("gemma")} == {
        "gemma-2-2b-it", "gemma-2-9b-it", "gemma-3-4b-it", "gemma-3-12b-it",
    }
    assert all("coder" in model.id or "code" in model.task_tags for model in search_catalog("code"))
    assert search_catalog("no-such-tag-xyz") == ()


def test_best_quant_prefers_highest_quality_that_fits():
    quant, fit = best_quant(model_by_id("lfm25-26b"), 128000)
    assert quant == "Q8_0"
    assert fit.verdict == "FITS"


def test_best_quant_falls_back_to_best_tight_option():
    quant, fit = best_quant(model_by_id("phi4-14b"), 32768)
    assert quant == "Q3_K_M"
    assert fit.verdict == "TIGHT"


def test_best_quant_returns_none_when_nothing_fits():
    assert best_quant(model_by_id("phi4-14b"), 131072) is None


def test_qwen38_distill_uses_local_conversion_and_bc250_fit_math():
    model = model_by_id("qwen38-9b-distill")
    assert model.repo == "empero-ai/Qwen3.8-9B-Distill"
    assert model.conversion is True
    assert model.allow_globs == {"Q5_K_M": "*", "Q6_K": "*"}
    assert model.temporary_disk_gib == 46.0
    assert model.true_block_count == 32
    assert model.max_context_tokens == 262144
    assert (model.temperature, model.top_p, model.top_k, model.min_p) == (0.6, 0.95, 20, 0.0)
    q5 = calculate_fit(model, "Q5_K_M", 8192, parallel_slots=4)
    assert q5.required_gib == pytest.approx(6.24 + 2.25 + 1.0, abs=0.002)
    assert q5.verdict == "FITS"
    assert calculate_fit(model, "Q5_K_M", 16384, parallel_slots=4).verdict == "TIGHT"
    with pytest.raises(ValueError, match="at most 262144"):
        calculate_fit(model, "Q5_K_M", 262145)


def test_discovery_matches_qwen38_distill_conversions_separately():
    from bc250_llm_mode.local_models import _catalog_match
    from pathlib import Path

    assert _catalog_match(Path("/models/Qwen3.8-9B-Distill-Q5_K_M.gguf")).id == "qwen38-9b-distill"
    assert _catalog_match(Path("/models/Qwen3.8-9B-Q5_K_M.gguf")).id == "qwen38-9b"


@pytest.mark.parametrize(
    ("filename", "model_id"),
    [
        ("smollm2-360m-instruct-q8_0.gguf", "smollm2-360m-instruct"),
        ("qwen2.5-0.5b-instruct-q8_0.gguf", "qwen25-0p5b-instruct"),
        ("Qwen3-0.6B-Q8_0.gguf", "qwen3-0p6b"),
        ("tinyllama-1.1b-chat-v1.0.Q6_K.gguf", "tinyllama-1p1b-chat"),
        ("DeepSeek-R1-Distill-Qwen-1.5B-Q5_K_M.gguf", "deepseek-r1-qwen-1p5b"),
        ("Qwen3-1.7B-Q8_0.gguf", "qwen3-1p7b"),
        ("smollm2-1.7b-instruct-q4_k_m.gguf", "smollm2-1p7b-instruct"),
        ("microsoft_Phi-4-mini-instruct-Q6_K.gguf", "phi4-mini-instruct"),
        ("google_gemma-3-12b-it-Q4_K_M.gguf", "gemma-3-12b-it"),
        ("DeepSeek-Coder-V2-Lite-Instruct-Q3_K_M.gguf", "deepseek-coder-v2-lite-16b"),
    ],
)
def test_discovery_matches_literal_catalog_filenames(filename, model_id):
    from pathlib import Path
    from bc250_llm_mode.local_models import _catalog_match

    assert _catalog_match(Path("/models") / filename).id == model_id


@pytest.mark.parametrize(
    ("model_id", "quant", "ctx", "slots", "verdict"),
    [
        ("smollm2-360m-instruct", "Q8_0", 8192, 4, "FITS"),
        ("qwen3-0p6b", "Q8_0", 32768, 4, "NO-FIT"),
        ("deepseek-r1-qwen-1p5b", "Q8_0", 32768, 4, "FITS"),
        ("phi4-mini-instruct", "Q8_0", 32768, 4, "NO-FIT"),
        ("gemma-3-4b-it", "Q8_0", 8192, 4, "FITS"),
        ("falcon3-10b-instruct", "Q5_K_M", 8192, 1, "FITS"),
        ("gemma-3-12b-it", "Q4_K_M", 8192, 1, "TIGHT"),
        ("qwen25-14b-instruct", "Q3_K_M", 8192, 1, "FITS"),
        ("deepseek-coder-v2-lite-16b", "Q3_K_M", 16384, 1, "FITS"),
    ],
)
def test_catalog_round5_bc250_fit_matrix(model_id, quant, ctx, slots, verdict):
    assert calculate_fit(model_by_id(model_id), quant, ctx, parallel_slots=slots).verdict == verdict


def test_round5_additions_remain_preview_until_physical_bc250_qualification():
    from bc250_llm_mode.catalog import validation_tier

    added = CATALOG[24:]
    assert len(added) == 16
    assert all(validation_tier(model) == "preview" for model in added)
