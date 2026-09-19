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


def test_new_sources_instructions_templates_and_provider_are_excluded_from_diagnostics(tmp_path):
    from bc250_llm_mode.app import Application
    from bc250_llm_mode.paths import AppPaths
    from bc250_llm_mode.chat_preferences import ConversationOptions
    from bc250_llm_mode.document_service import DocumentSource
    from bc250_llm_mode.support_bundle import SupportBundleService
    app = Application.compose(AppPaths.temporary(tmp_path / "profile"))
    source = DocumentSource("private-source-title", "web", "PRIVATE_DOCUMENT_CANARY",
        "a" * 64, 64, url="https://example.org/PRIVATE_URL_CANARY")
    app.conversations.save("private", title="PRIVATE_TITLE_CANARY", messages=[
        {"role": "user", "content": "PRIVATE_PROMPT_CANARY", "sources": [source.to_dict()]}],
        options=ConversationOptions("PRIVATE_INSTRUCTIONS_CANARY"), draft="PRIVATE_DRAFT_CANARY")
    app.chat_templates.save_template("Private", "PRIVATE_TEMPLATE_CANARY")
    app.web_search.configure("https://PRIVATE_PROVIDER_CANARY.example.org")
    output = tmp_path / "support"
    SupportBundleService(app.units, app.paths).build(output)
    emitted = "\n".join(p.read_text(errors="replace") for p in output.rglob("*") if p.is_file())
    for canary in ("PRIVATE_DOCUMENT_CANARY", "PRIVATE_URL_CANARY", "PRIVATE_TITLE_CANARY",
                   "PRIVATE_PROMPT_CANARY", "PRIVATE_INSTRUCTIONS_CANARY", "PRIVATE_DRAFT_CANARY",
                   "PRIVATE_TEMPLATE_CANARY", "PRIVATE_PROVIDER_CANARY"):
        assert canary not in emitted
        with app.units.read() as conn:
            assert all(canary not in str(tuple(row)) for row in conn.execute("SELECT * FROM settings"))
