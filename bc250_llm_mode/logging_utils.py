from __future__ import annotations

import logging
import shlex
import subprocess
import threading
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path

LogCallback = Callable[[str], None]

LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3


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
    def __init__(self, command: Sequence[str], returncode: int, output: str) -> None:
        self.command = list(command)
        self.returncode = returncode
        self.output = output
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
    ) -> subprocess.CompletedProcess[str]:
        argv = [str(item) for item in command]
        self.emit(f"$ {shlex.join(argv)}")
        process = subprocess.Popen(
            argv,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdin=subprocess.PIPE if input_text is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        output: list[str] = []
        if input_text is not None and process.stdin:
            process.stdin.write(input_text)
            process.stdin.close()
        assert process.stdout is not None
        for line in process.stdout:
            output.append(line)
            if emit_output:
                self.emit(line)
        returncode = process.wait()
        result = subprocess.CompletedProcess(argv, returncode, "".join(output), "")
        if check and returncode:
            raise CommandError(argv, returncode, result.stdout)
        return result
