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

    from bc250_llm_mode.db import initialize_and_close
    initialize_and_close(tmp_path / "state.db")
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


def test_openwebui_status_requires_receipt_bound_to_current_model(tmp_path, monkeypatch):
    monkeypatch.setattr(openwebui.shutil, "which", lambda name: "/usr/bin/podman")
    networks = json.dumps({openwebui.NETWORK: {}})
    outputs = {
        ("podman", "container", "exists", openwebui.CONTAINER): (0, ""),
        ("podman", "inspect", "--format", "{{.State.Status}}", openwebui.CONTAINER):
            (0, "running\n"),
        ("podman", "inspect", "--format", "{{json .NetworkSettings.Networks}}", openwebui.CONTAINER):
            (0, networks + "\n"),
        ("podman", "inspect", "--format",
         "{{index .Config.Labels \"io.bc250-llm-mode.openwebui-spec\"}}",
         openwebui.CONTAINER): (0, f"{openwebui.OPENWEBUI_SPEC_VERSION}\n"),
        ("podman", "inspect", "--format", "{{json .Mounts}}", openwebui.CONTAINER):
            (0, _named_data_mount()),
        _diginspect_tuple()[0]: _diginspect_tuple()[1],
    }
    receipt = {
        "schema_version": openwebui.OPENWEBUI_SPEC_VERSION,
        "container": openwebui.CONTAINER,
        "image": openwebui.IMAGE_REF,
        "provider_adapter": openwebui.OPENWEBUI_PROVIDER_ADAPTER_VERSION,
        "expected_model": "qwen38-9b",
        "provider_ready": True,
        "expected_model_visible": True,
        "stream_ready": True,
    }
    (tmp_path / openwebui.OPENWEBUI_RECEIPT_FILENAME).write_text(
        json.dumps(receipt), encoding="utf-8")
    state = {
        "app_dir": str(tmp_path), "current_model": "qwen38-9b",
        "gateway_provisioned": True,
    }
    status = openwebui.open_webui_status(state, FakeRunner(outputs))
    assert status["end_to_end_verified"] is True

    state["current_model"] = "different-model"
    stale = openwebui.open_webui_status(state, FakeRunner(outputs))
    assert stale["provider_ready"] is False
    assert stale["expected_model_visible"] is False
    assert stale["end_to_end_verified"] is False


def _diginspect_tuple():
    return (
        ("podman", "image", "inspect", openwebui.IMAGE_REF,
         "--format", "{{json .RepoDigests}}"),
        (0, json.dumps([openwebui.IMAGE_REF]) + "\n"),
    )


def test_openwebui_release_pin_is_v0111_linux_amd64_manifest():
    assert openwebui.IMAGE_TAG_DISPLAY == "v0.11.3"
    assert openwebui.IMAGE_DIGEST_SHA256 == (
        "sha256:751b617714b91e4cfd0186a509c72480c858e012976103b09a30dad053c36175"
    )
    assert openwebui.IMAGE_REF == (
        "ghcr.io/open-webui/open-webui@" + openwebui.IMAGE_DIGEST_SHA256
    )


def _named_data_mount():
    return json.dumps([{
        "Type": "volume",
        "Name": openwebui.DATA_VOLUME,
        "Destination": openwebui.DATA_DESTINATION,
    }]) + "\n"


def _legacy_data_mount():
    return json.dumps([{
        "Type": "bind",
        "Source": openwebui.LEGACY_DATA_BIND,
        "Destination": openwebui.DATA_DESTINATION,
    }]) + "\n"


def test_openwebui_facade_requires_named_credential_and_active_model(monkeypatch):
    monkeypatch.setattr(openwebui.shutil, "which", lambda _name: "/usr/bin/podman")
    with pytest.raises(RuntimeError, match="credential"):
        openwebui.install_open_webui({}, FakeRunner())
    with pytest.raises(RuntimeError, match="Start a model"):
        openwebui.install_open_webui(
            {"gateway_credential_file": "/private/client"}, FakeRunner())


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


def test_model_switch_accepts_managed_local_alias(tmp_path):
    from bc250_llm_mode.activation_command import ActivationOutcome

    target = tmp_path / "managed-local.gguf"
    target.write_bytes(b"local-model")
    state = {
        "current_model": "qwen38-9b",
        "current_ctx": 8192,
        "installed_models": [
            {
                "id": "local-a3515b591cfc",
                "path": str(target),
                "display_name": "Small local model",
                "quant": "UNKNOWN",
                "source_kind": "local",
            }
        ],
        "optimizations": {"parallel_slots": 1},
    }
    payloads = []

    class FakeActivation:
        def activate(self, payload):
            payloads.append(payload)
            return ActivationOutcome("op-local", "SUCCEEDED", {})

    class Store:
        activation = FakeActivation()

        def require_operational(self):
            return self

        def read_model(self):
            return {"current_model": "local-a3515b591cfc"}

    model_manager.switch_model(
        Store(), state, "local-a3515b591cfc", FakeRunner()
    )

    assert payloads == [
        {
            "model_alias": "local-a3515b591cfc",
            "requested_by": "cli",
        }
    ]
    assert state["current_model"] == "local-a3515b591cfc"


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
