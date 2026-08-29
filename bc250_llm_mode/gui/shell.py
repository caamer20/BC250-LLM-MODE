"""Persistent one-root native application shell.

The current setup/dashboard renderers are mounted as a temporary legacy page
while GUI-3 through GUI-6 convert them.  The default application entry already
uses this owner so Activity and logs can transition in-window without another
Tk root.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .app import GuiBase
from .dashboard import DashboardMixin
from .forms import FormsMixin
from .routes import SETUP_CHAPTERS, PRIMARY_ROUTES, Route, available_routes, parse_route
from .setup_page import SetupPageMixin, setup_resume_view
from .widgets import BottomDrawer, NoticeBar


class ApplicationWindow(SetupPageMixin, DashboardMixin, FormsMixin, GuiBase):
    """The only application-owned ``Tk`` root."""

    def _build_shell(self) -> None:
        self._route = Route.SETUP
        self._route_generation = 0
        self._page = None
        self._outer = ttk.Frame(self, padding=10)
        self._outer.pack(fill="both", expand=True)

        self._header = ttk.Frame(self._outer)
        self._header.pack(fill="x")
        self.heading = ttk.Label(self._header, text="BC250 LLM MODE", font=("TkDefaultFont", 16, "bold"))
        self.heading.pack(side="left", anchor="w")
        self._header_status = tk.StringVar(value="Desktop next boot · model auto-start off")
        ttk.Label(self._header, textvariable=self._header_status).pack(side="right")

        self.notice_bar = NoticeBar(self._outer)
        self._activity_shelf = ttk.Frame(self._outer, padding=(8, 5))
        self._activity_text = tk.StringVar(value="")
        ttk.Label(self._activity_shelf, textvariable=self._activity_text).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(
            self._activity_shelf, text="View activity",
            command=lambda: self.navigate(Route.ACTIVITY),
        ).pack(side="right")
        self._tracked_operation_ids: list[str] = []
        body = ttk.Frame(self._outer)
        body.pack(fill="both", expand=True, pady=(8, 0))
        self._nav = ttk.Frame(body, width=150)
        self._nav.pack(side="left", fill="y", padx=(0, 10))
        self._nav_buttons: dict[Route, object] = {}
        labels = {
            Route.HOME: "Home", Route.MODELS: "Models", Route.CHAT: "Chat",
            Route.ACTIVITY: "Activity", Route.SYSTEM: "System",
            Route.SETTINGS: "Settings", Route.HELP: "Help",
        }
        for route in PRIMARY_ROUTES:
            button = ttk.Button(self._nav, text=labels[route], command=lambda item=route: self.navigate(item))
            button.pack(fill="x", pady=2)
            self._nav_buttons[route] = button

        main = ttk.Frame(body)
        main.pack(side="left", fill="both", expand=True)
        self.progress = ttk.Progressbar(main, maximum=10)
        self.progress.pack(fill="x", pady=(0, 8))
        self.content = ttk.Frame(main)
        self.content.pack(fill="both", expand=True)

        # Compatibility log sink. It is never permanently packed; emit() keeps
        # a bounded in-memory tail and the drawer displays it on request.
        self.log_text = tk.Text(main, height=1, state="disabled")
        self._log_lines: list[str] = []

        self._setup_nav = ttk.Frame(main)
        self._setup_nav.pack(fill="x", pady=(8, 0))
        self.back_button = ttk.Button(self._setup_nav, text="Back", command=self.back)
        self.back_button.pack(side="left")
        self.continue_button = ttk.Button(self._setup_nav, text="Continue", command=self.continue_step)
        self.continue_button.pack(side="right")
        ttk.Button(self._setup_nav, text="Logs", command=self.open_logs).pack(side="right", padx=6)

        self.drawer = BottomDrawer(self._outer)
        self._update_navigation()

    def emit(self, line: str) -> None:
        self._log_lines.append(str(line))
        if len(self._log_lines) > 2000:
            del self._log_lines[:-2000]
        super().emit(line)

    def open_logs(self) -> None:
        self.drawer.show_log("Recent setup and action log", self._log_lines)

    def _update_navigation(self) -> None:
        permitted = set(available_routes(
            setup_complete=bool(self.state_data.get("setup_complete")),
            operational=bool(getattr(self.application, "operational", True)),
        ))
        management = Route.HOME in permitted and not bool(
            self.__dict__.get("_show_setup_ready", False)
        )
        if management:
            self._nav.pack(side="left", fill="y", padx=(0, 10))
            self._setup_nav.pack_forget()
            self.progress.pack_forget()
        else:
            self._nav.pack_forget()
            self.progress.pack(fill="x", pady=(0, 8), before=self.content)
            self._setup_nav.pack(fill="x", pady=(8, 0))
        for route, button in self._nav_buttons.items():
            button.configure(state="normal" if route in permitted else "disabled")

    def navigate(self, route: str | Route, context=None) -> None:
        target = parse_route(route)
        permitted = available_routes(
            setup_complete=bool(self.state_data.get("setup_complete")),
            operational=bool(getattr(self.application, "operational", True)),
        )
        if target not in permitted and target is not Route.SETUP:
            return
        if target is not Route.SETUP and bool(
            self.__dict__.get("_show_setup_ready", False)
        ):
            self._show_setup_ready = False
            self._update_navigation()
        self._route_generation += 1
        self._route = target
        self._dispose_page()
        self._clear()
        if target is Route.ACTIVITY:
            self.heading.configure(text="Activity")
            from .activity import ActivityCenterFrame

            self._page = ActivityCenterFrame(self.content, self.application)
            self._page.pack(fill="both", expand=True)
            self._page.start_polling()
            return
        if target is Route.HOME:
            self.heading.configure(text="Home")
            from .home_page import HomePage

            self._page = HomePage(self.content, self, self.application)
            self._page.mount()
            self._page.enter(context)
            return
        if target is Route.MODELS:
            self.heading.configure(text="Models")
            from .models_page import ModelsPage

            self._page = ModelsPage(
                self.content, self, self.application, context=context
            )
            self._page.mount()
            return
        self.heading.configure(text=target.value.replace("/", " · ").title())
        ttk.Label(
            self.content,
            text="This page is being converted into the unified native shell. Existing controls remain available from Home during this boundary.",
            wraplength=720,
        ).pack(anchor="w", pady=20)

    def _dispose_page(self) -> None:
        page = self._page
        if page is None:
            return
        leave = getattr(page, "leave", None)
        if callable(leave):
            leave()
        dispose = getattr(page, "dispose", None)
        if callable(dispose):
            dispose()
        stop = getattr(page, "stop_polling", None)
        if callable(stop):
            stop()
        self._page = None

    def show_step(self, step: int) -> None:
        if (
            step == 10
            and self.state_data.get("setup_complete")
            and not bool(self.__dict__.get("_show_setup_ready", False))
        ):
            self._update_navigation()
            self.navigate(Route.HOME)
            return
        self._route = Route.SETUP
        self._update_navigation()
        self._dispose_page()
        super().show_step(step)
        stage = str(self.state_data.get("setup_stage") or "WELCOME")
        active = None
        if getattr(self.application, "operation_query", None) is not None:
            try:
                active = self.application.operation_query.active_summary().to_dict()
            except Exception:
                active = None
        view = setup_resume_view(
            stage,
            visible_step=self.current_step,
            legacy_phase=int(self.state_data.get("setup_phase", 0)),
            reboot_required=bool(self.state_data.get("reboot_required")),
            active_operation=active,
        )
        chapter_name = SETUP_CHAPTERS[view.visible_chapter]
        self.heading.configure(
            text=f"Setup · Chapter {view.visible_chapter + 1} of 5 · {chapter_name}"
        )
        self.progress.configure(maximum=5, value=view.progress_value)
        self._header_status.set(view.status)

    def _operation_tracked(self, operation_id: str) -> None:
        if operation_id not in self._tracked_operation_ids:
            self._tracked_operation_ids.append(operation_id)
            del self._tracked_operation_ids[:-16]
        short = operation_id if len(operation_id) <= 20 else operation_id[:17] + "…"
        self._activity_text.set(f"Operation {short} was recorded")
        self._activity_shelf.pack(fill="x", pady=(0, 6), after=self.notice_bar)

    def _refresh_activity_shelf(self) -> None:
        query = getattr(self.application, "operation_query", None)
        if query is None:
            return
        try:
            summary = query.active_summary()
        except Exception:
            return
        if summary.recovery_required_count:
            self._activity_text.set(
                f"{summary.recovery_required_count} operation(s) need recovery"
            )
            self._activity_shelf.pack(fill="x", pady=(0, 6), after=self.notice_bar)
        elif summary.active_count:
            self._activity_text.set(
                f"{summary.running_count} running · {summary.queued_count} queued · "
                f"{summary.paused_count} paused"
            )
            self._activity_shelf.pack(fill="x", pady=(0, 6), after=self.notice_bar)
        elif not self._tracked_operation_ids:
            self._activity_shelf.pack_forget()

    def _open_activity_center(self) -> None:
        self.navigate(Route.ACTIVITY)

    def _refresh_cycle(self) -> None:
        broker = getattr(self, "instance_broker", None)
        if broker is not None:
            for request in broker.poll():
                try:
                    self.deiconify()
                    self.lift()
                    self.focus_force()
                except Exception:
                    pass
                if request.route:
                    self.navigate(request.route)
                elif request.verb == "OPEN_OPERATION":
                    self.navigate(Route.ACTIVITY, {"operation_id": request.identifier})
                elif request.verb == "OPEN_MODEL":
                    self.navigate(Route.MODELS, {"model_id": request.identifier})
        self._refresh_activity_shelf()
        page = self._page
        if page is not None and self._route in {Route.HOME, Route.MODELS}:
            refresh = getattr(page, "refresh", None)
            if callable(refresh) and not self.busy:
                try:
                    refresh()
                except Exception:
                    pass
        super()._refresh_cycle()

    def destroy(self) -> None:
        self._dispose_page()
        coordinator = getattr(self, "_refresh_coordinator", None)
        if coordinator is not None:
            coordinator.close()
        lanes = getattr(self, "_task_lanes", None)
        if lanes is not None:
            lanes.close()
        super().destroy()
