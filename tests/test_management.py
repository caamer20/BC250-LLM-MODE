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
    networks = json.dumps({openwebui.NETWORK: {}})
    outputs = {
        ("podman", "container", "exists", openwebui.CONTAINER): (0, ""),
        ("podman", "inspect", "--format", "{{.State.Status}}", openwebui.CONTAINER): (0, "running\n"),
        ("podman", "inspect", "--format", "{{json .NetworkSettings.Networks}}", openwebui.CONTAINER): (0, networks + "\n"),
        _diginspect_tuple()[0]: _diginspect_tuple()[1],
    }
    runner = FakeRunner(outputs)
    state = {}
    status = openwebui.open_webui_status(state, runner)
    assert status["installed"] is True
    assert status["running"] is True
    assert status["topology"] == "contained"

    openwebui.stop_open_webui(state, runner)
    assert any(command[:3] == ["podman", "stop", "--time"] for command, _ in runner.commands)


def _diginspect_tuple():
    return (
        ("podman", "image", "inspect", openwebui.IMAGE_REF,
         "--format", "{{json .RepoDigests}}"),
        (0, json.dumps([openwebui.IMAGE_REF]) + "\n"),
    )


def test_openwebui_install_is_loopback_contained(monkeypatch):
    """U0.6: no host networking; UI published strictly on 127.0.0.1."""
    monkeypatch.setattr(openwebui.shutil, "which", lambda name: "/usr/bin/podman")
    runner = FakeRunner(
        {
            ("podman", "network", "exists", openwebui.NETWORK): (1, ""),
            ("podman", "container", "exists", openwebui.CONTAINER): (1, ""),
            _diginspect_tuple()[0]: _diginspect_tuple()[1],
        }
    )
    state = {}
    openwebui.install_open_webui(state, runner)

    commands = [command for command, _ in runner.commands]
    assert ["podman", "network", "create", openwebui.NETWORK] in commands
    create = next(c for c in commands if c[:2] == ["podman", "create"])
    assert "--network" in create and "host" not in create
    assert "-p" in create
    assert create[create.index("-p") + 1] == "127.0.0.1:3000:8080"
    assert "--security-opt" in create and "no-new-privileges" in create
    assert "--cap-drop" in create and "all" in create
    assert ["podman", "start", openwebui.CONTAINER] in commands


def test_openwebui_install_refuses_unverified_image_digest(monkeypatch):
    """DEF-006/D5: start refuses until the local image IS the pinned digest."""
    monkeypatch.setattr(openwebui.shutil, "which", lambda name: "/usr/bin/podman")
    runner = FakeRunner()  # image inspect returns empty -> unverified
    state = {"openwebui_container": openwebui.CONTAINER}
    with pytest.raises(RuntimeError, match="digest"):
        openwebui.install_open_webui(state, runner)
    # Recovery story is surfaced; no create/start was attempted.
    commands = [command for command, _ in runner.commands]
    assert not any(command[:2] == ["podman", "create"] for command in commands)
    assert not any(command[0] == "podman" and command[1] == "start" for command in commands)
    assert runner.messages and "podman pull" in runner.messages[-1]


def test_openwebui_migrates_legacy_host_network_container(monkeypatch):
    """Legacy/uncontained topology is migrated with the volume preserved."""
    monkeypatch.setattr(openwebui.shutil, "which", lambda name: "/usr/bin/podman")
    legacy_networks = json.dumps({"host": {}})
    outputs = {
        ("podman", "network", "exists", openwebui.NETWORK): (0, ""),
        ("podman", "container", "exists", openwebui.CONTAINER): (0, ""),
        ("podman", "inspect", "--format", "{{json .NetworkSettings.Networks}}", openwebui.CONTAINER): (0, legacy_networks + "\n"),
        _diginspect_tuple()[0]: _diginspect_tuple()[1],
    }
    runner = FakeRunner(outputs)
    state = {"openwebui_container": openwebui.CONTAINER}
    openwebui.install_open_webui(state, runner)

    commands = [command for command, _ in runner.commands]
    assert ["podman", "stop", "--time", "10", openwebui.CONTAINER] in commands
    assert ["podman", "rm", openwebui.CONTAINER] in commands
    # Recreated contained: never started while still on host networking.
    create_index = next(
        i for i, c in enumerate(commands) if c[:2] == ["podman", "create"]
    )
    start_index = next(
        i for i, c in enumerate(commands) if c[:2] == ["podman", "start"]
    )
    assert create_index < start_index
    assert state["openwebui_container"] == openwebui.CONTAINER


def test_openwebui_update_pulls_verified_pin_recreates_and_preserves_data(monkeypatch):
    monkeypatch.setattr(openwebui.shutil, "which", lambda name: "/usr/bin/podman")
    networks = json.dumps({openwebui.NETWORK: {}})
    outputs = {
        ("podman", "container", "exists", openwebui.CONTAINER): (0, ""),
        ("podman", "inspect", "--format", "{{.State.Status}}", openwebui.CONTAINER): (0, "running\n"),
        ("podman", "inspect", "--format", "{{json .NetworkSettings.Networks}}", openwebui.CONTAINER): (0, networks + "\n"),
        ("podman", "network", "exists", openwebui.NETWORK): (0, ""),
        ("podman", "pull", openwebui.IMAGE_REF): (0, "pulled\n"),
        _diginspect_tuple()[0]: _diginspect_tuple()[1],
    }
    runner = FakeRunner(outputs)
    state = {
        "openwebui_container": openwebui.CONTAINER,
        "gateway_provisioned": True,
        "gateway_credential_file": "/profile/openwebui-credential",
    }

    result = openwebui.update_open_webui(state, runner)

    commands = [command for command, _ in runner.commands]
    pull_index = commands.index(["podman", "pull", openwebui.IMAGE_REF])
    stop_index = commands.index(["podman", "stop", "--time", "10", openwebui.CONTAINER])
    remove_index = commands.index(["podman", "rm", openwebui.CONTAINER])
    create_index = next(i for i, command in enumerate(commands) if command[:2] == ["podman", "create"])
    start_index = commands.index(["podman", "start", openwebui.CONTAINER])
    digest_indices = [
        i for i, command in enumerate(commands)
        if command[:3] == ["podman", "image", "inspect"]
    ]
    verified_before_stop = [index for index in digest_indices if pull_index < index < stop_index]
    assert verified_before_stop
    assert stop_index < remove_index < create_index < start_index
    assert all(command != ["podman", "pull", openwebui.IMAGE_TAG_DISPLAY] for command in commands)
    create = commands[create_index]
    assert openwebui.IMAGE_REF == create[-1]
    assert f"{openwebui.DATA_VOLUME}:/app/backend/data" in create
    assert any("src=/profile/openwebui-credential" in value for value in create)
    assert "--network" in create and create[create.index("--network") + 1] == openwebui.NETWORK
    assert not any(flag in commands[remove_index] for flag in ("-v", "--volumes"))
    assert result["updated"] is True
    assert result["running_state_restored"] is True


def test_openwebui_update_digest_failure_leaves_container_untouched(monkeypatch):
    monkeypatch.setattr(openwebui.shutil, "which", lambda name: "/usr/bin/podman")
    outputs = {
        ("podman", "container", "exists", openwebui.CONTAINER): (0, ""),
        ("podman", "inspect", "--format", "{{.State.Status}}", openwebui.CONTAINER): (0, "exited\n"),
        ("podman", "inspect", "--format", "{{json .NetworkSettings.Networks}}", openwebui.CONTAINER): (
            0, json.dumps({openwebui.NETWORK: {}}) + "\n"),
        ("podman", "pull", openwebui.IMAGE_REF): (0, "pulled\n"),
        _diginspect_tuple()[0]: (0, json.dumps(["ghcr.io/open-webui/open-webui@sha256:" + "0" * 64]) + "\n"),
    }
    runner = FakeRunner(outputs)

    with pytest.raises(RuntimeError, match="digest"):
        openwebui.update_open_webui(
            {
                "openwebui_container": openwebui.CONTAINER,
                "gateway_credential_file": "/profile/openwebui-credential",
            },
            runner,
        )

    commands = [command for command, _ in runner.commands]
    assert ["podman", "pull", openwebui.IMAGE_REF] in commands
    assert ["podman", "rm", openwebui.CONTAINER] not in commands
    assert not any(command[:2] == ["podman", "create"] for command in commands)
    assert ["podman", "start", openwebui.CONTAINER] not in commands


def test_openwebui_update_keeps_a_previously_stopped_container_stopped(monkeypatch):
    monkeypatch.setattr(openwebui.shutil, "which", lambda name: "/usr/bin/podman")
    outputs = {
        ("podman", "container", "exists", openwebui.CONTAINER): (0, ""),
        ("podman", "inspect", "--format", "{{.State.Status}}", openwebui.CONTAINER): (0, "exited\n"),
        ("podman", "inspect", "--format", "{{json .NetworkSettings.Networks}}", openwebui.CONTAINER): (
            0, json.dumps({openwebui.NETWORK: {}}) + "\n"),
        ("podman", "network", "exists", openwebui.NETWORK): (0, ""),
        ("podman", "pull", openwebui.IMAGE_REF): (0, "pulled\n"),
        _diginspect_tuple()[0]: _diginspect_tuple()[1],
    }
    runner = FakeRunner(outputs)

    result = openwebui.update_open_webui(
        {
            "openwebui_container": openwebui.CONTAINER,
            "gateway_credential_file": "/profile/openwebui-credential",
        },
        runner,
    )

    commands = [command for command, _ in runner.commands]
    assert ["podman", "stop", "--time", "10", openwebui.CONTAINER] not in commands
    assert ["podman", "start", openwebui.CONTAINER] not in commands
    assert result["running"] is False
    assert result["running_state_restored"] is True


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


def test_parallel_slot_change_checks_fit_then_activates():
    state = {
        "current_model": "lfm25-26b",
        "current_ctx": 128000,
        "installed_models": [{"id": "lfm25-26b", "quant": "Q5_K_M"}],
    }
    calls = []

    class FakeActivation:
        def activate(self, payload):
            calls.append(payload)

            class _Outcome:
                status = "SUCCEEDED"
                operation_id = "op-1"
                ok = True
                detail = {}

            return _Outcome()

    class Store:
        def require_operational(self):
            return self

        activation = FakeActivation()

        def read_model(self):
            fresh = dict(state)
            fresh["optimizations"] = {"parallel_slots": 4}
            return fresh

    result = model_manager.change_parallel_slots(
        Store(), state, 4, FakeRunner()
    )
    assert "128,000 tokens × 4 slots" in result
    assert calls == [
        {
            "model_alias": "lfm25-26b",
            "parallel_slots": 4,
            "requested_by": "cli",
        }
    ]
    # The disposable draft is refreshed from the durable read model.
    assert state["optimizations"]["parallel_slots"] == 4


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


def test_model_switch_reports_rollback_honestly():
    state = {
        "current_model": "qwen3-8b",
        "current_ctx": 8192,
        "installed_models": [
            {"id": "qwen3-8b", "quant": "Q4_K_M"},
            {"id": "qwen38-9b", "quant": "Q4_K_M"},
        ],
        "optimizations": {"parallel_slots": 1},
    }

    class FakeActivation:
        def activate(self, payload):
            from bc250_llm_mode.activation_command import ActivationOutcome

            return ActivationOutcome(
                "op-rb",
                "FAILED_ROLLED_BACK",
                {"reason": "restored and verified"},
            )

    class Store:
        def require_operational(self):
            return self

        activation = FakeActivation()

        def read_model(self):
            return {"refreshed": True}

    runner = FakeRunner()
    model_manager.switch_model(Store(), state, "qwen38-9b", runner)
    assert any(
        "previous working configuration was restored" in message
        for message in runner.messages
    )
    # The draft was refreshed from the durable read model exactly once.
    assert state == {"refreshed": True}


def test_context_change_requires_composed_store():
    state = {
        "current_model": "lfm25-26b",
        "current_ctx": 8192,
        "installed_models": [{"id": "lfm25-26b", "quant": "Q5_K_M"}],
        "optimizations": {"parallel_slots": 1},
    }

    class Store:
        pass

    with pytest.raises(RuntimeError, match="no fallback"):
        model_manager.change_context(Store(), state, 16384, FakeRunner())
