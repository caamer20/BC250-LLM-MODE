"""Quick start, diagnostics, support export, and explicit advanced tools."""

from __future__ import annotations

from typing import Any

import tkinter as tk
from tkinter import filedialog, ttk

from .view_state import Notice


class HelpPage(ttk.Frame):
    def __init__(self, parent, shell, application) -> None:
        super().__init__(parent)
        self.shell = shell
        self.application = application
        self._disposed = False
        platform = application.platform.status()
        distribution = platform.get("distribution") or {}
        ttk.Label(self, text="Quick start", font=("TkDefaultFont", 16, "bold")).pack(anchor="w")
        ttk.Label(
            self,
            text=(
                "1. Choose a model in Models.  2. Install and start it.  "
                "3. Chat locally or enable the authenticated remote endpoint.\n\n"
                "LLM Mode is current-boot only. Restarting returns to the normal graphical desktop "
                "with the model service disabled."
            ),
            wraplength=720,
            justify="left",
        ).pack(anchor="w", fill="x", pady=(3, 10))
        platform_frame = ttk.LabelFrame(self, text="Platform", padding=7)
        platform_frame.pack(fill="x")
        ttk.Label(
            platform_frame,
            text=(
                f"{distribution.get('name') or distribution.get('id') or 'Unknown host'} · "
                f"integration {platform.get('integration_tier')} · "
                f"boot manager {platform.get('boot_manager')}"
            ),
            wraplength=720,
        ).pack(anchor="w")
        self.result_var = tk.StringVar(value="Run checks for a bounded diagnostic summary.")
        ttk.Label(self, textvariable=self.result_var, wraplength=720, justify="left").pack(anchor="w", fill="x", pady=8)
        actions = ttk.Frame(self)
        actions.pack(fill="x")
        self.check_button = ttk.Button(
            actions, text="Run checks", command=self._run_doctor
        )
        self.check_button.pack(side="left")
        ttk.Button(actions, text="Create redacted support bundle…", command=self._support_bundle).pack(side="left", padx=5)
        ttk.Button(actions, text="Copy diagnostic summary", command=self._copy_summary).pack(side="left")
        advanced = ttk.LabelFrame(self, text="Advanced tools", padding=7)
        advanced.pack(fill="x", pady=10)
        ttk.Button(advanced, text="Start terminal chat", command=self.application.open_chat_terminal).pack(side="left")
        ttk.Button(advanced, text="Open setup log", command=lambda: self.shell.open_logs("setup")).pack(side="left", padx=5)
        ttk.Button(advanced, text="Open server log", command=lambda: self.shell.open_logs("server")).pack(side="left")

    def _run_doctor(self) -> None:
        result_box: dict[str, Any] = {}

        def action() -> None:
            result_box["report"] = self.application.doctor.run()

        def done() -> None:
            report = result_box["report"]
            worst = ", ".join(report.worst_ids) if report.worst_ids else "none"
            self.result_var.set(
                f"Overall: {report.overall} · {len(report.findings)} checks · highest-priority findings: {worst}"
            )

        self.shell._work(action, done)

    def _support_bundle(self) -> None:
        destination = filedialog.askdirectory(
            title="Choose a folder for the redacted support bundle",
            mustexist=False,
        )
        if not destination:
            return
        result_box: dict[str, Any] = {}

        def action() -> None:
            result_box["manifest"] = self.application.support_bundle.build(destination)

        def done() -> None:
            manifest = result_box["manifest"]
            self.shell.notice_bar.show_notice(Notice(
                "success", "Support bundle created",
                f"Redacted bundle created with digest {manifest.bundle_sha256[:12]}…",
            ))

        self.shell._work(action, done)

    def _copy_summary(self) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(self.result_var.get()[:4096])
        except Exception:
            pass

    def mount(self, parent=None):
        del parent
        self.pack(fill="both", expand=True)
        return self

    def enter(self, route_context=None) -> None:
        del route_context

    def refresh(self, snapshot=None) -> None:
        del snapshot

    def focus_primary(self) -> None:
        self.check_button.focus_set()

    def leave(self) -> None:
        return None

    def dispose(self) -> None:
        self._disposed = True


__all__ = ["HelpPage"]
