"""U1.4 §7.4: the ``bc250-llm-mode operations`` command group.

Human output is compact and actionable; ``--json`` emits one schema-
versioned document to STDOUT only. Exit codes are stable across the
group: 0 success, 1 refused/failed, 78 recovery-required gating, 2 usage
(argparse), 130 interrupted.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

_MAX_FOLLOW_SECONDS = 3600.0
_FOLLOW_INTERVAL_SECONDS = 0.5


def register_operations_parser(sub) -> argparse.ArgumentParser:
    ops = sub.add_parser(
        "operations",
        help="Inspect and control durable operations (U1.4 control plane)",
    )
    ops.add_argument(
        "action",
        choices=(
            "list", "show", "steps", "events", "wait", "cancel",
            "resume", "retry", "recover", "dismiss",
        ),
    )
    ops.add_argument("operation_id", nargs="?")
    ops.add_argument("--active", action="store_true",
                     help="list: only unfinished operations")
    ops.add_argument("--recent", action="store_true",
                     help="list: only finished operations, newest first")
    ops.add_argument("--kind", help="list: filter by operation kind")
    ops.add_argument("--limit", type=int, default=20,
                     help="page size for list/events (bounded)")
    ops.add_argument("--after", type=int, default=0,
                     help="events: cursor to read after")
    ops.add_argument("--follow", action="store_true",
                     help="events: keep printing until terminal/Ctrl-C/timeout")
    ops.add_argument("--timeout", type=float, default=30.0,
                     help="wait/follow bound in seconds (<= 3600)")
    ops.add_argument("--reason", help="cancel: durable reason text")
    ops.add_argument("--confirm", action="store_true",
                     help="recover: required to take over interrupted work")
    ops.add_argument("--restore", action="store_true",
                     help="dismiss: return a hidden operation to default views")
    ops.add_argument("--json", action="store_true",
                     help="schema-versioned JSON on stdout")
    return ops


def run_operations_command(application, args) -> int:
    """Dispatch one parsed `operations` invocation; returns exit code."""
    query = application.operation_query
    commands = application.operation_commands
    action = args.action
    operation_id = args.operation_id

    needs_id = action not in ("list",)
    if needs_id and not operation_id:
        print("error: an OPERATION_ID is required for this action",
              file=sys.stderr)
        return 2

    try:
        if action == "list":
            return _list(query, args)
        if action == "show":
            return _show(query, operation_id, args)
        if action == "steps":
            return _steps(query, operation_id, args)
        if action == "events":
            return _events(query, operation_id, args)
        if action == "wait":
            return _wait(query, operation_id, args)
        result = None
        if action == "cancel":
            result = commands.cancel(operation_id, reason=args.reason)
        elif action == "resume":
            result = commands.resume(operation_id)
        elif action == "retry":
            result = commands.retry(operation_id)
        elif action == "recover":
            result = commands.recover(operation_id, confirm=args.confirm)
        elif action == "dismiss":
            result = commands.dismiss(operation_id, restore=args.restore)
        else:  # pragma: no cover - argparse restricts choices
            return 2
        return _emit_result(result, args)
    except KeyError:
        print(f"error: no such operation: {operation_id}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


# -- query actions ---------------------------------------------------------------


def _list(query, args) -> int:
    scope = "all"
    if args.active:
        scope = "active"
    elif args.recent:
        scope = "recent"
    page = query.list(
        scope=scope,
        kind=args.kind,
        page_size=max(1, min(int(args.limit), 200)),
    )
    if args.json:
        print(json.dumps(page.to_dict(), sort_keys=True))
        return 0
    if not page.items:
        print("no operations match this view")
        return 0
    for item in page.items:
        progress = ""
        if item.progress_total:
            progress = (
                f" {item.progress_current}/{item.progress_total}"
                f"{item.progress_unit or ''}"
            )
        line = (
            f"{item.operation_id}  {item.kind}  {item.state}{progress}"
            f"  {item.title}"
        )
        print(line)
        if item.failure_code:
            print(
                f"    failed: {item.failure_code} — "
                f"{item.recovery_recommendation}"
            )
        elif item.state != "SUCCEEDED":
            print(f"    next: {item.recovery_recommendation}")
    return 0


def _show(query, operation_id, args) -> int:
    detail = query.show(operation_id)
    if detail is None:
        print(f"error: no such operation: {operation_id}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(detail.to_dict(), sort_keys=True))
        return 0
    s = detail.summary
    print(f"{s.operation_id}")
    print(f"  kind:     {s.kind} (request v{s.kind_version})")
    print(f"  state:    {s.state}" + (f" — {s.phase}" if s.phase else ""))
    if s.progress_total:
        print(
            f"  progress: {s.progress_current}/{s.progress_total}"
            f" {s.progress_unit or ''}"
        )
    print(f"  surface:  {s.surface}")
    if detail.current_step:
        print(f"  step:     {detail.current_step}")
    if detail.error_code:
        print(f"  failure:  {detail.error_code}")
    if detail.result_code:
        print(f"  result:   {detail.result_code}")
    actions = [
        name
        for name, enabled in (
            ("cancel", s.cancellable),
            ("resume", s.resumable),
            ("retry", s.retryable),
            ("recover", s.recoverable),
            ("dismiss", s.dismissable),
        )
        if enabled
    ]
    print(f"  actions:  {', '.join(actions) if actions else 'none'}")
    print(f"  next:     {s.recovery_recommendation}")
    for lease in detail.leases:
        state = "EXPIRED" if lease.expired else "held"
        print(f"  lease:    {lease.resource_key} owner={lease.owner} {state}")
    return 0


def _steps(query, operation_id, args) -> int:
    steps = query.steps(operation_id)
    if args.json:
        print(json.dumps(
            {"schema_version": 1,
             "steps": [s.to_dict() for s in steps]},
            sort_keys=True,
        ))
        return 0
    for step in steps:
        marker = {
            "VERIFIED": "[x]", "FAILED": "[!]",
        }.get(step.state, "[ ]")
        retries = f" ({step.attempts} attempts)" if step.attempts else ""
        print(f"{marker} {step.ordinal:>2}. {step.name} — {step.state}{retries}")
    return 0


def _events(query, operation_id, args) -> int:
    limit = max(1, min(int(args.limit), 500))
    page = query.events(operation_id, after_sequence=args.after, limit=limit)
    _print_events(page, args.json)
    if not args.follow:
        return 0
    deadline = time.monotonic() + min(max(args.timeout, 0.0), _MAX_FOLLOW_SECONDS)
    cursor = args.after + len(page.events)
    while True:
        time.sleep(_FOLLOW_INTERVAL_SECONDS)
        summary = query.show(operation_id)
        if summary is None:
            return 1
        page = query.events(operation_id, after_sequence=cursor, limit=limit)
        _print_events(page, args.json)
        cursor += len(page.events)
        if summary.summary.state in _TERMINAL_STATES:
            return 0
        if time.monotonic() >= deadline:
            print("follow timeout reached; operation still in progress",
                  file=sys.stderr)
            return 0


def _print_events(page, as_json: bool) -> None:
    if as_json:
        print(json.dumps(page.to_dict(), sort_keys=True))
        return
    for event in page.events:
        print(
            f"{event.sequence:>6} {event.timestamp} [{event.level}] "
            f"{event.event_type}: {event.message}"
        )


def _wait(query, operation_id, args) -> int:
    result = query.wait(
        operation_id,
        timeout_seconds=min(max(args.timeout, 0.001), 3600.0),
    )
    if args.json:
        print(json.dumps(result.to_dict(), sort_keys=True))
        return 0
    if result.timed_out:
        print(
            f"{operation_id}: still {result.state} after {args.timeout:g}s"
        )
        return 0
    print(f"{operation_id}: {result.state}")
    return 0


def _emit_result(result, args) -> int:
    if args.json:
        print(json.dumps(result.to_dict(), sort_keys=True))
        return result.exit_code
    if result.ok:
        message = result.reason or result.outcome
        suffix = f" ({result.state})" if result.state else ""
        print(f"{result.action}: {message}{suffix}")
        if result.detail.get("parent_operation_id"):
            print(f"  retried from: {result.detail['parent_operation_id']}")
        return result.exit_code
    print(f"{result.action}: refused — {result.reason}", file=sys.stderr)
    return result.exit_code


_TERMINAL_STATES = {
    "SUCCEEDED",
    "CANCELLED",
    "FAILED_SAFE",
    "FAILED_ROLLED_BACK",
    "RECOVERY_REQUIRED",
}
