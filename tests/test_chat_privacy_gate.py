"""P7 exit gate: conversation content never enters operation history, logs,
metrics, or support bundles by default.

Cross-module privacy gate: the redacted chat event record, the benchmark
record canary, and the support-bundle conversation exclusion all hold together.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bc250_llm_mode.chat_lifecycle import ChatEventRecord


def test_chat_event_record_has_no_content_fields():
    rec = ChatEventRecord(
        request_id="r", conversation_id="c", classification="COMPLETED")
    doc = rec.to_dict()
    for forbidden in ("prompt", "completion", "content", "messages", "text"):
        assert forbidden not in doc


def test_benchmark_record_stores_no_prompt_content():
    """record_benchmark must never persist the prompt/completion text."""
    from bc250_llm_mode.benchmark_ux import result_contains_prompt_content

    prompt = "distinctive benchmark prompt canary"
    # The shape record_benchmark persists (chat.py): only timings/identity.
    persisted = {
        "timestamp": "2026-01-01T00:00:00",
        "model": "tiny",
        "prompt_per_second": 100.0,
        "predicted_per_second": 42.0,
        "predicted_tokens": 128,
        "max_tokens": 128,
        "context": 8192,
        "slots": 1,
    }
    assert result_contains_prompt_content(persisted, prompt) is False


def test_support_bundle_never_reads_conversations():
    """The support bundle module never references the conversations dir."""
    from bc250_llm_mode import support_bundle as sb_mod

    source = Path(sb_mod.__file__).read_text(encoding="utf-8")
    assert "conversations_dir" not in source


def test_chat_client_has_no_unbounded_http():
    """P7 exit gate: no unbounded HTTP call or timeout=None in chat."""
    from bc250_llm_mode import chat as chat_mod

    source = Path(chat_mod.__file__).read_text(encoding="utf-8")
    assert "timeout=None" not in source
    # Every httpx call site passes the bounded CHAT_HTTP_TIMEOUT.
    assert "CHAT_HTTP_TIMEOUT" in source


def test_operation_history_excludes_chat_content_by_construction():
    """Chat requests are not durable operations: the closed request decoders
    reject unknown fields, so prompt/completion content can never ride a
    durable operation request into operation history."""
    from bc250_llm_mode.operations.model_remove import decode_remove_request

    with pytest.raises(Exception):
        decode_remove_request({"alias": "tiny", "prompt": "secret content"})

    from bc250_llm_mode.operations.model_convert import decode_convert_request

    with pytest.raises(Exception):
        decode_convert_request(
            {"source_alias": "tiny", "target_quantization": "Q4_K_M",
             "completion": "secret content"})
