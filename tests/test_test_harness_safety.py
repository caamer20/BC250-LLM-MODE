"""P0.2: the test harness itself must never terminate the parent process.

The removed defect (DEF-002) armed ``faulthandler.dump_traceback_later(
20, exit=True)`` at test-module import time, so any run importing that
module could be hard-killed without a report. These tests prove the
replacement contract:

- no test source arms a destructive watchdog or calls ``os._exit``;
- importing the previously-poisoned module arms NOTHING;
- scoped diagnostics always cancel and never kill the parent;
- subprocess hangs produce a structured timeout result in the parent.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
REPO_ROOT = TESTS_DIR.parent
PREVIOUSLY_POISONED = TESTS_DIR / "test_operation_worker.py"

from support_diagnostics import (  # noqa: E402
    ScopedTracebackDiagnostics,
    assert_no_destructive_watchdog,
    wait_with_diagnostics,
)


# -- static guard -----------------------------------------------------------------


def test_no_test_source_arms_a_destructive_watchdog():
    """Every test file is scanned: ``dump_traceback_later(..., exit=True)``
    and bare ``os._exit`` are forbidden anywhere under ``tests/``."""
    checked = 0
    for path in sorted(TESTS_DIR.rglob("*.py")):
        assert_no_destructive_watchdog(
            path.read_text(encoding="utf-8"), str(path)
        )
        checked += 1
    assert checked > 50  # the scan really covers the suite


def test_guard_rejects_the_removed_pattern():
    poisoned = (
        "import faulthandler\n"
        "faulthandler.dump_traceback_later(20, exit=True)\n"
    )
    with pytest.raises(AssertionError, match="destructive"):
        assert_no_destructive_watchdog(poisoned, "memory")


def test_guard_rejects_bare_os_exit():
    with pytest.raises(AssertionError, match="os._exit"):
        assert_no_destructive_watchdog("import os\nos._exit(9)\n", "memory")


# -- functional probe -------------------------------------------------------------


_PROBE_SOURCE = """
import importlib.util
import sys

import faulthandler as real_faulthandler


class RecordingShim:
    def __getattr__(self, name):
        return getattr(real_faulthandler, name)

    def dump_traceback_later(self, *args, **kwargs):
        ARMED.append((args, kwargs))

    def cancel_dump_traceback_later(self):
        ARMED.append("cancel")


ARMED = []
sys.modules["faulthandler"] = RecordingShim()

spec = importlib.util.spec_from_file_location("probe_target", TARGET)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print("PROBE_ARMED=" + repr(ARMED))
""".replace("TARGET", repr(str(PREVIOUSLY_POISONED)))


def test_importing_previously_poisoned_module_arms_no_timer():
    """The full-suite watchdog proof: importing the once-defective module
    in a fresh interpreter must arm no traceback timer at all."""
    result = subprocess.run(
        [sys.executable, "-c", _PROBE_SOURCE],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    line = next(
        l for l in result.stdout.splitlines() if l.startswith("PROBE_ARMED=")
    )
    assert line == "PROBE_ARMED=[]", line


# -- scoped diagnostics behavior ---------------------------------------------------


def test_scoped_diagnostics_survive_timeout_and_always_cancel():
    """A block exceeding the bound dumps stacks but the process lives on;
    after exit the timer is cancelled so nothing fires later."""
    with tempfile.TemporaryFile(mode="w+") as sink:
        started = time.monotonic()
        with ScopedTracebackDiagnostics(0.2, dest=sink):
            time.sleep(0.45)
        elapsed_after_bound = time.monotonic() - started - 0.45
        # Still alive well past the bound: nothing killed the parent.
        time.sleep(0.35)
        sink.seek(0)
        dumped = sink.read()
    assert "Thread" in dumped or "Current thread" in dumped
    assert elapsed_after_bound < 2.0  # dump did not block the block


def test_scoped_diagnostics_reject_nonpositive_bounds():
    with pytest.raises(ValueError):
        ScopedTracebackDiagnostics(0)


# -- bounded subprocess waiting ----------------------------------------------------


def test_fast_child_returns_structured_success():
    proc = subprocess.Popen(
        [sys.executable, "-c", "print('ok')"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    code, timed_out = wait_with_diagnostics(proc, 30)
    assert (code, timed_out) == (0, False)


def test_hung_child_is_killed_and_parent_reports_structured_result():
    """The hard-kill case lives entirely in the CHILD; the parent gets a
    structured ``(returncode, timed_out)`` instead of dying."""
    hang = (
        "import signal, time\n"
        "print('up', flush=True)\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "time.sleep(300)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", hang],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "up"
    started = time.monotonic()
    code, timed_out = wait_with_diagnostics(proc, 1.5)
    waited = time.monotonic() - started
    assert timed_out is True
    assert code != 0  # killed, not clean
    assert waited < 15  # bounded: diagnostics dump then immediate kill
    # Parent obviously survived to run these assertions.


def test_child_process_group_killed_not_just_leader():
    """A grandchild sharing the child's process group cannot outlive the
    bounded wait: the whole group is killed, not just the leader."""
    script = textwrap.dedent(
        """
        import subprocess, sys, time
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(300)"],
        )
        print(child.pid, flush=True)
        time.sleep(300)
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert proc.stdout is not None
    grandchild_pid = int(proc.stdout.readline().strip())
    _code, timed_out = wait_with_diagnostics(proc, 1.5)
    assert timed_out is True
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild_pid, 0)
        except ProcessLookupError:
            break  # grandchild reaped
        time.sleep(0.05)
    else:  # pragma: no cover
        pytest.fail("grandchild survived the group kill")
