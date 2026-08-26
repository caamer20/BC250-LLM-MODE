"""P0.2: scoped diagnostic timers — the safe replacement for the old
process-wide ``faulthandler.dump_traceback_later(..., exit=True)`` watchdog.

The removed pattern armed a 20-second ``exit=True`` timer at test-module
import time, so any full-suite run that imported that module could be
killed without a report. The contract here is inverted:

- diagnostics wrap ONE explicitly selected block or subprocess wait;
- on timeout they dump stacks (to the requested stream) and KEEP the
  parent alive so the failure is reported by the test runner;
- ``cancel_dump_traceback_later`` always runs, including on error paths;
- hard kills apply to CHILD processes only, and the parent receives a
  structured ``(returncode, timed_out)`` result.
"""

from __future__ import annotations

import faulthandler
import os
import signal
import subprocess
import sys
from types import TracebackType
from typing import Any


class ScopedTracebackDiagnostics:
    """Dump stacks if an explicit block exceeds ``timeout_seconds``.

    Never terminates the process: on timeout the stacks of all threads are
    written once to ``dest`` (default stderr) and execution continues until
    the block fails on its own, so the runner reports the real failure.
    """

    def __init__(self, timeout_seconds: float, dest: Any = None) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._timeout_seconds = float(timeout_seconds)
        self._dest = dest if dest is not None else sys.stderr
        self._armed = False

    def __enter__(self) -> "ScopedTracebackDiagnostics":
        # Deliberately NO exit=True: the parent must survive to report.
        faulthandler.dump_traceback_later(
            self._timeout_seconds, file=self._dest
        )
        self._armed = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        if self._armed:
            faulthandler.cancel_dump_traceback_later()
            self._armed = False
        return False  # never suppress exceptions


def wait_with_diagnostics(
    proc: subprocess.Popen,
    timeout_seconds: float,
    dest: Any = None,
) -> tuple[int | None, bool]:
    """Bounded child wait with stack dumps; structured timeout result.

    Returns ``(returncode, timed_out)``. A timed-out child is killed — its
    whole process group first when one exists — and the PARENT survives to
    report the structured result. Diagnostics dump shortly after the bound
    is exceeded; the kill follows immediately after.
    """
    with ScopedTracebackDiagnostics(timeout_seconds, dest=dest):
        try:
            return proc.wait(timeout=timeout_seconds), False
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            try:
                code = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - stubborn pid
                proc.kill()
                code = proc.wait()
            return code, True


def _kill_process_tree(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, AttributeError):
        proc.kill()


def assert_no_destructive_watchdog(source_text: str, origin: str) -> None:
    """Static guard: forbid ``dump_traceback_later(..., exit=True)`` and
    bare ``os._exit`` in test sources. Import-time arming without an
    explicit scoped context is exactly the defect this replaces."""
    import ast

    tree = ast.parse(source_text, filename=origin)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", getattr(func, "id", ""))
            if name == "dump_traceback_later":
                for kw in node.keywords:
                    if kw.arg == "exit" and getattr(kw.value, "value", None):
                        raise AssertionError(
                            f"{origin}: destructive dump_traceback_later"
                            " exit=True is forbidden; use"
                            " tests.support_diagnostics."
                            "ScopedTracebackDiagnostics"
                        )
            if name == "_exit" and isinstance(func, ast.Attribute):
                if isinstance(func.value, ast.Name) and func.value.id == "os":
                    raise AssertionError(
                        f"{origin}: os._exit is forbidden in tests; fail"
                        " through the runner instead"
                    )
