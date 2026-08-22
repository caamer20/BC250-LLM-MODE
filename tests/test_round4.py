import subprocess

import pytest

from bc250_llm_mode import optimize, server, thermals
from bc250_llm_mode.optimize import validate_settings


class FakeRunner:
    def __init__(self):
        self.commands = []
        self.messages = []

    def run(self, command, **kwargs):
        self.commands.append((list(command), kwargs))
        return subprocess.CompletedProcess(list(command), 0, "", "")

    def emit(self, message):
        self.messages.append(message)


def test_governor_profiles_apply_validated_ranges():
    balanced = validate_settings({"governor_profile": "balanced"})
    assert (balanced["gpu_min_mhz"], balanced["gpu_max_mhz"]) == (500, 1850)
    assert validate_settings({"governor_profile": "cool-quiet"})["gpu_max_mhz"] == 1400
    maximum = validate_settings({"governor_profile": "maximum"})
    assert (maximum["gpu_min_mhz"], maximum["gpu_max_mhz"]) == (800, 2000)


def test_invalid_governor_profile_rejected():
    with pytest.raises(ValueError, match="governor_profile"):
        validate_settings({"governor_profile": "turbo-9000"})


def test_threads_and_fast_sync_validate():
    checked = validate_settings({"threads": 8, "fast_sync": True})
    assert checked["threads"] == 8 and checked["fast_sync"] is True
    with pytest.raises(ValueError):
        validate_settings({"threads": 65})


def test_thermal_stop_point_must_exceed_throttle():
    assert validate_settings({"thermal_throttle_c": 85, "thermal_stop_c": 95})["thermal_stop_c"] == 95
    with pytest.raises(ValueError, match="stop point"):
        validate_settings({"thermal_throttle_c": 85, "thermal_stop_c": 85})


def test_apply_gpu_clock_limit_forces_custom_tuning():
    from bc250_llm_mode.optimize import apply_gpu_clock_limit

    state = {"disclaimer_ack": True}
    seen = {}
    orig = optimize.apply_optimizations
    optimize.apply_optimizations = lambda st, settings, runner: seen.update(settings) or st
    try:
        apply_gpu_clock_limit(state, 1400, FakeRunner())
    finally:
        optimize.apply_optimizations = orig
    assert seen["gpu_tuning_enabled"] is True and seen["gpu_max_mhz"] == 1400


def test_thermal_action_hysteresis():
    args = dict(throttle_c=85.0, recovery_c=75.0, stop_c=95.0)
    assert thermals.thermal_action("nominal", 60.0, **args) == "ok"
    assert thermals.thermal_action("nominal", 86.0, **args) == "throttle"
    assert thermals.thermal_action("throttled", 84.0, **args) == "hold"
    assert thermals.thermal_action("throttled", 74.0, **args) == "resume"
    assert thermals.thermal_action("nominal", 96.0, **args) == "stop"
    # A stopped server never resumes automatically.
    assert thermals.thermal_action("stopped", 30.0, **args) == "stop"


def test_watchdog_disabled_and_degraded_sensor(monkeypatch, tmp_path):
    from bc250_llm_mode.state import StateStore

    store = StateStore(tmp_path / "state.json")
    state = store.load()
    assert thermals.run_watchdog_once(store, state, FakeRunner())["state"] == "disabled"
    state["optimizations"] = dict(state["optimizations"], thermal_watchdog_enabled=True)
    monkeypatch.setattr(thermals, "read_gpu_temperature", lambda: None)
    result = thermals.run_watchdog_once(store, state, FakeRunner())
    assert result["state"] == "degraded"
    # A missing sensor under an enabled watchdog is a prominent safety warning.
    assert "warning" in result


def test_watchdog_throttles_then_resumes_exact_profile(monkeypatch, tmp_path):
    from bc250_llm_mode.state import StateStore

    store = StateStore(tmp_path / "state.json")
    state = store.load()
    state["disclaimer_ack"] = True
    state["optimizations"] = dict(
        state["optimizations"],
        thermal_watchdog_enabled=True,
        gpu_tuning_enabled=True,
        governor_profile="balanced",
        gpu_max_mhz=1850,
    )
    temps = iter([90.0, 90.0, 60.0])
    monkeypatch.setattr(thermals, "read_gpu_temperature", lambda: next(temps))
    clock_calls = []
    monkeypatch.setattr(
        optimize, "apply_gpu_clock_limit", lambda st, mhz, rn: clock_calls.append(mhz) or st
    )
    profile_restores = []
    monkeypatch.setattr(
        optimize, "_apply_gpu", lambda checked, st, rn: profile_restores.append(checked["gpu_max_mhz"])
    )
    first = thermals.run_watchdog_once(store, state, FakeRunner())
    assert (first["action"], first["state"]) == ("throttle", "throttled")
    # Hold poll keeps the latch without new clock commands.
    hold = thermals.run_watchdog_once(store, state, FakeRunner())
    assert hold["action"] == "hold"
    second = thermals.run_watchdog_once(store, state, FakeRunner())
    assert (second["action"], second["state"]) == ("resume", "nominal")
    # The temporary cap must never overwrite the user's configured ceiling.
    assert clock_calls[0] == 1400
    assert profile_restores[-1] == 1850
    assert state.get("thermal_watchdog_baseline") is None


def test_watchdog_stop_latches_and_never_stops_twice(monkeypatch, tmp_path):
    from bc250_llm_mode.state import StateStore

    store = StateStore(tmp_path / "state.json")
    state = store.load()
    state["optimizations"] = dict(state["optimizations"], thermal_watchdog_enabled=True)
    stops = []

    def temp_seq():
        yield 99.0
        while True:
            yield 50.0

    gen = temp_seq()
    monkeypatch.setattr(thermals, "read_gpu_temperature", lambda: next(gen))
    monkeypatch.setattr(
        "bc250_llm_mode.server.stop_service", lambda st, rn: stops.append(True)
    )
    first = thermals.run_watchdog_once(store, state, FakeRunner())
    assert first["action"] == "stop"
    for _ in range(3):
        result = thermals.run_watchdog_once(store, state, FakeRunner())
        assert result["action"] == "latched" and result["state"] == "latched"
    assert len(stops) == 1, "a latched stop is idempotent"


def test_reset_latch_requires_safe_temperature(monkeypatch, tmp_path):
    from bc250_llm_mode.state import StateStore

    store = StateStore(tmp_path / "state.json")
    state = store.load()
    state.update(thermal_watchdog_state="stopped")
    state["optimizations"] = dict(state["optimizations"], thermal_watchdog_enabled=True)
    monkeypatch.setattr(thermals, "read_gpu_temperature", lambda: 80.0)
    with pytest.raises(RuntimeError, match="let it cool"):
        thermals.reset_latch(store, state, FakeRunner())
    monkeypatch.setattr(thermals, "read_gpu_temperature", lambda: 60.0)
    result = thermals.reset_latch(store, state, FakeRunner())
    assert result["state"] == "nominal"


def test_launcher_adds_threads_cache_reuse_and_conditional_sync(tmp_path):
    state = {
        "app_dir": str(tmp_path),
        "llama_cpp_path": "/root/llama.cpp",
        "optimizations": {"threads": 8, "fast_sync": False},
    }
    text = server.generate_launcher(state).read_text(encoding="utf-8")
    embedded = text.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    compile(embedded, "generated-launcher", "exec")
    assert '--threads "${CFG[14]}"' in text and '--cache-reuse 256' in text
    assert "--defrag-threshold 0.1" in text
    assert 'if [ "${CFG[15]}" != "1" ]; then' in text
    assert "_cores.add((_socket, _val))" in embedded
    assert "--no-mmap" not in text


def test_service_memory_guards_only_when_safeguards_enabled(tmp_path):
    base = {"container_name": "llm", "logs_dir": str(tmp_path)}
    guarded = server._service_text(
        {**base, "optimizations": {"safeguards_enabled": True}}, tmp_path / "l.sh"
    )
    plain = server._service_text(base, tmp_path / "l.sh")
    for needle in ("MemoryHigh=3000M", "MemoryMax=3500M", "OOMScoreAdjust=500", "IOSchedulingClass=idle"):
        assert needle in guarded
    assert "MemoryMax" not in plain
