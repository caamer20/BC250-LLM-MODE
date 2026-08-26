"""P3 §9.3: CommandRunner is bounded — DEF-003 closed.

The legacy runner had no timeout, no output cap, and no process-group
cancellation: a hung or noisy child could freeze the appliance or
exhaust memory. Every guarantee here is exercised against REAL child
processes; the parent always survives to assert.
"""

from __future__ import annotations

import logging
import sys
import time

import pytest

from bc250_llm_mode.logging_utils import (
    DEFAULT_MAX_OUTPUT_BYTES,
    CommandError,
    CommandRunner,
)


@pytest.fixture()
def runner(tmp_path):
    logger = logging.getLogger("bounded-runner-test")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.NullHandler()
        logger.addHandler(handler)
    return CommandRunner(logger)


def test_normal_command_streams_and_returns_completed_process(runner, capsys):
    lines: list[str] = []
    result = runner.run(
        [sys.executable, "-c", "print('hello'); print('world')"],
        emit_output=True,
    )
    _ = capsys  # logging goes to the injected logger, not stdout
    assert result.returncode == 0
    assert "hello" in result.stdout and "world" in result.stdout


def test_failing_command_raises_command_error_as_before(runner):
    with pytest.raises(CommandError) as err:
        runner.run([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert err.value.returncode == 3
    assert not err.value.timed_out


def test_hung_child_is_stopped_at_its_time_bound(runner):
    """A child that never exits is stopped within its bound; the parent
    survives and receives a typed timeout failure."""
    started = time.monotonic()
    with pytest.raises(CommandError) as err:
        runner.run(
            [sys.executable, "-c", "import time; print('up'); time.sleep(300)"],
            timeout_seconds=1.5,
            emit_output=False,
        )
    waited = time.monotonic() - started
    assert err.value.timed_out is True
    assert waited < 15  # bound + grace + reaping, far under the old infinity


def test_timeout_kills_the_whole_process_group(runner):
    """A grandchild sharing the group cannot outlive the stop."""
    import os

    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen("
        "[sys.executable, '-c', 'import time; time.sleep(300)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(300)\n"
    )
    proc_holder: dict = {}

    def emitting(line: str) -> None:
        if line.strip().isdigit():
            proc_holder["grandchild"] = int(line.strip())

    logger = logging.getLogger("bounded-runner-test")
    runner2 = CommandRunner(logger, callback=emitting)
    with pytest.raises(CommandError):
        runner2.run(
            [sys.executable, "-u", "-c", script],
            timeout_seconds=1.5,
            emit_output=True,  # the callback captures the grandchild pid
        )
    assert "grandchild" in proc_holder
    grandchild = proc_holder["grandchild"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:  # pragma: no cover
        raise AssertionError("grandchild survived the process-group kill")


def test_noisy_output_is_capped_with_truncation_marker(runner):
    """Output beyond the cap is dropped from memory and from the log
    stream; a single truncation marker explains it."""
    emitted: list[str] = []
    logger = logging.getLogger("bounded-runner-test")
    tracked = CommandRunner(logger, callback=emitted.append)
    result = tracked.run(
        [sys.executable, "-c",
         "print('x' * 64, flush=True)\n" * 200],
        max_output_bytes=1024,
        emit_output=True,
        check=False,
    )
    total = sum(len(l) for l in result.stdout.splitlines())
    assert total < 4096  # far below the raw ~13 KB the child printed
    markers = [l for l in result.stdout.splitlines()
               if l.startswith("[output truncated")]
    assert len(markers) == 1


def test_default_bounds_are_bounded_constants():
    assert DEFAULT_MAX_OUTPUT_BYTES == 8 * 1024 * 1024
    # A module-level default exists and is finite.
    from bc250_llm_mode.logging_utils import DEFAULT_RUN_TIMEOUT_SECONDS

    assert 0 < DEFAULT_RUN_TIMEOUT_SECONDS < 3600


def test_secret_env_values_never_reach_the_log_stream(runner):
    """Env values ride to the child but never into emitted/log lines."""
    canary = "hf_secret_canary_value_9f2c"
    seen: list[str] = []

    class RecordingLogger(logging.Logger):
        def info(self, msg, *args, **kwargs):  # type: ignore[override]
            seen.append(str(msg))

    recording = RecordingLogger("recording-runner")
    tracked = CommandRunner(recording)
    result = tracked.run(
        [sys.executable, "-c",
         "import os; print('env-len', len(os.environ.get('HF_TOKEN','')))"],
        env={"HF_TOKEN": canary, "PATH": "/usr/bin:/bin"},
        emit_output=True,
        check=False,
    )
    assert result.returncode == 0
    joined = "\n".join(seen) + result.stdout
    assert canary not in joined
    assert "env-len" in result.stdout
