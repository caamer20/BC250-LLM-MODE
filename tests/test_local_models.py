from bc250_llm_mode.local_models import (
    LocalModel,
    discover_local_models,
    fit_entry_for_local,
    installed_fit_entry,
)


def sparse_model(path, size=2 * 1024 * 1024):
    with path.open("wb") as handle:
        handle.seek(size - 1)
        handle.write(b"\0")


def test_discovers_existing_standard_gguf_without_reading_it(tmp_path, monkeypatch):
    from bc250_llm_mode import local_models

    monkeypatch.setattr(local_models, "_candidate_roots", lambda _state: (tmp_path,))
    target = tmp_path / "Qwen3.5-9B-Q5_K_M.gguf"
    sparse_model(target)
    result = discover_local_models({"models_dir": str(tmp_path), "installed_models": []})
    assert len(result.models) == 1
    model = result.models[0]
    assert model.path == str(target.resolve())
    assert model.quant == "Q5_K_M"
    assert model.catalog_id == "qwen35-9b"


def test_discovery_rejects_fused_max_and_f16_intermediate(tmp_path, monkeypatch):
    from bc250_llm_mode import local_models

    monkeypatch.setattr(local_models, "_candidate_roots", lambda _state: (tmp_path,))
    sparse_model(tmp_path / "model-MAX-Q5.gguf")
    sparse_model(tmp_path / "model-f16.gguf")
    result = discover_local_models({"models_dir": str(tmp_path), "installed_models": []})
    assert result.models == ()
    assert len(result.rejected) == 1


def test_unknown_local_fit_uses_actual_size_and_conservative_kv():
    local = LocalModel("local-1", "custom", "/models/custom.gguf", "Q4_K_M", 5.0, "unknown", 0, 72.0)
    entry = fit_entry_for_local(local)
    assert entry.weights_gib_by_quant == {"Q4_K_M": 5.0}
    assert entry.kv_kib_per_token == 72.0


def test_installed_local_model_rebuilds_fit_metadata(tmp_path):
    target = tmp_path / "Qwen3.5-9B-Q5_K_M.gguf"
    sparse_model(target)
    entry = installed_fit_entry(
        {
            "id": "local-example",
            "display_name": "Existing Qwen",
            "path": str(target),
            "quant": "Q5_K_M",
            "source": "local",
            "catalog_id": "qwen35-9b",
        }
    )
    assert entry.id == "local-example"
    assert entry.kv_kib_per_token == 72.0
    assert entry.weights_gib_by_quant["Q5_K_M"] > 0


def test_unmarked_managed_local_alias_is_not_treated_as_catalog_model(tmp_path):
    """Regression: durable local aliases used to raise Unknown catalog model."""
    target = tmp_path / "small-model-Q4_K_M.gguf"
    sparse_model(target)

    entry = installed_fit_entry(
        {
            "id": "local-a3515b591cfc",
            "display_name": "Small local model",
            "path": str(target),
            "quant": "UNKNOWN",
        }
    )

    assert entry.id == "local-a3515b591cfc"
    assert entry.weights_gib_by_quant["UNKNOWN"] > 0


def test_repository_source_kind_marks_installed_local_model(tmp_path):
    target = tmp_path / "Qwen3.8-2B-Q4_K_M.gguf"
    sparse_model(target)
    entry = installed_fit_entry(
        {
            "id": "local-small-qwen",
            "display_name": "Qwen3.8 2B",
            "path": str(target),
            "quant": "Q4_K_M",
            "source_kind": "local",
            "catalog_id": "qwen38-2b-distill",
        }
    )

    assert entry.id == "local-small-qwen"
    assert entry.family == "qwen35"


def test_discovery_recognizes_expanded_catalog_filename(tmp_path, monkeypatch):
    from bc250_llm_mode import local_models

    monkeypatch.setattr(local_models, "_candidate_roots", lambda _state: (tmp_path,))
    target = tmp_path / "Mistral-Nemo-Instruct-2407-Q5_K_M.gguf"
    sparse_model(target)
    result = discover_local_models({"models_dir": str(tmp_path), "installed_models": []})
    assert result.models[0].catalog_id == "mistral-nemo-12b"


def test_discovery_recognizes_lfm25_and_uses_low_kv_estimate(tmp_path, monkeypatch):
    from bc250_llm_mode import local_models

    monkeypatch.setattr(local_models, "_candidate_roots", lambda _state: (tmp_path,))
    target = tmp_path / "LFM2.5-2.6B-Q5_K_M.gguf"
    sparse_model(target)
    result = discover_local_models({"models_dir": str(tmp_path), "installed_models": []})
    assert result.models[0].catalog_id == "lfm25-26b"
    assert result.models[0].kv_kib_per_token == 8.0


def test_discovery_recognizes_qwen38_and_uses_qwen35_kv_estimate(tmp_path, monkeypatch):
    from bc250_llm_mode import local_models

    monkeypatch.setattr(local_models, "_candidate_roots", lambda _state: (tmp_path,))
    target = tmp_path / "Qwen3.8-9B-Q4_K_M.gguf"
    sparse_model(target)
    result = discover_local_models({"models_dir": str(tmp_path), "installed_models": []})
    assert result.models[0].catalog_id == "qwen38-9b"
    assert result.models[0].family == "qwen35"
    assert result.models[0].kv_kib_per_token == 72.0
