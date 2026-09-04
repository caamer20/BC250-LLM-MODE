"""Lightweight native chat over the shared bounded session services."""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from typing import Any, Mapping

import tkinter as tk
from tkinter import filedialog, ttk

from ..message_catalog import safe_exception_message

from ..chat_lifecycle import ChatCancellation, ChatResultClassification
from ..chat_service import ChatObservation, trim_messages, estimate_message_tokens
from ..conversation_service import bounded_live_messages, ConversationConflict
from ..conversation_ux import (
    conversation_action_confirmation,
    export_privacy_warning,
    model_change_notice,
    profile_indicator,
    streaming_status_text,
)
from .routes import Route
from .view_state import Confirmation, Notice

MAX_COMPOSER_BYTES = 32 * 1024


class ChatPage(ttk.Frame):
    def __init__(self, parent, shell, application) -> None:
        super().__init__(parent)
        self.shell = shell
        self.application = application
        self._disposed = False
        self._streaming = False
        self._unsaved_response = False
        self._allow_leave = False
        self._pending_leave: Route | None = None
        self._pending_close = None
        self._cancellation: ChatCancellation | None = None
        self._conversation_id: str | None = None
        self._conversation_revision = None
        self._archived = False
        self._title = "New conversation"
        self._messages: list[dict[str, str]] = []
        self._partial = ""
        self._chunk_buffer: list[str] = []
        self._chunk_lock = threading.Lock()
        self._stream_started = 0.0
        self._tokens_emitted = 0
        self._first_token_ms: int | None = None
        self._last_result_classification: ChatResultClassification | None = None
        # The live endpoint probe belongs on the observation worker, never on
        # Tk's UI thread.  Start conservatively and let refresh apply it.
        self._observation = ChatObservation(
            model=None, context=8192, slots=1, ready=False,
            thermal_blocked=False, stale=True,
            guidance="Checking the local model endpoint…",
        )
        self._list_dirty = True
        self._draft_saving = False
        self._last_draft_save = 0.0
        self._last_draft_text = ""
        self._select_first = True
        self._list_offset = 0
        self._build()
        self._reload_list(select_first=True)
        self._render_transcript()
        self.refresh()

    def _build(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, 6))
        self.profile_var = tk.StringVar(value="Checking model…")
        self.notice_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.profile_var).pack(side="left")
        ttk.Label(top, textvariable=self.notice_var, wraplength=480).pack(side="right")

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True)
        sidebar = ttk.Frame(body, width=220)
        main = ttk.Frame(body, padding=(8, 0, 0, 0))
        body.add(sidebar, weight=1)
        body.add(main, weight=4)

        sidebar_actions = ttk.Frame(sidebar)
        sidebar_actions.pack(fill="x")
        ttk.Button(sidebar_actions, text="New", command=self.new_conversation).pack(side="left")
        self.archived_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            sidebar_actions, text="Archived", variable=self.archived_var,
            command=self._reload_list,
        ).pack(side="right")
        self.search_var = tk.StringVar(value="")
        ttk.Label(sidebar, text="Find conversations").pack(anchor="w", pady=(5, 0))
        search = ttk.Entry(sidebar, textvariable=self.search_var)
        search.pack(fill="x", pady=(2, 2))
        self.conversation_count_var = tk.StringVar(value="")
        ttk.Label(sidebar, textvariable=self.conversation_count_var).pack(anchor="w")
        self.search_var.trace_add("write", lambda *_: self._reload_list())
        self.conversation_tree = ttk.Treeview(sidebar, show="tree", height=18)
        self.conversation_tree.heading("#0", text="Conversations")
        self.conversation_tree.column("#0", width=205)
        self.conversation_tree.pack(fill="both", expand=True)
        self.conversation_tree.bind("<<TreeviewSelect>>", self._select_conversation)
        pages = ttk.Frame(sidebar)
        pages.pack(fill="x")
        self.previous_page = ttk.Button(pages, text="Previous", command=lambda: self._history_page(-1))
        self.previous_page.pack(side="left")
        self.next_page = ttk.Button(pages, text="Next", command=lambda: self._history_page(1))
        self.next_page.pack(side="left")

        title_row = ttk.Frame(main)
        title_row.pack(fill="x")
        self.title_var = tk.StringVar(value=self._title)
        ttk.Entry(title_row, textvariable=self.title_var, width=34).pack(side="left", fill="x", expand=True)
        ttk.Button(title_row, text="Rename", command=self.rename_conversation).pack(side="left", padx=4)
        conversation_actions = ttk.Frame(main)
        conversation_actions.pack(fill="x", pady=(3, 0))
        self.archive_button = ttk.Button(
            conversation_actions, text="Archive", command=self.archive_conversation
        )
        self.archive_button.pack(side="left")
        ttk.Button(
            conversation_actions, text="Delete permanently…",
            command=self.delete_conversation,
        ).pack(side="left", padx=(4, 0))

        ttk.Label(
            main,
            text="Conversation transcript · each message repeats its speaker",
        ).pack(anchor="w", pady=(4, 0))
        transcript_frame = ttk.Frame(main)
        transcript_frame.pack(fill="both", expand=True, pady=6)
        self.transcript = tk.Text(transcript_frame, wrap="word", state="disabled", height=18)
        self.transcript.tag_configure("role", font=("TkDefaultFont", 10, "bold"))
        self.transcript.tag_configure("system", foreground="#555555")
        self.transcript.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(transcript_frame, orient="vertical", command=self.transcript.yview)
        scroll.pack(side="right", fill="y")
        self.transcript.configure(yscrollcommand=scroll.set)

        self.follow_output = tk.BooleanVar(value=True)
        ttk.Checkbutton(main, text="Follow new response text", variable=self.follow_output).pack(anchor="w")
        self.budget_var = tk.StringVar(value="Context budget is estimated locally.")
        ttk.Label(main, textvariable=self.budget_var, wraplength=480).pack(anchor="w")
        self.status_var = tk.StringVar(value="Ready")
        status_row = ttk.Frame(main)
        status_row.pack(fill="x")
        ttk.Label(status_row, text="Generation status:").pack(side="left")
        ttk.Label(status_row, textvariable=self.status_var).pack(side="left", padx=(4, 0))
        self.example_frame = ttk.LabelFrame(main, text="Try a first prompt", padding=5)
        self.example_frame.pack(fill="x", pady=(3, 3))
        for text in (
            "Explain what this BC-250 can do in three bullets.",
            "Help me draft a concise project plan.",
            "Summarize text I paste and list the next actions.",
        ):
            ttk.Button(
                self.example_frame, text=text,
                command=lambda prompt=text: self._use_example(prompt),
            ).pack(fill="x", pady=1)
        ttk.Label(main, text="Message").pack(anchor="w", pady=(3, 0))
        self.composer = tk.Text(main, height=4, wrap="word")
        self.composer.pack(fill="x", pady=(4, 4))
        self.composer.bind("<KeyRelease>", lambda _event: self._update_budget())
        self.composer.bind("<Control-Return>", self._send_event)
        self.composer.bind("<Command-Return>", self._send_event)
        self.autosave_drafts = tk.BooleanVar(value=False)
        ttk.Checkbutton(main, text="Save local drafts every 10 seconds (on this device)",
                        variable=self.autosave_drafts).pack(anchor="w")
        actions = ttk.Frame(main)
        actions.pack(fill="x")
        self.send_button = ttk.Button(actions, text="Send", command=self.send)
        self.send_button.pack(side="left")
        ttk.Button(actions, text="Save response", command=self._recover_save).pack(side="right")
        self.stop_button = ttk.Button(actions, text="Stop generating", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=5)
        response_actions = ttk.Frame(main)
        response_actions.pack(fill="x", pady=(3, 0))
        self.retry_button = ttk.Button(
            response_actions, text="Retry last", command=self.retry_last, state="disabled"
        )
        self.retry_button.pack(side="left")
        self.copy_button = ttk.Button(
            response_actions, text="Copy last response", command=self.copy_last_response,
            state="disabled",
        )
        self.copy_button.pack(side="left", padx=5)
        ttk.Button(
            response_actions, text="Models",
            command=lambda: self.shell.navigate(Route.MODELS),
        ).pack(side="right")
        ttk.Button(
            response_actions, text="System",
            command=lambda: self.shell.navigate(Route.SYSTEM),
        ).pack(side="right", padx=5)
        export_actions = ttk.Frame(main)
        export_actions.pack(fill="x", pady=(4, 0))
        ttk.Button(
            export_actions, text="Export redacted…",
            command=lambda: self.export_conversation(redact=True),
        ).pack(side="left")
        ttk.Button(
            export_actions, text="Export full conversation…",
            command=lambda: self.export_conversation(redact=False),
        ).pack(side="left", padx=5)
        ttk.Label(
            main,
            text="Ctrl/Cmd+Enter sends · drafts are saved locally when you leave Chat",
        ).pack(anchor="w", pady=(3, 0))

    def mount(self, parent=None):
        del parent
        self.pack(fill="both", expand=True)
        return self

    def enter(self, route_context=None) -> None:
        del route_context
        self.refresh()

    def refresh(self, snapshot=None) -> None:
        if self._disposed:
            return
        self._flush_chunks()
        self._autosave_draft()
        if snapshot is None:
            query = (bool(self.archived_var.get()), self.search_var.get(), self._list_offset)
            dirty = self._list_dirty
            def observe():
                observation = self.application.chat_observation.current()
                rows = self.application.conversations.list(archived=query[0], query=query[1], limit=50, offset=query[2]) if dirty else None
                return observation, rows
            def apply(value):
                self._apply_observation(value[0])
                if value[1] is not None and query == (bool(self.archived_var.get()), self.search_var.get(), self._list_offset):
                    self._list_dirty = False
                    self._apply_list(value[1], select_first=self._select_first)
                    self._select_first = False
            self.shell.request_observation(observe, apply)
            return
        self._apply_observation(snapshot)

    def _apply_observation(self, observation) -> None:
        if self._disposed:
            return
        prior_model = self._observation.model
        self._observation = observation
        self.profile_var.set(profile_indicator(
            model=observation.model, context=observation.context,
            slots=observation.slots,
        ))
        change = model_change_notice(prior_model, observation.model)
        if not self._unsaved_response:
            self.notice_var.set(change or ("" if observation.ready else observation.guidance))
        if not self._streaming:
            self.send_button.configure(state="normal" if observation.ready else "disabled")

    def focus_primary(self) -> None:
        self.composer.focus_set()

    def observation_failed(self, _error: BaseException) -> None:
        self._observation = replace(self._observation, ready=False, stale=True)
        self.send_button.configure(state="disabled")
        self.notice_var.set(
            "Chat readiness is stale. No ready state was inferred; refresh will retry."
        )

    def _history_page(self, direction):
        self._list_offset = max(0, min(9950, self._list_offset + direction * 50))
        self._list_dirty = True
        self.refresh()

    def _update_budget(self):
        context = self._observation.context
        reserve = min(2048, max(64, context // 2))
        prompt = str(self.composer.get("1.0", "end-1c"))
        used = estimate_message_tokens(trim_messages(self._messages + [{"role": "user", "content": prompt}], context, reserve=reserve))
        self.budget_var.set(f"Estimated prompt: {used:,} / {context - reserve:,} tokens · response reserve: {reserve:,}")

    def _recover_save(self):
        if not self._conversation_id or self._streaming:
            return False
        try:
            record = self.application.conversations.save(self._conversation_id, title=self._title,
                messages=self._messages, last_model=self._observation.model,
                expected_revision=self._conversation_revision)
        except ConversationConflict:
            try:
                record = self.application.conversations.create((self._title[:145] + " (saved copy)"))
                record = self.application.conversations.save(record.conversation_id, title=record.title,
                    messages=self._messages, last_model=self._observation.model, expected_revision=record.revision)
                self._conversation_id, self._title = record.conversation_id, record.title
                self.title_var.set(record.title)
            except (OSError, ValueError):
                self.notice_var.set("Could not save a separate copy. Copy the response before leaving; your text remains here.")
                return False
        except (OSError, ValueError):
            self.notice_var.set("Save failed. The response remains here; copy it before leaving or retry Save response.")
            return False
        self._conversation_revision = record.revision
        self._unsaved_response = False
        self.notice_var.set("Conversation saved locally.")
        return True

    def _reload_list(self, *, select_first: bool = False) -> None:
        self._list_offset = 0
        self._list_dirty = True
        self._select_first = select_first
        self.refresh()

    def _apply_list(self, rows, *, select_first=False):
        self.previous_page.configure(state="normal" if self._list_offset else "disabled")
        self.next_page.configure(state="normal" if len(rows) == 50 and self._list_offset < 9950 else "disabled")
        self.conversation_count_var.set(
            f"{len(rows)} {'archived' if self.archived_var.get() else 'active'}"
        )
        self.conversation_tree.delete(*self.conversation_tree.get_children())
        for row in rows:
            identifier = str(row["conversation_id"])
            self.conversation_tree.insert("", "end", iid=identifier, text=str(row["title"]))
        if self._conversation_id and any(
            row["conversation_id"] == self._conversation_id for row in rows
        ):
            self.conversation_tree.selection_set(self._conversation_id)
        elif select_first and rows:
            identifier = str(rows[0]["conversation_id"])
            self.conversation_tree.selection_set(identifier)
            self._load(identifier)

    def _select_conversation(self, _event=None) -> None:
        selected = self.conversation_tree.selection()
        if selected and not self._streaming and not self._unsaved_response and not self._draft_saving:
            if self._save_draft() is not False:
                self._load(selected[0])

    def _load(self, conversation_id: str) -> None:
        try:
            record = self.application.conversations.load(conversation_id)
        except (OSError, ValueError):
            self.notice_var.set("This saved conversation needs repair. Its original file is preserved in the profile's conversations folder. The currently open conversation is unchanged.")
            if self._conversation_id:
                self.conversation_tree.selection_set(self._conversation_id)
            return
        self._conversation_revision = record.revision
        self._conversation_id = record.conversation_id
        self._title = record.title
        self._archived = record.archived
        self.title_var.set(record.title)
        self._messages = [dict(message) for message in record.messages]
        self._partial = ""
        self.composer.delete("1.0", "end")
        if record.draft:
            self.composer.insert("1.0", record.draft)
            self.status_var.set("Local draft restored.")
        self.archive_button.configure(
            text="Unarchive" if record.archived else "Archive"
        )
        self.notice_var.set(model_change_notice(record.last_model, self._observation.model) or "")
        self._render_transcript()
        if record.result_classification and record.result_classification != "COMPLETED":
            self.status_var.set("The last response is incomplete or was stopped. Its partial text is preserved.")

    def new_conversation(self) -> None:
        if self._draft_saving:
            return
        if self._unsaved_response and not self._recover_save():
            return
        if self._streaming:
            return
        if self._save_draft() is False:
            return
        try:
            record = self.application.conversations.create("New conversation")
        except (OSError, ValueError):
            self.notice_var.set("A new conversation could not be created. At 200 saved files, export and delete an older conversation first; your current conversation is kept.")
            return
        self._conversation_revision = record.revision
        self._conversation_id = record.conversation_id
        self._archived = False
        self._title = record.title
        self.title_var.set(record.title)
        self._messages = []
        self._partial = ""
        self.composer.delete("1.0", "end")
        self.archive_button.configure(text="Archive")
        self.archived_var.set(False)
        self._reload_list()
        self._render_transcript()
        self.composer.focus_set()

    def rename_conversation(self) -> None:
        if not self._conversation_id or self._streaming:
            return
        try:
            record = self.application.conversations.rename(
                self._conversation_id, self.title_var.get()
            )
        except ValueError as exc:
            message = safe_exception_message(
                exc, code="CONVERSATION_RENAME_INVALID"
            )
            self.shell.notice_bar.show_notice(Notice(
                message.level, message.title, message.body, dismissible=False,
            ))
            return
        self._title = record.title
        self._reload_list()

    def archive_conversation(self) -> None:
        if not self._conversation_id or self._streaming:
            return
        if self._archived:
            self.application.conversations.archive(self._conversation_id, False)
            self._archived = False
            self.archived_var.set(False)
            self.archive_button.configure(text="Archive")
            self.shell.notice_bar.show_notice(Notice(
                "success", "Conversation restored",
                "The conversation is back in the active list.",
            ))
            self._reload_list()
            self._render_transcript()
            return
        policy = conversation_action_confirmation("archive")
        self.shell.drawer.show_confirmation(
            Confirmation(
                "Archive conversation", policy["prompt"], policy["recovery"],
                "Archive", destructive=False,
            ),
            self._archive_confirmed,
        )

    def _archive_confirmed(self) -> None:
        if self._conversation_id:
            self.application.conversations.archive(self._conversation_id, True)
        self.shell.notice_bar.show_notice(Notice(
            "info", "Conversation archived",
            "Open Archived and choose Unarchive to restore it; no messages were deleted.",
        ))
        self._conversation_id = None
        self._archived = False
        self._messages = []
        self._reload_list(select_first=True)
        self._render_transcript()

    def delete_conversation(self) -> None:
        if not self._conversation_id or self._streaming:
            return
        policy = conversation_action_confirmation("delete")
        self.shell.drawer.show_confirmation(
            Confirmation(
                "Delete conversation", policy["prompt"], policy["recovery"],
                "Delete permanently", destructive=True,
                typed_phrase="DELETE",
            ),
            self._delete_confirmed,
        )

    def _delete_confirmed(self) -> None:
        if self._conversation_id:
            self.application.conversations.delete(self._conversation_id)
        self._conversation_id = None
        self._messages = []
        self._reload_list(select_first=True)
        self._render_transcript()

    def _send_event(self, _event=None):
        self.send()
        return "break"

    def _use_example(self, prompt: str) -> None:
        if self._streaming:
            return
        self.composer.delete("1.0", "end")
        self.composer.insert("1.0", prompt)
        self.composer.focus_set()

    def _autosave_draft(self):
        if self._streaming or self._draft_saving or self.shell.busy or not self.autosave_drafts.get():
            return
        now = time.monotonic()
        draft = str(self.composer.get("1.0", "end-1c"))
        if now - self._last_draft_save < 10 or draft == self._last_draft_text or len(draft.encode()) > MAX_COMPOSER_BYTES:
            return
        identifier, revision = self._conversation_id, self._conversation_revision
        if identifier is None and not draft.strip():
            return
        self._draft_saving = True
        self._last_draft_save = now
        box = {}
        def work():
            try:
                if identifier is None:
                    record = self.application.conversations.create("Draft conversation")
                    box["record"] = self.application.conversations.save_draft(record.conversation_id, draft, expected_revision=record.revision)
                else:
                    box["record"] = self.application.conversations.save_draft(identifier, draft, expected_revision=revision)
            except (OSError, ValueError):
                box["record"] = None
        def done():
            self._draft_saving = False
            if self._disposed:
                return
            record = box.get("record")
            if record is None:
                self.notice_var.set("Automatic draft saving failed. Your draft remains in the composer; copy it before closing.")
                return
            self._conversation_id, self._conversation_revision = record.conversation_id, record.revision
            self._title = record.title
            self.title_var.set(record.title)
            self._last_draft_text = draft
        self.shell._work(work, done)

    def _save_draft(self) -> None:
        if self._streaming:
            return
        draft = str(self.composer.get("1.0", "end-1c"))
        if len(draft.encode("utf-8")) > MAX_COMPOSER_BYTES:
            return
        if self._conversation_id is None:
            if not draft.strip():
                return
            record = self.application.conversations.create("Draft conversation")
            self._conversation_revision = record.revision
            self._conversation_id = record.conversation_id
            self._title = record.title
            self.title_var.set(record.title)
        try:
            record = self.application.conversations.save_draft(
                self._conversation_id, draft, expected_revision=self._conversation_revision)
            self._conversation_revision = record.revision
            return True
        except (OSError, ValueError):
            self.notice_var.set("The draft could not be saved, or this conversation changed in another client. Copy your draft before leaving.")
            return False

    def send(self) -> None:
        if self._draft_saving:
            self.notice_var.set("Finishing the local draft save; send again in a moment.")
            return
        if self._streaming:
            return
        prompt = str(self.composer.get("1.0", "end-1c")).strip()
        if not prompt:
            return
        if len(prompt.encode("utf-8")) > MAX_COMPOSER_BYTES:
            self.shell.notice_bar.show_notice(Notice(
                "error", "Message is too large",
                "One message may contain at most 32 KiB of UTF-8 text.",
                dismissible=False,
            ))
            return
        self._start_response(prompt, append_user=True)

    def _start_response(self, prompt: str, *, append_user: bool) -> None:
        if self._unsaved_response and not self._recover_save():
            return
        if not self._observation.ready:
            self.shell.notice_bar.show_notice(Notice(
                "warning", "Chat is not ready", self._observation.guidance,
                action_label="Open System", action_route=Route.SYSTEM.value,
                dismissible=False,
            ))
            return
        state = self.application.read_model()
        context = int(self._observation.context)
        reserve = min(2048, max(64, context // 2))
        prospective = self._messages + ([{"role": "user", "content": prompt}] if append_user else [])
        history = trim_messages(prospective, context, reserve=reserve)
        if estimate_message_tokens(history) > context - reserve:
            self.notice_var.set("This message exceeds the available context after reserving space for a response. Shorten it or choose a larger context in Profiles. Your draft is kept.")
            return
        if self._conversation_id is None:
            record = self.application.conversations.create(
                prompt[:60] or "New conversation"
            )
            self._conversation_revision = record.revision
            self._conversation_id = record.conversation_id
            self._title = record.title
            self.title_var.set(record.title)
        if append_user:
            self._messages.append({"role": "user", "content": prompt})
        try:
            saved = self.application.conversations.save(
                self._conversation_id, title=self._title,
                messages=self._messages, last_model=self._observation.model,
                draft="", expected_revision=self._conversation_revision,
            )
            self._conversation_revision = saved.revision
        except (OSError, ValueError):
            if append_user:
                self._messages.pop()
            self.notice_var.set("The conversation could not be saved. Your draft is kept; check available disk space and retry.")
            return
        if append_user:
            self.composer.delete("1.0", "end")
        self._partial = ""
        self._render_transcript()
        self._streaming = True
        self._cancellation = ChatCancellation()
        self._stream_started = time.monotonic()
        self._tokens_emitted = 0
        self._first_token_ms = None
        self._last_result_classification = None
        self.send_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set("waiting for first token…")
        generation = self.shell._route_generation

        def on_text(text: str) -> None:
            with self._chunk_lock:
                self._chunk_buffer.append(text)
                self._tokens_emitted += max(1, len(text) // 4)
                if self._first_token_ms is None:
                    self._first_token_ms = int(
                        (time.monotonic() - self._stream_started) * 1000
                    )

        def task():
            current = self.application.chat_observation.current()
            if not current.ready or current.model != self._observation.model or current.context != self._observation.context:
                return lambda: self.chat_failed(ValueError("Chat readiness changed"))
            result = self.application.chat_sessions.stream(
                state,
                history,
                on_text,
                conversation_id=self._conversation_id or "native",
                cancellation=self._cancellation,
            )
            return lambda: self._finish(result, generation)

        if not self.shell.submit_chat(task):
            self._streaming = False
            self.send_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            self.status_var.set("Another response is still active.")

    def retry_last(self) -> None:
        if self._streaming or not self._messages or not self._observation.ready:
            return
        user_index = next(
            (
                index for index in range(len(self._messages) - 1, -1, -1)
                if self._messages[index].get("role") == "user"
            ),
            None,
        )
        if user_index is None:
            return
        prompt = self._messages[user_index]["content"]
        # Preserve the earlier answer as history; a failed retry cannot erase it.
        self._start_response(prompt, append_user=True)

    def copy_last_response(self) -> None:
        response = next(
            (
                item["content"] for item in reversed(self._messages)
                if item.get("role") == "assistant"
            ),
            None,
        )
        if not response:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(response)
        except Exception:
            return
        self.shell.notice_bar.show_notice(Notice(
            "success", "Response copied",
            "The last assistant response was copied to the local clipboard.",
        ))

    def export_conversation(self, *, redact: bool) -> None:
        if not self._conversation_id or not self._messages:
            self.shell.notice_bar.show_notice(Notice(
                "info", "Nothing to export",
                "Send at least one message before exporting this conversation.",
            ))
            return
        if not redact:
            self.shell.drawer.show_confirmation(
                Confirmation(
                    "Export the full conversation?",
                    export_privacy_warning(),
                    "Cancel to keep all prompt and response text inside the app. "
                    "A redacted export is available without message content.",
                    "Choose export file",
                ),
                lambda: self._choose_export(redact=False),
            )
            return
        self._choose_export(redact=True)

    def _choose_export(self, *, redact: bool) -> None:
        destination = filedialog.asksaveasfilename(
            title="Export conversation",
            defaultextension=".md",
            filetypes=(("Markdown", "*.md"),),
            initialfile="conversation-redacted.md" if redact else "conversation.md",
        )
        if not destination or not self._conversation_id:
            return
        try:
            self.application.conversations.export_markdown(
                self._conversation_id, destination, redact=redact
            )
        except (OSError, ValueError) as exc:
            message = safe_exception_message(exc)
            self.shell.notice_bar.show_notice(Notice(
                message.level, message.title, message.body,
                dismissible=False,
            ))
            return
        self.shell.notice_bar.show_notice(Notice(
            "success",
            "Redacted export created" if redact else "Conversation exported",
            (
                "Roles and message order were exported; prompt and response text was replaced."
                if redact else
                "The full local conversation was exported to the file you selected."
            ),
        ))

    def stop(self) -> None:
        if self._cancellation is not None:
            self._cancellation.cancel()
            self.status_var.set("Stopping response…")
            self.stop_button.configure(state="disabled")

    def _flush_chunks(self) -> None:
        with self._chunk_lock:
            if not self._chunk_buffer:
                return
            chunk = "".join(self._chunk_buffer)
            self._chunk_buffer.clear()
        first = not self._partial
        self._partial += chunk
        try:
            following = self.transcript.yview()[1] >= 0.98
        except (TypeError, IndexError):
            following = True
        if getattr(self, "_display_bytes", 0) + len(chunk.encode()) > 4 * 1024 * 1024:
            self._render_transcript()
            return
        self._display_bytes = getattr(self, "_display_bytes", 0) + len(chunk.encode())
        self.transcript.configure(state="normal")
        if first:
            self.transcript.insert("end", "Assistant\n", "role")
        self.transcript.insert("end", chunk)
        self.transcript.configure(state="disabled")
        if following and self.follow_output.get():
            self.transcript.see("end")
        self.status_var.set(streaming_status_text(
            tokens_emitted=self._tokens_emitted,
            elapsed_s=max(0.001, time.monotonic() - self._stream_started),
            first_token_ms=self._first_token_ms,
        ))

    def _finish(self, result, generation: int) -> None:
        if self._disposed or generation != self.shell._route_generation:
            return
        self._flush_chunks()
        if result.text and not self._partial:
            self._partial = result.text
        if self._partial:
            self._messages.append({"role": "assistant", "content": self._partial})
        self._streaming = False
        self._cancellation = None
        self.send_button.configure(state="normal" if self._observation.ready else "disabled")
        self.stop_button.configure(state="disabled")
        self._unsaved_response = bool(self._partial)
        if self._conversation_id:
            try:
                saved = self.application.conversations.save(
                    self._conversation_id, title=self._title,
                    messages=self._messages, last_model=self._observation.model,
                    expected_revision=self._conversation_revision,
                    result_classification=result.classification.value,
                )
                self._conversation_revision = saved.revision
                self._unsaved_response = False
            except (OSError, ValueError):
                self.notice_var.set("The response is kept in this window but could not be saved. Copy it before closing, then check disk space.")
        self._partial = ""
        self._streaming = False
        self._cancellation = None
        self._last_result_classification = result.classification
        self.send_button.configure(state="normal" if self._observation.ready else "disabled")
        self.stop_button.configure(state="disabled")
        if result.classification is ChatResultClassification.COMPLETED:
            self.status_var.set(streaming_status_text(
                tokens_emitted=result.tokens_generated,
                elapsed_s=max(0.001, result.duration_ms / 1000),
                first_token_ms=result.time_to_first_token_ms,
            ))
        else:
            self.status_var.set(result.message)
            action_route = (
                Route.SYSTEM.value
                if result.classification in {
                    ChatResultClassification.SERVER_UNAVAILABLE,
                    ChatResultClassification.MODEL_MISMATCH,
                    ChatResultClassification.THERMAL_STOP,
                }
                else Route.MODELS.value
                if result.classification is ChatResultClassification.TIMEOUT
                else Route.ACTIVITY.value
            )
            level = (
                "info" if result.classification is ChatResultClassification.CANCELLED
                else "warning"
            )
            self.shell.notice_bar.show_notice(Notice(
                level,
                "Response stopped" if level == "info" else "Response needs attention",
                result.message,
                action_label=(
                    "Open System" if action_route == Route.SYSTEM.value
                    else "Review Models" if action_route == Route.MODELS.value
                    else "View Activity"
                ),
                action_route=action_route,
                details=(
                    f"Classification: {result.classification.value}\n"
                    f"Request ID: {result.request_id}\n"
                    f"Attempts: {result.attempts}"
                ),
                dismissible=result.classification is ChatResultClassification.CANCELLED,
            ))
        self._reload_list()
        self._render_transcript()
        if self._unsaved_response:
            self._pending_leave = None
            self._pending_close = None
            return
        pending = self._pending_leave
        self._pending_leave = None
        if pending is not None:
            self._allow_leave = True
            self.shell.navigate(pending)
            return
        pending_close = self._pending_close
        self._pending_close = None
        if pending_close is not None:
            pending_close()

    def chat_failed(self, _error) -> None:
        if self._cancellation is not None:
            self._cancellation.cancel()
        self._streaming = False
        self._cancellation = None
        self.send_button.configure(state="normal" if self._observation.ready else "disabled")
        self.stop_button.configure(state="disabled")
        self.notice_var.set("The response stopped unexpectedly. Your message is saved and any partial response remains here. Retry when ready.")
        self._flush_chunks()
        if self._partial:
            self._messages.append({"role": "assistant", "content": self._partial})
            self._partial = ""
            self._unsaved_response = True
            self._recover_save()
        self._pending_leave = None
        self._pending_close = None
        self._render_transcript()

    def _render_transcript(self) -> None:
        display = list(self._messages)
        if self._partial:
            display.append({"role": "assistant", "content": self._partial})
        bounded = bounded_live_messages(display)
        self._display_bytes = sum(len(item["content"].encode()) for item in bounded)
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        if not display:
            self.transcript.insert(
                "end",
                "Start a local conversation. Choose an example below or write your own message. "
                "Prompt and response text stays in this appliance's local conversation files.\n",
                "system",
            )
        if len(bounded) < len(display):
            self.transcript.insert("end", "Older messages remain in local history.\n\n", "system")
        for message in bounded:
            role = message["role"]
            label = "You" if role == "user" else "Assistant" if role == "assistant" else "System"
            self.transcript.insert("end", f"{label}\n", "role")
            self.transcript.insert("end", message["content"] + "\n\n")
        self.transcript.configure(state="disabled")
        self.transcript.see("end")
        if display:
            self.example_frame.pack_forget()
        elif not self.example_frame.winfo_manager():
            self.example_frame.pack(fill="x", pady=(3, 3), before=self.composer)
        has_user = any(item.get("role") == "user" for item in self._messages)
        has_response = any(item.get("role") == "assistant" for item in self._messages)
        self.retry_button.configure(
            state="normal" if has_user and not self._streaming else "disabled"
        )
        self.copy_button.configure(
            state="normal" if has_response and not self._streaming else "disabled"
        )

    def request_leave(self, target: Route) -> bool:
        if self._draft_saving:
            self.notice_var.set("Finishing the local draft save; try navigating again in a moment.")
            return False
        if self._unsaved_response and not self._recover_save():
            return False
        if not self._streaming or self._allow_leave:
            return self._save_draft() is not False
        self.shell.drawer.show_confirmation(
            Confirmation(
                "Stop this response?",
                "The current response will stop; the model server stays running.",
                "Your message and any partial response remain in local history.",
                "Stop and leave",
            ),
            lambda: self._stop_and_leave(target),
        )
        return False

    def _stop_and_leave(self, target: Route) -> None:
        self._pending_leave = target
        self.stop()

    def request_close(self, callback) -> bool:
        if self._draft_saving:
            self.notice_var.set("Finishing the local draft save; try closing again in a moment.")
            return False
        if self._unsaved_response and not self._recover_save():
            return False
        if not self._streaming:
            return self._save_draft() is not False
        self.shell.drawer.show_confirmation(
            Confirmation(
                "Stop this response and close?",
                "The response will stop. In Desktop mode, the model server also stops before the app closes.",
                "Your message and any partial response are saved locally. Reopen the app to start the model again.",
                "Stop and close",
            ),
            lambda: self._stop_and_close(callback),
        )
        return False

    def _stop_and_close(self, callback) -> None:
        self._pending_close = callback
        self.stop()

    def leave(self) -> None:
        self._save_draft()

    def dispose(self) -> None:
        self._save_draft()
        self._disposed = True


__all__ = ["ChatPage", "MAX_COMPOSER_BYTES"]
