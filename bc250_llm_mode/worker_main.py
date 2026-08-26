"""P0.1: thin executable entry for ONE detached worker host process.

``python -m bc250_llm_mode.worker_main`` runs ONE profile-scoped
:class:`WorkerHost` until it idles out or work completes (U1.3 policy).
It is started ONLY by an explicit detach handoff or an explicit user
command — never by composition, boot, or a frontend.

Contract (FINAL_PRODUCTION_READINESS plan §6 P0.1) — this module does
ONLY: parse profile/policy arguments; construct ``AppPaths`` explicitly
(no ``Path.home()`` leakage at import time); compose the application once;
run the requested worker-host action; map terminal outcomes to stable
exit codes; close resources deterministically. All orchestration stays in
``operations/worker_host.py``; all composition stays in ``app.py``.

Stable exit codes:

- ``0``  ran to idle exit / clean shutdown (JSON stats on stdout);
- ``2``  usage error (unknown argument, malformed or unsafe path/policy);
- ``3``  another live worker owns the profile lock;
- ``4``  repair required (missing database or non-operational composition);
- ``130`` interrupted (Ctrl-C) after a graceful shutdown request.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_ALREADY_RUNNING = 3
EXIT_REPAIR_REQUIRED = 4
EXIT_RUN_FAILED = 5
EXIT_INTERRUPTED = 130

_QUIET_PERIOD_BOUNDS = (0.5, 3600.0)
_LEASE_TTL_BOUNDS = (10, 600)


class _UsageError(Exception):
    """Malformed invocation; mapped to EXIT_USAGE with a stable code."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bc250_llm_mode.worker_main",
        description=(
            "Run ONE detached BC250 LLM MODE worker host against one"
            " application profile until it idles out. Never auto-started."
        ),
        allow_abbrev=False,
    )
    parser.add_argument(
        "--profile",
        type=Path,
        default=None,
        metavar="APP_DIR",
        help=(
            "application profile directory (must be absolute); defaults to"
            " the per-user profile"
        ),
    )
    parser.add_argument(
        "--quiet-period",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            "idle-exit bound override "
            f"[{_QUIET_PERIOD_BOUNDS[0]:g}, {_QUIET_PERIOD_BOUNDS[1]:g}]"
        ),
    )
    parser.add_argument(
        "--lease-ttl",
        type=int,
        default=None,
        metavar="SECONDS",
        help=(
            "lease/heartbeat TTL override "
            f"[{_LEASE_TTL_BOUNDS[0]}, {_LEASE_TTL_BOUNDS[1]}]"
        ),
    )
    return parser


def _resolve_paths(raw_profile: Path | None) -> tuple[Any, str | None]:
    """Resolve AppPaths without import-time home lookups.

    Returns ``(paths, error_code)``; exactly one of the two is None.
    """
    from .paths import AppPaths

    if raw_profile is None:
        if not os_environ_home_exists():
            return None, "WORKER_PROFILE_UNRESOLVED"
        try:
            return AppPaths.for_home(), None
        except (OSError, RuntimeError, ValueError):
            return None, "WORKER_PROFILE_UNRESOLVED"
    raw = raw_profile.expanduser()
    if not raw.is_absolute():
        return None, "WORKER_PROFILE_NOT_ABSOLUTE"
    try:
        paths = AppPaths.from_app_dir(raw)
        # Refuse symlink swaps before anything else touches the profile.
        paths.validate()
    except (OSError, RuntimeError, ValueError):
        return None, "WORKER_PROFILE_UNSAFE"
    return paths, None


def os_environ_home_exists() -> bool:
    """True when a home directory is resolvable for the default profile."""
    import os

    home = os.environ.get("HOME") or ""
    return bool(home.strip())


def _validate_policy(
    quiet_period: float | None, lease_ttl: int | None
) -> tuple[float | None, int | None]:
    if quiet_period is not None:
        low, high = _QUIET_PERIOD_BOUNDS
        if not (low <= quiet_period <= high):
            raise _UsageError("WORKER_POLICY_OUT_OF_RANGE: --quiet-period")
    if lease_ttl is not None:
        low, high = _LEASE_TTL_BOUNDS
        if not (low <= lease_ttl <= high):
            raise _UsageError("WORKER_POLICY_OUT_OF_RANGE: --lease-ttl")
    return quiet_period, lease_ttl


def _emit(payload: dict) -> None:
    """One JSON line on stdout; diagnostics go to stderr."""
    print(json.dumps(payload, sort_keys=True), flush=True)


def _fail(code: str, message: str, exit_code: int) -> int:
    print(f"{code}: {message}", file=sys.stderr, flush=True)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    """Process entry; returns a stable exit code (never raises)."""
    parser = build_parser()
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else list(argv))
        quiet_period, lease_ttl = _validate_policy(
            args.quiet_period, args.lease_ttl
        )
        paths, error_code = _resolve_paths(args.profile)
    except _UsageError as exc:
        return _fail(str(exc).split(":")[0], str(exc), EXIT_USAGE)
    except SystemExit as exc:  # argparse usage errors
        return EXIT_USAGE if exc.code else EXIT_OK
    if error_code is not None:
        if error_code == "WORKER_PROFILE_NOT_ABSOLUTE":
            return _fail(
                error_code, "--profile must be an absolute path", EXIT_USAGE
            )
        if error_code == "WORKER_PROFILE_UNSAFE":
            return _fail(
                error_code,
                "profile path is unsafe (symlinked or unreadable)",
                EXIT_USAGE,
            )
        return _fail(
            error_code,
            "no profile directory could be resolved; pass --profile APP_DIR",
            EXIT_USAGE,
        )

    assert paths is not None  # narrowed above
    database = paths.database_path
    if not database.is_file():
        return _fail(
            "WORKER_NO_DATABASE",
            f"no database at {database}; run setup before detaching work",
            EXIT_REPAIR_REQUIRED,
        )

    # Startup receipt (observability §18): proves this specific pid started
    # with which arguments, even when stdout/stderr are detached.
    try:
        paths.logs_dir.mkdir(parents=True, exist_ok=True)
        (paths.logs_dir / "worker-startup.txt").write_text(
            json.dumps({"pid": os.getpid(), "argv": sys.argv[1:]})
            + "\n",
        )
    except OSError:
        pass

    logger = logging.getLogger("bc250.worker_main")
    try:
        from .app import Application, RepairRequired
        from .operations.worker_host import (
            WorkerHost,
            WorkerHostPolicy,
            WorkerStartRefused,
        )

        try:
            application = Application.compose(paths)
            application.require_operational()
        except RepairRequired as exc:
            return _fail(
                "WORKER_REPAIR_REQUIRED", str(exc), EXIT_REPAIR_REQUIRED
            )
        except (OSError, ValueError) as exc:
            return _fail("WORKER_PROFILE_UNSAFE", str(exc), EXIT_USAGE)

        policy_kwargs: dict = {}
        if quiet_period is not None:
            policy_kwargs["quiet_period_seconds"] = float(quiet_period)
        if lease_ttl is not None:
            policy_kwargs["lease_ttl_seconds"] = int(lease_ttl)
        policy = WorkerHostPolicy(**policy_kwargs)

        condition = threading.Condition()

        def waiter(timeout_seconds: float) -> bool:
            # Bounded condition wait — timeout=0 polling is forbidden.
            with condition:
                return condition.wait(timeout=max(0.05, float(timeout_seconds)))

        host = WorkerHost(
            application.units,
            application.registry,
            policy=policy,
            monotonic=time.monotonic,
            waiter=waiter,
        )
    except Exception as exc:  # noqa: BLE001 - entry point maps, never traces
        logger.debug("worker host construction failed", exc_info=exc)
        return _fail(
            "WORKER_START_FAILED", f"{type(exc).__name__}", EXIT_REPAIR_REQUIRED
        )

    try:
        stats = host.start()
    except WorkerStartRefused:
        return _fail(
            "WORKER_ALREADY_RUNNING",
            "another live worker owns this profile lock",
            EXIT_ALREADY_RUNNING,
        )
    except KeyboardInterrupt:
        host.request_shutdown()
        _emit({"worker": "interrupted", **host.stats})
        return EXIT_INTERRUPTED
    except Exception as exc:  # noqa: BLE001 - bounded restarts already ran
        logger.debug("worker host crashed", exc_info=exc)
        return _fail(
            "WORKER_RUN_FAILED", f"{type(exc).__name__}", EXIT_RUN_FAILED
        )
    finally:
        # Deterministic close: composition owns no connection, so flushing
        # and closing log handlers ends every resource the child holds.
        logging.shutdown()

    _emit({"worker": "idle-exit", **stats})
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - process entry
    raise SystemExit(main())
