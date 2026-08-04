from bc250_llm_mode.server import _service_text, diagnose_server_log, generate_launcher


def test_launcher_keeps_mmap_and_vulkan_workarounds(tmp_path):
    state = {
        "app_dir": str(tmp_path),
        "llama_cpp_path": "/root/llama.cpp",
    }
    text = generate_launcher(state).read_text(encoding="utf-8")
    assert "--no-mmap" not in text
    assert "GGML_VK_DISABLE_F16=1" in text
    assert "--n-gpu-layers 99" in text
    assert "--cache-type-k \"${CFG[6]}\"" in text
    assert "--flash-attn \"${CFG[3]}\"" in text
    assert "--batch-size \"${CFG[4]}\"" in text


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
