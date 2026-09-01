"""Shared, truthful progress projection for durable operations.

The projection is intentionally pure.  It never polls, sleeps, mutates an
operation, or guesses an ETA.  GUI and CLI surfaces receive the same bounded
interpretation of durable operation timestamps, phases, totals, and actions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .problem_details import ProblemDetail, problem_detail


PROGRESS_PROJECTION_SCHEMA_VERSION = 1
PROGRESS_QUIET_SECONDS = 15
GATEWAY_READY_DEADLINE_SECONDS = 10
OPENWEBUI_READY_DEADLINE_SECONDS = 120
MODEL_READY_DEADLINE_SECONDS = 120
TAILNET_READY_DEADLINE_SECONDS = 30
INTEGRATION_READY_DEADLINE_SECONDS = 300

_ACTIVE_DEADLINE_STATES = frozenset({
    "PREPARING", "RUNNING", "VERIFYING", "COMMITTING",
})
_TERMINAL_FAILURE_STATES = frozenset({
    "FAILED_SAFE", "FAILED_ROLLED_BACK", "RECOVERY_REQUIRED",
})
_MEASURED_TOTAL_UNITS = frozenset({"bytes", "files", "items"})

_TASK_TITLES = {
    "MODEL_ACQUIRE": "Install model",
    "MODEL_IMPORT": "Import model",
    "MODEL_ACTIVATE": "Start model",
    "MODEL_REMOVE": "Remove model",
    "MODEL_CONVERT": "Prepare model",
    "INTEGRATION_SETUP": "Set up private connection",
    "STORAGE_CLEANUP": "Clean up storage",
    "PROFILE_CALIBRATE": "Measure performance profile",
    "RUNTIME_UPDATE": "Update model runtime",
    "RUNTIME_ROLLBACK": "Restore model runtime",
    "APPLICATION_UPDATE": "Update BC250 LLM MODE",
    "BACKUP_CREATE": "Create backup",
    "BACKUP_RESTORE": "Restore backup",
}

_PHASE_LABELS = {
    "client": "Preparing separate client access",
    "model": "Starting and checking the model",
    "gateway": "Starting the private API",
    "openwebui": "Starting and checking Open WebUI",
    "tailnet": "Publishing private Tailscale access",
    "verify": "Testing authorized and blocked access",
    "fetch": "Downloading runtime source",
    "configure": "Preparing runtime configuration",
    "build": "Building the model runtime",
    "download": "Downloading",
    "transfer": "Downloading",
    "running": "Working",
    "rollback": "Restoring previous state",
}

_STEP_LABELS = {
    "ensure_clients": "Preparing separate client access",
    "start_model": "Starting and checking the model",
    "start_gateway": "Starting the private API",
    "start_openwebui": "Starting and checking Open WebUI",
    "publish_tailnet": "Publishing private Tailscale access",
    "verify_client": "Testing authorized and blocked access",
    "resolve_source": "Resolving model source",
    "reserve_storage": "Checking storage",
    "transfer_source": "Downloading model data",
    "materialize_candidate": "Preparing model files",
    "validate_candidate": "Verifying model files",
    "publish_artifact": "Installing model",
    "register_installation": "Registering model",
    "finalize_staging": "Finishing installation",
    "resolve_candidate": "Resolving installed model",
    "capture_prior": "Saving rollback state",
    "commit_candidate_config": "Applying model configuration",
    "publish_candidate_handoff": "Publishing runtime configuration",
    "restart_candidate": "Starting the model service",
    "verify_candidate_health": "Checking model health",
    "verify_candidate_inference": "Verifying a model response",
    "promote_known_good": "Saving the known-good model",
}

_NEXT_CHECKPOINTS = {
    "client": "Separate client credential recorded",
    "model": "Model health, identity, and inference verified",
    "gateway": "Private API socket and health verified",
    "openwebui": "Open WebUI provider and streamed response verified",
    "tailnet": "Private HTTPS mappings verified with Funnel disabled",
    "verify": "Authorized models and streaming checks complete",
    "download": "Downloaded bytes verified",
    "transfer": "Downloaded bytes verified",
}


@dataclass(frozen=True)
class TaskProgressProjection:
    """Versioned presentation truth shared by all operation surfaces."""

    task_title: str
    phase_label: str
    state: str
    elapsed_seconds: int
    last_progress_at: str
    determinate_fraction: float | None
    cancel_available: bool
    close_safe: bool
    next_checkpoint: str
    problem: ProblemDetail | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["schema_version"] = PROGRESS_PROJECTION_SCHEMA_VERSION
        return value


def project_operation_progress(
    summary: Any,
    *,
    current_step: str | None = None,
    now: datetime | str | None = None,
) -> TaskProgressProjection:
    """Project one operation without inventing percentage or ETA evidence."""
    state = _bounded_text(getattr(summary, "state", None), "UNKNOWN")
    kind = _bounded_text(getattr(summary, "kind", None), "OPERATION")
    phase = _bounded_text(getattr(summary, "phase", None), "").lower()
    step = _bounded_text(current_step, "").lower()
    created_at = _bounded_text(getattr(summary, "created_at", None), "")
    updated_at = _bounded_text(getattr(summary, "updated_at", None), "")
    now_value = _as_datetime(now) or datetime.now(timezone.utc)
    created_value = _as_datetime(created_at)
    updated_value = _as_datetime(updated_at)
    elapsed = _seconds_since(created_value, now_value)
    quiet = _seconds_since(updated_value, now_value)

    base_phase = _phase_label(state=state, phase=phase, current_step=step)
    if state in _ACTIVE_DEADLINE_STATES and quiet >= PROGRESS_QUIET_SECONDS:
        phase_label = f"Still working — {base_phase}"
    else:
        phase_label = base_phase

    fraction = _measured_fraction(summary, state=state)
    failure_code = _bounded_text(getattr(summary, "failure_code", None), "")
    problem = _terminal_problem(state, failure_code)
    if problem is None and state in _ACTIVE_DEADLINE_STATES:
        deadline = _deadline_for(kind=kind, phase=phase, current_step=step)
        phase_elapsed = quiet if updated_value is not None else elapsed
        aggregate_expired = (
            kind == "INTEGRATION_SETUP"
            and elapsed >= INTEGRATION_READY_DEADLINE_SECONDS
        )
        if aggregate_expired or (deadline is not None and phase_elapsed >= deadline):
            problem = problem_detail("OPERATION_DEADLINE_EXCEEDED")

    recommendation = _bounded_text(
        getattr(summary, "recovery_recommendation", None), "View Activity"
    )
    next_checkpoint = _next_checkpoint(
        state=state,
        phase=phase,
        current_step=step,
        recommendation=recommendation,
    )
    return TaskProgressProjection(
        task_title=_TASK_TITLES.get(
            kind,
            _bounded_text(getattr(summary, "title", None), "Background task"),
        ),
        phase_label=phase_label,
        state=state,
        elapsed_seconds=elapsed,
        last_progress_at=updated_at,
        determinate_fraction=fraction,
        cancel_available=bool(getattr(summary, "cancellable", False)),
        close_safe=True,
        next_checkpoint=next_checkpoint,
        problem=problem,
    )


def format_elapsed(seconds: int) -> str:
    """Compact elapsed duration with no ETA implication."""
    value = max(0, int(seconds))
    if value < 60:
        return f"{value}s elapsed"
    minutes, remainder = divmod(value, 60)
    if minutes < 60:
        return f"{minutes}m {remainder:02d}s elapsed"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m elapsed"


def _phase_label(*, state: str, phase: str, current_step: str) -> str:
    if state == "QUEUED":
        return "Waiting for a safe turn"
    if state == "PAUSED":
        return "Paused at a safe checkpoint"
    if state == "CANCEL_REQUESTED":
        return "Stopping at the next safe checkpoint"
    if state == "ROLLING_BACK":
        return "Restoring the previous state"
    if state == "VERIFYING":
        return "Verifying the result"
    if state == "COMMITTING":
        return "Saving the verified result"
    if state == "SUCCEEDED":
        return "Result verified"
    if state == "CANCELLED":
        return "Stopped safely"
    if state == "FAILED_ROLLED_BACK":
        return "Previous state restored"
    if state in {"FAILED_SAFE", "RECOVERY_REQUIRED"}:
        return "Stopped with a recorded problem"
    if current_step in _STEP_LABELS:
        return _STEP_LABELS[current_step]
    if phase in _PHASE_LABELS:
        return _PHASE_LABELS[phase]
    if phase:
        return _humanize(phase)
    return "Preparing"


def _next_checkpoint(
    *, state: str, phase: str, current_step: str, recommendation: str
) -> str:
    if state == "SUCCEEDED":
        return "Result verified"
    if state in {"FAILED_SAFE", "FAILED_ROLLED_BACK", "RECOVERY_REQUIRED"}:
        return recommendation
    if state == "CANCELLED":
        return "No further work is scheduled"
    key = current_step or phase
    return _NEXT_CHECKPOINTS.get(
        key,
        f"{_STEP_LABELS.get(key, _humanize(key))} completes"
        if key else "The next durable checkpoint is recorded",
    )


def _measured_fraction(summary: Any, *, state: str) -> float | None:
    if state in {"SUCCEEDED", "CANCELLED", "FAILED_SAFE", "FAILED_ROLLED_BACK", "RECOVERY_REQUIRED"}:
        return None
    unit = _bounded_text(getattr(summary, "progress_unit", None), "").lower()
    if unit not in _MEASURED_TOTAL_UNITS:
        return None
    try:
        current = max(0, int(getattr(summary, "progress_current", 0) or 0))
        total = int(getattr(summary, "progress_total", 0) or 0)
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    # An active operation at its measured total is still awaiting durable
    # verification; the fraction remains truthful while UI text avoids a
    # "completed" claim.
    return min(current, total) / total


def _deadline_for(*, kind: str, phase: str, current_step: str) -> int | None:
    key = current_step or phase
    if key in {"start_gateway", "gateway"}:
        return GATEWAY_READY_DEADLINE_SECONDS
    if key in {"start_openwebui", "openwebui"}:
        return OPENWEBUI_READY_DEADLINE_SECONDS
    if key in {"publish_tailnet", "tailnet"}:
        return TAILNET_READY_DEADLINE_SECONDS
    if key in {
        "start_model", "model", "restart_candidate",
        "verify_candidate_health", "verify_candidate_inference",
    }:
        return MODEL_READY_DEADLINE_SECONDS
    if kind == "MODEL_ACTIVATE":
        return MODEL_READY_DEADLINE_SECONDS
    return None


def _terminal_problem(state: str, failure_code: str) -> ProblemDetail | None:
    if state not in _TERMINAL_FAILURE_STATES:
        return None
    if state == "RECOVERY_REQUIRED":
        return problem_detail("RECOVERY_REQUIRED")
    return problem_detail(failure_code or "OPERATION_FAILED")


def _as_datetime(value: datetime | str | None) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _seconds_since(value: datetime | None, now: datetime) -> int:
    if value is None:
        return 0
    return max(0, int((now - value).total_seconds()))


def _humanize(value: str) -> str:
    clean = " ".join(value.replace("-", "_").split("_"))[:80].strip()
    return clean.capitalize() if clean else "Working"


def _bounded_text(value: Any, default: str) -> str:
    if not isinstance(value, str):
        return default
    text = " ".join(value.strip().split())[:160]
    return text or default


__all__ = [
    "GATEWAY_READY_DEADLINE_SECONDS",
    "INTEGRATION_READY_DEADLINE_SECONDS",
    "MODEL_READY_DEADLINE_SECONDS",
    "OPENWEBUI_READY_DEADLINE_SECONDS",
    "PROGRESS_PROJECTION_SCHEMA_VERSION",
    "PROGRESS_QUIET_SECONDS",
    "TAILNET_READY_DEADLINE_SECONDS",
    "TaskProgressProjection",
    "format_elapsed",
    "project_operation_progress",
]
