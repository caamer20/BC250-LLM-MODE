"""Small reusable widgets owned by the unified application shell."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .view_state import Confirmation, Notice

MAX_LOG_LINES = 2000
MAX_LOG_BYTES = 2 * 1024 * 1024


class NoticeBar(ttk.Frame):
    def __init__(self, parent) -> None:
        super().__init__(parent, padding=(8, 6))
        self._title = tk.StringVar(value="")
        self._message = tk.StringVar(value="")
        ttk.Label(self, textvariable=self._title, style="NoticeTitle.TLabel").pack(anchor="w")
        ttk.Label(self, textvariable=self._message, wraplength=760).pack(anchor="w", fill="x")
        self._dismiss = ttk.Button(self, text="Dismiss", command=self.hide)

    def show_notice(self, notice: Notice) -> None:
        self._title.set(notice.title)
        self._message.set(notice.message)
        if notice.dismissible:
            self._dismiss.pack(side="right")
        else:
            self._dismiss.pack_forget()
        self.pack(fill="x", pady=(0, 6))

    def hide(self) -> None:
        self.pack_forget()


class BottomDrawer(ttk.Frame):
    def __init__(self, parent) -> None:
        super().__init__(parent, padding=8)
        self._content = ttk.Frame(self)
        self._content.pack(fill="both", expand=True)
        self._confirm = None
        self._typed = tk.StringVar(value="")

    def clear(self) -> None:
        for child in self._content.winfo_children():
            child.destroy()
        self._confirm = None
        self._typed.set("")
        self.pack_forget()

    def show_confirmation(self, confirmation: Confirmation, on_confirm) -> None:
        self.clear()
        self._confirm = confirmation
        ttk.Label(self._content, text=confirmation.title, style="DrawerTitle.TLabel").pack(anchor="w")
        ttk.Label(self._content, text=confirmation.consequence, wraplength=760).pack(anchor="w")
        ttk.Label(self._content, text=f"Recovery: {confirmation.recovery}", wraplength=760).pack(anchor="w")
        if confirmation.typed_phrase:
            ttk.Label(self._content, text=f'Type "{confirmation.typed_phrase}" to continue:').pack(anchor="w")
            ttk.Entry(self._content, textvariable=self._typed).pack(fill="x")
        row = ttk.Frame(self._content)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="Cancel", command=self.clear).pack(side="right")

        def confirm() -> None:
            if confirmation.typed_phrase and self._typed.get() != confirmation.typed_phrase:
                return
            self.clear()
            on_confirm()

        ttk.Button(row, text=confirmation.confirm_label, command=confirm).pack(side="right", padx=6)
        self.pack(fill="x")

    def show_log(self, title: str, lines: list[str]) -> None:
        self.clear()
        ttk.Label(self._content, text=title, style="DrawerTitle.TLabel").pack(anchor="w")
        frame = ttk.Frame(self._content)
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, height=12, wrap="word", state="normal")
        bounded = _bounded_log(lines)
        text.insert("1.0", "\n".join(bounded))
        text.configure(state="disabled")
        text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        scroll.pack(side="right", fill="y")
        text.configure(yscrollcommand=scroll.set)
        ttk.Button(self._content, text="Close", command=self.clear).pack(anchor="e", pady=(5, 0))
        self.pack(fill="both")

    def show_details(self, title: str, detail: str) -> None:
        """Show one bounded read-only technical record in the shared drawer."""
        self.clear()
        ttk.Label(self._content, text=title, style="DrawerTitle.TLabel").pack(anchor="w")
        frame = ttk.Frame(self._content)
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, height=14, wrap="word", state="normal")
        text.insert("1.0", str(detail)[:8192])
        text.configure(state="disabled")
        text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        scroll.pack(side="right", fill="y")
        text.configure(yscrollcommand=scroll.set)
        ttk.Button(self._content, text="Close", command=self.clear).pack(anchor="e", pady=(5, 0))
        self.pack(fill="both")


def _bounded_log(lines: list[str]) -> list[str]:
    retained: list[str] = []
    size = 0
    for line in reversed(lines[-MAX_LOG_LINES:]):
        encoded = str(line).encode("utf-8", errors="replace")
        if size + len(encoded) > MAX_LOG_BYTES:
            break
        retained.append(str(line))
        size += len(encoded)
    retained.reverse()
    return retained
