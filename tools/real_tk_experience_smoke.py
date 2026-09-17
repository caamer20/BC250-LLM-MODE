"""Real Tk journeys for conversation controls, layout, and private storage.

Uses a temporary profile and an in-memory SSE endpoint. It never invokes host
services or a real model. Run separately from pytest's widget stubs.
"""
from __future__ import annotations

import json
import tempfile
import time
import sys
import gc
from pathlib import Path
from types import SimpleNamespace


def main():
    import tkinter as tk
    from tkinter import ttk
    from bc250_llm_mode.app import Application
    from bc250_llm_mode.paths import AppPaths
    from bc250_llm_mode.gui.shell import ApplicationWindow
    from bc250_llm_mode.gui.routes import Route
    from bc250_llm_mode.gui.theme import apply_theme
    from bc250_llm_mode.chat_context import select_context
    from bc250_llm_mode.chat_service import ChatObservation, ChatSessionService
    from bc250_llm_mode.web_search import WebSearchService

    class Stream:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def raise_for_status(self):
            pass
        def iter_lines(self):
            yield "data: " + json.dumps({"choices": [{"delta": {"content": "# Answer\nA **clear** response.\n```python\nprint('ok')\n```\n"}}]})
            yield "data: [DONE]"
    class Http:
        def stream(self, *_args, **_kwargs):
            return Stream()
    class WebResponse(Stream):
        status_code = 200
        def iter_bytes(self):
            yield json.dumps({"results": [{"title": "Web fixture", "url": "https://example.org/fixture",
                "content": "Public excerpt for an offline UI test."}]}).encode()
    class WebHttp:
        def stream(self, method, url, **kwargs):
            assert method == "POST" and kwargs["data"]["q"] == "public fixture query"
            assert "messages" not in kwargs["data"]
            return WebResponse()
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)
    def button(parent, text):
        return next(w for w in descendants(parent) if isinstance(w, ttk.Button) and w.cget("text") == text)
    def wait(window, predicate):
        until = time.monotonic() + 10
        while not predicate():
            window.update()
            if time.monotonic() >= until:
                raise AssertionError("UI action did not settle within 10 seconds")
            time.sleep(0.01)
        window.update()

    # Queries in unrelated appliance pages must not reach the host.
    ApplicationWindow.request_observation = lambda *_args, **_kwargs: False
    results = []
    with tempfile.TemporaryDirectory(prefix="bc250-experience-tk-") as temporary:
        app = Application.compose(AppPaths.temporary(Path(temporary)))
        initial = app.read_model()
        app.commit_settings_changes(initial, {**initial, "setup_complete": True, "disclaimer_ack": True})
        state = app.read_model()
        context = int(state.get("current_ctx") or 8192)
        observation = ChatObservation(model=state.get("current_model"), context=context, slots=1,
            ready=True, thermal_blocked=False, stale=False, guidance="Fixture ready")
        app.chat_observation = SimpleNamespace(current=lambda: observation)
        app.chat_context = SimpleNamespace(prepare=lambda state, messages, response_tokens=2048, **kw:
            select_context(messages, int(state.get("current_ctx") or 8192), response_tokens))
        app.chat_sessions = ChatSessionService(http_client=Http())
        app.web_search = WebSearchService(app.paths.app_dir, http_client=WebHttp())
        window = ApplicationWindow(app, management=True)
        errors = []
        unraisable = []
        sys.unraisablehook = lambda item: unraisable.append(type(item.exc_value).__name__)
        window.report_callback_exception = lambda *error: errors.append(str(error[1]))
        try:
            for scale in (100, 200):
                window.geometry("1100x800")
                apply_theme(window, "light", scale_percent=scale)
                window.navigate(Route.CHAT)
                page = window._page
                page._apply_observation(observation)
                window.update()
                page.composer.insert("1.0", "Fixture question")
                page.send()
                wait(window, lambda: not page._streaming)
                assert page._messages[-1]["role"] == "assistant"
                assert page._code_blocks == ["print('ok')\n"]
                assert page.transcript.tag_ranges("heading")
                assert page.transcript.tag_ranges("code")
                prior_button = button(page.transcript, "Copy code 1")
                prior_button.invoke()
                assert page.clipboard_get() == "print('ok')\n"
                page._render_transcript()
                assert not prior_button.winfo_exists()
                assert len(page._code_copy_buttons) == 1
                assert len(page.transcript.winfo_children()) == 1
                source_id = page._conversation_id
                source = app.conversations.load(source_id)
                source_messages = source.messages
                page.chat_settings()
                wait(window, lambda: not page._draft_saving)
                editor = next(w for w in descendants(window.drawer) if isinstance(w, tk.Text))
                editor.delete("1.0", "end")
                editor.insert("1.0", "Use concise explanations.")
                button(window.drawer, "Save settings").invoke()
                wait(window, lambda: not page._draft_saving)
                assert app.conversations.load(source_id).options.instructions == "Use concise explanations."
                page.edit_prompt()
                editor = next(w for w in descendants(window.drawer) if isinstance(w, tk.Text))
                editor.delete("1.0", "end")
                editor.insert("1.0", "Edited fixture question")
                button(window.drawer, "Create branch").invoke()
                wait(window, lambda: not page._draft_saving)
                assert page._conversation_id != source_id
                assert app.conversations.load(source_id).messages == source_messages
                assert page.composer.get("1.0", "end-1c") == "Edited fixture question"
                note = Path(temporary) / "fixture.txt"
                note.write_text("Local document contents for the UI fixture.")
                page._preview_document(app.documents.extract(note))
                button(window.drawer, "Add to message").invoke()
                assert len(page._draft_sources) == 1
                page._save_draft()
                assert app.conversations.load(page._conversation_id).draft_sources
                page.review_sources()
                button(window.drawer, "Remove").invoke()
                assert not page._draft_sources
                page._load(source_id)
                page.summarize()
                wait(window, lambda: not page._streaming)
                button(window.drawer, "Create conversation with summary").invoke()
                wait(window, lambda: not page._draft_saving)
                assert app.conversations.load(page._conversation_id).parent_conversation_id == source_id
                page._show_web_search("http://localhost:8888")
                query_entry = [w for w in descendants(window.drawer) if isinstance(w, ttk.Entry)][-1]
                query_entry.insert(0, "public fixture query")
                button(window.drawer, "Search").invoke()
                wait(window, lambda: not page._draft_saving)
                button(window.drawer, "Attach selected excerpts").invoke()
                assert page._draft_sources[0].url == "https://example.org/fixture"
                page.review_sources()
                button(window.drawer, "Read text").invoke()
                button(window.drawer, "Back to sources").invoke()
                window.drawer.clear()
                page._save_draft()
                assert app.conversations.load(page._conversation_id).draft_sources[0].kind == "web"
                page.composer.insert("1.0", "x" * 32769)
                assert page.request_leave(Route.HOME) is False
                assert len(page.composer.get("1.0", "end-1c")) >= 32769
                page.composer.delete("1.0", "end")
                page.context_details()
                window.update()
                window.drawer.clear()
                window.update()
                assert not errors, errors
                gc.collect()
                assert not unraisable, unraisable
                for control in (page.composer, page.send_button, page.transcript):
                    assert control.winfo_ismapped(), str(control)
                    assert control.winfo_rooty() + control.winfo_height() <= window.winfo_rooty() + window.winfo_height(), str(control)
                results.append({"scale": scale, "transcript_height": page.transcript.winfo_height(),
                    "composer_height": page.composer.winfo_height(), "widgets": len(list(descendants(page))),
                    "send_branch_settings_summary_sources_web": "passed", "inline_code_copy_and_cleanup": "passed"})
                from tkinter import filedialog
                archive = Path(temporary) / f"portable-{scale}.tar"
                filedialog.asksaveasfilename = lambda **_: str(archive)
                filedialog.askopenfilename = lambda **_: str(archive)
                window.navigate(Route.BACKUPS)
                backups = window._page
                backups._portable_export()
                button(window.drawer, "Choose destination…").invoke()
                wait(window, lambda: not window.busy)
                assert archive.is_file()
                before = len(app.conversations.list())
                backups._portable_import()
                wait(window, lambda: not window.busy)
                button(window.drawer, "Import as new conversations").invoke()
                wait(window, lambda: not window.busy)
                assert len(app.conversations.list()) > before
                assert not errors, errors
                gc.collect()
                assert not unraisable, unraisable
                results[-1]["portable_export_import"] = "passed"
                window.navigate(Route.HOME)
        finally:
            window._refresh_coordinator.close()
            if window._task_lanes:
                window._task_lanes.close()
            window.destroy()
            gc.collect()
            assert not unraisable, unraisable
    print(json.dumps({"tk": tk.TkVersion, "journeys": results, "physical_qualification": "pending"}, indent=2))


if __name__ == "__main__":
    main()
