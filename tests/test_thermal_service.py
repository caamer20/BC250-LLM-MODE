"""A1 (Road to 1.0): the thermal latch is safety-authoritative.

Given a latched thermal stop, a concurrent stale status/configuration write
cannot clear or downgrade the latch; a reset above the resume threshold or
without a sensor is rejected without changing durable state; failed profile
restoration retains durable recovery evidence.
"""

from __future__ import annotations

import json

import pytest

from bc250_llm_mode import thermals
from bc250_llm_mode.compat_state import CompatStateStore
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.services import (
    ThermalLatchProtected,
    ThermalStateService,
)


class FakeRunner:
    def emit(self, *_lines):
        pass

    def run(self, *a, **k):
        raise AssertionError("no host commands in service tests")


def _store(tmp_path):
    return CompatStateStore(AppPaths.temporary(tmp_path / "root"))


def _latch_stopped(store) -> ThermalStateService:
    service = ThermalStateService.for_database(store.paths.database_path)
    service.ensure_throttle({
        "gpu_max_mhz": 1850, "gpu_min_mhz": 500, "governor_profile": "balanced",
    })
    service.mark_stopped()
    return service


def test_stale_whole_state_save_cannot_clear_or_downgrade_latch(tmp_path):
    """The plan's safest-first test, end to end."""
    store = _store(tmp_path)
    _latch_stopped(store)

    # A stale GUI/CLI draft loads, claims nominal, and saves whole-state.
    draft = store.load()
    assert draft["thermal_watchdog_state"] == "stopped"
    draft["thermal_watchdog_state"] = "nominal"
    draft["thermal_watchdog_baseline"] = None
    draft["server_port"] = 1234  # unrelated change must still persist
    store.save(draft)

    reloaded = store.load()
    assert reloaded["thermal_watchdog_state"] == "stopped"
    assert reloaded["thermal_watchdog_baseline"] is not None
    assert reloaded["server_port"] == 1234

    # Direct service downgrade attempts are refused at the persistence layer.
    service = ThermalStateService.for_database(store.paths.database_path)
    with pytest.raises(ThermalLatchProtected):
        service.mark_nominal(clear_baseline=True)
    with pytest.raises(ThermalLatchProtected):
        service.ensure_throttle({"gpu_max_mhz": 900})
    current = service.current()
    assert current["latch_state"] == "stopped"
    assert current["baseline"]["gpu_max_mhz"] == 1850


def test_reset_above_resume_threshold_rejected_without_durable_change(
    tmp_path, monkeypatch
):
    store = _store(tmp_path)
    service = _latch_stopped(store)
    before = service.current()
    state = store.load()

    monkeypatch.setattr(thermals, "read_gpu_temperature", lambda: 90.0)
    with pytest.raises(RuntimeError, match="let it cool"):
        thermals.reset_latch(store, state, FakeRunner())

    assert service.current() == before


def test_missing_sensor_cannot_clear_latch(tmp_path, monkeypatch):
    store = _store(tmp_path)
    service = _latch_stopped(store)
    before = service.current()
    state = store.load()

    monkeypatch.setattr(thermals, "read_gpu_temperature", lambda: None)
    with pytest.raises(RuntimeError, match="sensor"):
        thermals.reset_latch(store, state, FakeRunner())

    assert service.current() == before


def test_reset_after_safe_probe_clears_latch_and_baseline(
    tmp_path, monkeypatch
):
    store = _store(tmp_path)
    _latch_stopped(store)
    state = store.load()
    monkeypatch.setattr(thermals, "read_gpu_temperature", lambda: 55.0)
    monkeypatch.setattr(
        "bc250_llm_mode.optimize.restore_gpu_profile",
        lambda st, settings, rn: st,
    )
    result = thermals.reset_latch(store, state, FakeRunner())
    assert result["state"] == "nominal"

    fresh = CompatStateStore(AppPaths.temporary(tmp_path / "root"))
    assert fresh.load()["thermal_watchdog_state"] == "nominal"
    assert ThermalStateService.for_database(fresh.paths.database_path).current()["baseline"] is None


def test_failed_profile_restoration_retains_recovery_evidence(
    tmp_path, monkeypatch
):
    store = _store(tmp_path)
    _latch_stopped(store)
    state = store.load()
    monkeypatch.setattr(thermals, "read_gpu_temperature", lambda: 50.0)

    def broken_restore(st, settings, rn):
        raise RuntimeError("clock write failed")

    monkeypatch.setattr(
        "bc250_llm_mode.optimize.restore_gpu_profile", broken_restore
    )
    with pytest.raises(RuntimeError, match="clock write failed"):
        thermals.reset_latch(store, state, FakeRunner())

    service = ThermalStateService.for_database(store.paths.database_path)
    current = service.current()
    assert current["latch_state"] == "stopped"
    assert "clock write failed" in (current["baseline"] or {}).get(
        "last_restore_error", ""
    )

    # Recovery completes once restoration works again.
    monkeypatch.setattr(
        "bc250_llm_mode.optimize.restore_gpu_profile",
        lambda st, settings, rn: st,
    )
    thermals.reset_latch(store, state, FakeRunner())
    assert service.current()["latch_state"] == "nominal"


def test_latch_survives_new_service_and_store_instances(tmp_path):
    store = _store(tmp_path)
    _latch_stopped(store)

    fresh_store = CompatStateStore(AppPaths.temporary(tmp_path / "root"))
    assert fresh_store.load()["thermal_watchdog_state"] == "stopped"
    assert (
        ThermalStateService.for_database(fresh_store.paths.database_path).current()["latch_state"]
        == "stopped"
    )


def test_status_probe_cannot_overwrite_a_latched_stop(tmp_path, monkeypatch):
    store = _store(tmp_path)
    service = _latch_stopped(store)
    before = service.current()
    state = store.load()
    state["optimizations"] = dict(
        state["optimizations"], thermal_watchdog_enabled=True
    )
    monkeypatch.setattr(thermals, "read_gpu_temperature", lambda: 30.0)

    result = thermals.run_watchdog_once(store, state, FakeRunner())
    assert result["action"] == "latched"
    assert service.current() == before


def test_stop_intent_persisted_before_server_stop(tmp_path, monkeypatch):
    store = _store(tmp_path)
    state = store.load()
    state["optimizations"] = dict(
        state["optimizations"], thermal_watchdog_enabled=True
    )
    monkeypatch.setattr(thermals, "read_gpu_temperature", lambda: 99.0)

    observed_at_stop = []

    def fake_stop(st, rn):
        # The latch must already be durable when the server stops.
        observed_at_stop.append(
            ThermalStateService.for_database(store.paths.database_path).current()["latch_state"]
        )

    monkeypatch.setattr("bc250_llm_mode.server.stop_service", fake_stop)
    result = thermals.run_watchdog_once(store, state, FakeRunner())
    assert result["action"] == "stop"
    assert observed_at_stop == ["stopped"]

    fresh = CompatStateStore(AppPaths.temporary(tmp_path / "root"))
    assert fresh.load()["thermal_watchdog_state"] == "stopped"


def test_benchmark_recording_is_narrow_capped_and_enriched(tmp_path):
    """A2: benchmark records append via the capped repository."""
    from bc250_llm_mode import chat

    store = _store(tmp_path)
    state = store.load()
    state.update(current_model="lfm25-26b", current_ctx=16384)
    state["optimizations"] = {**state["optimizations"], "parallel_slots": 3}
    store.save(state)

    loaded = store.load()
    for i in range(25):
        chat.record_benchmark(
            store,
            loaded,
            {"predicted_per_second": float(i), "max_tokens": 64},
        )

    history = CompatStateStore(AppPaths.temporary(tmp_path / "root")).load()[
        "bench_history"
    ]
    assert len(history) == 20, "retention cap enforced by the repository"
    newest = history[-1]
    assert newest["model"] == "lfm25-26b"
    assert newest["context"] == 16384
    assert newest["slots"] == 3

    # Content canary: even if a caller passes prompt/generated text in the
    # result, none of it may reach durable benchmark history.
    chat.record_benchmark(
        store,
        loaded,
        {
            "predicted_per_second": 99.0,
            "prompt": "SECRET-PROMPT-CANARY should not be stored",
            "generated": "SECRET-GENERATION-CANARY",
        },
    )
    dump = json.dumps(CompatStateStore(AppPaths.temporary(tmp_path / "root")).load()["bench_history"])
    assert "SECRET-PROMPT-CANARY" not in dump
    assert "SECRET-GENERATION-CANARY" not in dump


def test_autotune_repository_append_caps_with_stable_order(tmp_path):
    store = _store(tmp_path)
    for i in range(45):
        store.autotune.append({"ctx": 8192, "median": float(i)})
    rows = store.autotune.list()
    assert len(rows) == 40
    assert [r["median"] for r in rows] == [float(i) for i in range(5, 45)]