"""R2.1: frozen JSON schema fixtures must load, migrate, and round-trip."""

import json
from pathlib import Path

import pytest

from bc250_llm_mode.state import StateStore

FIXTURES = Path(__file__).parent / "fixtures"


def test_v4_fixture_migrates_to_v5_with_telemetry_keys(tmp_path):
    source = json.loads((FIXTURES / "state_v4.json").read_text(encoding="utf-8"))
    assert source["schema_version"] == 4
    path = tmp_path / "state.json"
    path.write_text(json.dumps(source), encoding="utf-8")

    state = StateStore(path).load()
    assert state["schema_version"] == 5
    for key in ("bench_history", "autotune_history", "llamacpp_history"):
        assert state[key] == []
    # Real v4 data survives migration.
    assert state["current_model"] == "lfm25-26b"
    assert state["installed_models"][0]["id"] == "lfm25-26b"
    assert state["optimizations"]["parallel_slots"] == 4


def test_v5_fixture_round_trips_through_transaction(tmp_path):
    source = json.loads((FIXTURES / "state_v5.json").read_text(encoding="utf-8"))
    store = StateStore(tmp_path / "state.json")
    store.save(source)

    def bump(current):
        # Revision itself is owned by the store; just make a data change.
        current["bench_history"][0]["model"] = "lfm25-26b-verified"
        return current

    after = store.transaction(bump)
    reloaded = store.load()
    assert reloaded["schema_version"] == 5
    assert reloaded["llamacpp_build"]["describe"] == "b7598"
    assert reloaded["bench_history"][0]["predicted_per_second"] == 21.4
    assert reloaded["revision"] == after["revision"] == source["revision"] + 1


def test_both_fixtures_are_valid_json_documents():
    for name in ("state_v4.json", "state_v5.json"):
        data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert isinstance(data.get("installed_models"), list)
