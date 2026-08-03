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


def test_discovery_recognizes_expanded_catalog_filename(tmp_path, monkeypatch):
    from bc250_llm_mode import local_models

    monkeypatch.setattr(local_models, "_candidate_roots", lambda _state: (tmp_path,))
    target = tmp_path / "Mistral-Nemo-Instruct-2407-Q5_K_M.gguf"
    sparse_model(target)
    result = discover_local_models({"models_dir": str(tmp_path), "installed_models": []})
    assert result.models[0].catalog_id == "mistral-nemo-12b"
