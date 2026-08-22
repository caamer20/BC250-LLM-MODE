import json

from bc250_llm_mode import state as state_module
from bc250_llm_mode.state import StateStore


def test_state_defaults_and_atomic_update(tmp_path):
    store = StateStore(tmp_path / "state.json")
    state = store.load()
    assert state["current_ctx"] == 8192
    assert state["https_sharing_enabled"] is False
    assert state["https_webui_port"] == 8443
    assert state["https_api_port"] == 10000
    store.update(disclaimer_ack=True)
    data = json.loads(store.path.read_text())
    assert data["disclaimer_ack"] is True
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_v1_phase_is_migrated_for_new_optimize_step(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.path.write_text('{"schema_version": 1, "setup_phase": 7}', encoding="utf-8")
    state = store.load()
    assert state["schema_version"] == 5
    assert state["setup_phase"] == 8


def test_llm_session_reconciles_to_desktop_after_boot(tmp_path, monkeypatch):
    monkeypatch.setattr(state_module, "_current_boot_id", lambda: "new-boot")
    store = StateStore(tmp_path / "state.json")
    store.path.write_text(
        '{"schema_version": 3, "boot_policy": "desktop", "system_mode": "llm-session", '
        '"llm_session_boot_id": "old-boot", "llm_mode_done": true}',
        encoding="utf-8",
    )
    state = store.load()
    assert state["system_mode"] == "desktop"
    assert state["llm_mode_done"] is False
    assert state["llm_session_boot_id"] is None


def test_v3_gpu_tuning_name_is_migrated(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.path.write_text(
        '{"schema_version": 3, "optimizations": {"gpu_enabled": true, "parallel_slots": 2}}',
        encoding="utf-8",
    )
    state = store.load()
    assert state["schema_version"] == 5
    assert state["optimizations"]["gpu_tuning_enabled"] is True
    assert "gpu_enabled" not in state["optimizations"]
    assert state["optimizations"]["parallel_slots"] == 2
