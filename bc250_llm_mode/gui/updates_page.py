"""Single-window signed application Updates page (ADR 013)."""

from __future__ import annotations

import json
from typing import Any

import tkinter as tk
from tkinter import ttk

from ..application_update import UpdateOutcome
from ..presentation import format_bytes
from .view_state import Confirmation, Notice


class UpdatesPage(ttk.Frame):
    def __init__(self, parent, shell, application) -> None:
        super().__init__(parent)
        self.shell = shell
        self.application = application
        self._disposed = False
        self._loaded = False
        self._preview = None
        self._rollback_preview = None
        self._candidate_version = tk.StringVar(value="")

        ttk.Label(
            self, text="Application updates", font=("TkDefaultFont", 15, "bold")
        ).pack(anchor="w")
        ttk.Label(
            self,
            text=(
                "Only evaluator-eligible signed releases can appear here. "
                "Checks, imports, updates, and rollback are always explicit."
            ),
            wraplength=800,
        ).pack(anchor="w", fill="x", pady=(2, 8))

        status_box = ttk.LabelFrame(self, text="Installed application", padding=8)
        status_box.pack(fill="x")
        self._installed = tk.StringVar(value="Observing installed application…")
        self._channel = tk.StringVar(value="")
        ttk.Label(status_box, textvariable=self._installed, wraplength=800).pack(
            anchor="w", fill="x"
        )
        ttk.Label(status_box, textvariable=self._channel, wraplength=800).pack(
            anchor="w", fill="x", pady=(3, 0)
        )

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(8, 5))
        ttk.Button(actions, text="Check signed channel", command=self._check).pack(
            side="left"
        )
        ttk.Label(actions, text="Version").pack(side="left", padx=(12, 4))
        self._version_entry = ttk.Entry(
            actions, textvariable=self._candidate_version, width=18
        )
        self._version_entry.pack(side="left")
        ttk.Button(actions, text="Preview", command=self._preview_update).pack(
            side="left", padx=5
        )
        self._apply_button = ttk.Button(
            actions, text="Apply verified update", command=self._confirm_apply,
            state="disabled",
        )
        self._apply_button.pack(side="left")

        import_box = ttk.LabelFrame(self, text="Offline signed bundle", padding=8)
        import_box.pack(fill="x", pady=(4, 5))
        self._bundle_path = tk.StringVar(value="")
        ttk.Label(
            import_box,
            text="Enter the existing archive path. The archive is not trusted by location.",
        ).pack(anchor="w")
        row = ttk.Frame(import_box)
        row.pack(fill="x", pady=(4, 0))
        ttk.Entry(row, textvariable=self._bundle_path).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(row, text="Verify and import", command=self._import).pack(
            side="left", padx=(5, 0)
        )

        detail = ttk.Panedwindow(self, orient="horizontal")
        detail.pack(fill="both", expand=True, pady=(4, 0))
        plan = ttk.LabelFrame(detail, text="Plan", padding=8)
        notes = ttk.LabelFrame(detail, text="Signed release notes · plain text", padding=8)
        detail.add(plan, weight=2)
        detail.add(notes, weight=3)
        self._plan = tk.StringVar(value="Select Check or enter a version to preview.")
        ttk.Label(plan, textvariable=self._plan, wraplength=320).pack(
            anchor="w", fill="x"
        )
        lower = ttk.Frame(plan)
        lower.pack(fill="x", pady=(10, 0))
        ttk.Button(lower, text="Preview rollback", command=self._preview_rollback).pack(
            side="left"
        )
        self._rollback_button = ttk.Button(
            lower, text="Roll back", command=self._confirm_rollback,
            state="disabled",
        )
        self._rollback_button.pack(side="left", padx=5)
        ttk.Button(lower, text="Cleanup dry run", command=self._cleanup).pack(
            side="left"
        )
        self._notes = tk.Text(notes, height=10, wrap="word", state="disabled")
        self._notes.pack(fill="both", expand=True)
        self.refresh(force=True)

    def mount(self, parent=None):
        del parent
        self.pack(fill="both", expand=True)
        return self

    def enter(self, route_context=None) -> None:
        context = route_context if isinstance(route_context, dict) else {}
        version = context.get("version")
        if isinstance(version, str):
            self._candidate_version.set(version)

    def refresh(self, payload=None, *, force: bool = False) -> None:
        if self._disposed:
            return
        if payload is not None:
            self._apply_status(payload)
            return
        if self._loaded and not force:
            return
        # Refresh observes local status only. It never checks a channel.
        self.shell.request_observation(
            self.application.application_update_commands.status,
            self._apply_status,
        )

    def _apply_status(self, status) -> None:
        installed = status.installed
        digest = installed.release_set_digest
        identity = digest[:12] + "…" if digest else "unverified legacy install"
        self._installed.set(
            f"Version {installed.version} · {identity} · schema "
            f"{installed.database_schema} · generation {installed.pointer_generation}"
        )
        if installed.recovery_barrier:
            channel = f"Recovery barrier: {installed.recovery_barrier}"
        elif status.channel_available:
            channel = "Signed update source is available. It is checked only on request."
        else:
            channel = (
                "Signed updates are unavailable in this build. No untrusted pip, "
                "branch, URL, or local wheel fallback will be offered."
            )
        self._channel.set(channel)
        self._loaded = True

    def _check(self) -> None:
        self.shell.request_observation(
            self.application.application_update_commands.check,
            self._apply_check,
        )

    def _apply_check(self, check) -> None:
        if check.release is None:
            self.shell.notice_bar.show_notice(Notice(
                "warning", "No eligible signed update",
                check.reason_code.value, dismissible=True,
            ))
            return
        self._candidate_version.set(check.release.version)
        self._set_notes(check.release.notes)
        self._plan.set(
            f"Verified {check.release.source_ref} · "
            f"{format_bytes(check.release.total_bytes)} signed release set. "
            "Preview before applying."
        )

    def _preview_update(self) -> None:
        version = self._candidate_version.get().strip()
        if not version:
            return
        self.shell.request_observation(
            lambda: self.application.application_update_commands.preview(version),
            self._apply_preview,
        )

    def _apply_preview(self, preview) -> None:
        self._preview = preview
        ready = preview.outcome is UpdateOutcome.READY
        self._apply_button.configure(state="normal" if ready else "disabled")
        if ready:
            self._set_notes(preview.release_notes_plain_text)
            self._plan.set(
                f"Requires {format_bytes(preview.required_free_bytes)}; "
                f"{format_bytes(preview.available_free_bytes)} available. "
                f"Profile backup: yes. Restart into new slot: yes. "
                f"Rollback slot: {preview.rollback_installation_id or 'not established'}."
            )
        else:
            self._plan.set(f"Unavailable: {preview.reason_code.value}")
        self.shell.drawer.show_details(
            "Signed update preview", json.dumps(preview.to_dict(), indent=2, sort_keys=True)
        )

    def _confirm_apply(self) -> None:
        preview = self._preview
        if preview is None or preview.outcome is not UpdateOutcome.READY:
            return
        self.shell.drawer.show_confirmation(Confirmation(
            f"Install verified version {preview.version}?",
            "A verified profile backup is created, a new immutable slot is staged, and the application restarts into it.",
            "The exact current slot remains as previous; failure restores pointers and profile evidence or stops for recovery.",
            "Install update",
            typed_phrase=preview.confirmation_token,
        ), self._apply_update)

    def _apply_update(self) -> None:
        preview = self._preview
        if preview is None:
            return
        self._run_mutation(
            lambda: self.application.application_update_commands.apply(
                preview.version,
                preview_digest=preview.preview_digest,
                confirmation_token=preview.confirmation_token,
                requested_by="gui",
            ),
            success="Application update verified",
        )

    def _import(self) -> None:
        path = self._bundle_path.get().strip()
        if not path:
            return
        self._run_mutation(
            lambda: self.application.application_update_commands.import_bundle(path),
            success="Signed bundle imported",
        )

    def _preview_rollback(self) -> None:
        self.shell.request_observation(
            self.application.application_update_commands.rollback_preview,
            self._apply_rollback_preview,
        )

    def _apply_rollback_preview(self, preview) -> None:
        self._rollback_preview = preview
        ready = preview.outcome is UpdateOutcome.READY
        self._rollback_button.configure(state="normal" if ready else "disabled")
        self._plan.set(
            (f"Rollback to verified {preview.version}; profile restore is required."
             if ready else f"Rollback unavailable: {preview.reason_code.value}")
        )
        self.shell.drawer.show_details(
            "Application rollback preview",
            json.dumps(preview.to_dict(), indent=2, sort_keys=True),
        )

    def _confirm_rollback(self) -> None:
        preview = self._rollback_preview
        if preview is None or preview.outcome is not UpdateOutcome.READY:
            return
        self.shell.drawer.show_confirmation(Confirmation(
            f"Roll back to verified version {preview.version}?",
            "The prior immutable slot becomes current and profile compatibility is re-verified.",
            "A fresh verified backup is created first; ambiguity stops in recovery instead of guessing.",
            "Roll back",
            typed_phrase=preview.confirmation_token,
        ), self._run_rollback)

    def _run_rollback(self) -> None:
        preview = self._rollback_preview
        if preview is None:
            return
        self._run_mutation(
            lambda: self.application.application_update_commands.rollback(
                preview_digest=preview.preview_digest,
                confirmation_token=preview.confirmation_token,
                requested_by="gui",
            ),
            success="Application rollback verified",
        )

    def _cleanup(self) -> None:
        self.shell.request_observation(
            self.application.application_update_commands.cleanup_preview,
            lambda value: self.shell.drawer.show_details(
                "Application update cleanup dry run",
                json.dumps(value.to_dict(), indent=2, sort_keys=True),
            ),
        )

    def _run_mutation(self, action, *, success: str) -> None:
        box: dict[str, Any] = {}

        def work() -> None:
            box["result"] = action()

        def done() -> None:
            result = box["result"]
            self.shell.track_operation_id(getattr(result, "operation_id", None))
            self.shell.notice_bar.show_notice(Notice(
                "success" if result.ok else "warning",
                success if result.ok else "Update action refused",
                str(getattr(result, "reason_code", "UNKNOWN")),
                dismissible=True,
            ))
            payload = result.to_dict()
            release = payload.get("release")
            if isinstance(release, dict) and isinstance(release.get("version"), str):
                self._candidate_version.set(release["version"])
            self.shell.drawer.show_details(
                "Application update result", json.dumps(payload, indent=2, sort_keys=True)
            )
            self._loaded = False
            self.refresh(force=True)

        self.shell._work(work, done)

    def _set_notes(self, value: str) -> None:
        self._notes.configure(state="normal")
        self._notes.delete("1.0", "end")
        self._notes.insert("1.0", str(value)[:32768])
        self._notes.configure(state="disabled")

    def focus_primary(self) -> None:
        self._version_entry.focus_set()

    def observation_failed(self, _error: BaseException) -> None:
        self.shell.notice_bar.show_notice(Notice(
            "error", "Update evidence unavailable",
            "No release or rollback eligibility was inferred.",
            dismissible=False,
        ))

    def dispose(self) -> None:
        self._disposed = True
        self._preview = None
        self._rollback_preview = None


__all__ = ["UpdatesPage"]
