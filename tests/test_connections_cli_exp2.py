from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bc250_llm_mode import __main__ as entry
from bc250_llm_mode.app import Application
from bc250_llm_mode.connection_setup import ConnectionProbeReport
from bc250_llm_mode.paths import AppPaths


class TTY(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture()
def world(tmp_path, monkeypatch):
    application = Application.compose(AppPaths.temporary(tmp_path))
    state = application.read_model()
    application.commit_settings_changes(
        state, {**state, "disclaimer_ack": True, "setup_complete": True})
    monkeypatch.setattr(
        Application, "compose", classmethod(lambda cls, *a, **k: application))
    return application


def test_parser_accepts_all_planned_connections_commands():
    parse = entry._parser().parse_args
    assert parse(("connections", "status")).connection_action == "status"
    assert parse(("connections", "doctor")).connection_action == "doctor"
    assert parse(("connections", "capabilities")).connection_action == "capabilities"
    assert parse(("connections", "clients")).connection_action == "clients"
    add = parse(("connections", "add-client", "--label", "Phone", "--type", "pocketpal"))
    assert add.label == "Phone" and add.client_type == "pocketpal"
    assert parse(("connections", "rotate-client", "1" * 32)).client_id == "1" * 32
    assert parse(("connections", "revoke-client", "1" * 32)).client_id == "1" * 32
    assert parse(("connections", "disable-all")).connection_action == "disable-all"
    assert parse(("connections", "instructions", "python")).client_type == "python"
    assert parse(("connections", "test", "1" * 32)).client_id == "1" * 32


def test_non_tty_creation_refuses_before_writing_secret(world, capsys):
    assert entry.cli((
        "connections", "add-client", "--label", "Phone",
        "--type", "pocketpal")) == 1
    assert "interactive terminal" in capsys.readouterr().err
    assert world.connection_credentials.list_clients() == []


def test_tty_create_outputs_redacted_json_and_secret_once(world, monkeypatch, capsys):
    tty = TTY()
    monkeypatch.setattr(entry.sys, "stderr", tty)
    assert entry.cli((
        "connections", "add-client", "--label", "Phone",
        "--type", "pocketpal")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "created"
    assert payload["secret_revealed"] is False
    secret_line = tty.getvalue()
    assert "API key (shown once):" in secret_line
    assert len(secret_line.split(":", 1)[1].strip()) >= 8
    assert secret_line.split(":", 1)[1].strip() not in json.dumps(payload)
    listed = world.connection_credentials.list_clients()
    assert listed[0]["label"] == "Phone"
    assert "active_fingerprint" not in listed[0]


def test_status_clients_and_instructions_are_redacted(world, monkeypatch, capsys):
    snapshot = {
        "ready": True,
        "model": {"public_alias": "defiant-fable-q5"},
        "urls": {
            "base_url": "https://bazzite.tail2168f.ts.net:10000/v1",
            "webui_url": "https://bazzite.tail2168f.ts.net:8443/",
        },
    }
    world.connections = SimpleNamespace(
        snapshot=lambda **_kwargs: SimpleNamespace(to_dict=lambda: snapshot))
    assert entry.cli(("connections", "status")) == 0
    assert json.loads(capsys.readouterr().out)["ready"] is True
    assert entry.cli(("connections", "instructions", "pocketpal")) == 0
    guide = json.loads(capsys.readouterr().out)
    assert guide["values"]["Base URL"].endswith(":10000/v1")
    assert guide["values"]["Model"] == "defiant-fable-q5"
    assert "secret" not in json.dumps(guide).lower()

    assert entry.cli(("connections", "capabilities")) == 0
    contract = json.loads(capsys.readouterr().out)
    assert contract["profile"] == "bc250-openai-compatible-v1"
    assert len(contract["capabilities"]) == 8


def test_doctor_is_secret_free_and_fails_when_a_step_needs_attention(
    world, capsys,
):
    snapshot = {
        "ready": False,
        "readiness": {
            "remote_client_ready": False,
            "primary_problem_code": "MODEL_NOT_RUNNING",
        },
        "checks": [{"id": "model", "passed": False}],
    }
    world.connections = SimpleNamespace(
        snapshot=lambda **_kwargs: SimpleNamespace(to_dict=lambda: snapshot)
    )

    assert entry.cli(("connections", "doctor")) == 1
    diagnosis = json.loads(capsys.readouterr().out)
    assert diagnosis["ready"] is False
    assert diagnosis["next_action_route"] in {"connections", "models", "system"}
    assert "secret" not in json.dumps(diagnosis).lower()


def test_capabilities_is_offline_and_precomposition(monkeypatch, capsys):
    monkeypatch.setattr(
        Application,
        "compose",
        classmethod(lambda cls, *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("offline compatibility must not compose a profile")
        )),
    )

    assert entry.cli(("connections", "capabilities")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["offline"] is True


def test_test_command_uses_same_snapshot_and_probe_service(world, capsys):
    snapshot = {
        "model": {"public_alias": "defiant-fable-q5"},
        "urls": {"base_url": "https://bazzite.tail2168f.ts.net:10000/v1"},
    }
    seen = {}
    world.connections = SimpleNamespace(
        snapshot=lambda **_kwargs: SimpleNamespace(to_dict=lambda: snapshot))
    world.connection_probes = SimpleNamespace(run=lambda **kwargs: (
        seen.update(kwargs) or ConnectionProbeReport(
            1, "2026-08-29T12:00:00Z", "1" * 32,
            {"local_unauthorized": "passed"}, True)))
    assert entry.cli(("connections", "test", "1" * 32)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
    assert seen["public_alias"] == "defiant-fable-q5"
    assert seen["tailnet_base_url"].endswith(":10000/v1")


def test_emergency_disable_does_not_consult_model_health(world, monkeypatch, capsys):
    tty = TTY()
    monkeypatch.setattr(entry.sys, "stderr", tty)
    assert entry.cli((
        "connections", "add-client", "--label", "Phone",
        "--type", "pocketpal")) == 0
    capsys.readouterr()
    world.connections = SimpleNamespace(
        snapshot=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("model health must not be read")))
    assert entry.cli(("connections", "disable-all")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "disabled-all"
    assert payload["access"]["enabled"] is False
    assert world.connection_credentials.list_clients()[0]["revoked_at"] is not None
