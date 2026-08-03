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
    assert len(CATALOG) == 10
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
