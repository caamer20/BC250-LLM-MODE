from __future__ import annotations

import logging
import os
import shlex
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from logging.handlers import RotatingFileHandler
from pathlib import Path

LogCallback = Callable[[str], None]

LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3

# P3 §9.1/§9.3 (DEF-003): every child process is bounded. These defaults
# sit far above any administrative command that runs through this runner
# (systemctl/elevated helpers finish in seconds); individual call sites
# may pass larger explicit bounds when a longer-lived command is proven.
DEFAULT_RUN_TIMEOUT_SECONDS = 600.0
DEFAULT_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
TERMINATION_GRACE_SECONDS = 5.0


def configure_logging(logs_dir: str | Path) -> logging.Logger:
    path = Path(logs_dir).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("bc250_llm_mode")
    logger.setLevel(logging.INFO)
    log_path = (path / "setup.log").resolve()
    if not any(
        isinstance(h, RotatingFileHandler) and Path(h.baseFilename) == log_path
        for h in logger.handlers
    ):
        handler = RotatingFileHandler(
            log_path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


class CommandError(RuntimeError):
    def __init__(self, command: Sequence[str], returncode: int, output: str,
                 *, timed_out: bool = False) -> None:
        self.command = list(command)
        self.returncode = returncode
        self.output = output
        self.timed_out = timed_out
        if timed_out:
            super().__init__(
                f"Command exceeded its time bound and was stopped: "
                f"{shlex.join(command)}"
            )
        else:
            super().__init__(f"Command failed ({returncode}): {shlex.join(command)}")


class CommandRunner:
    """Run argument-vector commands and stream merged output without a shell."""

    def __init__(self, logger: logging.Logger, callback: LogCallback | None = None) -> None:
        self.logger = logger
        self.callback = callback
        self._lock = threading.Lock()

    def emit(self, line: str) -> None:
        line = line.rstrip("\n")
        self.logger.info(line)
        if self.callback:
            with self._lock:
                self.callback(line)

    def run(
        self,
        command: Sequence[str],
        *,
        check: bool = True,
        env: dict[str, str] | None = None,
        cwd: str | Path | None = None,
        input_text: str | None = None,
        emit_output: bool = True,
        timeout_seconds: float = DEFAULT_RUN_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> subprocess.CompletedProcess[str]:
        """Bounded, streaming execution (P3 §9.3, DEF-003).

        The child runs in its own process group; on timeout the WHOLE
        group receives TERM then KILL after a bounded grace. Output is
        capped: bytes beyond ``max_output_bytes`` are dropped from the
        returned buffer while a single truncation marker is emitted.
        """
        argv = [str(item) for item in command]
        self.emit(f"$ {shlex.join(argv)}")
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            argv,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdin=subprocess.PIPE if input_text is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        output: list[str] = []
        captured_bytes = 0
        truncated = False
        timed_out = threading.Event()

        def stop_on_deadline() -> None:
            # The stream read below blocks between lines; a silent hung
            # child must be stopped from OUTSIDE the read loop.
            timed_out.set()
            self._terminate_group(process)

        watchdog = threading.Timer(max(1.0, float(timeout_seconds)),
                                   stop_on_deadline)
        watchdog.daemon = True
        watchdog.start()
        try:
            if input_text is not None and process.stdin:
                process.stdin.write(input_text)
                process.stdin.close()
            assert process.stdout is not None
            for line in process.stdout:
                if captured_bytes < max_output_bytes:
                    encoded = len(line.encode("utf-8", "replace"))
                    output.append(line)
                    captured_bytes += encoded
                elif not truncated:
                    truncated = True
                    marker = (
                        f"[output truncated at {max_output_bytes} bytes]\n"
                    )
                    output.append(marker)
                    if emit_output:
                        self.emit(marker)
                if emit_output:
                    self.emit(line)
            returncode = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            # wait() raced a still-dying group after our own stop.
            self._terminate_group(process)
            returncode = process.wait()
        except BaseException:
            self._terminate_group(process)
            raise
        finally:
            watchdog.cancel()
        result = subprocess.CompletedProcess(argv, returncode, "".join(output), "")
        if timed_out.is_set():
            raise CommandError(
                argv, returncode or 124, result.stdout, timed_out=True
            )
        if check and returncode:
            raise CommandError(argv, returncode, result.stdout)
        return result

    def _terminate_group(self, process: subprocess.Popen) -> None:
        """TERM the whole process group, bounded grace, then KILL."""
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            process.wait(timeout=TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
            process.wait()
