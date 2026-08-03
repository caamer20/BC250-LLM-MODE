import json

from bc250_llm_mode.state import StateStore


def test_state_defaults_and_atomic_update(tmp_path):
    store = StateStore(tmp_path / "state.json")
    state = store.load()
    assert state["current_ctx"] == 8192
    store.update(disclaimer_ack=True)
    data = json.loads(store.path.read_text())
    assert data["disclaimer_ack"] is True
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_v1_phase_is_migrated_for_new_optimize_step(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.path.write_text('{"schema_version": 1, "setup_phase": 7}', encoding="utf-8")
    state = store.load()
    assert state["schema_version"] == 2
    assert state["setup_phase"] == 8
