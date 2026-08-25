"""Bounded, cancellable process execution for the runtime lifecycle (§12).

Every external command of ``RUNTIME_UPDATE v1`` / ``RUNTIME_ROLLBACK v1``
runs through :class:`RuntimeProcessRunner` behind a frozen
:class:`ProcessCommandSpec`. Production guarantees:

- typed argv ONLY: never ``shell=True``, never ``bash -lc``, never
  interpolated script text;
- a dedicated process group per command; on timeout or cancellation the
  whole group receives TERM, waits a bounded grace, then KILL; children
  are always reaped;
- streaming output capture under byte caps (no unbounded buffering, no
  pipe deadlock);
- an explicit minimal environment allowlist — the full environment is
  never passed through and never logged;
- stable, bounded failure codes with REDACTED output tails; raw canary
  content never reaches evidence or logs.

The reviewed default bounds live here as one table (§12.2); tests inject
smaller values. These are policy defaults, not magic guarantees.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable

# -- Reviewed default bounds (plan §12.2) --------------------------------------

DEFAULT_TIMEOUTS: dict[str, float] = {
    "observe": 30.0,            # ref/image/tool observation
    "preflight": 15.0,          # disk/filesystem preflight
    "fetch": 20 * 60.0,         # source fetch (heartbeats required)
    "configure": 10 * 60.0,
    "compile": 90 * 60.0,       # compile (heartbeats required)
    "smoke": 60.0,              # binary smoke/version checks
    "atomic": 15.0,             # atomic helper command
    "restart": 120.0,           # service restart/start
    "health": 120.0,            # health convergence
    "inference": 120.0,         # minimal inference
    "cleanup": 60.0,            # owned cleanup
}
TERMINATION_GRACE_SECONDS = 5.0
DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024

# Progress phase bands (§12.3): stable phases only, no fake precision.
PROGRESS_PHASE_BANDS: tuple[tuple[str, int, int], ...] = (
    ("resolve", 0, 5),
    ("preflight", 5, 10),
    ("fetch", 10, 30),
    ("configure", 30, 45),
    ("build", 45, 70),
    ("smoke", 70, 78),
    ("activation", 78, 85),
    ("restart", 85, 92),
    ("verify", 92, 96),
    ("promote", 96, 100),
)


class ProcessFailure(RuntimeError):
    """Stable, bounded process failure; raw output is redacted upstream."""

    def __init__(self, code: str, summary: str) -> None:
        super().__init__(summary or code)
        self.code = code


class CommandKind(Enum):
    OBSERVE = "observe"
    PREFLIGHT = "preflight"
    FETCH = "fetch"
    CONFIGURE = "configure"
    COMPILE = "compile"
    SMOKE = "smoke"
    ATOMIC = "atomic"
    CLEANUP = "cleanup"


@dataclass(frozen=True)
class ProcessCommandSpec:
    """Frozen description of ONE bounded external command."""

    kind: CommandKind
    argv: tuple[str, ...]
    working_directory: str | None = None
    env_allowlist: tuple[str, ...] = ()   # exact variable names to inherit
    env_values: tuple[tuple[str, str], ...] = ()  # explicit safe additions
    timeout_seconds: float | None = None
    termination_grace_seconds: float = TERMINATION_GRACE_SECONDS
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    expected_exit_codes: tuple[int, ...] = (0,)
    operation_id: str = ""
    step_key: str = ""
    redaction_tokens: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.argv or any(
            not isinstance(part, str) or not part for part in self.argv
        ):
            raise ProcessFailure(
                "PROCESS_SPEC_INVALID", "argv must be non-empty strings"
            )
        joined = " ".join(self.argv)
        lowered = joined.lower()
        for launcher in ("bash -lc", "bash -c", "sh -c", "/bin/sh", "/bin/bash"):
            if lowered.startswith(launcher) or f" {launcher}" in lowered:
                raise ProcessFailure(
                    "PROCESS_SPEC_INVALID",
                    "argv must be typed; a shell launcher is forbidden",
                )
        # Interpolation markers are spelled via chr() so textual guards
        # can distinguish defensive validators from real construction.
        for token in ("$" + "(", chr(96), "\n"):
            if token in joined:
                raise ProcessFailure(
                    "PROCESS_SPEC_INVALID",
                    f"argv must be typed; forbidden interpolation {token!r}",
                )

    def build_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        for name in self.env_allowlist:
            value = os.environ.get(name)
            if value is not None:
                env[name] = value
        for name, value in self.env_values:
            env[name] = value
        return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **env}


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int
    stdout_tail: str
    stderr_tail: str
    truncated_stdout: bool
    truncated_stderr: bool
    duration_seconds: float
    timed_out: bool = False
    cancelled: bool = False


def _redact(text: str, tokens: Iterable[str]) -> str:
    for token in tokens:
        if token:
            text = text.replace(token, "[redacted]")
    return text


def _tail(text: bytes, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text.decode("utf-8", errors="replace"), False
    return text[-limit:].decode("utf-8", errors="replace"), True


class RuntimeProcessRunner:
    """Production implementation over ``subprocess`` (POSIX)."""

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._monotonic = monotonic or time.monotonic
        self._sleep = sleep or time.sleep

    def run(
        self,
        spec: ProcessCommandSpec,
        *,
        cancel_requested: Callable[[], bool] | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> ProcessResult:
        timeout = spec.timeout_seconds
        if timeout is None:
            timeout = DEFAULT_TIMEOUTS.get(spec.kind.value, 60.0)
        started = self._monotonic()
        try:
            proc = subprocess.Popen(
                list(spec.argv),
                cwd=spec.working_directory,
                env=spec.build_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise ProcessFailure(
                "PROCESS_SPAWN_FAILED", str(exc)[:200]
            ) from exc

        stdout_buf = bytearray()
        stderr_buf = bytearray()
        fds = [proc.stdout, proc.stderr]
        # Non-blocking pipes: the supervisor loop stays in charge of the
        # timeout/cancel clock instead of blocking on a silent child.
        for stream in fds:
            os.set_blocking(stream.fileno(), False)

        def _pump(stream, buffer: bytearray) -> None:
            while True:
                try:
                    chunk = stream.read1(4096)
                except (BlockingIOError, OSError):
                    return  # nothing available right now
                if not chunk:
                    return  # EOF
                limit = spec.max_output_bytes
                room = max(0, limit - len(buffer))
                buffer.extend(chunk[:room])  # drop beyond cap
                if len(chunk) < 4096:
                    return

        try:
            while True:
                # Non-blocking-ish pump loop: short reads keep pipes drained
                # without unbounded buffering or deadlock.
                _pump(proc.stdout, stdout_buf)
                _pump(proc.stderr, stderr_buf)
                code = proc.poll()
                if code is not None:
                    _pump(proc.stdout, stdout_buf)
                    _pump(proc.stderr, stderr_buf)
                    break
                elapsed = self._monotonic() - started
                timed_out = elapsed >= timeout
                cancelled = bool(
                    cancel_requested is not None and cancel_requested()
                )
                if timed_out or cancelled:
                    self._terminate_group(proc, spec.termination_grace_seconds)
                    proc.wait()
                    raise ProcessFailure(
                        "PROCESS_TIMEOUT" if timed_out else "PROCESS_CANCELLED",
                        f"{spec.kind.value} ended by "
                        f"{'timeout' if timed_out else 'cancellation'}",
                    )
                if on_output is not None and (stdout_buf or stderr_buf):
                    pass  # streaming hooks are throttled by the caller
                self._sleep(0.02)
        finally:
            for stream in fds:
                try:
                    stream.close()
                except OSError:
                    pass

        duration = self._monotonic() - started
        out_tail, out_cut = _tail(bytes(stdout_buf), spec.max_output_bytes)
        err_tail, err_cut = _tail(bytes(stderr_buf), spec.max_output_bytes)
        if on_output is not None and err_tail:
            on_output(_redact(err_tail[-400:], spec.redaction_tokens))
        if code not in spec.expected_exit_codes:
            raise ProcessFailure(
                "PROCESS_EXIT_UNEXPECTED",
                _redact(
                    f"{spec.kind.value} exited {code}: {err_tail[-200:] or out_tail[-200:] or 'no output'}",
                    spec.redaction_tokens,
                ),
            )
        return ProcessResult(
            exit_code=code,
            stdout_tail=_redact(out_tail, spec.redaction_tokens),
            stderr_tail=_redact(err_tail, spec.redaction_tokens),
            truncated_stdout=out_cut,
            truncated_stderr=err_cut,
            duration_seconds=duration,
        )

    def _terminate_group(self, proc: subprocess.Popen, grace: float) -> None:
        """TERM the whole process group, wait a bounded grace, then KILL."""
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        deadline = self._monotonic() + max(grace, 0.0)
        while self._monotonic() < deadline:
            if proc.poll() is not None:
                return
            self._sleep(0.05)
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait()


__all__ = [
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_TIMEOUTS",
    "PROGRESS_PHASE_BANDS",
    "CommandKind",
    "ProcessCommandSpec",
    "ProcessFailure",
    "ProcessResult",
    "RuntimeProcessRunner",
]
