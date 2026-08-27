"""P4 §10: the gateway CLI verb — durable credential lifecycle through the
console boundary against a REAL composed application on a temporary profile
(ADR 005 D3: the secret rides only the 0600 file; SQLite holds the
fingerprint alone)."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from bc250_llm_mode import __main__ as entry
from bc250_llm_mode.app import Application
from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.repositories import SettingsRepository
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory


class GatewayCliWorld:
    def __init__(self, tmp_path: Path) -> None:
        self.paths = AppPaths.temporary(tmp_path / "profile")
        initialize_and_close(self.paths.database_path)
        self.units = UnitOfWorkFactory(self.paths.database_path)
        self.application = Application.compose(self.paths)
        self.service = self.application.gateway

    def acknowledge_disclaimer(self) -> None:
        with self.units.begin() as conn:
            SettingsRepository(conn).set_many({"disclaimer_ack": True})

    def run(self, *argv: str) -> int:
        return entry.cli(("gateway", *argv))


@pytest.fixture()
def world(tmp_path, monkeypatch):
    world = GatewayCliWorld(tmp_path)
    monkeypatch.setattr(
        Application,
        "compose",
        classmethod(lambda cls, *a, **k: world.application),
    )
    return world


def _last_stdout_json(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip())


def test_parser_accepts_the_gateway_surface():
    parse = entry._parser().parse_args
    assert parse(("gateway", "status")).action == "status"
    assert parse(("gateway", "provision")).action == "provision"
    assert parse(("gateway", "rotate", "--secret", "s")).secret == "s"
    assert parse(("gateway", "revoke")).action == "revoke"
    assert parse(("gateway", "verify")).action == "verify"
    with pytest.raises(SystemExit):
        parse(("gateway", "bogus-action"))


def test_status_before_provision_is_fail_closed(world, capsys):
    assert world.run("status") == 0
    payload = _last_stdout_json(capsys)
    assert payload["gateway_provisioned"] is False
    assert payload["gateway_verified"] is False
    assert payload["gateway_backend_identity"] == "not-provisioned"
    assert payload["gateway_credential_file"] is None


def test_provision_without_acknowledgment_fails_closed(world, capsys):
    assert world.run("provision") == 1
    assert "disclaimer" in capsys.readouterr().err.lower()
    # Nothing was provisioned: durable truth is untouched.
    assert world.service.verify()["backend_identity"] == "not-provisioned"


def test_provision_rotate_revoke_lifecycle(world, capsys):
    world.acknowledge_disclaimer()

    # provision: secret ONLY in a 0600 file, redacted JSON on stdout
    assert world.run("provision", "--secret", "cycle-secret-1") == 0
    first = _last_stdout_json(capsys)
    assert first["provisioned"] is True
    assert len(first["fingerprint_prefix"]) == 8
    assert first["scopes"] == "inference:read,inference:stream,models:list"
    cred = Path(first["credential_file"])
    assert cred.exists()
    assert stat.S_IMODE(cred.stat().st_mode) == 0o600
    assert cred.read_bytes().strip() == b"cycle-secret-1"
    assert "cycle-secret-1" not in json.dumps(first)

    # verify through the CLI reads the 0600 file
    assert world.run("verify") == 0
    assert _last_stdout_json(capsys)["backend_identity"] == "verified"

    # rotate: old secret dies, new one verifies
    assert world.run("rotate", "--secret", "cycle-secret-2") == 0
    second = _last_stdout_json(capsys)
    assert second["fingerprint_prefix"] != first["fingerprint_prefix"]
    assert world.service.verify(presented_secret="cycle-secret-1")["verified"] is False
    assert world.service.verify(presented_secret="cycle-secret-2")["verified"] is True

    # status reflects refreshed durable fields
    assert world.run("status") == 0
    payload = _last_stdout_json(capsys)
    assert payload["gateway_provisioned"] is True
    assert payload["gateway_verified"] is True
    assert payload["gateway_backend_identity"] == "verified"
    assert payload["gateway_credential_file"] == str(cred)

    # revoke: file cleared, everything fail-closed afterwards
    assert world.run("revoke") == 0
    assert _last_stdout_json(capsys)["revoked"] is True
    assert not cred.exists()
    assert world.service.verify()["backend_identity"] == "revoked"
    assert world.service.resolve_credential_file() is None
    assert world.run("verify") == 0
    assert _last_stdout_json(capsys)["backend_identity"] == "revoked"


def test_secret_never_reaches_the_database(world, capsys):
    world.acknowledge_disclaimer()
    assert world.run("provision", "--secret", "db-canary-secret") == 0
    capsys.readouterr()
    with world.units.read() as conn:
        settings_blob = " ".join(
            repr(tuple(row)) for row in conn.execute(
                "SELECT key, value_json FROM settings"
            ).fetchall()
        )
        row = conn.execute(
            "SELECT fingerprint, scopes FROM gateway_credentials WHERE id = 1"
        ).fetchone()
    assert "db-canary-secret" not in settings_blob
    assert row is not None
    assert row["fingerprint"] != "db-canary-secret"
    assert len(row["fingerprint"]) == 64
