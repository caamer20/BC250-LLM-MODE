"""U1.2 §12: bounded process port + fixed atomic exchange helper."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bc250_llm_mode.runtime_exchange_helper import (
    HELPER_DIGEST,
    HELPER_SOURCE,
    Refusal,
    build_helper_invocation,
    run_local_exchange,
    validate_exchange_request,
    verify_helper_digest,
)
from bc250_llm_mode.runtime_process import (
    CommandKind,
    ProcessCommandSpec,
    ProcessFailure,
    RuntimeProcessRunner,
)


# -- ProcessCommandSpec contract ----------------------------------------------


def test_spec_rejects_shell_tokens_and_empty_argv():
    with pytest.raises(ProcessFailure):
        ProcessCommandSpec(kind=CommandKind.OBSERVE, argv=())
    with pytest.raises(ProcessFailure) as err:
        ProcessCommandSpec(
            kind=CommandKind.FETCH,
            argv=("bash", "-lc", "git clone https://x && rm -rf /"),
        )
    assert err.value.code == "PROCESS_SPEC_INVALID"
    with pytest.raises(ProcessFailure):
        ProcessCommandSpec(kind=CommandKind.CONFIGURE, argv=("sh", "-c", "echo $HOME"))


def test_spec_env_allowlist_never_passes_whole_environment(monkeypatch):
    monkeypatch.setenv("BC250_CANARY_SECRET_TOKEN", "s3cret")
    monkeypatch.setenv("HOME", "/home/fake")
    spec = ProcessCommandSpec(
        kind=CommandKind.SMOKE,
        argv=("true",),
        env_allowlist=("HOME",),
        redaction_tokens=("s3cret",),
    )
    env = spec.build_env()
    assert "BC250_CANARY_SECRET_TOKEN" not in env
    assert env["HOME"] == "/home/fake"


def test_runner_executes_typed_argv_and_reports_bounds(tmp_path):
    runner = RuntimeProcessRunner()
    spec = ProcessCommandSpec(
        kind=CommandKind.SMOKE,
        argv=(sys.executable, "-c", "print('hello'); import sys; "
              "sys.stderr.write('warn-line\\n')"),
        max_output_bytes=64,
    )
    result = runner.run(spec)
    assert result.exit_code == 0
    assert "hello" in result.stdout_tail


def test_runner_enforces_timeout_and_kills_process_group():
    runner = RuntimeProcessRunner()
    # A child that spawns a grandchild holding the pipe open: only a
    # process-group kill reaps both.
    import time as _time

    script_file = Path(_time.strftime("killer_%H%M%S.py"))
    # Deterministic name under this test's tmp dir is unnecessary; the
    # runner only needs a path. Write into CWD-agnostic temp.
    script_path = Path(os.environ.get("TMPDIR", "/tmp")) / f"bc250-test-{os.getpid()}-killer.py"
    script_path.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "print('spawned', flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    spec = ProcessCommandSpec(
        kind=CommandKind.OBSERVE,
        argv=(sys.executable, str(script_path)),
        timeout_seconds=0.5,
        termination_grace_seconds=0.5,
    )
    with pytest.raises(ProcessFailure) as err:
        runner.run(spec)
    assert err.value.code == "PROCESS_TIMEOUT"


def test_runner_honors_cancellation_between_pulses():
    runner = RuntimeProcessRunner()
    spec = ProcessCommandSpec(
        kind=CommandKind.COMPILE,
        argv=(sys.executable, "-c", "import time; time.sleep(10)"),
        timeout_seconds=None,  # falls back to compile bound (long)
    )
    with pytest.raises(ProcessFailure) as err:
        runner.run(spec, cancel_requested=lambda: True)
    assert err.value.code == "PROCESS_CANCELLED"


def test_runner_redacts_canary_tokens_from_output():
    runner = RuntimeProcessRunner()
    canary = "BC250-CANARY-9f2b7c"
    spec = ProcessCommandSpec(
        kind=CommandKind.SMOKE,
        argv=(sys.executable, "-c",
              f"import sys; sys.stderr.write('token {canary} leaked\\n')"),
        expected_exit_codes=(0,),
        redaction_tokens=(canary,),
    )
    result = runner.run(spec)
    assert canary not in result.stderr_tail
    assert "[redacted]" in result.stderr_tail


def test_runner_unexpected_exit_carries_bounded_redacted_reason():
    runner = RuntimeProcessRunner()
    canary = "CANARY-tail-424242"
    spec = ProcessCommandSpec(
        kind=CommandKind.CLEANUP,
        argv=(sys.executable, "-c",
              f"import sys; sys.stderr.write('boom {canary}\\n'); raise SystemExit(3)"),
        redaction_tokens=(canary,),
    )
    with pytest.raises(ProcessFailure) as err:
        runner.run(spec)
    assert err.value.code == "PROCESS_EXIT_UNEXPECTED"
    assert canary not in str(err.value)


# -- Atomic exchange helper ------------------------------------------------------


@pytest.fixture()
def exchange_world(tmp_path):
    root = tmp_path / "runtime"
    active = root / "active"
    candidate = root / "managed" / "candidate"
    for tree, marker in ((active, "ACTIVE"), (candidate, "CANDIDATE")):
        tree.mkdir(parents=True)
        (tree / "marker.txt").write_text(marker, encoding="utf-8")
    return {"root": root, "active": active, "candidate": candidate}


def test_hostile_path_corpus_is_refused(exchange_world, tmp_path):
    root = str(exchange_world["root"])
    good = str(exchange_world["candidate"])
    hostile_targets = [
        "", "/", ".", "..", str(tmp_path / "../escape"),
        "with\nnewline", "with\x00nul",
    ]
    for target in hostile_targets:
        with pytest.raises(Refusal):
            validate_exchange_request(target, good, approved_root=root)
    with pytest.raises(Refusal) as err:
        validate_exchange_request(good, good, approved_root=root)
    assert err.value.code == "EXCHANGE_REFUSAL_PATH_OVERLAP"


def test_symlink_or_missing_trees_refused(exchange_world, tmp_path):
    root = str(exchange_world["root"])
    link = tmp_path / "link-active"
    link.symlink_to(exchange_world["active"])
    with pytest.raises(Refusal) as err:
        validate_exchange_request(str(link), str(exchange_world["candidate"]),
                                  approved_root=root)
    assert err.value.code == "EXCHANGE_REFUSAL_SYMLINK"
    with pytest.raises(Refusal):
        validate_exchange_request(
            str(tmp_path / "missing"), str(exchange_world["candidate"]),
            approved_root=root,
        )


def test_containment_escape_and_cross_device_refused(exchange_world, tmp_path):
    outside = tmp_path / "outside-tree"
    outside.mkdir()
    with pytest.raises(Refusal) as err:
        validate_exchange_request(
            str(outside), str(exchange_world["candidate"]),
            approved_root=str(exchange_world["root"]),
        )
    assert err.value.code == "EXCHANGE_REFUSAL_CONTAINMENT"
    # Nested overlap.
    nested = exchange_world["active"] / "nested"
    nested.mkdir()
    with pytest.raises(Refusal):
        validate_exchange_request(
            str(exchange_world["active"]), str(nested),
            approved_root=str(exchange_world["root"]),
        )


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="renameat2(RENAME_EXCHANGE) is Linux-only; refusal path verified above",
)
def test_local_exchange_swaps_content_atomically(exchange_world):
    run_local_exchange(
        str(exchange_world["active"]), str(exchange_world["candidate"]),
        approved_root=str(exchange_world["root"]),
    )
    assert (exchange_world["active"] / "marker.txt").read_text() == "CANDIDATE"
    assert (exchange_world["candidate"] / "marker.txt").read_text() == "ACTIVE"


def test_helper_digest_is_recorded_and_enforced(tmp_path):
    assert len(HELPER_DIGEST) == 64
    verify_helper_digest(HELPER_DIGEST)
    with pytest.raises(Refusal) as err:
        verify_helper_digest("0" * 64)
    assert err.value.code == "EXCHANGE_HELPER_DIGEST_MISMATCH"
    # The shipped source must be exactly what the recorded digest claims.
    assert hashlib.sha256(HELPER_SOURCE.encode()).hexdigest() == HELPER_DIGEST


def test_helper_invocation_argv_is_typed_and_shell_free():
    argv = build_helper_invocation(
        "/op-owned/bc250-exchange-helper.py",
        "/runtime/active", "/managed/candidate", "/runtime",
    )
    assert argv[0] == "python3"
    assert "--root" in argv
    joined = " ".join(argv)
    for token in ("bash", "-lc", "&&", "|", ";"):
        assert token not in joined
