"""One-window Repair, durable cleanup, and evidence-backed Undo page."""

from __future__ import annotations

import json
import time
from typing import Any

import tkinter as tk
from tkinter import ttk

from ..presentation import format_bytes, format_timestamp
from ..storage_cleanup_adapter import MAX_DISCOVERY_ENTRIES
from ..undo import MAX_UNDO_CANDIDATES
from ..ux_guidance import (
    operation_reason_guidance,
    repair_duration_label,
    repair_reversibility_label,
)
from .view_state import Confirmation, Notice

MAX_VISIBLE_CLEANUP = min(32, MAX_DISCOVERY_ENTRIES)
SECRET_REVEAL_SECONDS = 30.0


class RepairPage(ttk.Frame):
    def __init__(self, parent, shell, application) -> None:
        super().__init__(parent)
        self.shell = shell
        self.application = application
        self._disposed = False
        self._loaded = False
        self._actions: dict[str, dict[str, Any]] = {}
        self._undo_rows: dict[str, dict[str, Any]] = {}
        self._repair_preview = None
        self._cleanup_preview_value = None
        self._undo_preview_value = None
        self._secret_deadline: float | None = None

        ttk.Label(
            self, text="Guided repair", font=("TkDefaultFont", 15, "bold")
        ).pack(anchor="w")
        ttk.Label(
            self,
            text=(
                "Every change is previewed from current evidence, confirmed, "
                "executed by its typed owner, and verified afterward."
            ),
            wraplength=800,
        ).pack(anchor="w", fill="x", pady=(2, 7))
        self._tabs = ttk.Notebook(self)
        self._tabs.pack(fill="both", expand=True)
        self._build_repairs_tab()
        self._build_cleanup_tab()
        self._build_undo_tab()
        self.refresh(force=True)

    def _build_repairs_tab(self) -> None:
        tab = ttk.Frame(self._tabs, padding=8)
        self._tabs.add(tab, text="Repairs")
        split = ttk.Panedwindow(tab, orient="horizontal")
        split.pack(fill="both", expand=True)
        left = ttk.Frame(split)
        right = ttk.Frame(split, padding=(8, 0, 0, 0))
        split.add(left, weight=3)
        split.add(right, weight=2)
        ttk.Label(
            left, text="Repairs table · select a row to read its safe preview summary",
        ).pack(anchor="w", fill="x", pady=(0, 3))
        repair_table = ttk.Frame(left)
        repair_table.pack(fill="both", expand=True)
        self._repair_tree = ttk.Treeview(
            repair_table, columns=("status", "privilege"), show="tree headings",
            selectmode="browse", height=12,
        )
        self._repair_tree.heading("#0", text="Repair")
        self._repair_tree.heading("status", text="Status")
        self._repair_tree.heading("privilege", text="Access")
        self._repair_tree.column("#0", width=300)
        self._repair_tree.column("status", width=90)
        self._repair_tree.column("privilege", width=80)
        self._repair_tree.grid(row=0, column=0, sticky="nsew")
        repair_vertical = ttk.Scrollbar(
            repair_table, orient="vertical", command=self._repair_tree.yview
        )
        repair_vertical.grid(row=0, column=1, sticky="ns")
        repair_horizontal = ttk.Scrollbar(
            repair_table, orient="horizontal", command=self._repair_tree.xview
        )
        repair_horizontal.grid(row=1, column=0, sticky="ew")
        self._repair_tree.configure(
            yscrollcommand=repair_vertical.set,
            xscrollcommand=repair_horizontal.set,
        )
        repair_table.rowconfigure(0, weight=1)
        repair_table.columnconfigure(0, weight=1)
        self._repair_tree.bind(
            "<<TreeviewSelect>>", lambda _event: self._repair_selected()
        )
        target_row = ttk.Frame(right)
        target_row.pack(fill="x")
        ttk.Label(target_row, text="Target (when required)").pack(anchor="w")
        self._target = tk.StringVar(value="")
        self._target_entry = ttk.Entry(target_row, textvariable=self._target)
        self._target_entry.pack(fill="x", pady=(2, 6))
        self._repair_detail = tk.StringVar(value="Select a repair.")
        ttk.Label(
            right, textvariable=self._repair_detail, wraplength=340,
        ).pack(anchor="w", fill="x")
        row = ttk.Frame(right)
        row.pack(fill="x", pady=(8, 0))
        self._preview_repair_button = ttk.Button(
            row, text="Preview", command=self._preview_repair
        )
        self._preview_repair_button.pack(side="left")
        self._run_repair_button = ttk.Button(
            row, text="Run verified repair", command=self._confirm_repair,
            state="disabled",
        )
        self._run_repair_button.pack(side="left", padx=5)
        self._secret = tk.StringVar(value="No one-time credential is being shown.")
        ttk.Label(
            right, textvariable=self._secret, wraplength=340,
        ).pack(anchor="w", fill="x", pady=(12, 0))

    def _build_cleanup_tab(self) -> None:
        tab = ttk.Frame(self._tabs, padding=8)
        self._tabs.add(tab, text="Storage cleanup")
        top = ttk.Frame(tab)
        top.pack(fill="x")
        ttk.Label(top, text="Mode").pack(side="left")
        self._cleanup_mode = tk.StringVar(value="QUARANTINE")
        ttk.Combobox(
            top, textvariable=self._cleanup_mode,
            values=("QUARANTINE", "RESTORE", "PURGE"),
            state="readonly", width=14,
        ).pack(side="left", padx=5)
        ttk.Button(
            top, text="Build dry run", command=self._preview_cleanup
        ).pack(side="left")
        self._apply_cleanup_button = ttk.Button(
            top, text="Apply selected preview", command=self._confirm_cleanup,
            state="disabled",
        )
        self._apply_cleanup_button.pack(side="left", padx=5)
        self._cleanup_summary = tk.StringVar(
            value="Dry run first. Nothing is selected or removed automatically."
        )
        ttk.Label(
            tab, textvariable=self._cleanup_summary, wraplength=800,
        ).pack(anchor="w", fill="x", pady=(6, 5))
        cleanup_table = ttk.Frame(tab)
        cleanup_table.pack(fill="both", expand=True)
        self._cleanup_tree = ttk.Treeview(
            cleanup_table, columns=("kind", "size", "reason"), show="tree headings",
            selectmode="extended", height=11,
        )
        for column, title, width in (
            ("#0", "Opaque target", 270), ("kind", "Kind", 170),
            ("size", "Estimated", 90), ("reason", "Evidence", 170),
        ):
            self._cleanup_tree.heading(column, text=title)
            self._cleanup_tree.column(column, width=width)
        self._cleanup_tree.grid(row=0, column=0, sticky="nsew")
        cleanup_vertical = ttk.Scrollbar(
            cleanup_table, orient="vertical", command=self._cleanup_tree.yview
        )
        cleanup_vertical.grid(row=0, column=1, sticky="ns")
        cleanup_horizontal = ttk.Scrollbar(
            cleanup_table, orient="horizontal", command=self._cleanup_tree.xview
        )
        cleanup_horizontal.grid(row=1, column=0, sticky="ew")
        self._cleanup_tree.configure(
            yscrollcommand=cleanup_vertical.set,
            xscrollcommand=cleanup_horizontal.set,
        )
        cleanup_table.rowconfigure(0, weight=1)
        cleanup_table.columnconfigure(0, weight=1)
        self._cleanup_accessible = tk.StringVar(
            value="No cleanup targets are selected."
        )
        ttk.Label(
            tab, textvariable=self._cleanup_accessible, wraplength=800,
        ).pack(anchor="w", fill="x", pady=(4, 0))
        self._cleanup_tree.bind(
            "<<TreeviewSelect>>", lambda _event: self._cleanup_selected()
        )

    def _build_undo_tab(self) -> None:
        tab = ttk.Frame(self._tabs, padding=8)
        self._tabs.add(tab, text="Undo")
        ttk.Label(
            tab,
            text=(
                "Only exact, still-verifiable inverses appear here. An Undo "
                "creates a child operation and never rewrites history."
            ),
            wraplength=800,
        ).pack(anchor="w", fill="x", pady=(0, 6))
        undo_table = ttk.Frame(tab)
        undo_table.pack(fill="both", expand=True)
        self._undo_tree = ttk.Treeview(
            undo_table, columns=("deadline", "source"), show="tree headings",
            selectmode="browse", height=10,
        )
        self._undo_tree.heading("#0", text="Available inverse")
        self._undo_tree.heading("deadline", text="Available until")
        self._undo_tree.heading("source", text="Source operation")
        self._undo_tree.column("#0", width=260)
        self._undo_tree.column("deadline", width=170)
        self._undo_tree.column("source", width=220)
        self._undo_tree.grid(row=0, column=0, sticky="nsew")
        undo_vertical = ttk.Scrollbar(
            undo_table, orient="vertical", command=self._undo_tree.yview
        )
        undo_vertical.grid(row=0, column=1, sticky="ns")
        undo_horizontal = ttk.Scrollbar(
            undo_table, orient="horizontal", command=self._undo_tree.xview
        )
        undo_horizontal.grid(row=1, column=0, sticky="ew")
        self._undo_tree.configure(
            yscrollcommand=undo_vertical.set,
            xscrollcommand=undo_horizontal.set,
        )
        undo_table.rowconfigure(0, weight=1)
        undo_table.columnconfigure(0, weight=1)
        self._undo_accessible = tk.StringVar(value="No Undo is selected.")
        ttk.Label(
            tab, textvariable=self._undo_accessible, wraplength=800,
        ).pack(anchor="w", fill="x", pady=(4, 0))
        self._undo_tree.bind(
            "<<TreeviewSelect>>", lambda _event: self._undo_selected()
        )
        buttons = ttk.Frame(tab)
        buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(
            buttons, text="Refresh available Undo", command=self._reload
        ).pack(side="left")
        ttk.Button(
            buttons, text="Preview selected", command=self._preview_undo
        ).pack(side="left", padx=5)
        self._run_undo_button = ttk.Button(
            buttons, text="Restore exact prior identity",
            command=self._confirm_undo, state="disabled",
        )
        self._run_undo_button.pack(side="left")

    def mount(self, parent=None):
        del parent
        self.pack(fill="both", expand=True)
        return self

    def enter(self, route_context=None) -> None:
        context = route_context if isinstance(route_context, dict) else {}
        section = str(context.get("section") or "")
        if section == "cleanup":
            self._tabs.select(1)
            self._preview_cleanup()
        elif section == "undo":
            self._tabs.select(2)

    def refresh(self, payload=None, *, force: bool = False) -> None:
        if self._disposed:
            return
        if payload is not None:
            self._apply_snapshot(payload)
            return
        if self._loaded and not force:
            self._expire_secret()
            return
        self.shell.request_observation(
            lambda: (
                self.application.repair_commands.list_actions(),
                self.application.undo.list(),
            ),
            self._apply_snapshot,
        )

    def _apply_snapshot(self, payload) -> None:
        if self._disposed:
            return
        actions, undo_rows = payload
        selected = self._repair_tree.selection()
        selected_id = selected[0] if selected else None
        for row in self._repair_tree.get_children():
            self._repair_tree.delete(row)
        self._actions = {}
        for action in actions[:32]:
            action_id = str(action["action_id"])
            self._actions[action_id] = action
            self._repair_tree.insert(
                "", "end", iid=action_id, text=str(action["title"]),
                values=(action["outcome"], action["privilege"]),
            )
        if selected_id in self._actions:
            self._repair_tree.selection_set(selected_id)
        elif self._actions:
            self._repair_tree.selection_set(next(iter(self._actions)))
        self._repair_selected()
        for row in self._undo_tree.get_children():
            self._undo_tree.delete(row)
        self._undo_rows = {}
        for item in undo_rows[:MAX_UNDO_CANDIDATES]:
            undo_id = str(item["undo_id"])
            self._undo_rows[undo_id] = item
            self._undo_tree.insert(
                "", "end", iid=undo_id, text=str(item["title"]),
                values=(
                    format_timestamp(item["deadline"]),
                    item["source_operation_id"],
                ),
            )
        if self._undo_rows:
            self._undo_tree.selection_set(next(iter(self._undo_rows)))
        self._undo_selected()
        self._loaded = True

    def _cleanup_selected(self) -> None:
        selected = tuple(str(item) for item in self._cleanup_tree.selection())
        if not selected:
            self._cleanup_accessible.set("No cleanup targets are selected.")
            return
        first = selected[0]
        suffix = "" if len(selected) == 1 else f" and {len(selected) - 1} more"
        self._cleanup_accessible.set(
            f"Selected cleanup target: {first}{suffix}. Review the exact dry run before applying."
        )

    def _undo_selected(self) -> None:
        selected = self._undo_tree.selection()
        item = self._undo_rows.get(str(selected[0])) if selected else None
        if item is None:
            self._undo_accessible.set("No Undo is selected.")
            return
        self._undo_accessible.set(
            f"Selected Undo: {item['title']} · available until "
            f"{format_timestamp(item['deadline'])} · source operation "
            f"{item['source_operation_id']}."
        )

    def _repair_selected(self) -> None:
        selected = self._repair_tree.selection()
        item = self._actions.get(selected[0]) if selected else None
        self._repair_preview = None
        self._run_repair_button.configure(state="disabled")
        if item is None:
            self._repair_detail.set("No repair selected.")
            return
        required = item.get("target_policy") == "REQUIRED"
        self._repair_detail.set(
            f"Availability: {'ready to preview' if item.get('available') else 'not currently available'}. "
            f"Expected time: {repair_duration_label(item.get('duration_class'))}. "
            f"{repair_reversibility_label(item.get('reversibility'), prior_state_survives=bool(item.get('prior_state_survives', True)))} "
            + ("Enter the required target identifier, then preview."
               if required else "Preview current evidence before running anything.")
        )
        self._target_entry.configure(state="normal" if required else "disabled")

    def _selected_action_id(self) -> str | None:
        selected = self._repair_tree.selection()
        return str(selected[0]) if selected else None

    def _preview_repair(self) -> None:
        action_id = self._selected_action_id()
        if not action_id:
            return
        target = self._target.get().strip() or None
        self.shell.request_observation(
            lambda: self.application.repair_commands.preview(action_id, target),
            self._apply_repair_preview,
        )

    def _apply_repair_preview(self, preview) -> None:
        self._repair_preview = preview
        self._run_repair_button.configure(
            state="normal" if preview.ready else "disabled"
        )
        self._repair_detail.set(
            "Ready for confirmation."
            if preview.ready else (
                f"{operation_reason_guidance(preview.reason_code).title}. "
                f"{operation_reason_guidance(preview.reason_code).next_step}"
            )
        )
        self.shell.drawer.show_details(
            "Repair preview", json.dumps(preview.to_dict(), indent=2, sort_keys=True)
        )

    def _confirm_repair(self) -> None:
        preview = self._repair_preview
        if preview is None or not preview.ready:
            return
        action = preview.action
        self.shell.drawer.show_confirmation(Confirmation(
            action.title,
            f"The app will run {len(action.mutation_steps)} reviewed step(s). "
            f"Expected time: {repair_duration_label(action.duration_class)}.",
            repair_reversibility_label(
                action.reversibility,
                prior_state_survives=action.prior_state_survives,
            ),
            "Run repair",
            destructive=action.destructive,
            typed_phrase=(preview.confirmation_token if action.destructive else None),
        ), self._run_repair)

    def _run_repair(self) -> None:
        preview = self._repair_preview
        if preview is None:
            return
        box: dict[str, Any] = {}

        def work() -> None:
            box["result"] = self.application.repair_commands.run(
                preview.action.action_id, preview.target_id,
                preview_digest=preview.preview_digest,
                confirmation_token=preview.confirmation_token,
            )

        def done() -> None:
            result = box["result"]
            self.shell.track_operation_id(result.operation_id)
            if result.one_time_secret:
                self._show_secret(result.one_time_secret)
            guidance = operation_reason_guidance(result.result_code)
            self.shell.notice_bar.show_notice(Notice(
                "success" if result.ok else "error",
                "Repair verified" if result.ok else "Repair needs attention",
                (
                    "The requested repair completed and its result was verified."
                    if result.ok else
                    f"{guidance.explanation} {guidance.next_step}"
                ),
                details=f"Stable repair result: {result.result_code}",
                dismissible=result.ok,
            ))
            if not result.ok:
                self.shell.drawer.show_details(
                    "Local support handoff",
                    json.dumps(result.to_dict()["support_handoff"], indent=2),
                )
            self._repair_preview = None
            self._loaded = False
            self.refresh(force=True)

        self.shell._work(work, done)

    def _preview_cleanup(self) -> None:
        selected = tuple(self._cleanup_tree.selection())
        self.shell.request_observation(
            lambda: self.application.storage_cleanup.preview(
                mode=self._cleanup_mode.get(), target_ids=selected or None
            ),
            self._apply_cleanup_preview,
        )

    def _apply_cleanup_preview(self, preview) -> None:
        self._cleanup_preview_value = preview
        for row in self._cleanup_tree.get_children():
            self._cleanup_tree.delete(row)
        selected_ids = {str(item["target_id"]) for item in preview.selected}
        for item in preview.candidates[:MAX_VISIBLE_CLEANUP]:
            target = str(item["target_id"])
            self._cleanup_tree.insert(
                "", "end", iid=target, text=target,
                values=(item["kind"], format_bytes(int(item["expected_bytes"])),
                        item["reason_code"]),
            )
            if target in selected_ids:
                self._cleanup_tree.selection_add(target)
        self._cleanup_summary.set(
            f"What will happen: {preview.mode.lower()} {len(preview.selected)} selected target(s), "
            f"covering about {format_bytes(preview.reclaimable_bytes)}. "
            + (
                "Can be undone until the displayed retention deadline. "
                if preview.mode in {"QUARANTINE", "RESTORE"} else
                "Cannot be undone after verified purge. "
            )
            + f"Preview expires {preview.expires_at}. Nothing runs until you confirm."
        )
        self._cleanup_selected()
        self._apply_cleanup_button.configure(
            state="normal" if preview.ready else "disabled"
        )
        self.shell.drawer.show_details(
            "Durable cleanup dry run",
            json.dumps(preview.to_dict(), indent=2, sort_keys=True),
        )

    def _confirm_cleanup(self) -> None:
        preview = self._cleanup_preview_value
        if preview is None or not preview.ready:
            return
        purge = preview.mode == "PURGE"
        self.shell.drawer.show_confirmation(Confirmation(
            f"Apply {preview.mode.lower()} to {len(preview.selected)} target(s)?",
            f"The exact preview covers {format_bytes(preview.reclaimable_bytes)}.",
            "Quarantine and restore are reversible until retention expires. "
            "Expired purge has no inverse.",
            "Apply cleanup",
            destructive=purge,
            typed_phrase=preview.confirmation_token if purge else None,
        ), self._run_cleanup)

    def _run_cleanup(self) -> None:
        preview = self._cleanup_preview_value
        if preview is None:
            return
        box: dict[str, Any] = {}

        def work() -> None:
            box["result"] = self.application.storage_cleanup.apply(
                mode=preview.mode,
                target_ids=tuple(item["target_id"] for item in preview.selected),
                preview_digest=preview.preview_digest,
                confirmation_token=preview.confirmation_token,
                requested_by="gui",
            )

        def done() -> None:
            result = box["result"]
            self.shell.track_operation_id(result.operation_id)
            guidance = operation_reason_guidance(result.result_code)
            self.shell.notice_bar.show_notice(Notice(
                "success" if result.ok else "error",
                "Cleanup verified" if result.ok else "Cleanup needs attention",
                (
                    "The selected cleanup completed and its retained/removed identity was verified."
                    if result.ok else
                    f"{guidance.explanation} {guidance.next_step}"
                ),
                details=f"Stable cleanup result: {result.result_code}",
                dismissible=result.ok,
            ))
            self._cleanup_preview_value = None
            self._apply_cleanup_button.configure(state="disabled")
            self._loaded = False
            self.refresh(force=True)

        self.shell._work(work, done)

    def _preview_undo(self) -> None:
        selected = self._undo_tree.selection()
        if not selected:
            return
        self.shell.request_observation(
            lambda: self.application.undo.preview(str(selected[0])),
            self._apply_undo_preview,
        )

    def _apply_undo_preview(self, preview) -> None:
        self._undo_preview_value = preview
        self._run_undo_button.configure(
            state="normal" if preview.ready else "disabled"
        )
        self.shell.drawer.show_details(
            "Undo preview", json.dumps(preview.to_dict(), indent=2, sort_keys=True)
        )

    def _confirm_undo(self) -> None:
        preview = self._undo_preview_value
        if preview is None or not preview.ready:
            return
        self.shell.drawer.show_confirmation(Confirmation(
            "Restore the retained item?",
            "The app will verify the retained item and move it back to its original managed location.",
            "The original action remains in Activity and a new receipt records this restoration.",
            "Restore",
        ), self._run_undo)

    def _run_undo(self) -> None:
        preview = self._undo_preview_value
        if preview is None:
            return
        box: dict[str, Any] = {}

        def work() -> None:
            box["result"] = self.application.undo.run(
                preview.undo_id,
                preview_digest=preview.preview_digest,
                confirmation_token=preview.confirmation_token,
            )

        def done() -> None:
            result = box["result"]
            self.shell.track_operation_id(result.operation_id)
            guidance = operation_reason_guidance(result.result_code)
            self.shell.notice_bar.show_notice(Notice(
                "success" if result.ok else "error",
                "Undo verified" if result.ok else "Undo refused",
                (
                    "The retained item was restored and verified."
                    if result.ok else
                    f"{guidance.explanation} {guidance.next_step}"
                ),
                details=f"Stable Undo result: {result.result_code}",
                dismissible=result.ok,
            ))
            self._undo_preview_value = None
            self._run_undo_button.configure(state="disabled")
            self._loaded = False
            self.refresh(force=True)

        self.shell._work(work, done)

    def _show_secret(self, secret: str) -> None:
        self._secret.set(
            "One-time API key (visible for 30 seconds): " + str(secret)
        )
        self._secret_deadline = time.monotonic() + SECRET_REVEAL_SECONDS

    def _expire_secret(self) -> None:
        if self._secret_deadline is None or time.monotonic() < self._secret_deadline:
            return
        self._secret.set("The one-time API key is no longer available.")
        self._secret_deadline = None

    def _reload(self) -> None:
        self._loaded = False
        self.refresh(force=True)

    def focus_primary(self) -> None:
        self._repair_tree.focus_set()

    def observation_failed(self, _error: BaseException) -> None:
        self.shell.notice_bar.show_notice(Notice(
            "warning", "Repair evidence is unavailable",
            "No repair or Undo availability was inferred. Refresh after resolving the evidence source.",
            dismissible=False,
        ))

    def leave(self) -> None:
        self._secret.set("No one-time credential is being shown.")
        self._secret_deadline = None

    def dispose(self) -> None:
        self.leave()
        self._disposed = True
        self._actions = {}
        self._undo_rows = {}


__all__ = ["MAX_VISIBLE_CLEANUP", "RepairPage"]
