from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from bc250_llm_mode.gateway_runtime import BridgeObservation
from bc250_llm_mode.gateway_service import (
    GATEWAY_SERVICE_NAME,
    GATEWAY_UNIT_MARKER,
    GatewayService,
    GatewayServiceError,
    KNOWN_DIAGNOSTIC_UNIT_SHA256S,
    render_gateway_launcher,
    render_gateway_unit,
)
from bc250_llm_mode.paths import AppPaths


IDENTITY = "sha256:" + "a" * 64


def _bridge(identity=IDENTITY):
    return BridgeObservation(
        network="bc250-openwebui", driver="bridge",
        subnet="10.89.0.0/24", gateway="10.89.0.1",
        identity=identity,
    )


class Runner:
    def __init__(self, systemd_dir: Path):
        self.systemd_dir = systemd_dir
        self.commands = []
        self.active = False
        self.enabled = False

    def run(self, command, **_kwargs):
        command = list(command)
        self.commands.append(command)
        if command[:2] == ["install", "-m"]:
            target = Path(command[-1])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(command[-2], target)
        if command[:2] == ["rm", str(self.systemd_dir / GATEWAY_SERVICE_NAME)]:
            Path(command[1]).unlink()
        if command[:2] == ["systemctl", "start"] or command[:2] == ["systemctl", "restart"]:
            self.active = True
        if command[:2] == ["systemctl", "stop"]:
            self.active = False
        if command[:3] == ["systemctl", "disable", "--now"]:
            self.active = False
            self.enabled = False
        if command[:2] == ["systemctl", "disable"]:
            self.enabled = False
        stdout = ""
        if command[:2] == ["systemctl", "show"]:
            prop = next(part.split("=", 1)[1] for part in command if part.startswith("--property="))
            present = (self.systemd_dir / GATEWAY_SERVICE_NAME).exists()
            stdout = {
                "LoadState": "loaded" if present else "not-found",
                "ActiveState": "active" if self.active else "inactive",
                "SubState": "running" if self.active else "dead",
                "UnitFileState": "enabled" if self.enabled else "disabled",
                "ExecMainStartTimestamp": "Tue 2026-09-01 01:00:00 UTC" if self.active else "",
            }[prop] + "\n"
        return subprocess.CompletedProcess(command, 0, stdout, "")


@pytest.fixture()
def world(tmp_path, monkeypatch):
    paths = AppPaths.from_app_dir(tmp_path / "profile")
    paths.ensure_directories()
    systemd_dir = tmp_path / "systemd"
    runner = Runner(systemd_dir)
    monkeypatch.setattr(
        "bc250_llm_mode.gateway_service.elevated", lambda command: command)

    def listeners(_bridge_address, _runner):
        return ("127.0.0.1", "10.89.0.1") if runner.active else ()

    service = GatewayService(
        paths, systemd_dir=systemd_dir,
        bridge_observer=_bridge,
        listener_observer=listeners,
        health_observer=lambda: {
            "responsive": True, "status": "ready",
            "backend_identity": "verified",
        },
        boot_identity=lambda: "boot-1",
        sleep=lambda _seconds: None,
    )
    return paths, runner, service


def test_unit_and_launcher_are_current_boot_only_secret_free_and_stable(world):
    paths, _runner, service = world
    launcher = render_gateway_launcher(
        paths=paths, expected_network_identity=IDENTITY)
    unit = render_gateway_unit(paths=paths, launcher=service.launcher_path)

    assert unit.startswith(GATEWAY_UNIT_MARKER)
    assert "After=bc250-llm.service" in unit
    assert "Requires=bc250-llm.service" not in unit
    assert "WantedBy=" not in unit
    assert "0.0.0.0" not in unit
    assert "10.89.0.1" not in unit
    assert "credential" not in unit.lower()
    assert "api_key" not in unit.lower()
    assert "Authorization" not in unit
    assert f'ExecStart="/bin/sh" "{service.launcher_path}"' in unit
    assert f'ExecStart="{service.launcher_path}"' not in unit
    assert "gateway_runtime" in launcher
    assert str(paths.application_current_link / "venv/bin/python") in launcher
    assert str(paths.app_dir / "app-venv/bin/python") in launcher
    assert "BC250_LLM_MODE" not in launcher
    assert KNOWN_DIAGNOSTIC_UNIT_SHA256S == {
        "93189f4531e60fcae7752183979cb97ec281d7eaff7947ece73fe972cd049a97"
    }


def test_plan_install_start_stop_and_remove_preserve_boot_policy_and_data(world):
    paths, runner, service = world
    plan = service.plan()
    assert plan.mutation_allowed is True
    assert plan.existing_identity == "absent"
    assert plan.current_boot_only is True

    installed = service.install(runner)
    assert installed["installed"] is True
    assert installed["active"] is False
    assert installed["enabled"] is False
    assert service.launcher_path.stat().st_mode & 0o777 == 0o700
    assert service.receipt_path.stat().st_mode & 0o777 == 0o600

    started = service.start(runner)
    assert started["protocol_ready"] is True
    assert started["backend_identity_verified"] is True
    assert started["listener_addresses"] == ["127.0.0.1", "10.89.0.1"]
    assert started["enabled"] is False

    stopped = service.stop(runner)
    assert stopped["active"] is False
    result = service.remove(runner)
    assert result["removed"] is True
    assert result["models_preserved"] is True
    assert result["credentials_preserved"] is True
    assert result["openwebui_data_preserved"] is True
    assert not service.unit_path.exists()
    assert not service.launcher_path.exists()
    assert paths.models_dir.exists()


def test_running_service_with_model_down_is_protocol_ready_but_backend_unverified(world):
    _paths, runner, service = world
    service.install(runner)
    service._health_observer = lambda: {
        "responsive": True, "status": "degraded",
        "backend_identity": "unverified",
    }
    runner.active = True

    status = service.status(runner)

    assert status["protocol_ready"] is True
    assert status["backend_identity_verified"] is False
    assert status["health_status"] == "degraded"


def test_unfamiliar_unit_and_port_conflict_are_never_overwritten(world):
    _paths, runner, service = world
    service.unit_path.parent.mkdir(parents=True, exist_ok=True)
    service.unit_path.write_text("[Service]\nExecStart=/unknown\n", encoding="utf-8")

    assert service.plan().reason_code == "GATEWAY_UNIT_NOT_OWNED"
    with pytest.raises(GatewayServiceError, match="unfamiliar"):
        service.install(runner)
    assert service.unit_path.read_text() == "[Service]\nExecStart=/unknown\n"
    assert not any(command[:2] == ["install", "-m"] for command in runner.commands)


def test_absent_unit_with_existing_listener_blocks_install(world):
    paths, runner, _service = world
    service = GatewayService(
        paths, systemd_dir=runner.systemd_dir,
        bridge_observer=_bridge,
        listener_observer=lambda *_args: ("0.0.0.0",),
        health_observer=lambda: {},
    )

    with pytest.raises(GatewayServiceError, match="already has"):
        service.install(runner)


def test_network_identity_change_requires_regenerated_launcher(world):
    _paths, runner, service = world
    service.install(runner)
    before = service.launcher_path.read_text()
    service._bridge_observer = lambda: _bridge("sha256:" + "b" * 64)

    refreshed = service.install(runner)
    after = service.launcher_path.read_text()

    assert refreshed["network_identity_current"] is True
    assert before != after
    assert "sha256:" + "b" * 64 in after


def test_consumer_release_stops_only_after_last_current_boot_owner(world):
    _paths, runner, service = world

    first = service.acquire("openwebui", runner)
    second = service.acquire("sharing", runner)
    assert first["active"] is True
    assert second["current_boot_consumers"] == ["openwebui", "sharing"]

    retained = service.release("sharing", runner)
    assert retained["active"] is True
    assert retained["current_boot_consumers"] == ["openwebui"]
    stopped = service.release("openwebui", runner)
    assert stopped["active"] is False
