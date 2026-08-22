"""GPU thermal watchdog for sustained inference on the BC-250.

Pure decision logic lives in ``thermal_action``; host side effects (clock
changes, service stop) are applied only by ``run_watchdog_once`` so the state
machine stays unit-testable without hardware.
"""

from __future__ import annotations

from typing import Any

from .logging_utils import CommandRunner

NOMINAL = "nominal"
THROTTLED = "throttled"
STOPPED = "stopped"


def read_gpu_temperature() -> float | None:
    from .hardware import _read_text, find_amd_gpu

    gpu = find_amd_gpu()
    if not gpu:
        return None
    for hwmon in sorted((gpu / "hwmon").glob("hwmon*")):
        raw = _read_text(hwmon / "temp1_input")
        if raw:
            try:
                return int(raw) / 1000.0
            except ValueError:
                continue
    return None


def thermal_action(
    current_state: str,
    temp_c: float,
    *,
    throttle_c: float,
    recovery_c: float,
    stop_c: float | None = None,
) -> str:
    """Hysteresis state machine: ok | throttle | hold | resume | stop."""
    if current_state == STOPPED:
        # A stopped server stays stopped; restarting is always a human decision.
        return "stop"
    if stop_c is not None and temp_c >= stop_c:
        return "stop"
    if current_state == THROTTLED:
        if temp_c <= recovery_c:
            return "resume"
        return "hold"
    if temp_c >= throttle_c:
        return "throttle"
    return "ok"


def _cool_max_mhz(settings: dict[str, Any]) -> int:
    ceiling = int(settings.get("gpu_max_mhz", 1850))
    floor = int(settings.get("gpu_min_mhz", 500))
    return max(floor + 200, min(ceiling, 1400))


def run_watchdog_once(
    store: Any,
    state: dict[str, Any],
    runner: CommandRunner,
) -> dict[str, Any]:
    """One watchdog poll. Returns {state, temperature, action}.

    A latched stop is idempotent: polling reports ``latched`` and never calls
    stop_service again; only an explicit reset (safe temperature + human
    intent) clears the latch. Throttling preserves the user's configured GPU
    profile in a dedicated baseline so recovery restores it exactly.
    """
    from .optimize import apply_gpu_clock_limit, normalized_settings, restore_gpu_profile

    settings = normalized_settings(state.get("optimizations"))
    if not settings.get("thermal_watchdog_enabled"):
        return {"state": "disabled", "temperature": None, "action": "none"}
    current = str(state.get("thermal_watchdog_state", NOMINAL))
    if current == STOPPED:
        # Latched: one stop already happened. Report without side effects.
        return {"state": "latched", "temperature": None, "action": "latched"}
    temp = read_gpu_temperature()
    if temp is None:
        return {
            "state": "degraded",
            "temperature": None,
            "action": "none",
            "warning": "Thermal watchdog is enabled but no GPU temperature sensor is readable.",
        }
    action = thermal_action(
        current,
        temp,
        throttle_c=float(settings["thermal_throttle_c"]),
        recovery_c=float(settings["thermal_recovery_c"]),
        stop_c=None if current == STOPPED else float(settings["thermal_stop_c"]),
    )
    if action == "throttle":
        if not state.get("thermal_watchdog_baseline"):
            state["thermal_watchdog_baseline"] = {
                "gpu_max_mhz": int(settings["gpu_max_mhz"]),
                "gpu_min_mhz": int(settings["gpu_min_mhz"]),
                "governor_profile": settings.get("governor_profile", "balanced"),
            }
            runner.emit(
                "Thermal watchdog: saved original GPU profile "
                f"({settings['gpu_min_mhz']}-{settings['gpu_max_mhz']} MHz) before throttling"
            )
        cool = _cool_max_mhz(settings)
        runner.emit(f"Thermal watchdog: {temp:.1f}°C >= throttle point; capping GPU clocks to {cool} MHz")
        apply_gpu_clock_limit(state, cool, runner)
        new_state = THROTTLED
    elif action == "resume":
        baseline = state.get("thermal_watchdog_baseline") or {}
        restored = normalized_settings(state.get("optimizations"))
        restored.update(
            governor_profile=baseline.get("governor_profile", restored["governor_profile"]),
            gpu_min_mhz=int(baseline.get("gpu_min_mhz", restored["gpu_min_mhz"])),
            gpu_max_mhz=int(baseline.get("gpu_max_mhz", restored["gpu_max_mhz"])),
        )
        runner.emit(
            f"Thermal watchdog: {temp:.1f}°C <= recovery point; restoring saved GPU profile "
            f"({restored['gpu_min_mhz']}-{restored['gpu_max_mhz']} MHz)"
        )
        restore_gpu_profile(state, restored, runner)
        state.pop("thermal_watchdog_baseline", None)
        new_state = NOMINAL
    elif action == "stop":
        from .server import stop_service

        runner.emit(f"Thermal watchdog: {temp:.1f}°C hit the stop point; stopping the model server")
        stop_service(state, runner)
        new_state = STOPPED
    else:
        new_state = THROTTLED if action == "hold" else current
    state["thermal_watchdog_state"] = new_state
    store.save(state)
    result: dict[str, Any] = {"state": new_state, "temperature": round(temp, 1), "action": action}
    if state.get("thermal_watchdog_baseline"):
        result["baseline_preserved"] = True
    return result


def reset_latch(
    store: Any,
    state: dict[str, Any],
    runner: CommandRunner,
    *, require_safe_temperature: bool = True,
) -> dict[str, Any]:
    """Explicit human reset of a latched thermal stop.

    Restores any preserved GPU baseline and clears the latch only when the
    current temperature is at or below the recovery threshold.
    """
    from .optimize import normalized_settings

    settings = normalized_settings(state.get("optimizations"))
    temp = read_gpu_temperature()
    if require_safe_temperature and temp is not None:
        recovery = float(settings["thermal_recovery_c"])
        if temp > recovery:
            raise RuntimeError(
                f"GPU is still at {temp:.1f}°C (recovery threshold {recovery:.0f}°C); "
                "let it cool before resetting the thermal latch."
            )
    baseline = state.pop("thermal_watchdog_baseline", None)
    if baseline:
        restored = dict(settings)
        restored.update(
            governor_profile=baseline.get("governor_profile", settings.get("governor_profile", "balanced")),
            gpu_min_mhz=int(baseline.get("gpu_min_mhz", settings["gpu_min_mhz"])),
            gpu_max_mhz=int(baseline.get("gpu_max_mhz", settings["gpu_max_mhz"])),
        )
        restore_gpu_profile(state, restored, runner)
    state["thermal_watchdog_state"] = NOMINAL
    store.save(state)
    runner.emit("Thermal latch cleared by explicit reset; model server may be started manually.")
    return {"state": NOMINAL, "temperature": round(temp, 1) if temp is not None else None}


def watch_loop(
    store: Any,
    state: dict[str, Any],
    runner: CommandRunner,
    *,
    interval_sec: float = 5.0,
    iterations: int | None = None,
) -> None:
    import time

    count = 0
    while iterations is None or count < iterations:
        result = run_watchdog_once(store, state, runner)
        if result["temperature"] is not None and result["action"] != "none":
            runner.emit(f"watch: {result['state']} {result['temperature']}°C ({result['action']})")
        count += 1
        time.sleep(interval_sec)
