"""P7 §13.1/§13.2: chat request lifecycle contract (pure).

Pins the shared request/result/error semantics for both chat clients: bounded
deadlines (never None), cancellation, the closed terminal classification
vocabulary + precedence, duck-typed exception classification, the retry policy
(never after tokens emitted; transient pre-response only; at most once), the
redacted event record (no prompt/completion content), and recoverable messages
that carry the request ID instead of a traceback.
"""

from __future__ import annotations

import json

import pytest

from bc250_llm_mode.chat_lifecycle import (
    CHAT_REQUEST_SCHEMA_VERSION,
    CLASSIFICATIONS,
    ChatCancellation,
    ChatCancelled,
    ChatDeadline,
    ChatEventRecord,
    ChatRequest,
    ChatResultClassification,
    classify_exception,
    classify_outcome,
    recoverable_message,
    should_retry,
)


def test_classification_vocabulary_is_closed():
    assert set(CLASSIFICATIONS) == {
        "COMPLETED", "CANCELLED", "TIMEOUT", "SERVER_UNAVAILABLE",
        "MODEL_MISMATCH", "THERMAL_STOP", "MALFORMED_RESPONSE", "UNKNOWN",
    }


def test_deadline_bounds_reject_non_positive():
    d = ChatDeadline()
    assert d.connect_s > 0 and d.read_s > 0 and d.write_s > 0 and d.total_s > 0
    assert d.to_dict()["read_s"] == d.read_s
    with pytest.raises(ValueError):
        ChatDeadline(connect_s=0)
    with pytest.raises(ValueError):
        ChatDeadline(read_s=-1)


def test_chat_request_requires_ids_and_positive_caps():
    req = ChatRequest(request_id="r1", conversation_id="c1")
    doc = req.to_dict()
    assert doc["schema_version"] == CHAT_REQUEST_SCHEMA_VERSION
    assert doc["request_id"] == "r1"
    assert doc["conversation_id"] == "c1"
    assert doc["max_prompt_tokens"] > 0
    with pytest.raises(ValueError):
        ChatRequest(request_id="", conversation_id="c1")
    with pytest.raises(ValueError):
        ChatRequest(request_id="r1", conversation_id="")
    with pytest.raises(ValueError):
        ChatRequest(request_id="r1", conversation_id="c1",
                    max_generated_tokens=0)


def test_cancellation_token():
    token = ChatCancellation()
    assert token.is_cancelled is False
    token.raise_if_cancelled()  # does not raise
    token.cancel()
    assert token.is_cancelled is True
    with pytest.raises(ChatCancelled):
        token.raise_if_cancelled()


def test_classify_outcome_precedence():
    C = ChatResultClassification
    # cancelled wins over everything
    assert classify_outcome(cancelled=True, timed_out=True) is C.CANCELLED
    # thermal before model mismatch / timeout
    assert classify_outcome(thermal_stop=True, model_mismatch=True) is C.THERMAL_STOP
    assert classify_outcome(model_mismatch=True, timed_out=True) is C.MODEL_MISMATCH
    assert classify_outcome(timed_out=True, server_unavailable=True) is C.TIMEOUT
    assert classify_outcome(server_unavailable=True, malformed=True) is C.SERVER_UNAVAILABLE
    assert classify_outcome(malformed=True) is C.MALFORMED_RESPONSE
    assert classify_outcome(completed=True) is C.COMPLETED
    assert classify_outcome() is C.UNKNOWN


def test_classify_exception_duck_types():
    C = ChatResultClassification

    class FakeReadTimeout(Exception):
        pass

    class FakeConnectError(Exception):
        pass

    class FakeDecodeError(Exception):
        pass

    assert classify_exception(FakeReadTimeout()) is C.TIMEOUT
    assert classify_exception(TimeoutError()) is C.TIMEOUT
    assert classify_exception(FakeConnectError()) is C.SERVER_UNAVAILABLE
    assert classify_exception(ConnectionRefusedError()) is C.SERVER_UNAVAILABLE
    assert classify_exception(FakeDecodeError()) is C.MALFORMED_RESPONSE
    assert classify_exception(ChatCancelled()) is C.CANCELLED
    assert classify_exception(RuntimeError("boom")) is C.UNKNOWN


def test_retry_policy_never_after_tokens_emitted():
    C = ChatResultClassification
    # Pre-response transient failure -> one retry allowed.
    assert should_retry(C.SERVER_UNAVAILABLE, tokens_emitted=0, attempts=1) is True
    assert should_retry(C.TIMEOUT, tokens_emitted=0, attempts=1) is True
    # Second attempt exhausted.
    assert should_retry(C.TIMEOUT, tokens_emitted=0, attempts=2) is False
    # Never retry after ANY token was emitted.
    assert should_retry(C.SERVER_UNAVAILABLE, tokens_emitted=5, attempts=1) is False
    # Non-transient classifications never retry.
    assert should_retry(C.THERMAL_STOP, tokens_emitted=0, attempts=1) is False
    assert should_retry(C.MODEL_MISMATCH, tokens_emitted=0, attempts=1) is False
    assert should_retry(C.MALFORMED_RESPONSE, tokens_emitted=0, attempts=1) is False
    assert should_retry(C.COMPLETED, tokens_emitted=0, attempts=1) is False


def test_event_record_is_redacted_and_closed():
    rec = ChatEventRecord(
        request_id="r1", conversation_id="c1",
        classification="COMPLETED", duration_ms=123,
        time_to_first_token_ms=45, tokens_generated=10, prompt_tokens=8)
    doc = rec.to_dict()
    assert doc["schema_version"] == CHAT_REQUEST_SCHEMA_VERSION
    # No prompt/completion content fields exist.
    assert "prompt" not in doc and "completion" not in doc
    assert "content" not in doc
    json.dumps(doc)
    with pytest.raises(ValueError):
        ChatEventRecord(request_id="r", conversation_id="c",
                        classification="NOT_A_STATE")


def test_recoverable_message_carries_request_id_not_traceback():
    msg = recoverable_message(ChatResultClassification.TIMEOUT, "req-42")
    assert "req-42" in msg
    assert "Traceback" not in msg
    assert "timed out" in msg.lower()
    thermal = recoverable_message(ChatResultClassification.THERMAL_STOP, "req-7")
    assert "req-7" in thermal and "thermal" in thermal.lower()
    mismatch = recoverable_message("MODEL_MISMATCH", "req-9")
    assert "req-9" in mismatch
    completed = recoverable_message(ChatResultClassification.COMPLETED, "req-1")
    assert "completed" in completed.lower()


def test_chat_module_never_uses_timeout_none():
    """P7 §13.1/exit gate: no unbounded HTTP call in the chat client."""
    from pathlib import Path

    from bc250_llm_mode import chat as chat_mod

    source = Path(chat_mod.__file__).read_text(encoding="utf-8")
    assert "timeout=None" not in source
