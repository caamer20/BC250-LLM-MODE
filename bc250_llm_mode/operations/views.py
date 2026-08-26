"""U1.4 §7.1: immutable, versioned operation view models.

The ONLY shapes the query service returns — never raw SQL rows. Rules
enforced here and by the query service:

- no secrets, absolute source paths, raw prompts, auth headers, or
  unbounded command output (request payloads are path-label redacted);
- stable serialization (`to_dict`) for CLI JSON with an explicit schema
  version;
- event types / failure codes are closed enums or versioned strings that
  degrade to ``UNKNOWN`` rather than leaking raw internals;
- safe-action availability derives from durable state via the frozen ADR
  002 transition table plus lease ownership.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

OPERATION_VIEW_SCHEMA_VERSION = 1

_MAX_EMBEDDED_STR = 512


def _label_for_path(value: str) -> str:
    """Redact absolute user/model paths to a stable file label."""
    if not isinstance(value, str) or not value:
        return value
    if value.startswith("/") or value.startswith("~"):
        return "file:" + os.path.basename(value)
    return value


def redact_request(payload: dict[str, Any]) -> dict[str, Any]:
    """View-safe copy of a durable request payload.

    Durable request rows are already sanitized at persistence time; this
    additionally removes absolute source paths (the one class of content
    the view contract forbids) and bounds string sizes.
    """
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str):
            value = _label_for_path(value)[:_MAX_EMBEDDED_STR]
        elif isinstance(value, dict):
            value = redact_request(value)
        out[key] = value
    return out


@dataclass(frozen=True)
class OperationStepView:
    """One durable workflow step, presentation-shaped."""

    ordinal: int
    name: str
    phase: str
    state: str
    attempts: int
    started_at: str | None
    finished_at: str | None
    checkpointed_at: str | None
    failure_code: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "name": self.name,
            "phase": self.phase,
            "state": self.state,
            "attempts": self.attempts,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "checkpointed_at": self.checkpointed_at,
            "failure_code": self.failure_code,
        }


@dataclass(frozen=True)
class OperationEventView:
    """One audit event; ``sequence`` is the monotone cursor."""

    sequence: int
    timestamp: str
    level: str
    event_type: str  # closed code or "UNKNOWN"
    message_key: str
    message: str  # sanitized public summary (persisted redacted)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "level": self.level,
            "event_type": self.event_type,
            "message_key": self.message_key,
            "message": self.message,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class LeaseView:
    resource_key: str
    owner: str
    lease_revision: int
    acquired_at: str
    heartbeat_at: str
    expires_at: str
    expired: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_key": self.resource_key,
            "owner": self.owner,
            "lease_revision": self.lease_revision,
            "acquired_at": self.acquired_at,
            "heartbeat_at": self.heartbeat_at,
            "expires_at": self.expires_at,
            "expired": self.expired,
        }


@dataclass(frozen=True)
class OperationSummary:
    operation_id: str
    kind: str
    kind_version: int
    title: str
    state: str
    phase: str | None
    progress_current: int
    progress_total: int | None
    progress_unit: str | None
    created_at: str
    updated_at: str
    surface: str
    cancellable: bool
    resumable: bool
    retryable: bool
    recoverable: bool
    dismissable: bool
    failure_code: str | None
    recovery_recommendation: str
    dismissed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "kind": self.kind,
            "kind_version": self.kind_version,
            "title": self.title,
            "state": self.state,
            "phase": self.phase,
            "progress_current": self.progress_current,
            "progress_total": self.progress_total,
            "progress_unit": self.progress_unit,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "surface": self.surface,
            "actions": {
                "cancellable": self.cancellable,
                "resumable": self.resumable,
                "retryable": self.retryable,
                "recoverable": self.recoverable,
                "dismissable": self.dismissable,
            },
            "failure_code": self.failure_code,
            "recovery_recommendation": self.recovery_recommendation,
            "dismissed": self.dismissed,
        }


@dataclass(frozen=True)
class OperationDetail:
    summary: OperationSummary
    request: dict[str, Any]
    steps: tuple[OperationStepView, ...]
    current_step: str | None
    leases: tuple[LeaseView, ...]
    result_code: str | None
    error_code: str | None
    parent_operation_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": OPERATION_VIEW_SCHEMA_VERSION,
            **self.summary.to_dict(),
            "request": dict(self.request),
            "steps": [s.to_dict() for s in self.steps],
            "current_step": self.current_step,
            "leases": [l.to_dict() for l in self.leases],
            "result_code": self.result_code,
            "error_code": self.error_code,
            "parent_operation_id": self.parent_operation_id,
        }


@dataclass(frozen=True)
class OperationPage:
    items: tuple[OperationSummary, ...]
    page: int
    page_size: int
    total: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": OPERATION_VIEW_SCHEMA_VERSION,
            "items": [i.to_dict() for i in self.items],
            "page": self.page,
            "page_size": self.page_size,
            "total": self.total,
        }


@dataclass(frozen=True)
class EventPage:
    operation_id: str
    events: tuple[OperationEventView, ...]
    after_sequence: int
    limit: int

    @property
    def has_more(self) -> bool:
        return len(self.events) == self.limit

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": OPERATION_VIEW_SCHEMA_VERSION,
            "operation_id": self.operation_id,
            "events": [e.to_dict() for e in self.events],
            "after_sequence": self.after_sequence,
            "limit": self.limit,
            "has_more": self.has_more,
        }


@dataclass(frozen=True)
class OperationWaitResult:
    operation_id: str
    state: str
    latest_revision: int
    changed: bool
    timed_out: bool
    recommended_command: str

    @property
    def terminal(self) -> bool:
        from .model import OperationState, is_terminal

        return is_terminal(OperationState(self.state))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": OPERATION_VIEW_SCHEMA_VERSION,
            "operation_id": self.operation_id,
            "state": self.state,
            "latest_revision": self.latest_revision,
            "changed": self.changed,
            "timed_out": self.timed_out,
            "terminal": self.terminal,
            "recommended_command": self.recommended_command,
        }


@dataclass(frozen=True)
class ActiveOperationSummary:
    active_count: int
    queued_count: int
    running_count: int
    paused_count: int
    recovery_required_count: int
    oldest_queued_id: str | None
    worker_lock_owner: str | None
    worker_lock_expired: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": OPERATION_VIEW_SCHEMA_VERSION,
            "active_count": self.active_count,
            "queued_count": self.queued_count,
            "running_count": self.running_count,
            "paused_count": self.paused_count,
            "recovery_required_count": self.recovery_required_count,
            "oldest_queued_id": self.oldest_queued_id,
            "worker_lock_owner": self.worker_lock_owner,
            "worker_lock_expired": self.worker_lock_expired,
        }
