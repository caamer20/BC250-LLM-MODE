"""P0.1: the real detached worker entry point and its child-process gates.

DEF-001: ``--detach`` spawned ``python -m bc250_llm_mode.worker_main``
while that module did not exist, so handoff could fail after the user
believed work was handed off. These tests prove the installed module:

- parses profile/policy arguments strictly (stable usage exit code 2);
- fails closed on missing database / repair-required profiles (exit 4);
- refuses an already-owned profile lock (exit 3);
- runs ONE composed application — never two service graphs;
- completes a REAL production operation (MODEL_IMPORT v1 of a tiny valid
  GGUF) exactly once from a detached child process that survives
  frontend closure;
- works from an installed wheel with the repository root off sys.path.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
REPO_ROOT = TESTS_DIR.parent

from bc250_llm_mode.acquisition_adapter import AcquisitionHostAdapter  # noqa: E402
from bc250_llm_mode.db import initialize_and_close  # noqa: E402
from bc250_llm_mode.legacy_import import utcnow  # noqa: E402
from bc250_llm_mode.operations.acquisition import build_import_workflow  # noqa: E402
from bc250_llm_mode.operations.model import OperationState  # noqa: E402
from bc250_llm_mode.operations.repositories import (  # noqa: E402
    OperationRepository,
    WorkerLockRepository,
)
from bc250_llm_mode.repositories import (  # noqa: E402
    ModelArtifactRepository,
    ModelInstallationsRepository,
)
from bc250_llm_mode.operations.workflow import EnqueueService, WorkflowRegistry  # noqa: E402
from bc250_llm_mode.paths import AppPaths  # noqa: E402
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory  # noqa: E402
from bc250_llm_mode.worker_main import (  # noqa: E402
    EXIT_ALREADY_RUNNING,
    EXIT_OK,
    EXIT_REPAIR_REQUIRED,
    EXIT_USAGE,
    main as worker_main_entry,
)

from support_diagnostics import wait_with_diagnostics  # noqa: E402


# -- world helpers ----------------------------------------------------------------


def tiny_standard_gguf() -> bytes:
    """A minimal VALID standard-layout GGUF (llama arch, one tensor)."""

    def s(value: bytes) -> bytes:
        return struct.pack("<Q", len(value)) + value

    header = b"GGUF" + struct.pack("<I", 3)
    header += struct.pack("<Q", 1) + struct.pack("<Q", 1)
    return header + s(b"general.architecture") + struct.pack("<I", 8) + s(b"llama")


class Profile:
    """A temporary application profile with the production schema."""

    def __init__(self, tmp_path: Path) -> None:
        self.paths = AppPaths.temporary(tmp_path / "profile")
        initialize_and_close(self.paths.database_path)
        self.units = UnitOfWorkFactory(self.paths.database_path)

    def enqueue_import(self, source_path: Path, alias: str = "tiny-model"):
        host = AcquisitionHostAdapter(
            self.paths, self.units, hub_client=None, clock=utcnow
        )
        registry = WorkflowRegistry()
        registry.register(build_import_workflow(host))
        service = EnqueueService(
            self.units,
            registry.freeze(),
            clock=utcnow,
            uuid_factory=lambda: str(uuid.uuid4()),
        )
        return service.enqueue(
            operation_type="MODEL_IMPORT",
            payload={
                "source_path": str(source_path),
                "alias": alias,
                "requested_by": "test",
            },
            surface="test",
        )

    def op_state(self, operation_id: str) -> OperationState:
        with self.units.begin() as conn:
            return OperationRepository(conn).require(operation_id).state

    def transition(self, operation_id: str, target: OperationState):
        with self.units.begin() as conn:
            ops = OperationRepository(conn)
            record = ops.require(operation_id)
            ops.compare_and_transition(
                operation_id,
                expected_state=record.state,
                expected_revision=record.state_revision,
                target_state=target,
                event_code=f"TEST_{target.value}",
                event_summary="seeded by worker entry test",
            )


@pytest.fixture()
def profile(tmp_path):
    return Profile(tmp_path)


def spawn_worker(profile_dir: Path, cwd: Path, extra=()):
    argv = [
        sys.executable, "-m", "bc250_llm_mode.worker_main",
        "--profile", str(profile_dir),
        "--quiet-period", "1",
        "--lease-ttl", "10",
        *extra,
    ]
    return subprocess.Popen(
        argv,
        cwd=str(cwd),  # repo root NOT implicitly importable via cwd
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


# -- argument contract (in process) -----------------------------------------------


def test_help_exits_zero_and_documents_profile(capsys):
    assert worker_main_entry(["--help"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "--profile" in out


def test_unknown_argument_is_usage_error(capsys):
    assert worker_main_entry(["--bogus"]) == EXIT_USAGE
    assert capsys.readouterr().err


def test_relative_profile_refused(capsys, tmp_path):
    rc = worker_main_entry(["--profile", "relative/path"])
    assert rc == EXIT_USAGE
    assert "WORKER_PROFILE_NOT_ABSOLUTE" in capsys.readouterr().err


def test_symlinked_profile_refused(capsys, tmp_path):
    real = tmp_path / "real-profile"
    real.mkdir()
    link = tmp_path / "linked-profile"
    link.symlink_to(real)
    rc = worker_main_entry(["--profile", str(link)])
    assert rc == EXIT_USAGE
    assert "WORKER_PROFILE_UNSAFE" in capsys.readouterr().err


def test_policy_out_of_bounds_refused(capsys, profile):
    rc = worker_main_entry([
        "--profile", str(profile.paths.app_dir), "--quiet-period", "99999",
    ])
    assert rc == EXIT_USAGE
    assert "WORKER_POLICY_OUT_OF_RANGE" in capsys.readouterr().err


def test_missing_database_fails_closed_repair_required(capsys, tmp_path):
    empty = tmp_path / "empty-profile"
    empty.mkdir()
    rc = worker_main_entry(["--profile", str(empty)])
    assert rc == EXIT_REPAIR_REQUIRED
    err = capsys.readouterr().err
    assert "WORKER_NO_DATABASE" in err


def test_default_home_profile_resolved_from_environment(capsys, tmp_path, monkeypatch):
    """No --profile: the default per-user layout resolves from HOME — and
    its absence of a database is reported against THAT path, proving no
    developer-home leakage."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("USERPROFILE", raising=False)
    rc = worker_main_entry([])
    assert rc == EXIT_REPAIR_REQUIRED
    err = capsys.readouterr().err
    assert "WORKER_NO_DATABASE" in err
    assert str(tmp_path) in err


def test_compose_called_exactly_once(monkeypatch, profile, tmp_path):
    """The entry composes ONE application graph — never a second one."""
    import bc250_llm_mode.app as app_module

    calls = []
    real_compose = app_module.Application.compose

    def counting(paths=None):
        calls.append(paths)
        return real_compose(paths)

    monkeypatch.setattr(
        app_module.Application, "compose", staticmethod(counting)
    )
    source = tmp_path / "source.gguf"
    source.write_bytes(tiny_standard_gguf())
    profile.enqueue_import(source)
    rc = worker_main_entry([
        "--profile", str(profile.paths.app_dir), "--quiet-period", "0.6",
    ])
    assert rc == EXIT_OK
    assert calls == [profile.paths]


# -- detached child gates ----------------------------------------------------------


def _child_stats(stdout_text: str) -> dict:
    for line in reversed(stdout_text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no JSON stats line in child stdout: {stdout_text!r}")


def test_detached_child_completes_real_import_once_and_survives_frontend(
    tmp_path,
):
    """MANDATORY P0 gate: a REAL detached child — new session, stdio cut —
    finishes a production MODEL_IMPORT exactly once after the parent stops
    touching the profile, then idles out with exit 0."""
    world = Profile(tmp_path)
    source = tmp_path / "incoming" / "model-file.gguf"
    source.parent.mkdir()
    source.write_bytes(tiny_standard_gguf())
    record = world.enqueue_import(source)

    proc = spawn_worker(world.paths.app_dir, cwd=tmp_path)
    # The child is session-detached: it structurally outlives this parent.
    assert os.getpgid(proc.pid) != os.getpgrp()

    code, timed_out = wait_with_diagnostics(proc, 120)
    assert not timed_out
    assert code == EXIT_OK, proc.stderr.read()[-2000:]
    stats = _child_stats(proc.stdout.read())
    assert stats["claims"] == 1 and stats["resumes"] == 0

    assert world.op_state(record.id) is OperationState.SUCCEEDED
    with world.units.begin() as conn:
        quarantined = ModelArtifactRepository(conn).list_quarantined()
        installs = ModelInstallationsRepository(conn).list()
    assert not quarantined
    assert len(installs) == 1  # registered EXACTLY once
    assert installs[0]["id"] == "tiny-model"
    assert installs[0]["managed"] is True
    published = [
        p for p in world.paths.model_artifacts_dir.rglob("*.gguf")
    ]
    assert len(published) == 1  # bytes published EXACTLY once
    staging = world.paths.model_staging_dir
    assert not staging.exists() or not any(staging.iterdir())
    # Reboot-safety invariant untouched by the detached worker.
    from bc250_llm_mode.repositories import SettingsRepository

    with world.units.read() as conn:
        values = SettingsRepository(conn).all()
    assert values.get("boot_policy", "desktop") == "desktop"


def test_child_with_no_work_idles_out_quickly(tmp_path):
    world = Profile(tmp_path)
    proc = spawn_worker(world.paths.app_dir, cwd=tmp_path)
    code, timed_out = wait_with_diagnostics(proc, 60)
    assert not timed_out
    assert code == EXIT_OK
    stats = _child_stats(proc.stdout.read())
    assert stats["idle_exits"] >= 1
    assert stats["claims"] == 0 and stats["resumes"] == 0
    with world.units.read() as conn:
        assert WorkerLockRepository(conn).get() is None  # lock released


def test_paused_operation_is_never_resumed_by_a_fresh_child(tmp_path):
    world = Profile(tmp_path)
    source = tmp_path / "paused-src.gguf"
    source.write_bytes(tiny_standard_gguf())
    record = world.enqueue_import(source)
    world.transition(record.id, OperationState.PAUSED)

    proc = spawn_worker(world.paths.app_dir, cwd=tmp_path)
    code, timed_out = wait_with_diagnostics(proc, 60)
    assert not timed_out and code == EXIT_OK
    stats = _child_stats(proc.stdout.read())
    assert stats["claims"] == 0
    assert world.op_state(record.id) is OperationState.PAUSED


def test_cancelled_operation_is_terminal_for_a_fresh_child(tmp_path):
    world = Profile(tmp_path)
    source = tmp_path / "cancel-src.gguf"
    source.write_bytes(tiny_standard_gguf())
    record = world.enqueue_import(source)
    world.transition(record.id, OperationState.CANCELLED)

    proc = spawn_worker(world.paths.app_dir, cwd=tmp_path)
    code, timed_out = wait_with_diagnostics(proc, 60)
    assert not timed_out and code == EXIT_OK
    stats = _child_stats(proc.stdout.read())
    assert stats["claims"] == 0
    assert world.op_state(record.id) is OperationState.CANCELLED


def test_poisoned_source_ends_failed_safe_not_crash_loop(tmp_path):
    world = Profile(tmp_path)
    record = world.enqueue_import(tmp_path / "missing-dir" / "nope.gguf")

    proc = spawn_worker(world.paths.app_dir, cwd=tmp_path)
    code, timed_out = wait_with_diagnostics(proc, 90)
    assert not timed_out and code == EXIT_OK
    stats = _child_stats(proc.stdout.read())
    assert world.op_state(record.id) is OperationState.FAILED_SAFE
    assert stats["failures"] == 0  # engine mapped it; restart policy unused


def test_live_lock_elsewhere_refuses_second_child_exit_3(tmp_path):
    world = Profile(tmp_path)
    future = lambda: "2099-01-01T00:00:00Z"  # noqa: E731
    with world.units.begin() as conn:
        WorkerLockRepository(conn, clock=future).acquire(
            owner="parent-holder", ttl_seconds=600
        )
    proc = spawn_worker(world.paths.app_dir, cwd=tmp_path)
    code, timed_out = wait_with_diagnostics(proc, 60)
    assert not timed_out
    assert code == EXIT_ALREADY_RUNNING
    assert "WORKER_ALREADY_RUNNING" in proc.stderr.read()


# -- clean-wheel gate ---------------------------------------------------------------


@pytest.mark.slow
def test_installed_wheel_runs_worker_module_without_repository_root(tmp_path):
    """P0 exit gate: build the wheel, install it away from the source tree,
    run ``python -m bc250_llm_mode.worker_main`` with the repository root
    absent from sys.path."""
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    build = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps",
         "--no-build-isolation", "--wheel-dir", str(wheel_dir), str(REPO_ROOT)],
        capture_output=True, text=True,
    )
    assert build.returncode == 0, build.stderr[-2000:]
    wheels = list(wheel_dir.glob("*.whl"))
    assert wheels
    target = tmp_path / "site"
    target.mkdir()
    install = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet",
         "--target", str(target), str(wheels[0])],
        capture_output=True, text=True,
    )
    assert install.returncode == 0, install.stderr[-2000:]

    env = {**os.environ, "PYTHONPATH": str(target)}

    help_run = subprocess.run(
        [sys.executable, "-m", "bc250_llm_mode.worker_main", "--help"],
        capture_output=True, text=True, cwd=str(tmp_path), env=env,
    )
    assert help_run.returncode == 0
    assert "--profile" in help_run.stdout

    absent = tmp_path / "no-database-profile"
    absent.mkdir()
    repair_run = subprocess.run(
        [sys.executable, "-m", "bc250_llm_mode.worker_main",
         "--profile", str(absent)],
        capture_output=True, text=True, cwd=str(tmp_path), env=env,
    )
    assert repair_run.returncode == EXIT_REPAIR_REQUIRED
    assert "WORKER_NO_DATABASE" in repair_run.stderr
