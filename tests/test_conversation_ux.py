"""P7 §13.3: conversation UX presentation contract (pure).

Pins the headless conversation-UX semantics: model-change detection + notice,
profile indicator, export privacy warning + redaction (never stores prompt
content when redacted), mutation confirmations with recovery policy, bounded
search, and the streaming status indicator.
"""

from __future__ import annotations

import pytest

from bc250_llm_mode.conversation_ux import (
    bounded_search,
    conversation_action_confirmation,
    export_privacy_warning,
    model_change_notice,
    model_changed_since_last_message,
    profile_indicator,
    redact_conversation_for_export,
    streaming_status_text,
)


def test_model_changed_detection():
    assert model_changed_since_last_message("model-a", "model-b") is True
    assert model_changed_since_last_message("model-a", "model-a") is False
    assert model_changed_since_last_message(None, "model-a") is False
    notice = model_change_notice("model-a", "model-b")
    assert notice and "model-a" in notice and "model-b" in notice
    assert model_change_notice("model-a", "model-a") is None


def test_profile_indicator():
    assert profile_indicator(model="tiny", context=8192, slots=2) == (
        "tiny · ctx 8192 · slots 2")
    assert profile_indicator(model=None, context=None, slots=None) == "no profile"


def test_export_privacy_warning_mentions_local_and_redaction():
    warning = export_privacy_warning()
    assert "locally" in warning
    assert "redact" in warning.lower()


def test_redacted_export_never_stores_content():
    messages = [
        {"role": "user", "content": "secret prompt"},
        {"role": "assistant", "content": "secret completion"},
    ]
    redacted = redact_conversation_for_export(messages, redact=True)
    assert [m["role"] for m in redacted] == ["user", "assistant"]
    assert all(m["content"] == "[REDACTED]" for m in redacted)
    assert "secret prompt" not in str(redacted)
    assert "secret completion" not in str(redacted)
    # Unredacted export preserves content (explicit user choice).
    full = redact_conversation_for_export(messages, redact=False)
    assert full[0]["content"] == "secret prompt"


def test_export_is_bounded():
    messages = [{"role": "user", "content": f"m{i}"} for i in range(600)]
    redacted = redact_conversation_for_export(messages, redact=False)
    assert len(redacted) == 500


def test_action_confirmations_carry_recovery_policy():
    rename = conversation_action_confirmation("rename")
    assert rename["destructive"] is False and rename["recovery"]
    archive = conversation_action_confirmation("archive")
    assert archive["destructive"] is False and "unarchived" in archive["recovery"]
    delete = conversation_action_confirmation("delete")
    assert delete["destructive"] is True
    with pytest.raises(ValueError):
        conversation_action_confirmation("teleport")


def test_bounded_search():
    convos = [
        {"title": "Alpha notes"},
        {"title": "Beta plan"},
        {"title": "alpha redux"},
    ]
    assert [c["title"] for c in bounded_search(convos, "alpha")] == [
        "Alpha notes", "alpha redux"]
    assert bounded_search(convos, "")[:1] == [{"title": "Alpha notes"}]
    assert bounded_search(convos, "alpha", limit=1) == [{"title": "Alpha notes"}]
    assert bounded_search(convos, "alpha", limit=0) == []


def test_streaming_status_indicator():
    assert "waiting" in streaming_status_text(
        tokens_emitted=0, elapsed_s=1.0, first_token_ms=None)
    status = streaming_status_text(
        tokens_emitted=100, elapsed_s=2.0, first_token_ms=250)
    assert "100 tokens" in status and "50.0 tok/s" in status
    assert "250 ms" in status
