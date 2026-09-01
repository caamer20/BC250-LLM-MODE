"""Progressive-disclosure hub for secondary one-window destinations."""

from __future__ import annotations

from tkinter import ttk

from .routes import MORE_DESTINATIONS, MoreDestination, Route


class MorePage(ttk.Frame):
    def __init__(self, parent, shell) -> None:
        super().__init__(parent)
        self.shell = shell
        self._disposed = False
        ttk.Label(
            self, text="More tools and details",
            font=("TkDefaultFont", 18, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            self,
            text=("The everyday path stays in Home, Models, Chat, and "
                  "Connections. These pages keep advanced status and controls nearby."),
            wraplength=720,
        ).pack(anchor="w", fill="x", pady=(3, 10))
        self._buttons = []
        for destination in MORE_DESTINATIONS:
            row = ttk.LabelFrame(self, text=destination.title, padding=7)
            row.pack(fill="x", pady=3)
            ttk.Label(
                row, text=destination.summary, wraplength=610,
            ).pack(side="left", fill="x", expand=True)
            button = ttk.Button(
                row, text="Open",
                command=lambda route=destination.route: self.shell.navigate(route),
            )
            button.pack(side="right", padx=(8, 0))
            self._buttons.append(button)

        advanced = ttk.LabelFrame(self, text="Advanced workload tuning", padding=7)
        advanced.pack(fill="x", pady=(10, 3))
        ttk.Label(
            advanced,
            text=("Profiles changes model context and concurrency. Most users can "
                  "keep the recommended profile."),
            wraplength=610,
        ).pack(side="left", fill="x", expand=True)
        self._profiles_button = ttk.Button(
            advanced, text="Open Profiles",
            command=lambda: self.shell.navigate(Route.PROFILES),
        )
        self._profiles_button.pack(side="right", padx=(8, 0))

    def mount(self, parent=None):
        del parent
        self.pack(fill="both", expand=True)
        return self

    def enter(self, route_context=None) -> None:
        del route_context

    def focus_primary(self) -> None:
        if self._buttons:
            self._buttons[0].focus_set()

    def leave(self) -> None:
        return None

    def dispose(self) -> None:
        self._disposed = True


__all__ = ["MORE_DESTINATIONS", "MoreDestination", "MorePage"]
