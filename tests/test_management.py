import json
import subprocess

import pytest

from bc250_llm_mode import model_manager, openwebui, server, tailscale


class FakeRunner:
    def __init__(self, outputs=None):
        self.outputs = outputs or {}
        self.commands = []
        self.messages = []

    def run(self, command, **kwargs):
        command = list(command)
        self.commands.append((command, kwargs))
        returncode, output = self.outputs.get(tuple(command), (0, ""))
        return subprocess.CompletedProcess(command, returncode, output, "")

    def emit(self, message):
        self.messages.append(message)


def test_llm_service_status_and_stop_use_systemd(monkeypatch):
    monkeypatch.setattr(server, "elevated", lambda command: command)
    service_name = "bc250-llm.service"
    outputs = {
        ("systemctl", "show", service_name, "--property=LoadState", "--value"): (0, "loaded\n"),
        ("systemctl", "show", service_name, "--property=ActiveState", "--value"): (0, "active\n"),
        ("systemctl", "show", service_name, "--property=SubState", "--value"): (0, "running\n"),
        ("systemctl", "show", service_name, "--property=UnitFileState", "--value"): (0, "enabled\n"),
    }
    runner = FakeRunner(outputs)
    status = server.service_status({"service_name": service_name}, runner)
    assert status["installed"] is True
    assert status["active"] is True
    assert status["enabled"] is True

    server.stop_service({"service_name": service_name}, runner)
    assert (["systemctl", "stop", service_name], {"check": False}) in runner.commands
    assert (["systemctl", "reset-failed", service_name], {"check": False}) in runner.commands


def test_service_install_starts_now_but_disables_next_boot(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "elevated", lambda command: command)
    monkeypatch.setattr(server.os, "getuid", lambda: 0)
    runner = FakeRunner()
    state = {
        "app_dir": str(tmp_path),
        "llama_cpp_path": "/root/llama.cpp",
        "logs_dir": str(tmp_path),
        "container_name": "llm",
        "service_name": "bc250-llm.service",
    }

    server.install_service(state, runner)

    commands = [command for command, _kwargs in runner.commands]
    assert ["systemctl", "disable", "bc250-llm.service"] in commands
    assert ["systemctl", "start", "bc250-llm.service"] in commands
    assert not any(command[:3] == ["systemctl", "enable", "--now"] for command in commands)
    assert state["desktop_on_reboot"] is True
    assert state["llm_autostart"] is False


def test_openwebui_status_and_lifecycle(monkeypatch):
    monkeypatch.setattr(openwebui.shutil, "which", lambda name: "/usr/bin/podman")
    outputs = {
        ("podman", "container", "exists", openwebui.CONTAINER): (0, ""),
        ("podman", "inspect", "--format", "{{.State.Status}}", openwebui.CONTAINER): (0, "running\n"),
    }
    runner = FakeRunner(outputs)
    state = {}
    status = openwebui.open_webui_status(state, runner)
    assert status["installed"] is True
    assert status["running"] is True

    openwebui.stop_open_webui(state, runner)
    assert any(command[:3] == ["podman", "stop", "--time"] for command, _ in runner.commands)


def test_tailscale_status_separates_daemon_and_tailnet(monkeypatch):
    monkeypatch.setattr(
        tailscale.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in {"tailscale", "tailscaled"} else None,
    )
    payload = {
        "BackendState": "Running",
        "Self": {"Online": True, "DNSName": "bc250.example.ts.net.", "TailscaleIPs": ["100.64.0.1"]},
    }
    outputs = {
        ("systemctl", "show", tailscale.SERVICE, "--property=LoadState", "--value"): (0, "loaded\n"),
        ("systemctl", "show", tailscale.SERVICE, "--property=ActiveState", "--value"): (0, "active\n"),
        ("/usr/bin/tailscale", "status", "--json"): (0, json.dumps(payload)),
    }
    runner = FakeRunner(outputs)
    status = tailscale.tailscale_status(runner)
    assert status["daemon_active"] is True
    assert status["connected"] is True
    assert status["dns_name"] == "bc250.example.ts.net."
    status_calls = [kwargs for command, kwargs in runner.commands if command[-2:] == ["status", "--json"]]
    assert status_calls == [{"check": False, "emit_output": False}]


def test_tailscale_connect_starts_daemon_then_runs_up(monkeypatch):
    monkeypatch.setattr(tailscale, "elevated", lambda command: command)
    monkeypatch.setattr(tailscale.shutil, "which", lambda name: f"/usr/bin/{name}")
    runner = FakeRunner()
    tailscale.connect_tailscale(runner)
    commands = [command for command, _ in runner.commands]
    assert commands[0] == ["systemctl", "start", tailscale.SERVICE]
    assert commands[1] == ["tailscale", "up"]


def test_parallel_slot_change_checks_fit_then_restarts(monkeypatch):
    state = {
        "current_model": "lfm25-26b",
        "current_ctx": 128000,
        "installed_models": [{"id": "lfm25-26b", "quant": "Q5_K_M"}],
    }

    class Store:
        saved = None

        def save(self, value):
            self.saved = value

    restarted = []
    monkeypatch.setattr(model_manager, "restart_service", lambda *_args: restarted.append(True))
    monkeypatch.setattr(model_manager, "health_check", lambda *_args: {"healthy": True})
    result = model_manager.change_parallel_slots(Store(), state, 4, FakeRunner())
    assert "128,000 tokens × 4 slots" in result
    assert state["optimizations"]["parallel_slots"] == 4
    assert restarted == [True]


def test_parallel_slot_change_rejects_unsafe_large_model(monkeypatch):
    state = {
        "current_model": "qwen35-9b",
        "current_ctx": 32768,
        "installed_models": [{"id": "qwen35-9b", "quant": "Q5_K_M"}],
    }

    class Store:
        def save(self, _value):
            raise AssertionError("unsafe settings must not be saved")

    with pytest.raises(ValueError, match="NO-FIT"):
        model_manager.change_parallel_slots(Store(), state, 4, FakeRunner())
