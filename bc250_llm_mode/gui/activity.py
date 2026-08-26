"""P2/U1.5: Activity Center v1.

One truthful, live view of durable operations. The GUI owns NO
persistence and NO host infrastructure: this module imports only
tkinter widgets and the frozen view models; every read goes through
``application.operation_query`` and every action through
``application.operation_commands`` (both composed once in app.py).

The presentation contract lives in PURE functions so the §8.2 state/
action matrix is testable headlessly without any widget:

- plain-language state labels with exact semantics;
- action buttons whose availability comes from ``OperationSummary``
  flags only;
- progress never shown as 100% until the terminal verification step;
- recovery-required rendered as a prominent warning that cannot be
  mistaken for success;
- error copy answers "what happened / what is safe / what can I do now";
- copy-support-details applies the same redaction rules as the views.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..operations.model import TERMINAL_STATES, OperationState
from ..operations.views import OperationDetail, OperationSummary

_REFRESH_MS = 1500  # bounded refresh cadence; coalesced by _poll guard


# -- pure presentation contract ----------------------------------------------------


_STATE_LABELS = {
    "QUEUED": "Waiting to start",
    "PREPARING": "Starting safely",
    "RUNNING": "Working",
    "VERIFYING": "Verifying results",
    "COMMITTING": "Finishing safely",
    "CANCEL_REQUESTED": "Finishing safely",
    "ROLLING_BACK": "Restoring previous state",
    "PAUSED": "Paused safely",
    "SUCCEEDED": "Completed",
    "CANCELLED": "Cancelled safely",
    "FAILED_SAFE": "Stopped — nothing was broken",
    "FAILED_ROLLED_BACK": "Failed — previous state restored",
    "RECOVERY_REQUIRED": "ATTENTION REQUIRED",
}

_SEVERITY_ORDER = {
    "recovery_required": 0,
    "failed": 1,
    "attention": 2,
    "working": 3,
    "done": 4,
}


def severity_of(state: str) -> str:
    """Deterministic severity bucket used for color-free state cues."""
    if state == "RECOVERY_REQUIRED":
        return "recovery_required"
    if state in ("FAILED_SAFE", "FAILED_ROLLED_BACK"):
        return "failed"
    if state in ("PAUSED", "CANCEL_REQUESTED", "ROLLING_BACK"):
        return "attention"
    if state in TERMINAL_STATES:
        return "done"
    return "working"


def severity_rank(state: str) -> int:
    """Lower sorts first: recovery-required before failures before work."""
    return _SEVERITY_ORDER[severity_of(state)]


def headline(summary: OperationSummary) -> str:
    """Plain-language label with exact semantics (plan §8.2)."""
    label = _STATE_LABELS.get(summary.state, summary.state)
    return f"{label}: {summary.title}"


def progress_text(summary: OperationSummary) -> str:
    """Bounded progress text; NEVER renders 100% before a terminal state."""
    if summary.state in TERMINAL_STATES:
        return ""
    if not summary.progress_total:
        return ""
    current = min(int(summary.progress_current or 0), int(summary.progress_total))
    percent = int(current * 100 / int(summary.progress_total))
    if percent >= 100:
        percent = 99  # terminal verification has not completed yet
    unit = summary.progress_unit or ""
    return f"{current}/{summary.progress_total} {unit} ({percent}%)"


def message_copy(summary: OperationSummary) -> str:
    """"What happened / what is safe / what can I do now" copy."""
    state = summary.state
    happened = {
        "QUEUED": "Your request is accepted and waiting for its turn.",
        "RUNNING": "The operation is running right now.",
        "PAUSED": "The operation was interrupted but stopped at a safe point.",
        "CANCEL_REQUESTED": "A stop was requested; work finishes at the next safe point.",
        "SUCCEEDED": "The operation completed and its result was verified.",
        "CANCELLED": "You cancelled this operation; it stopped at a safe point.",
        "FAILED_SAFE": "The operation hit a problem and stopped without changing anything unsafe.",
        "FAILED_ROLLED_BACK": "The operation failed; your previous working setup was restored and verified.",
        "RECOVERY_REQUIRED": (
            "The operation could not prove whether its last step completed."
            " Everything possibly needed was kept."
        ),
    }.get(state, "Status updated.")
    safe = {
        "FAILED_ROLLED_BACK": "Nothing is broken; the prior state is active.",
        "RECOVERY_REQUIRED": "Nothing has been deleted; protected files are retained.",
    }.get(state)
    lines = [happened]
    if safe:
        lines.append(safe)
    if summary.failure_code:
        lines.append(f"Reason code: {summary.failure_code}.")
    lines.append(f"What you can do now: {summary.recovery_recommendation}.")
    return "\n".join(lines)


class ActionSpec:
    """One available operator action derived from durable truth."""

    __slots__ = ("key", "label", "needs_confirm", "command")

    def __init__(self, key: str, label: str, command, needs_confirm: bool = False):
        self.key = key
        self.label = label
        self.needs_confirm = needs_confirm
        self.command = command


def action_plan(summary: OperationSummary, commands) -> tuple[ActionSpec, ...]:
    """Buttons whose availability comes from OperationSummary flags ONLY."""
    plan: list[ActionSpec] = []
    op_id = summary.operation_id
    if summary.cancellable:
        plan.append(ActionSpec(
            "cancel", "Stop safely",
            lambda: commands.cancel(op_id),
        ))
    if summary.resumable:
        plan.append(ActionSpec("resume", "Resume", lambda: commands.resume(op_id)))
    if summary.retryable:
        plan.append(ActionSpec("retry", "Try again", lambda: commands.retry(op_id)))
    if summary.recoverable:
        plan.append(ActionSpec(
            "recover", "Recover…", lambda: commands.recover(op_id, confirm=True),
            needs_confirm=True,
        ))
    if summary.dismissable:
        plan.append(ActionSpec(
            "dismiss", "Hide from list",
            lambda: commands.dismiss(op_id),
            needs_confirm=True,
        ))
    return tuple(plan)


def support_text(detail: OperationDetail) -> str:
    """Redacted clipboard block: identity, state, codes, recommendation."""
    s = detail.summary
    lines = [
        f"operation: {s.operation_id}",
        f"kind: {s.kind} (request v{s.kind_version})",
        f"state: {s.state}",
    ]
    if detail.current_step:
        lines.append(f"step: {detail.current_step}")
    if s.failure_code:
        lines.append(f"failure_code: {s.failure_code}")
    if detail.result_code:
        lines.append(f"result_code: {detail.result_code}")
    lines.append(f"next: {s.recovery_recommendation}")
    return "\n".join(lines)


# -- widget layer ---------------------------------------------------------------------


class ActivityCenterFrame(ttk.Frame):
    """Live operation center over the composed query/command services."""

    def __init__(self, master, application, *, refresh_ms: int = _REFRESH_MS):
        super().__init__(master)
        self.application = application
        self._refresh_ms = max(500, int(refresh_ms))
        self._selected_id: str | None = None
        self._after_token: str | None = None
        self.rendered_summary: OperationSummary | None = None

        self.status_strip = ttk.Label(self, text="")
        self.status_strip.pack(fill="x")

        columns = ("state", "kind", "progress")
        self.operation_tree = ttk.Treeview(
            self, columns=columns, show="headings", height=8,
            selectmode="browse",
        )
        for key, title, width in (
            ("state", "State", 170), ("kind", "Operation", 180),
            ("progress", "Progress", 140),
        ):
            self.operation_tree.heading(key, text=title)
            self.operation_tree.column(key, width=width)
        self.operation_tree.pack(fill="both", expand=True)
        self.operation_tree.bind(
            "<<TreeviewSelect>>", self._on_select_event
        )

        self.detail_headline = ttk.Label(self, text="")
        self.detail_headline.pack(fill="x")
        self.detail_progress = ttk.Label(self, text="")
        self.detail_progress.pack(fill="x")
        self.detail_message = ttk.Label(self, text="", wraplength=560)
        self.detail_message.pack(fill="x")
        self.action_bar = ttk.Frame(self)
        self.action_bar.pack(fill="x")
        self.copy_button = ttk.Button(
            self, text="Copy support details",
            command=self._copy_support_details,
        )
        self.copy_button.pack(anchor="e")

        self.refresh()

    # -- data ------------------------------------------------------------------

    def refresh(self) -> None:
        """Coalesced bounded refresh; never blocks on worker work."""
        page = self.application.operation_query.list(page_size=50)
        active = self.application.operation_query.active_summary()
        strip_bits = []
        if active.running_count:
            strip_bits.append(f"{active.running_count} working")
        if active.paused_count:
            strip_bits.append(f"{active.paused_count} paused")
        if active.recovery_required_count:
            strip_bits.append(
                f"{active.recovery_required_count} NEED ATTENTION"
            )
        lock = f"worker: {active.worker_lock_owner}" if (
            active.worker_lock_owner and not active.worker_lock_expired
        ) else ""
        if lock:
            strip_bits.append(lock)
        self.status_strip.config(
            text="  ·  ".join(strip_bits) if strip_bits else "No activity"
        )

        self.operation_tree.delete(*self.operation_tree.get_children())
        ordered = sorted(
            page.items,
            key=lambda s: (severity_rank(s.state), s.updated_at),
        )
        for item in ordered:
            self.operation_tree.insert(
                "", "end", iid=item.operation_id,
                values=(headline(item), item.kind, progress_text(item)),
            )
            if item.operation_id == self._selected_id:
                self.operation_tree.selection_set(item.operation_id)
        if self._selected_id and not self._selected_exists(ordered):
            self._selected_id = None
            self._render_detail(None)
        elif self._selected_id:
            self._render_detail(
                self.application.operation_query.show(self._selected_id)
            )

    def _selected_exists(self, items) -> bool:
        return any(i.operation_id == self._selected_id for i in items)

    def poll_once(self) -> None:
        """Timer tick: one refresh, then reschedule."""
        try:
            self.refresh()
        finally:
            self._after_token = self.after(
                self._refresh_ms, self.poll_once
            )

    def start_polling(self) -> None:
        if self._after_token is None:
            self._after_token = self.after(self._refresh_ms, self.poll_once)

    def stop_polling(self) -> None:
        if self._after_token is not None:
            self.after_cancel(self._after_token)
            self._after_token = None

    # -- selection/actions -------------------------------------------------------

    def _on_select_event(self, _event=None) -> None:
        selected = self.operation_tree.selection()
        self._selected_id = selected[0] if selected else None
        self.refresh()

    def _render_detail(self, detail: OperationDetail | None) -> None:
        self.rendered_summary = detail.summary if detail else None
        for child in self.action_bar.winfo_children():
            child.destroy()
        if detail is None:
            self.detail_headline.config(text="Select an operation")
            self.detail_progress.config(text="")
            self.detail_message.config(text="")
            return
        summary = detail.summary
        self.detail_headline.config(text=headline(summary))
        self.detail_progress.config(text=progress_text(summary))
        self.detail_message.config(text=message_copy(summary))
        for spec in action_plan(summary, self.application.operation_commands):
            button = ttk.Button(
                self.action_bar, text=spec.label, command=self._guarded(spec),
            )
            button.pack(side="left", padx=3)

    def _guarded(self, spec: ActionSpec):
        def run() -> None:
            result = spec.command()
            if not result.ok:
                self.detail_message.config(
                    text=(
                        f"{spec.label} could not start: {result.reason}\n"
                        "Refreshed to show who owns the work now."
                    )
                )
            self.refresh()
        return run

    def _copy_support_details(self) -> None:
        if not self._selected_id:
            return
        detail = self.application.operation_query.show(self._selected_id)
        if detail is None:
            return
        text = support_text(detail)
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
        except Exception:  # noqa: BLE001 - headless/stub safety
            pass


__all__ = [
    "ActivityCenterFrame",
    "action_plan",
    "headline",
    "message_copy",
    "progress_text",
    "severity_of",
    "severity_rank",
    "support_text",
]
