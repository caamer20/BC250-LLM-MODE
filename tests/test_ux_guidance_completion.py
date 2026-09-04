"""Focused contracts for the post-review usability implementation."""

from __future__ import annotations

import json

import pytest

from bc250_llm_mode.conversation_service import ConversationService
from bc250_llm_mode.ux_guidance import (
    SETUP_CHAPTER_GUIDANCE,
    WORKLOAD_PRESETS,
    connection_doctor,
    friendly_state,
    http_status_guidance,
    model_install_time_guidance,
    operation_reason_guidance,
    quantization_guidance,
    repair_duration_label,
    repair_reversibility_label,
    setup_chapter_guidance,
    workload_preset,
)


def _connection_snapshot(*, failed: str | None = None):
    ids = (
        "model", "gateway", "credential", "local-authorized",
        "local-unauthorized", "tailscale-dns", "serve-mappings",
        "funnel-disabled", "tailnet-unauthorized", "tailnet-models",
        "tailnet-stream",
    )
    return {
        "ready": failed is None,
        "checks": [
            {
                "id": check_id,
                "passed": check_id != failed,
                "next_action": (
                    "Repair this exact step." if check_id == failed else None
                ),
            }
            for check_id in ids
        ],
    }


def test_setup_and_workload_guidance_are_closed_and_plain_language():
    assert len(SETUP_CHAPTER_GUIDANCE) == 5
    assert setup_chapter_guidance(0).chapter == 1
    assert "Progress" not in setup_chapter_guidance(4).change_summary
    with pytest.raises(ValueError):
        setup_chapter_guidance(5)

    assert {item.preset_id for item in WORKLOAD_PRESETS} == {
        "interactive", "long-context", "shared", "cool",
    }
    assert workload_preset("Shared").slots == 4
    assert workload_preset("Cool / conservative").context == 4096
    with pytest.raises(ValueError):
        workload_preset("unsafe-max")
    assert "Balanced" in quantization_guidance("Q5_K_M")
    assert "10–45 minutes" in model_install_time_guidance(6.2, installed=False)
    assert "Already installed" in model_install_time_guidance(6.2, installed=True)
    assert friendly_state("RECOVERY_REQUIRED") == "Recovery needs attention"


def test_connection_doctor_returns_one_safe_next_action_and_bounded_steps():
    ready = connection_doctor(_connection_snapshot())
    assert ready.ready and ready.passed_count == ready.total_count == 11
    assert ready.next_action_label is None

    blocked = connection_doctor(_connection_snapshot(failed="tailnet-models"))
    assert blocked.ready is False
    assert blocked.headline == "One connection step needs attention"
    assert blocked.next_action_route == "connections"
    assert "model" in blocked.explanation.lower()
    assert len(blocked.steps) <= 12
    assert "secret" not in json.dumps(blocked.to_dict()).lower()


def test_connection_doctor_prefers_stable_problem_copy_without_raw_paths():
    snapshot = _connection_snapshot(failed="credential")
    snapshot["readiness"] = {
        "remote_client_ready": False,
        "primary_problem_code": "SCOPE_NOT_GRANTED",
    }
    view = connection_doctor(snapshot)
    assert view.headline == "Client permission is missing"
    assert view.technical_code == "SCOPE_NOT_GRANTED"
    assert "/root/" not in json.dumps(view.to_dict())


@pytest.mark.parametrize(
    ("status", "needle"),
    ((401, "key"), (403, "permission"), (404, "address"), (502, "model")),
)
def test_http_failures_have_plain_diagnosis_and_fix(status, needle):
    guidance = http_status_guidance(status)
    text = f"{guidance.explanation} {guidance.action}".lower()
    assert needle in text
    assert guidance.technical_code


def test_operation_and_repair_guidance_hides_codes_from_primary_copy():
    unavailable = operation_reason_guidance("SIGNED_UPDATE_CHANNEL_UNAVAILABLE")
    assert "not configured" in unavailable.title.lower()
    assert "SIGNED_" not in unavailable.explanation
    assert "few minutes" in repair_duration_label("SHORT")
    assert "Undo" in repair_reversibility_label("EXACT_UNTIL")


def test_conversation_draft_and_explicit_redacted_export_are_atomic(tmp_path):
    service = ConversationService(
        tmp_path / "conversations", ids=lambda: "conversation-draft"
    )
    record = service.create("private prompt title")
    service.save_draft(record.conversation_id, "unsent private draft")
    assert service.load(record.conversation_id).draft == "unsent private draft"
    service.save(
        record.conversation_id,
        title=record.title,
        messages=(
            {"role": "user", "content": "private prompt"},
            {"role": "assistant", "content": "private answer"},
        ),
        draft="",
    )
    target = service.export_markdown(
        record.conversation_id, tmp_path / "redacted.md", redact=True
    )
    text = target.read_text(encoding="utf-8")
    assert "[REDACTED]" in text
    assert "private prompt" not in text
    assert "private answer" not in text
    assert "private prompt title" not in text
    assert not list(tmp_path.glob("*.tmp"))
