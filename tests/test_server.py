import pytest

from bc250_llm_mode import server
from bc250_llm_mode.server import _service_text, diagnose_server_log, generate_launcher


def test_launcher_keeps_mmap_and_vulkan_workarounds(tmp_path):
    state = {
        "app_dir": str(tmp_path),
        "llama_cpp_path": "/root/llama.cpp",
    }
    text = generate_launcher(state).read_text(encoding="utf-8")
    assert "--no-mmap" not in text
    assert "GGML_VK_DISABLE_F16=1" in text
    assert '"--defrag-thold", "0.1"' in text
    assert "--defrag-threshold" not in text
    assert '"--n-gpu-layers", "99"' in text
    # Handoff-only: the legacy state.json argv builder is gone.
    assert "state.json" not in text
    assert "LEGACY_STATE" not in text
    assert "<<'PYS'" not in text
    handoff = text.split("<<'PYH'\n", 1)[1].split("\nPYH\n", 1)[0]
    compile(handoff, "handoff-argv-builder", "exec")
    assert '"--n-gpu-layers", "99"' in text
    assert "--cache-type-k" in text
    assert "--parallel" in text
    assert "--alias" in text
    assert "--temp" in text
    assert "--top-p" in text
    assert "--top-k" in text
    assert "--min-p" in text
    assert "--repeat-penalty" in text
    assert "GGML_VK_DISABLE_F16=1" in text


def test_service_safeguards_are_bounded_by_selected_settings(tmp_path):
    state = {
        "container_name": "llm",
        "logs_dir": str(tmp_path),
        "optimizations": {
            "safeguards_enabled": True,
            "restart_window_sec": 240,
            "restart_burst": 2,
            "restart_delay_sec": 15,
        },
    }
    text = _service_text(state, tmp_path / "run-model.sh")
    assert "StartLimitIntervalSec=240" in text
    assert "StartLimitBurst=2" in text
    assert "RestartSec=15" in text


def test_root_service_uses_bazzite_safe_system_log_path(tmp_path, monkeypatch):
    monkeypatch.setattr("bc250_llm_mode.server.os.getuid", lambda: 0)
    state = {"container_name": "llm", "logs_dir": str(tmp_path)}
    text = _service_text(state, tmp_path / "run-model.sh")
    assert "StandardOutput=append:/var/log/bc250-llm-server.log" in text
    assert "StandardError=append:/var/log/bc250-llm-server.log" in text
    assert "-d -m 0700 -o 0 -g 0 /run/user/0" in text
    assert state["server_log"] == "/var/log/bc250-llm-server.log"


def test_diagnostics():
    assert "MTP metadata" in diagnose_server_log("missing tensor 'blk.2.nextn.foo'")
    assert "Block-count" in diagnose_server_log("missing tensor 'blk.32.attn'")
    assert "fused MAX" in diagnose_server_log("MAX imatrix fused ErrorOutOfDeviceMemory")


def test_health_reports_actual_server_context_and_slots(monkeypatch):
    def fake_get(url, timeout=5):
        if url.endswith("/health"):
            return {"status": "ok"}
        if url.endswith("/v1/models"):
            return {"data": [{"id": "lfm"}]}
        return {"default_generation_settings": {"n_ctx": 128000}, "total_slots": 4}

    monkeypatch.setattr(server, "_json_get", fake_get)
    monkeypatch.setattr(server, "system_metrics", lambda: {"vram_used_mib": 3500, "vram_total_mib": 12288})
    result = server.health_check(
        {"server_port": 8080, "current_model": "lfm", "current_ctx": 131072}, timeout=1
    )
    assert result["n_ctx"] == 128000
    assert result["context_per_slot"] == 128000
    assert result["context_total"] == 512000
    assert result["requested_ctx"] == 131072
    assert result["parallel_slots"] == 4
    # §11.2: model_id is the OBSERVED id from /v1/models, and the desired
    # model is reported separately with an explicit match flag.
    assert result["model_id"] == "lfm"
    assert result["desired_model"] == "lfm"
    assert result["model_matches_desired"] is True


def test_local_server_readiness_is_bounded_and_identity_checked(monkeypatch):
    calls = []

    def fake_get(url, timeout=5):
        calls.append((url, timeout))
        if url.endswith("/health"):
            return {"status": "ok"}
        return {"data": [{"id": "ornith-1.5-9b"}]}

    monkeypatch.setattr(server, "_json_get", fake_get)
    result = server.local_server_readiness(
        {"server_port": 8080, "current_model": "ornith-1.5-9b"},
        timeout=0.75,
    )
    assert result == {
        "healthy": True,
        "model_id": "ornith-1.5-9b",
        "desired_model": "ornith-1.5-9b",
        "model_matches_desired": True,
    }
    assert len(calls) == 2
    assert all(timeout == 0.75 for _url, timeout in calls)


def test_health_model_id_is_observed_not_desired(monkeypatch):
    """§11.2: the desired current_model is never proof of a live model."""

    def fake_get(url, timeout=5):
        if url.endswith("/health"):
            return {"status": "ok"}
        if url.endswith("/v1/models"):
            return {"data": [{"id": "actually-loaded.gguf"}]}
        return {"default_generation_settings": {"n_ctx": 8192}, "total_slots": 1}

    monkeypatch.setattr(server, "_json_get", fake_get)
    monkeypatch.setattr(server, "system_metrics", lambda: {})
    result = server.health_check(
        {"server_port": 8080, "current_model": "something-else",
         "current_ctx": 8192},
        timeout=1,
    )
    assert result["healthy"] is True
    assert result["model_id"] == "actually-loaded.gguf"
    assert result["desired_model"] == "something-else"
    assert result["model_matches_desired"] is False


def test_health_model_id_none_when_server_reports_nothing(monkeypatch):
    def fake_get(url, timeout=5):
        if url.endswith("/health"):
            return {"status": "ok"}
        if url.endswith("/v1/models"):
            return {"data": []}
        return {}

    monkeypatch.setattr(server, "_json_get", fake_get)
    monkeypatch.setattr(server, "system_metrics", lambda: {})
    result = server.health_check(
        {"server_port": 8080, "current_model": "desired-alias",
         "current_ctx": 8192},
        timeout=1,
    )
    # Fail-closed: no observed identity, and the desired alias is NOT
    # substituted as proof of liveness.
    assert result["model_id"] is None
    assert result["desired_model"] == "desired-alias"
    assert result["model_matches_desired"] is False


def test_health_polling_pulses_durable_operation_lease(monkeypatch):
    calls = []
    times = iter((0.0, 0.0, 1.0, 2.0))

    def unavailable(_url, timeout=5):
        raise OSError("not ready")

    monkeypatch.setattr(server, "_json_get", unavailable)
    with pytest.raises(TimeoutError):
        server.health_check(
            {"server_port": 8080},
            timeout=2,
            monotonic=lambda: next(times),
            sleep=lambda _seconds: None,
            pulse=lambda: calls.append("pulse"),
        )
    assert calls == ["pulse", "pulse"]
