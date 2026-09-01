"""EUF-8 shared truthful progress and finite deadline contract."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _gui_stubs import install  # noqa: E402

install()

from bc250_llm_mode.gui.models_page import build_install_progress_view
from bc250_llm_mode.progress_projection import (
    GATEWAY_READY_DEADLINE_SECONDS,
    INTEGRATION_READY_DEADLINE_SECONDS,
    MODEL_READY_DEADLINE_SECONDS,
    OPENWEBUI_READY_DEADLINE_SECONDS,
    PROGRESS_QUIET_SECONDS,
    TAILNET_READY_DEADLINE_SECONDS,
    format_elapsed,
    project_operation_progress,
)


NOW = datetime(2026, 9, 1, 12, 5, tzinfo=timezone.utc)


def _summary(**changes):
    values = {
        "kind": "INTEGRATION_SETUP",
        "title": "INTEGRATION_SETUP (gui)",
        "state": "RUNNING",
        "phase": "gateway",
        "progress_current": 2,
        "progress_total": None,
        "progress_unit": None,
        "created_at": "2026-09-01T12:00:00+00:00",
        "updated_at": "2026-09-01T12:04:55+00:00",
        "cancellable": True,
        "failure_code": None,
        "recovery_recommendation": "Open Activity",
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_projection_has_closed_truthful_shape_and_no_eta():
    projected = project_operation_progress(
        _summary(
            created_at="2026-09-01T12:04:00+00:00",
            updated_at="2026-09-01T12:04:59+00:00",
        ),
        now=NOW,
    )

    assert projected.task_title == "Set up private connection"
    assert projected.phase_label == "Starting the private API"
    assert projected.state == "RUNNING"
    assert projected.elapsed_seconds == 60
    assert projected.last_progress_at == "2026-09-01T12:04:59+00:00"
    assert projected.determinate_fraction is None
    assert projected.cancel_available is True
    assert projected.close_safe is True
    assert "socket" in projected.next_checkpoint.lower()
    assert projected.problem is None
    assert set(projected.to_dict()) == {
        "schema_version", "task_title", "phase_label", "state",
        "elapsed_seconds", "last_progress_at", "determinate_fraction",
        "cancel_available", "close_safe", "next_checkpoint", "problem",
    }


def test_percentage_requires_a_measured_total():
    bytes_progress = project_operation_progress(
        _summary(
            kind="MODEL_ACQUIRE", phase="transfer",
            progress_current=25, progress_total=100, progress_unit="bytes",
            created_at="2026-09-01T12:04:50+00:00",
            updated_at="2026-09-01T12:04:59+00:00",
        ),
        now=NOW,
    )
    synthetic_steps = project_operation_progress(
        _summary(progress_current=3, progress_total=6, progress_unit="steps"),
        now=NOW,
    )

    assert bytes_progress.determinate_fraction == pytest.approx(0.25)
    assert synthetic_steps.determinate_fraction is None


def test_quiet_interval_says_still_working_without_guessing_eta():
    projected = project_operation_progress(
        _summary(
            phase="openwebui",
            created_at="2026-09-01T12:04:00+00:00",
            updated_at="2026-09-01T12:04:44+00:00",
        ),
        now=NOW,
    )

    assert PROGRESS_QUIET_SECONDS == 15
    assert projected.phase_label.startswith("Still working — ")
    assert "Open WebUI" in projected.phase_label
    assert "ETA" not in projected.phase_label
    assert projected.problem is None


@pytest.mark.parametrize(
    ("phase", "deadline"),
    [
        ("gateway", GATEWAY_READY_DEADLINE_SECONDS),
        ("openwebui", OPENWEBUI_READY_DEADLINE_SECONDS),
        ("model", MODEL_READY_DEADLINE_SECONDS),
        ("tailnet", TAILNET_READY_DEADLINE_SECONDS),
    ],
)
def test_each_startup_phase_has_a_typed_finite_deadline(phase, deadline):
    projected = project_operation_progress(
        _summary(
            phase=phase,
            created_at="2026-09-01T12:04:00+00:00",
            updated_at=datetime.fromtimestamp(
                NOW.timestamp() - deadline, tz=timezone.utc
            ).isoformat(),
        ),
        now=NOW,
    )

    assert deadline > 0
    assert projected.problem is not None
    assert projected.problem.code == "OPERATION_DEADLINE_EXCEEDED"
    assert projected.problem.safe_action_id == "view-activity"


def test_complete_integration_deadline_is_finite_and_aggregate():
    assert INTEGRATION_READY_DEADLINE_SECONDS == 300
    projected = project_operation_progress(
        _summary(
            phase="verify",
            created_at="2026-09-01T12:00:00+00:00",
            updated_at="2026-09-01T12:04:59+00:00",
        ),
        now=NOW,
    )
    assert projected.problem is not None
    assert projected.problem.code == "OPERATION_DEADLINE_EXCEEDED"


def test_terminal_failure_uses_stable_problem_and_success_has_none():
    failed = project_operation_progress(
        _summary(state="FAILED_SAFE", failure_code="OPENWEBUI_START_FAILED"),
        now=NOW,
    )
    succeeded = project_operation_progress(
        _summary(state="SUCCEEDED", cancellable=False), now=NOW,
    )

    assert failed.problem is not None
    assert failed.problem.code == "OPENWEBUI_START_FAILED"
    assert succeeded.problem is None
    assert succeeded.phase_label == "Result verified"


def test_models_page_uses_the_same_phase_and_elapsed_projection():
    summary = _summary(
        kind="MODEL_ACTIVATE", phase="model",
        created_at="2026-09-01T12:04:00+00:00",
        updated_at="2026-09-01T12:04:59+00:00",
    )
    shared = project_operation_progress(
        summary, current_step="verify_candidate_health", now=NOW,
    )
    view = build_install_progress_view(
        summary, model_name="Qwen", current_step="verify_candidate_health",
    )

    assert shared.phase_label in view.message
    assert "elapsed" in view.message
    assert view.mode == "indeterminate"


def test_elapsed_format_is_bounded_and_has_no_eta():
    assert format_elapsed(5) == "5s elapsed"
    assert format_elapsed(65) == "1m 05s elapsed"
    assert format_elapsed(3665) == "1h 01m elapsed"


def test_progress_surfaces_add_no_independent_timer_or_blocking_callback():
    package = Path(__file__).parent.parent / "bc250_llm_mode" / "gui"
    for name in ("activity.py", "models_page.py", "connections_page.py"):
        source = (package / name).read_text(encoding="utf-8")
        assert ".after(" not in source
        assert "time.sleep(" not in source
