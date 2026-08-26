"""U1.4 §7.4: the operations command group — parser shape, stdout/JSON
discipline, and stable exit codes, exercised against a REAL composed
application on a temporary profile."""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent

from bc250_llm_mode.__main__ import _parser
from bc250_llm_mode.acquisition_adapter import AcquisitionHostAdapter
from bc250_llm_mode.app import Application
from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.legacy_import import utcnow
from bc250_llm_mode.operations.acquisition import build_import_workflow
from bc250_llm_mode.operations.repositories import OperationRepository
from bc250_llm_mode.operations.workflow import EnqueueService, WorkflowRegistry
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.operations_cli import run_operations_command
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory


def tiny_standard_gguf() -> bytes:
    def s(value: bytes) -> bytes:
        return struct.pack("<Q", len(value)) + value

    header = b"GGUF" + struct.pack("<I", 3)
    header += struct.pack("<Q", 1) + struct.pack("<Q", 1)
    return header + s(b"general.architecture") + struct.pack("<I", 8) + s(b"llama")


class CliWorld:
    def __init__(self, tmp_path: Path) -> None:
        self.paths = AppPaths.temporary(tmp_path / "profile")
        initialize_and_close(self.paths.database_path)
        self.units = UnitOfWorkFactory(self.paths.database_path)
        host = AcquisitionHostAdapter(
            self.paths, self.units, hub_client=None, clock=utcnow
        )
        registry = WorkflowRegistry()
        registry.register(build_import_workflow(host))
        self.enqueue = EnqueueService(
            self.units,
            registry.freeze(),
            clock=utcnow,
            uuid_factory=lambda: str(uuid.uuid4()),
        )
        self.application = Application.compose(self.paths)

    def seed_import(self, source_path: Path):
        return self.enqueue.enqueue(
            operation_type="MODEL_IMPORT",
            payload={"source_path": str(source_path), "alias": "cli-model"},
            surface="test",
        ).id

    def run(self, *argv: str):
        args = _parser().parse_args(("operations", *argv))
        return run_operations_command(self.application, args)


@pytest.fixture()
def world(tmp_path):
    return CliWorld(tmp_path)


def test_parser_accepts_the_documented_surface():
    parse = _parser().parse_args
    assert parse(("operations", "list", "--json")).action == "list"
    assert parse(("operations", "show", "op-1")).operation_id == "op-1"
    assert parse(("operations", "events", "op-1", "--follow")).follow is True
    assert parse(("operations", "wait", "op-1", "--timeout", "5")).timeout == 5.0
    assert parse(("operations", "cancel", "op-1", "--reason", "x")).reason == "x"
    assert parse(("operations", "recover", "op-1", "--confirm")).confirm is True
    with pytest.raises(SystemExit):
        parse(("operations", "bogus-action"))


def test_operation_id_required_for_id_actions(world, capsys):
    assert world.run("show") == 2
    assert "OPERATION_ID" in capsys.readouterr().err


def tmp_gguf(world) -> Path:
    source = world.paths.app_dir / "incoming.gguf"
    source.write_bytes(tiny_standard_gguf())
    return source


def test_list_json_goes_to_stdout_with_schema(world, capsys):
    world.seed_import(tmp_gguf(world))
    code = world.run("list", "--json")
    out = capsys.readouterr()
    assert code == 0
    document = json.loads(out.out.strip())
    assert document["schema_version"] >= 1
    assert len(document["items"]) == 1


def test_show_reports_actions_and_next_step(world, capsys):
    operation_id = world.seed_import(tmp_gguf(world))
    code = world.run("show", operation_id)
    out = capsys.readouterr().out
    assert code == 0
    assert "MODEL_IMPORT" in out
    assert "cancel" in out  # available while queued
    assert "next:" in out


def test_cancel_exit_zero_then_dismiss_refused_exit_one(world, capsys):
    operation_id = world.seed_import(tmp_gguf(world))
    assert world.run("cancel", operation_id, "--json") == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["outcome"] == "ACCEPTED"
    assert payload["state"] == "CANCEL_REQUESTED"

    # Non-terminal rows refuse dismissal: exit 1 with stderr reason.
    assert world.run("dismiss", operation_id, "--json") == 1
    refusal = json.loads(capsys.readouterr().out.strip())
    assert refusal["code"] == "NOT_TERMINAL"


def test_recover_without_confirm_is_refused_and_gated(world, capsys):
    operation_id = world.seed_import(tmp_gguf(world))
    code = world.run("recover", operation_id, "--json")
    assert code == 1
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["code"] == "CONFIRMATION_REQUIRED"


def test_wait_times_out_bounded_with_truth(world, capsys):
    operation_id = world.seed_import(tmp_gguf(world))
    code = world.run("wait", operation_id, "--timeout", "0.5", "--json")
    out = capsys.readouterr()
    assert code == 0
    payload = json.loads(out.out.strip())
    assert payload["timed_out"] is True
    assert payload["state"] == "QUEUED"
    assert payload["recommended_command"]


def test_unknown_operation_maps_to_exit_one(world, capsys):
    assert world.run("show", "does-not-exist") == 1
    err = capsys.readouterr().err
    assert "no such operation" in err


@pytest.mark.slow
def test_operations_cli_from_installed_wheel(tmp_path):
    """P1 exit gate: JSON contracts work from the installed console script
    with the repository root absent from sys.path."""
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    build = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps",
         "--no-build-isolation", "--wheel-dir", str(wheel_dir),
         str(REPO_ROOT)],
        capture_output=True, text=True,
    )
    assert build.returncode == 0, build.stderr[-2000:]
    target = tmp_path / "site"
    target.mkdir()
    wheels = list(wheel_dir.glob("*.whl"))
    install = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet",
         "--target", str(target), str(wheels[0])],
        capture_output=True, text=True,
    )
    assert install.returncode == 0, install.stderr[-2000:]

    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "PYTHONPATH": str(target),
        "HOME": str(home),  # default per-user profile resolves here
    }
    help_run = subprocess.run(
        [sys.executable, "-m", "bc250_llm_mode", "operations", "list",
         "--json"],
        capture_output=True, text=True, cwd=str(tmp_path), env=env,
    )
    assert help_run.returncode == 0, help_run.stderr[-2000:]
    document = json.loads(help_run.stdout.strip())
    assert document["schema_version"] >= 1
