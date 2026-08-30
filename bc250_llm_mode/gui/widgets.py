"""Small reusable widgets owned by the unified application shell."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..command_palette import PaletteCommand, match_palette
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
        self._log_search = tk.StringVar(value="")
        self._palette_query = tk.StringVar(value="")

    def clear(self) -> None:
        for child in self._content.winfo_children():
            child.destroy()
        self._confirm = None
        self._typed.set("")
        self._log_search.set("")
        self._palette_query.set("")
        self.pack_forget()

    def show_palette(
        self, commands: tuple[PaletteCommand, ...], on_open,
    ) -> None:
        """Show a bounded local navigator; it never executes an action."""
        self.clear()
        ttk.Label(
            self._content, text="Command palette", style="DrawerTitle.TLabel"
        ).pack(anchor="w")
        ttk.Label(
            self._content,
            text=(
                "Search local pages and previews. Protected actions open their "
                "normal preview; nothing runs from this palette."
            ),
            wraplength=760,
        ).pack(anchor="w", fill="x")
        search = ttk.Entry(self._content, textvariable=self._palette_query)
        search.pack(fill="x", pady=(5, 4))
        results = tk.Listbox(self._content, height=8, exportselection=False)
        results.pack(fill="x")
        detail = tk.StringVar(value="Type to search local commands.")
        ttk.Label(
            self._content, textvariable=detail, wraplength=760, justify="left",
        ).pack(anchor="w", fill="x", pady=(4, 2))
        visible: list[PaletteCommand] = []
        row = ttk.Frame(self._content)
        row.pack(fill="x", pady=(4, 0))
        open_button = ttk.Button(row, text="Open", state="disabled")
        open_button.pack(side="right", padx=5)
        ttk.Button(row, text="Close", command=self.clear).pack(side="right")

        def selected() -> PaletteCommand | None:
            selection = results.curselection()
            if not selection:
                return None
            index = int(selection[0])
            return visible[index] if 0 <= index < len(visible) else None

        def update_selection(_event=None) -> None:
            command = selected()
            if command is None:
                detail.set("No local command selected.")
                open_button.configure(state="disabled")
                return
            suffix = (
                f" Blocked: {command.blocked_reason}"
                if not command.enabled else
                (" Opens the normal preview page." if command.protected else "")
            )
            detail.set(command.description + suffix)
            open_button.configure(state="normal" if command.enabled else "disabled")

        def render(*_args) -> None:
            visible[:] = match_palette(commands, self._palette_query.get())
            results.delete(0, "end")
            for command in visible:
                prefix = "Blocked · " if not command.enabled else ""
                results.insert("end", prefix + command.label)
            if visible:
                results.selection_set(0)
            update_selection()

        def open_selected(_event=None):
            command = selected()
            if command is None or not command.enabled:
                return "break"
            self.clear()
            on_open(command)
            return "break"

        open_button.configure(command=open_selected)
        results.bind("<<ListboxSelect>>", update_selection)
        results.bind("<Return>", open_selected)
        search.bind("<Return>", open_selected)
        self._palette_query.trace_add("write", render)
        render()
        self.pack(fill="x")
        search.focus_set()

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
        bounded = _bounded_log(lines)
        controls = ttk.Frame(self._content)
        controls.pack(fill="x", pady=(3, 5))
        ttk.Label(controls, text="Search loaded lines").pack(side="left")
        search = ttk.Entry(controls, textvariable=self._log_search, width=28)
        search.pack(side="left", padx=(5, 8))
        count_var = tk.StringVar(value=f"{len(bounded)} lines · snapshot")
        ttk.Label(controls, textvariable=count_var).pack(side="left")
        frame = ttk.Frame(self._content)
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, height=12, wrap="word", state="normal")
        text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        scroll.pack(side="right", fill="y")
        text.configure(yscrollcommand=scroll.set)

        def render(*_args) -> None:
            needle = self._log_search.get().strip().casefold()
            visible = (
                [line for line in bounded if needle in line.casefold()]
                if needle else bounded
            )
            text.configure(state="normal")
            text.delete("1.0", "end")
            text.insert("1.0", "\n".join(visible))
            text.configure(state="disabled")
            count_var.set(f"{len(visible)} of {len(bounded)} lines · snapshot")

        self._log_search.trace_add("write", render)
        render()
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
