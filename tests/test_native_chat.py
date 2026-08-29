"""GUI-6 shared transport, conversation, native-page, and privacy gates."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _gui_stubs import install  # noqa: E402

install()

from bc250_llm_mode.chat_lifecycle import (  # noqa: E402
    ChatCancellation,
    ChatResultClassification,
)
from bc250_llm_mode.chat_service import ChatSessionService  # noqa: E402
from bc250_llm_mode.conversation_service import (  # noqa: E402
    ConversationService,
    MAX_LIVE_BYTES,
    MAX_LIVE_MESSAGES,
    bounded_live_messages,
)


class StreamResponse:
    def __init__(self, lines) -> None:
        self._lines = tuple(lines)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        return iter(self._lines)


class SequenceHttp:
    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = []

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return StreamResponse(outcome)


def _state():
    return {"server_port": 8080, "current_model": "tiny", "current_ctx": 8192}


def _messages(canary="private prompt canary"):
    return [{"role": "user", "content": canary}]


def test_shared_transport_streams_and_emits_only_redacted_metrics():
    http = SequenceHttp([(
        'data: {"model":"tiny","choices":[{"delta":{"content":"Hel"}}]}',
        'data: {"choices":[{"delta":{"content":"lo"}}],"timings":{"predicted_n":2}}',
        "data: [DONE]",
    )])
    chunks = []
    result = ChatSessionService(
        http_client=http, request_ids=lambda: "request-1"
    ).stream(_state(), _messages(), chunks.append, conversation_id="conversation-1")
    assert result.ok and result.text == "Hello" and chunks == ["Hel", "lo"]
    assert result.classification is ChatResultClassification.COMPLETED
    event = result.event_record().to_dict()
    assert event["request_id"] == "request-1"
    assert "private prompt canary" not in json.dumps(event)
    assert all(key not in event for key in ("prompt", "completion", "content", "messages"))
    method, url, request = http.calls[0]
    assert method == "POST" and url == "http://127.0.0.1:8080/v1/chat/completions"
    assert request["json"]["cache_prompt"] is True
    assert request["timeout"].connect > 0 and request["timeout"].read > 0


def test_pre_token_connection_failure_retries_once():
    http = SequenceHttp([
        ConnectionRefusedError("offline"),
        ('data: {"choices":[{"delta":{"content":"ready"}}]}', "data: [DONE]"),
    ])
    result = ChatSessionService(http_client=http).stream(
        _state(), _messages(), lambda _text: None
    )
    assert result.ok and result.attempts == 2 and len(http.calls) == 2


def test_cancellation_preserves_partial_response_without_retry():
    token = ChatCancellation()
    http = SequenceHttp([(
        'data: {"choices":[{"delta":{"content":"partial"}}]}',
        'data: {"choices":[{"delta":{"content":" ignored"}}]}',
        "data: [DONE]",
    )])

    def receive(_text):
        token.cancel()

    result = ChatSessionService(http_client=http).stream(
        _state(), _messages(), receive, cancellation=token
    )
    assert result.classification is ChatResultClassification.CANCELLED
    assert result.text == "partial" and result.attempts == 1


def test_malformed_and_model_changed_are_closed_results():
    malformed = ChatSessionService(http_client=SequenceHttp([(
        "data: not-json", "data: [DONE]",
    )])).stream(_state(), _messages(), lambda _text: None)
    assert malformed.classification is ChatResultClassification.MALFORMED_RESPONSE

    mismatch = ChatSessionService(
        http_client=SequenceHttp([(
            'data: {"choices":[{"delta":{"content":"x"}}]}',
            "data: [DONE]",
        )]),
        active_model=lambda: "different-model",
    ).stream(_state(), _messages(), lambda _text: None)
    assert mismatch.classification is ChatResultClassification.MODEL_MISMATCH
    assert mismatch.text == ""


def test_conversation_crud_is_atomic_local_and_archive_aware(tmp_path):
    directory = tmp_path / "conversations"
    ids = iter(("conversation-a", "conversation-b"))
    service = ConversationService(
        directory, clock=lambda: "2026-08-29T12:00:00Z",
        ids=lambda: next(ids),
    )
    record = service.create("First chat")
    record = service.save(
        record.conversation_id,
        title=record.title,
        messages=(
            {"role": "user", "content": "secret local prompt"},
            {"role": "assistant", "content": "secret local answer"},
        ),
        last_model="tiny",
    )
    assert service.load(record.conversation_id).messages == record.messages
    assert directory.stat().st_mode & 0o777 == 0o700
    assert (directory / "conversation-a.json").stat().st_mode & 0o777 == 0o600
    assert list(directory.glob("*.tmp")) == []
    assert service.list()[0]["title"] == "First chat"

    renamed = service.rename(record.conversation_id, "Renamed chat")
    assert renamed.title == "Renamed chat"
    service.archive(record.conversation_id)
    assert service.list() == ()
    assert service.list(archived=True)[0]["conversation_id"] == record.conversation_id
    service.delete(record.conversation_id)
    with pytest.raises(ValueError, match="No saved conversation"):
        service.load(record.conversation_id)


def test_conversation_reader_accepts_legacy_list_and_rejects_malformed(tmp_path):
    directory = tmp_path / "conversations"
    directory.mkdir()
    (directory / "legacy.json").write_text(
        '[{"role":"user","content":"hello"}]', encoding="utf-8"
    )
    (directory / "bad.json").write_text('{"messages":[]}', encoding="utf-8")
    service = ConversationService(directory)
    assert service.load("legacy").messages[0]["content"] == "hello"
    with pytest.raises(ValueError, match="malformed"):
        service.load("bad")


def test_live_transcript_bounds_messages_and_utf8_bytes():
    messages = [
        {"role": "user", "content": "x" * 10_000}
        for _ in range(MAX_LIVE_MESSAGES + 50)
    ]
    bounded = bounded_live_messages(messages)
    assert len(bounded) <= MAX_LIVE_MESSAGES
    assert sum(len(item["content"].encode("utf-8")) for item in bounded) <= MAX_LIVE_BYTES


def test_native_chat_route_constructs_without_network(tmp_path):
    from bc250_llm_mode.app import Application
    from bc250_llm_mode.gui import Wizard
    from bc250_llm_mode.gui.chat_page import ChatPage
    from bc250_llm_mode.gui.routes import Route
    from bc250_llm_mode.paths import AppPaths

    application = Application.compose(AppPaths.temporary(tmp_path))
    state = application.read_model()
    application.commit_settings_changes(
        state, {**state, "setup_complete": True, "disclaimer_ack": True}
    )
    window = Wizard(application, management=True)
    window.navigate(Route.CHAT)
    assert isinstance(window._page, ChatPage)
    assert window._page._streaming is False
    assert window._page._observation.ready is False


def test_terminal_and_native_clients_share_transport_and_no_http_lives_in_gui():
    root = Path(__file__).parent.parent / "bc250_llm_mode"
    terminal = (root / "chat.py").read_text(encoding="utf-8")
    native = (root / "gui" / "chat_page.py").read_text(encoding="utf-8")
    assert "application.chat_sessions.stream(" in terminal
    assert "application.chat_sessions.stream(" in native
    for token in ("httpx", "127.0.0.1", "/v1/chat/completions"):
        assert token not in native
    assert "application.conversations" in terminal
    assert "application.conversations" in native
