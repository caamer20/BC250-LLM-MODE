"""Quick start, diagnostics, support export, and explicit advanced tools."""

from __future__ import annotations

from typing import Any

import tkinter as tk
from tkinter import filedialog, ttk

from ..client_compatibility import (
    CLIENT_COMPATIBILITY_SCHEMA_VERSION,
    capability_display_rows,
)
from ..message_catalog import glossary_entries
from .routes import Route
from .view_state import Notice
from .widgets import VerticalScrollFrame


class HelpPage(ttk.Frame):
    def __init__(self, parent, shell, application) -> None:
        super().__init__(parent)
        self.shell = shell
        self.application = application
        self._disposed = False
        self._scroller = VerticalScrollFrame(self)
        self._scroller.pack(fill="both", expand=True)
        body = self._scroller.inner
        platform = application.platform.status()
        distribution = platform.get("distribution") or {}
        ttk.Label(body, text="Quick start", font=("TkDefaultFont", 16, "bold")).pack(anchor="w")
        ttk.Label(
            body,
            text=(
                "1. Open Models and choose a recommended model.  "
                "2. Select Install, Start and Chat.  "
                "3. Send a prompt in native Chat.\n\n"
                "Use Connections only when you want Open WebUI, a phone, or another "
                "OpenAI-compatible app. The guided setup provides the exact Base URL, "
                "model name, and a separate one-time key."
            ),
            wraplength=720,
            justify="left",
        ).pack(anchor="w", fill="x", pady=(3, 10))
        platform_frame = ttk.LabelFrame(body, text="Platform", padding=7)
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
        compatibility = ttk.LabelFrame(
            body,
            text=f"Offline model API compatibility · v{CLIENT_COMPATIBILITY_SCHEMA_VERSION}",
            padding=7,
        )
        compatibility.pack(fill="x", pady=(8, 0))
        compatibility_text = "\n".join(
            f"{status.upper()} · {endpoint} — {summary}"
            for endpoint, status, summary in capability_display_rows()
        )
        ttk.Label(
            compatibility, text=compatibility_text,
            wraplength=720, justify="left",
        ).pack(anchor="w", fill="x")
        ttk.Label(
            compatibility,
            text=(
                "Use the displayed /v1 Base URL and a separate named key. "
                "Open WebUI /api is a browser route, not a model API."
            ),
            wraplength=720, justify="left",
        ).pack(anchor="w", fill="x", pady=(3, 0))
        self.result_var = tk.StringVar(value="Run checks for a bounded diagnostic summary.")
        ttk.Label(body, textvariable=self.result_var, wraplength=720, justify="left").pack(anchor="w", fill="x", pady=8)
        actions = ttk.Frame(body)
        actions.pack(fill="x")
        self.check_button = ttk.Button(
            actions, text="Run checks", command=self._run_doctor
        )
        self.check_button.pack(side="left")
        ttk.Button(actions, text="Create redacted support bundle…", command=self._support_bundle).pack(side="left", padx=5)
        ttk.Button(actions, text="Copy diagnostic summary", command=self._copy_summary).pack(side="left")
        ttk.Button(
            actions, text="Open Connections",
            command=lambda: self.shell.navigate(Route.CONNECTIONS),
        ).pack(side="left", padx=5)

        troubleshooting = ttk.LabelFrame(
            body, text="Something failed", padding=7
        )
        troubleshooting.pack(fill="x", pady=(8, 0))
        ttk.Label(
            troubleshooting,
            text=(
                "No response: open System and verify the selected model.  "
                "401: replace the client API key.  403: check key permission and use "
                "the Base URL ending once in /v1.  502: the private address works, "
                "but the model backend needs to be started or repaired."
            ),
            wraplength=720, justify="left",
        ).pack(anchor="w", fill="x")
        quick_actions = ttk.Frame(troubleshooting)
        quick_actions.pack(fill="x", pady=(5, 0))
        ttk.Button(
            quick_actions, text="Open Chat",
            command=lambda: self.shell.navigate(Route.CHAT),
        ).pack(side="left")
        ttk.Button(
            quick_actions, text="Open Models",
            command=lambda: self.shell.navigate(Route.MODELS),
        ).pack(side="left", padx=5)
        ttk.Button(
            quick_actions, text="View Activity",
            command=lambda: self.shell.navigate(Route.ACTIVITY),
        ).pack(side="left")
        advanced = ttk.LabelFrame(body, text="Advanced tools", padding=7)
        advanced.pack(fill="x", pady=10)
        advanced_actions = ttk.Frame(advanced)
        advanced_actions.pack(fill="x")
        ttk.Button(advanced_actions, text="Start terminal chat", command=self.application.open_chat_terminal).pack(side="left")
        ttk.Button(advanced_actions, text="Open setup log", command=lambda: self.shell.open_logs("setup")).pack(side="left", padx=5)
        ttk.Button(advanced_actions, text="Open server log", command=lambda: self.shell.open_logs("server")).pack(side="left")
        ttk.Label(
            advanced,
            text=(
                "LLM Mode is an advanced current-boot serving mode. It may close the "
                "graphical desktop; restarting returns to the normal desktop and leaves "
                "model auto-start off."
            ),
            wraplength=700, justify="left",
        ).pack(anchor="w", fill="x", pady=(6, 0))

        glossary = ttk.LabelFrame(body, text="Offline glossary", padding=7)
        glossary.pack(fill="both", expand=True, pady=(0, 8))
        controls = ttk.Frame(glossary)
        controls.pack(fill="x")
        ttk.Label(controls, text="Find a term").pack(side="left")
        self.glossary_query = tk.StringVar(value="")
        self.glossary_search = ttk.Entry(
            controls, textvariable=self.glossary_query, width=28
        )
        self.glossary_search.pack(side="left", padx=6)
        self.glossary_count = tk.StringVar(value="")
        ttk.Label(controls, textvariable=self.glossary_count).pack(side="left")
        self.glossary_terms = tk.Listbox(
            glossary, height=6, exportselection=False
        )
        self.glossary_terms.pack(fill="x", pady=(5, 3))
        self.glossary_detail = tk.StringVar(value="Select a term for its definition.")
        ttk.Label(
            glossary, textvariable=self.glossary_detail, wraplength=720,
            justify="left",
        ).pack(anchor="w", fill="x")
        self._glossary_rows = ()
        self.glossary_query.trace_add("write", self._render_glossary)
        self.glossary_terms.bind("<<ListboxSelect>>", self._select_glossary)
        self._render_glossary()

        accessibility = ttk.LabelFrame(
            body, text="Keyboard and accessibility", padding=7
        )
        accessibility.pack(fill="x", pady=(0, 8))
        ttk.Label(
            accessibility,
            text=(
                "Ctrl+1 through Ctrl+9 opens primary pages. Ctrl+K opens the "
                "local command palette, Ctrl+F focuses the current page's "
                "primary control, Ctrl+L opens bounded logs, and Escape closes "
                "the in-window drawer. Settings offers 100–200% interface "
                "scaling and reduced motion. Status is always written as text, "
                "not conveyed by color alone."
            ),
            wraplength=720, justify="left",
        ).pack(anchor="w", fill="x")
        ttk.Label(
            accessibility,
            text=(
                "Known limitation: Tk table and screen-reader announcements "
                "vary across desktop environments. Important tables therefore "
                "repeat the selected row in adjacent text or a Details view. "
                "Screen-reader parity remains pending physical Bazzite and "
                "CachyOS qualification."
            ),
            wraplength=720, justify="left",
        ).pack(anchor="w", fill="x", pady=(4, 0))

    def _render_glossary(self, *_args) -> None:
        rows = glossary_entries(self.glossary_query.get())
        self._glossary_rows = rows
        self.glossary_terms.delete(0, "end")
        for entry in rows:
            self.glossary_terms.insert("end", entry.term)
        self.glossary_count.set(f"{len(rows)} local terms")
        if rows:
            self.glossary_terms.selection_set(0)
            self.glossary_detail.set(f"{rows[0].term}: {rows[0].definition}")
        else:
            self.glossary_detail.set("No local glossary terms match that search.")

    def _select_glossary(self, _event=None) -> None:
        selection = self.glossary_terms.curselection()
        if not selection:
            return
        index = int(selection[0])
        if 0 <= index < len(self._glossary_rows):
            entry = self._glossary_rows[index]
            self.glossary_detail.set(f"{entry.term}: {entry.definition}")

    def _run_doctor(self) -> None:
        result_box: dict[str, Any] = {}

        def action() -> None:
            result_box["report"] = self.application.doctor.run()

        def done() -> None:
            report = result_box["report"]
            worst = [
                finding for finding in report.findings
                if finding.id in set(report.worst_ids)
            ]
            if report.overall == "PASS":
                summary = "All bounded checks passed."
            elif worst:
                finding = worst[0]
                summary = f"{finding.title}. {finding.evidence}"
            else:
                summary = "Checks completed, but no single next action was identified."
            self.result_var.set(
                f"Overall: {report.overall.title()} · {len(report.findings)} checks. {summary}"
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
