"""Small reusable widgets owned by the unified application shell."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..command_palette import PaletteCommand, match_palette
from .view_state import Confirmation, Notice

MAX_LOG_LINES = 2000
MAX_LOG_BYTES = 2 * 1024 * 1024


class VerticalScrollFrame(ttk.Frame):
    """One lightweight scroll owner for long, mostly read-only pages."""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.canvas = tk.Canvas(
            self, borderwidth=0, highlightthickness=1, takefocus=True
        )
        self.scrollbar = ttk.Scrollbar(
            self, orient="vertical", command=self.canvas.yview
        )
        self.inner = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window(
            (0, 0), window=self.inner, anchor="nw"
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.inner.bind("<Configure>", self._content_changed)
        self.canvas.bind("<Configure>", self._canvas_changed)
        self.canvas.bind("<Up>", lambda _event: self._scroll_units(-1))
        self.canvas.bind("<Down>", lambda _event: self._scroll_units(1))
        self.canvas.bind("<Prior>", lambda _event: self._scroll_pages(-1))
        self.canvas.bind("<Next>", lambda _event: self._scroll_pages(1))
        self.canvas.bind("<Home>", lambda _event: self._scroll_to(0.0))
        self.canvas.bind("<End>", lambda _event: self._scroll_to(1.0))
        self.canvas.bind("<MouseWheel>", self._mousewheel)
        self.canvas.bind("<Button-4>", lambda _event: self._scroll_units(-3))
        self.canvas.bind("<Button-5>", lambda _event: self._scroll_units(3))
        self._scroll_tag = "BC250Scroll" + str(id(self))
        self.bind_class(self._scroll_tag, "<MouseWheel>", self._child_wheel)
        self.bind_class(self._scroll_tag, "<Button-4>", self._child_wheel)
        self.bind_class(self._scroll_tag, "<Button-5>", self._child_wheel)
        self.bind_class(self._scroll_tag, "<FocusIn>", self._reveal_focus)
        self.bind("<Destroy>", self._remove_scroll_bindings, add="+")

    def _content_changed(self, _event=None) -> None:
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
            pending = list(self.inner.winfo_children())
            count = 0
            while pending and count < 1000:
                widget = pending.pop()
                count += 1
                tags = tuple(widget.bindtags())
                if self._scroll_tag not in tags:
                    widget.bindtags(tags[:2] + (self._scroll_tag,) + tags[2:])
                pending.extend(widget.winfo_children())
        except Exception:
            pass

    def _canvas_changed(self, event) -> None:
        try:
            self.canvas.itemconfigure(self._window, width=int(event.width))
        except (AttributeError, TypeError, ValueError):
            pass

    def _child_wheel(self, event):
        if event.widget.winfo_class() in {"Text", "Listbox", "Treeview"}:
            return None
        if getattr(event, "num", None) in (4, 5):
            return self._scroll_units(-3 if event.num == 4 else 3)
        return self._mousewheel(event)

    def _reveal_focus(self, event):
        try:
            top = event.widget.winfo_rooty() - self.inner.winfo_rooty()
            bottom = top + event.widget.winfo_height()
            visible = self.canvas.canvasy(0)
            height = self.canvas.winfo_height()
            content = max(1, self.inner.winfo_height())
            if top < visible:
                self.canvas.yview_moveto(max(0, top - 8) / content)
            elif bottom > visible + height:
                self.canvas.yview_moveto(max(0, bottom - height + 8) / content)
        except (AttributeError, TypeError, ValueError, tk.TclError):
            pass

    def _remove_scroll_bindings(self, event):
        if event.widget is self:
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>", "<FocusIn>"):
                self.unbind_class(self._scroll_tag, sequence)

    def _scroll_units(self, units: int):
        try:
            self.canvas.yview_scroll(int(units), "units")
        except Exception:
            pass
        return "break"

    def _scroll_pages(self, pages: int):
        try:
            self.canvas.yview_scroll(int(pages), "pages")
        except Exception:
            pass
        return "break"

    def _scroll_to(self, fraction: float):
        try:
            self.canvas.yview_moveto(float(fraction))
        except Exception:
            pass
        return "break"

    def _mousewheel(self, event):
        delta = int(getattr(event, "delta", 0) or 0)
        if not delta:
            return None
        # macOS supplies small deltas while Windows commonly supplies 120.
        units = -1 if delta > 0 else 1
        if abs(delta) >= 120:
            units *= max(1, abs(delta) // 120)
        return self._scroll_units(units)


class NoticeBar(ttk.Frame):
    def __init__(self, parent, *, on_route=None, on_details=None) -> None:
        super().__init__(parent, padding=(8, 6), takefocus=True)
        self._on_route = on_route
        self._on_details = on_details
        self._notice: Notice | None = None
        self._title = tk.StringVar(value="")
        self._message = tk.StringVar(value="")
        ttk.Label(self, textvariable=self._title, style="NoticeTitle.TLabel").pack(anchor="w")
        ttk.Label(self, textvariable=self._message, wraplength=760).pack(anchor="w", fill="x")
        self._actions = ttk.Frame(self)
        self._action = ttk.Button(self._actions, command=self._open_action)
        self._details = ttk.Button(
            self._actions, text="Technical details", command=self._open_details
        )
        self._dismiss = ttk.Button(self._actions, text="Dismiss", command=self.hide)

    def show_notice(self, notice: Notice) -> None:
        self._notice = notice
        self._title.set(notice.title)
        self._message.set(notice.message)
        self._action.pack_forget()
        self._details.pack_forget()
        self._dismiss.pack_forget()
        if notice.action_label and notice.action_route and self._on_route is not None:
            self._action.configure(text=notice.action_label)
            self._action.pack(side="left")
        if notice.details and self._on_details is not None:
            self._details.pack(side="left", padx=(5, 0))
        if notice.dismissible:
            self._dismiss.pack(side="right")
        if any((
            notice.action_label and notice.action_route and self._on_route is not None,
            notice.details and self._on_details is not None,
            notice.dismissible,
        )):
            self._actions.pack(fill="x", pady=(5, 0))
        else:
            self._actions.pack_forget()
        self.pack(fill="x", pady=(0, 6))
        if not notice.dismissible and notice.action_label and notice.action_route:
            try:
                self._action.focus_set()
            except Exception:
                pass
        elif not notice.dismissible:
            # Giving the persistent status region focus makes the state change
            # discoverable to keyboard and assistive-technology users even
            # when there is no route action to focus.
            try:
                self.focus_set()
            except Exception:
                pass

    def _open_action(self) -> None:
        notice = self._notice
        if (
            notice is not None and notice.action_route
            and self._on_route is not None
        ):
            self._on_route(notice.action_route)

    def _open_details(self) -> None:
        notice = self._notice
        if notice is not None and notice.details and self._on_details is not None:
            self._on_details(notice.title, notice.details)

    def hide(self) -> None:
        self._notice = None
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
        typed_entry = None
        if confirmation.typed_phrase:
            ttk.Label(self._content, text=f'Type "{confirmation.typed_phrase}" to continue:').pack(anchor="w")
            typed_entry = ttk.Entry(self._content, textvariable=self._typed)
            typed_entry.pack(fill="x")
        row = ttk.Frame(self._content)
        row.pack(fill="x", pady=(6, 0))
        cancel_button = ttk.Button(row, text="Cancel", command=self.clear)
        cancel_button.pack(side="right")

        def confirm() -> None:
            if confirmation.typed_phrase and self._typed.get() != confirmation.typed_phrase:
                return
            self.clear()
            on_confirm()

        confirm_button = ttk.Button(
            row, text=confirmation.confirm_label, command=confirm
        )
        confirm_button.pack(side="right", padx=6)
        self.pack(fill="x")
        if typed_entry is not None:
            typed_entry.bind("<Return>", lambda _event: confirm())
            typed_entry.focus_set()
        else:
            # A confirmation is never the implicit default. Keyboard users
            # begin on the safe Cancel choice and may tab to the action.
            cancel_button.focus_set()

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
        search.focus_set()

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
