import pytest

from bc250_llm_mode.catalog import (
    CATALOG,
    calculate_fit,
    is_forbidden_artifact,
    model_by_id,
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
    assert len(CATALOG) == 13
    assert len({model.id for model in CATALOG}) == len(CATALOG)
    for model in CATALOG:
        for pattern in model.allow_globs.values():
            validate_artifact(model.repo, pattern)


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
    assert model.repo == "empero-ai/Qwen3.8-9B-GGUF"
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
