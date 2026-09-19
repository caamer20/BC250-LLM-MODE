"""Context, branching and settings behavior over real private files."""
import json
import math

import pytest

from bc250_llm_mode.chat_context import ChatContextService, select_context
from bc250_llm_mode.chat_preferences import ConversationOptions, PromptTemplateService
from bc250_llm_mode.conversation_service import ConversationConflict, ConversationService


def test_context_preserves_instructions_and_discloses_omitted_complete_turns():
    messages = [{"role": "system", "content": "Always retain these instructions"}]
    for i in range(8):
        messages.extend(({"role": "user", "content": f"Question {i}: " + "x" * 160},
                         {"role": "assistant", "content": "y" * 160}))
    messages.append({"role": "user", "content": "Latest question"})
    original = json.dumps(messages)
    plan = select_context(messages, 512, 128)
    assert plan.fits and plan.excluded and 0 in plan.included
    assert plan.messages[0] == messages[0]
    assert plan.messages[1]["role"] == "user"
    assert plan.messages[-1] == messages[-1]
    assert sorted(plan.included + plan.excluded) == list(range(len(messages)))
    assert "saved history is unchanged" in plan.description
    assert json.dumps(messages) == original


def test_oversized_latest_turn_is_refused_not_truncated():
    messages = [{"role": "user", "content": "漢字" * 1000}]
    plan = select_context(messages, 512, 128)
    assert not plan.fits and plan.messages[0] == messages[0]


class TokenResponse:
    def __init__(self, value, status=200):
        self.value = value
        self.status = status
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def raise_for_status(self):
        if self.status != 200:
            raise ValueError("endpoint unavailable")
    def iter_bytes(self):
        yield json.dumps(self.value).encode()


class TokenHttp:
    def __init__(self, unavailable=False):
        self.calls = []
        self.unavailable = unavailable
    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.unavailable:
            return TokenResponse({}, 404)
        if url.endswith("apply-template"):
            return TokenResponse({"prompt": "chat template with special tokens"})
        return TokenResponse({"tokens": [1, 2, 3, 4, 5]})


def test_runtime_count_uses_template_then_tokenizer_and_marks_fallback():
    messages = [{"role": "user", "content": "私の文章"}]
    http = TokenHttp()
    plan = ChatContextService(http_client=http).prepare({"current_ctx": 8192}, messages)
    assert plan.prompt_tokens == 5 and not plan.estimated
    assert http.calls[0][1].endswith("/apply-template")
    assert http.calls[1][1].endswith("/tokenize")
    assert http.calls[1][2]["json"]["parse_special"] is True
    assert http.calls[0][2]["follow_redirects"] is False
    fallback = ChatContextService(http_client=TokenHttp(True)).prepare({}, messages)
    assert fallback.estimated and "Estimated" in fallback.description


def test_branches_and_summaries_leave_original_bytes_unchanged(tmp_path):
    service = ConversationService(tmp_path / "conversations")
    options = ConversationOptions("Answer concisely", 0.3, 4096)
    source = service.save("source", title="Original", options=options, draft="keep draft", messages=[
        {"role": "user", "content": "First"}, {"role": "assistant", "content": "First answer"},
        {"role": "user", "content": "Second"}, {"role": "assistant", "content": "Second answer"},
    ])
    before = (tmp_path / "conversations/source.json").read_bytes()
    branch = service.branch("source", 2, "Revised second question", expected_revision=source.revision)
    assert branch.messages == source.messages[:2]
    assert branch.draft == "Revised second question" and branch.options == options
    assert branch.parent_conversation_id == "source" and branch.parent_message_index == 2
    summary = service.from_summary("source", "Reviewable summary", expected_revision=source.revision)
    assert "Reviewable summary" in summary.messages[0]["content"]
    assert service.load(summary.conversation_id).options == options
    assert (tmp_path / "conversations/source.json").read_bytes() == before
    assert (tmp_path / "conversations" / (branch.conversation_id + ".json")).stat().st_mode & 0o777 == 0o600


def test_stale_branch_and_invalid_edit_do_not_create_files(tmp_path):
    service = ConversationService(tmp_path / "conversations")
    source = service.save("source", title="Original", messages=[{"role": "assistant", "content": "Answer"}])
    with pytest.raises(ConversationConflict):
        service.branch("source", 0, "edit", expected_revision=source.revision + 1)
    with pytest.raises(ValueError):
        service.branch("source", 0, "edit", expected_revision=source.revision)
    assert len(list((tmp_path / "conversations").glob("*.json"))) == 1


def test_v1_conversation_loads_defaults_and_options_survive_other_actions(tmp_path):
    directory = tmp_path / "conversations"
    directory.mkdir()
    (directory / "legacy.json").write_text(json.dumps({
        "schema_version": 1, "conversation_id": "legacy", "title": "Old chat",
        "messages": [],
    }))
    service = ConversationService(directory)
    legacy = service.load("legacy")
    assert legacy.options == ConversationOptions()
    changed = service.save("legacy", title=legacy.title, messages=[],
                           options=ConversationOptions("private instruction", 0.0, 512))
    service.rename("legacy", "Renamed")
    service.archive("legacy")
    service.save_draft("legacy", "draft")
    assert service.load("legacy").options == changed.options


@pytest.mark.parametrize("changes", [{"temperature": math.nan}, {"temperature": True},
                                      {"response_tokens": 999999}, {"instructions": "x" * 16385}])
def test_invalid_options_fail_closed(changes):
    with pytest.raises(ValueError):
        ConversationOptions(**changes)


def test_editable_templates_are_private_and_reusable(tmp_path):
    templates = PromptTemplateService(tmp_path)
    assert "Coding" in templates.list()
    templates.save_template("Coding", "My coding preferences")
    templates.save_template("Meeting notes", "Find action items")
    loaded = PromptTemplateService(tmp_path).list()
    assert loaded["Coding"] == "My coding preferences"
    assert loaded["Meeting notes"] == "Find action items"
    assert templates.path.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "state.db").exists()
