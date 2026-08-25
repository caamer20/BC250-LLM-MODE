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
    spawner: Callable[[list[str]], Any] | None = None,
) -> int:
    """Start one detached worker process; returns its pid.

    The default spawner uses a fresh process group with stdio detached so
    the worker outlives the frontend cleanly. Tests inject a recorder.
    """
    argv = list(WORKER_MAIN_ARGV)
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
    """Entry for ``python -m bc250_llm_mode.worker_main``."""
    from .app import Application
    from .operations.worker_host import WorkerHost, WorkerHostPolicy

    application = Application.compose()
    application.require_operational()
    policy = WorkerHostPolicy()

    import threading
    import time as _time

    condition = threading.Condition()
    monotonic = _time.monotonic

    def waiter(timeout_seconds: float) -> bool:
        # Bounded condition wait — never timeout=0 polling.
        with condition:
            return condition.wait(timeout=max(0.05, float(timeout_seconds)))

    host = WorkerHost(
        application.units,
        application.registry,
        policy=policy,
        monotonic=monotonic,
        waiter=waiter,
    )
    stats = host.start()
    print(json_safe(stats))
    return 0


def json_safe(stats: dict[str, Any]) -> str:
    import json

    return json.dumps({"worker": "idle-exit", **stats}, sort_keys=True)


if __name__ == "__main__":  # pragma: no cover - process entry
    raise SystemExit(run_worker_main())
