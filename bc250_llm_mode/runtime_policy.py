"""Current-boot inference supervisor, owned by the model's systemd unit.

No tray, login service or boot enablement. The child and monitor share one unit;
if the monitor exits, its child is terminated before restart is considered.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import time
import urllib.request
from pathlib import Path

from .fsops import atomic_write_text


def observed_policy(profile: Path) -> dict:
    try:
        with (profile / "runtime-policy.json").open("rb") as source:
            raw = source.read(4097)
        if len(raw) > 4096:
            raise ValueError("size")
        data = json.loads(raw)
        if data.get("boot_id") != _boot_id() or not 0 <= time.monotonic() - data["monotonic"] <= 15:
            return {"monitoring": "unavailable", "reason": "supervisor heartbeat is stale"}
        os.kill(int(data["pid"]), 0)
        if not data.get("process_start") or data["process_start"] != _process_start(int(data["pid"])):
            raise ValueError("supervisor identity changed")
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return {"monitoring": "unavailable", "reason": "no live current-boot supervisor"}


def _process_start(pid):
    try:
        with Path(f"/proc/{pid}/stat").open("rb") as source:
            value = source.read(8193)
        return value.rsplit(b")", 1)[1].split()[19].decode() if len(value) <= 8192 else None
    except (OSError, ValueError, IndexError):
        return None


def available_host_memory():
    try:
        with Path("/proc/meminfo").open("rb") as source:
            raw = source.read(65537)
        if len(raw) > 65536:
            return None
        for line in raw.decode().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, UnicodeError):
        pass
    return None


def _boot_id():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return "unavailable"


def request_activity(port: int):
    """Observe counters covering all backend clients, without prompt content."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2) as response:
            raw = response.read(131073)
        if len(raw) > 131072:
            return None
        values = {}
        for line in raw.decode().splitlines():
            if line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) == 2:
                values[parts[0]] = float(parts[1])
        active = values.get("llamacpp:requests_processing")
        deferred = values.get("llamacpp:requests_deferred")
        prompt = values.get("llamacpp:prompt_tokens_total")
        output = values.get("llamacpp:tokens_predicted_total")
        if any(value is None or not math.isfinite(value) or value < 0 for value in (active, deferred, prompt, output)):
            return None
        return {"active": int(active + deferred), "counter": [prompt, output]}
    except (OSError, ValueError, UnicodeError):
        return None


def run(profile: Path, command: list[str]) -> int:
    from .app import Application
    from .paths import AppPaths
    from .server import require_safe_start
    from .thermals import read_gpu_temperature, run_watchdog_once
    from .legacy_import import utcnow

    app = Application.compose(AppPaths.from_app_dir(profile)).require_operational()
    state = app.read_model()
    require_safe_start(state)
    enabled = bool((state.get("optimizations") or {}).get("thermal_watchdog_enabled"))
    if enabled and read_gpu_temperature() is None:
        raise RuntimeError("Enabled thermal monitoring requires a readable GPU sensor")
    available = available_host_memory()
    if available is not None and available < 512 * 1024**2:
        raise RuntimeError("Less than 512 MiB host memory is available; stop optional services before starting a model")
    child = subprocess.Popen(command)
    previous_counter = None
    last_request_at = None
    try:
        while child.poll() is None:
            state = app.read_model()  # Fresh settings and authoritative latch.
            result = run_watchdog_once(app, state, app.runner())
            activity = request_activity(int(state.get("server_port") or 8080))
            if activity and (activity["active"] or activity["counter"] != previous_counter):
                last_request_at = utcnow()
            previous_counter = activity["counter"] if activity else None
            decision = app.idle_policy.enforce_once(
                last_request_at=last_request_at,
                active_requests=activity["active"] if activity else None)
            status = {"pid": os.getpid(), "process_start": _process_start(os.getpid()), "boot_id": _boot_id(), "monotonic": time.monotonic(),
                      "observed_at": utcnow(), "monitoring": "disabled" if result["state"] == "disabled"
                      else "unavailable" if result["state"] == "degraded" else "active",
                      "temperature": result.get("temperature"), "stop_outcome": result.get("stop_outcome"),
                      "active_requests": activity["active"] if activity else None,
                      "idle_policy": decision["policy"], "idle_reason": decision["reason_code"]}
            atomic_write_text(profile / "runtime-policy.json", json.dumps(status), mode=0o600)
            # A missing sensor after startup cannot silently disable protection.
            if result["state"] == "degraded":
                app.safety.mark_stopped()
                child.terminate()
            try:
                return child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        return int(child.returncode or 0)
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("explicit model command required")
    return run(args.profile, command)


if __name__ == "__main__":
    raise SystemExit(main())
