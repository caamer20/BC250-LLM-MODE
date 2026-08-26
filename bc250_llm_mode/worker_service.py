"""U1.3 detached worker entry point and typed spawn helper.

``python -m bc250_llm_mode.worker_main`` runs ONE WorkerHost to idle exit
against the default profile. It is started ONLY by an explicit enqueue
with ``--detach`` (or an explicit user command) — never by composition,
boot, or the GUI. The spawned process inherits nothing privileged; before
U3, operations needing interactive privilege pause at their authorization
boundary instead of pretending to be detached.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any, Callable

WORKER_MAIN_ARGV = (sys.executable, "-m", "bc250_llm_mode.worker_main")


def spawn_detached(
    *,
    extra_argv: tuple[str, ...] | list[str] = (),
    spawner: Callable[[list[str]], Any] | None = None,
) -> int:
    """Start one detached worker process; returns its pid.

    The default spawner uses a fresh process group with stdio detached so
    the worker outlives the frontend cleanly. Tests inject a recorder.
    ``extra_argv`` extends the fixed base argv (e.g. ``--profile DIR``
    for U1.4 generic detach); it never replaces it.
    """
    argv = list(WORKER_MAIN_ARGV) + [str(a) for a in extra_argv]
    if spawner is not None:
        result = spawner(argv)
        return int(result)
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return int(proc.pid)


def run_worker_main() -> int:
    """Compatibility alias for ``python -m bc250_llm_mode.worker_main``.

    P0.1 moved the real entry (argument parsing, profile resolution,
    stable exit codes) into :mod:`bc250_llm_mode.worker_main`; this alias
    delegates so there is exactly ONE worker entry implementation.
    """
    from .worker_main import main

    return main([])


if __name__ == "__main__":  # pragma: no cover - process entry
    raise SystemExit(run_worker_main())
