"""Lightweight native chat over the shared bounded session services."""

from __future__ import annotations

import threading
import time
from typing import Any, Mapping

import tkinter as tk
from tkinter import ttk

from ..chat_lifecycle import ChatCancellation, ChatResultClassification
from ..chat_service import trim_messages
from ..conversation_service import bounded_live_messages
from ..conversation_ux import (
    conversation_action_confirmation,
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
        self._allow_leave = False
        self._pending_leave: Route | None = None
        self._pending_close = None
        self._cancellation: ChatCancellation | None = None
        self._conversation_id: str | None = None
        self._title = "New conversation"
        self._messages: list[dict[str, str]] = []
        self._partial = ""
        self._chunk_buffer: list[str] = []
        self._chunk_lock = threading.Lock()
        self._stream_started = 0.0
        self._tokens_emitted = 0
        self._first_token_ms: int | None = None
        self._observation = application.chat_observation.current()
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
        search = ttk.Entry(sidebar, textvariable=self.search_var)
        search.pack(fill="x", pady=5)
        self.search_var.trace_add("write", lambda *_: self._reload_list())
        self.conversation_tree = ttk.Treeview(sidebar, show="tree", height=18)
        self.conversation_tree.heading("#0", text="Conversations")
        self.conversation_tree.column("#0", width=205)
        self.conversation_tree.pack(fill="both", expand=True)
        self.conversation_tree.bind("<<TreeviewSelect>>", self._select_conversation)

        title_row = ttk.Frame(main)
        title_row.pack(fill="x")
        self.title_var = tk.StringVar(value=self._title)
        ttk.Entry(title_row, textvariable=self.title_var, width=34).pack(side="left", fill="x", expand=True)
        ttk.Button(title_row, text="Rename", command=self.rename_conversation).pack(side="left", padx=4)
        ttk.Button(title_row, text="Archive", command=self.archive_conversation).pack(side="left")
        ttk.Button(title_row, text="Delete", command=self.delete_conversation).pack(side="left", padx=(4, 0))

        transcript_frame = ttk.Frame(main)
        transcript_frame.pack(fill="both", expand=True, pady=6)
        self.transcript = tk.Text(transcript_frame, wrap="word", state="disabled", height=18)
        self.transcript.tag_configure("role", font=("TkDefaultFont", 10, "bold"))
        self.transcript.tag_configure("system", foreground="#555555")
        self.transcript.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(transcript_frame, orient="vertical", command=self.transcript.yview)
        scroll.pack(side="right", fill="y")
        self.transcript.configure(yscrollcommand=scroll.set)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(main, textvariable=self.status_var).pack(anchor="w")
        self.composer = tk.Text(main, height=4, wrap="word")
        self.composer.pack(fill="x", pady=(4, 4))
        self.composer.bind("<Control-Return>", self._send_event)
        actions = ttk.Frame(main)
        actions.pack(fill="x")
        self.send_button = ttk.Button(actions, text="Send", command=self.send)
        self.send_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="Stop generating", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=5)
        ttk.Button(actions, text="Models", command=lambda: self.shell.navigate(Route.MODELS)).pack(side="right")
        ttk.Button(actions, text="System", command=lambda: self.shell.navigate(Route.SYSTEM)).pack(side="right", padx=5)

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
        if snapshot is None:
            self.shell.request_observation(
                self.application.chat_observation.current,
                self._apply_observation,
            )
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
        self.notice_var.set(change or ("" if observation.ready else observation.guidance))
        if not self._streaming:
            self.send_button.configure(state="normal" if observation.ready else "disabled")

    def focus_primary(self) -> None:
        self.composer.focus_set()

    def observation_failed(self, _error: BaseException) -> None:
        self.notice_var.set(
            "Chat readiness is stale. No ready state was inferred; refresh will retry."
        )

    def _reload_list(self, *, select_first: bool = False) -> None:
        rows = self.application.conversations.list(
            archived=bool(self.archived_var.get()),
            query=self.search_var.get(), limit=50,
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
        if selected and not self._streaming:
            self._load(selected[0])

    def _load(self, conversation_id: str) -> None:
        record = self.application.conversations.load(conversation_id)
        self._conversation_id = record.conversation_id
        self._title = record.title
        self.title_var.set(record.title)
        self._messages = [dict(message) for message in record.messages]
        self._partial = ""
        self.notice_var.set(model_change_notice(record.last_model, self._observation.model) or "")
        self._render_transcript()

    def new_conversation(self) -> None:
        if self._streaming:
            return
        record = self.application.conversations.create("New conversation")
        self._conversation_id = record.conversation_id
        self._title = record.title
        self.title_var.set(record.title)
        self._messages = []
        self._partial = ""
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
            self.shell.notice_bar.show_notice(Notice(
                "error", "Conversation was not renamed", str(exc), dismissible=False,
            ))
            return
        self._title = record.title
        self._reload_list()

    def archive_conversation(self) -> None:
        if not self._conversation_id or self._streaming:
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
        self._conversation_id = None
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

    def send(self) -> None:
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
        if not self._observation.ready:
            self.shell.notice_bar.show_notice(Notice(
                "warning", "Chat is not ready", self._observation.guidance,
                action_label="Open System", action_route=Route.SYSTEM.value,
                dismissible=False,
            ))
            return
        if self._conversation_id is None:
            record = self.application.conversations.create(
                prompt[:60] or "New conversation"
            )
            self._conversation_id = record.conversation_id
            self._title = record.title
            self.title_var.set(record.title)
        self.composer.delete("1.0", "end")
        self._messages.append({"role": "user", "content": prompt})
        self.application.conversations.save(
            self._conversation_id, title=self._title,
            messages=self._messages, last_model=self._observation.model,
        )
        self._partial = ""
        self._render_transcript()
        self._streaming = True
        self._cancellation = ChatCancellation()
        self._stream_started = time.monotonic()
        self._tokens_emitted = 0
        self._first_token_ms = None
        self.send_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set("waiting for first token…")
        state = self.application.read_model()
        history = trim_messages(
            self._messages,
            int(self._observation.context),
            reserve=min(512, max(128, int(self._observation.context) // 4)),
        )
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

    def stop(self) -> None:
        if self._cancellation is not None:
            self._cancellation.cancel()
            self.status_var.set("Stopping after the current network chunk…")
            self.stop_button.configure(state="disabled")

    def _flush_chunks(self) -> None:
        with self._chunk_lock:
            if not self._chunk_buffer:
                return
            chunk = "".join(self._chunk_buffer)
            self._chunk_buffer.clear()
        self._partial += chunk
        self._render_transcript()
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
        if self._conversation_id:
            self.application.conversations.save(
                self._conversation_id, title=self._title,
                messages=self._messages, last_model=self._observation.model,
            )
        self._partial = ""
        self._streaming = False
        self._cancellation = None
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
        self._reload_list()
        self._render_transcript()
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

    def _render_transcript(self) -> None:
        display = list(self._messages)
        if self._partial:
            display.append({"role": "assistant", "content": self._partial})
        bounded = bounded_live_messages(display)
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        if len(bounded) < len(display):
            self.transcript.insert("end", "Older messages remain in local history.\n\n", "system")
        for message in bounded:
            role = message["role"]
            label = "You" if role == "user" else "Assistant" if role == "assistant" else "System"
            self.transcript.insert("end", f"{label}\n", "role")
            self.transcript.insert("end", message["content"] + "\n\n")
        self.transcript.configure(state="disabled")
        self.transcript.see("end")

    def request_leave(self, target: Route) -> bool:
        if not self._streaming or self._allow_leave:
            return True
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
        if not self._streaming:
            return True
        self.shell.drawer.show_confirmation(
            Confirmation(
                "Stop this response and close?",
                "The response will stop; the model server remains under System control.",
                "Your message and any partial response are saved locally before closing.",
                "Stop and close",
            ),
            lambda: self._stop_and_close(callback),
        )
        return False

    def _stop_and_close(self, callback) -> None:
        self._pending_close = callback
        self.stop()

    def leave(self) -> None:
        return None

    def dispose(self) -> None:
        self._disposed = True


__all__ = ["ChatPage", "MAX_COMPOSER_BYTES"]
