"""Persistent one-root native application shell."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..command_palette import palette_commands
from .routes import SETUP_CHAPTERS, PRIMARY_ROUTES, Route, available_routes, parse_route
from .setup_page import SetupWindow, setup_resume_view
from .widgets import BottomDrawer, NoticeBar
from .view_state import Confirmation, Notice


class ApplicationWindow(SetupWindow):
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
            Route.HOME: "Home", Route.MODELS: "Models",
            Route.PROFILES: "Profiles", Route.CHAT: "Chat",
            Route.CONNECTIONS: "Connections",
            Route.ACTIVITY: "Activity", Route.MAINTENANCE: "Maintenance",
            Route.SYSTEM: "System",
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
        self.protocol("WM_DELETE_WINDOW", self.request_close)
        for index, route in enumerate(PRIMARY_ROUTES[:9], 1):
            self.bind(
                f"<Control-Key-{index}>",
                lambda _event, target=route: self._shortcut_route(target),
            )
        self.bind("<Control-l>", lambda _event: self._shortcut_logs())
        self.bind("<Control-k>", lambda _event: self._shortcut_palette())
        self.bind("<Control-f>", lambda _event: self._shortcut_focus())
        self.bind("<Escape>", lambda _event: self._shortcut_escape())
        self.bind("<Map>", lambda _event: self._set_mapped(True))
        self.bind("<Unmap>", lambda _event: self._set_mapped(False))

    def emit(self, line: str) -> None:
        self._log_lines.append(str(line))
        if len(self._log_lines) > 2000:
            del self._log_lines[:-2000]
        super().emit(line)

    def open_logs(self, source: str = "setup") -> None:
        try:
            lines = list(self.application.logs.tail(source, lines=200))
        except (ValueError, OSError):
            lines = []
        if source == "setup" and self._log_lines:
            lines.extend(self._log_lines[-200:])
        self.drawer.show_log(f"Recent {source} log", lines)

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
        is_management_subroute = (
            target in {Route.REPAIR, Route.UPDATES} and Route.HOME in permitted
        )
        if (
            target not in permitted
            and target is not Route.SETUP
            and not is_management_subroute
        ):
            return
        current_page = self._page
        if current_page is not None and target is not self._route:
            request_leave = getattr(current_page, "request_leave", None)
            if callable(request_leave) and not request_leave(target):
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

            self._page = ActivityCenterFrame(
                self.content, self.application, shell=self
            )
            self._page.mount()
            self._page.enter(context)
            self.heading.focus_set()
            return
        if target is Route.HOME:
            self.heading.configure(text="Home")
            from .home_page import HomePage

            self._page = HomePage(self.content, self, self.application)
            self._page.mount()
            self._page.enter(context)
            self.heading.focus_set()
            return
        if target is Route.MODELS:
            self.heading.configure(text="Models")
            from .models_page import ModelsPage

            self._page = ModelsPage(
                self.content, self, self.application, context=context
            )
            self._page.mount()
            self.heading.focus_set()
            return
        if target is Route.PROFILES:
            self.heading.configure(text="Profiles")
            from .profiles_page import ProfilesPage

            self._page = ProfilesPage(self.content, self, self.application)
            self._page.mount()
            self._page.enter(context)
            self.heading.focus_set()
            return
        if target is Route.CHAT:
            self.heading.configure(text="Chat")
            from .chat_page import ChatPage

            self._page = ChatPage(self.content, self, self.application)
            self._page.mount()
            self._page.enter(context)
            self.heading.focus_set()
            return
        if target is Route.CONNECTIONS:
            self.heading.configure(text="Connections")
            from .connections_page import ConnectionsPage

            self._page = ConnectionsPage(self.content, self, self.application)
            self._page.mount()
            self._page.enter(context)
            self.heading.focus_set()
            return
        if target is Route.MAINTENANCE:
            self.heading.configure(text="Maintenance")
            from .maintenance_page import MaintenancePage

            self._page = MaintenancePage(self.content, self, self.application)
            self._page.mount()
            self._page.enter(context)
            self.heading.focus_set()
            return
        if target is Route.REPAIR:
            self.heading.configure(text="Maintenance · Repair")
            from .repair_page import RepairPage

            self._page = RepairPage(self.content, self, self.application)
            self._page.mount()
            self._page.enter(context)
            self.heading.focus_set()
            return
        if target is Route.UPDATES:
            self.heading.configure(text="Maintenance · Updates")
            from .updates_page import UpdatesPage

            self._page = UpdatesPage(self.content, self, self.application)
            self._page.mount()
            self._page.enter(context)
            self.heading.focus_set()
            return
        if target is Route.SYSTEM:
            self.heading.configure(text="System")
            from .system_page import SystemPage

            self._page = SystemPage(self.content, self, self.application)
            self._page.mount()
            self._page.enter(context)
            self.heading.focus_set()
            return
        if target is Route.SETTINGS:
            self.heading.configure(text="Settings")
            from .settings_page import SettingsPage

            self._page = SettingsPage(self.content, self, self.application)
            self._page.mount()
            self._page.enter(context)
            self.heading.focus_set()
            return
        if target is Route.HELP:
            self.heading.configure(text="Help")
            from .help_page import HelpPage

            self._page = HelpPage(self.content, self, self.application)
            self._page.mount()
            self._page.enter(context)
            self.heading.focus_set()
            return
        self.heading.configure(text=target.value.replace("/", " · ").title())
        ttk.Label(
            self.content,
            text="This page is being converted into the unified native shell. Existing controls remain available from Home during this boundary.",
            wraplength=720,
        ).pack(anchor="w", pady=20)
        self.heading.focus_set()

    def _shortcut_route(self, route: Route):
        self.navigate(route)
        return "break"

    def _shortcut_logs(self):
        self.open_logs()
        return "break"

    def _shortcut_focus(self):
        focus = getattr(self._page, "focus_primary", None)
        if callable(focus):
            focus()
        return "break"

    def _shortcut_palette(self):
        commands = palette_commands(
            setup_complete=bool(self.state_data.get("setup_complete")),
            operational=bool(getattr(self.application, "operational", True)),
        )
        self.drawer.show_palette(commands, self._open_palette_command)
        return "break"

    def _open_palette_command(self, command) -> None:
        # A palette command contains only a route and plain bounded context;
        # mutation continues to belong to the destination page's preview.
        self.navigate(command.route, command.route_context())

    def _shortcut_escape(self):
        self.drawer.clear()
        return "break"

    def _set_mapped(self, mapped: bool) -> None:
        coordinator = getattr(self, "_refresh_coordinator", None)
        if coordinator is not None:
            coordinator.mapped = mapped
            coordinator.request_now()

    def apply_preferences(self, preferences) -> None:
        from .theme import apply_theme

        self.gui_preferences = dict(preferences)
        self.reduced_motion = bool(preferences.get("reduced_motion", False))
        apply_theme(
            self, str(preferences.get("appearance") or "system"),
            scale_percent=int(preferences.get("ui_scale_percent") or 100),
        )

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
        self._page = None

    def show_setup_screen(self, step: int) -> None:
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
        super().show_setup_screen(step)
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
        self._show_activity_shelf()

    def _show_activity_shelf(self) -> None:
        if self._activity_shelf.winfo_manager() == "pack":
            return
        anchor = (
            self.notice_bar
            if self.notice_bar.winfo_manager() == "pack"
            else self._header
        )
        self._activity_shelf.pack(fill="x", pady=(0, 6), after=anchor)

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
            self._show_activity_shelf()
        elif summary.active_count:
            self._activity_text.set(
                f"{summary.running_count} running · {summary.queued_count} queued · "
                f"{summary.paused_count} paused"
            )
            self._show_activity_shelf()
        elif not self._tracked_operation_ids:
            self._activity_shelf.pack_forget()
        page_streaming = bool(getattr(self._page, "_streaming", False))
        self._refresh_coordinator.active = bool(
            summary.active_count or self.busy or page_streaming
        )

    def _open_activity_center(self) -> None:
        self.navigate(Route.ACTIVITY)

    def _launch_chat_terminal(self) -> None:
        self.application.open_chat_terminal()

    def request_close(self) -> None:
        page = self._page
        page_close = getattr(page, "request_close", None)
        if callable(page_close) and not page_close(self.request_close):
            return
        try:
            summary = self.application.operation_query.active_summary()
        except Exception:
            summary = None
        if summary is not None and summary.active_count:
            worker = (
                f"Worker {summary.worker_lock_owner} currently owns execution."
                if summary.worker_lock_owner and not summary.worker_lock_expired
                else "Foreground-only work may pause safely until the app is reopened."
            )
            self.drawer.show_confirmation(
                Confirmation(
                    "Close BC250 LLM MODE?",
                    f"{summary.active_count} durable operation(s) are active. {worker} "
                    "In Desktop mode, a running model will stop before the app closes.",
                    "Operation history and recovery evidence remain saved; closing never marks work cancelled. "
                    "Reopen the app to start the model again.",
                    "Close app",
                ),
                self._close_application,
            )
            return
        self._close_application()

    def _close_application(self) -> None:
        """Stop Desktop-mode inference before ending the GUI process.

        LLM Mode is an explicit current-boot serving session, so closing its
        control window does not end that session.  In normal Desktop mode the
        GUI owns the interactive model lifetime: it verifies the live service
        state, stops an active server, and refuses to disappear if the stop
        cannot be verified.  Window unmap/minimize and route changes never call
        this boundary.
        """
        try:
            state = self.application.read_model()
            if str(state.get("system_mode") or "") == "desktop":
                service = self.application.model_server
                runner = self.runner()
                status = service.status(state, runner)
                if not isinstance(status, dict) or "active" not in status:
                    raise RuntimeError("model service status was unavailable")
                if status["active"]:
                    stopped = service.stop(state, runner)
                    if not isinstance(stopped, dict) or stopped.get("active") is not False:
                        raise RuntimeError("model service did not reach inactive state")
        except Exception as exc:
            self.emit(f"Desktop close kept the app open: {exc.__class__.__name__}")
            self.notice_bar.show_notice(Notice(
                "error",
                "The model could not be stopped",
                "BC250 LLM MODE stayed open so inference is not left running unexpectedly. "
                "Use Stop on the System page, then close the app again.",
                action_label="Open System",
                action_route=Route.SYSTEM.value,
                dismissible=False,
            ))
            return
        self.destroy()

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
        if page is not None and self._route in {
            Route.HOME, Route.MODELS, Route.CHAT, Route.CONNECTIONS,
            Route.ACTIVITY, Route.MAINTENANCE,
            Route.REPAIR, Route.UPDATES, Route.SYSTEM,
        }:
            refresh = getattr(page, "refresh", None)
            if callable(refresh) and not self.busy:
                try:
                    refresh()
                except Exception:
                    pass
            elif self.busy:
                progress_refresh = getattr(page, "refresh_progress", None)
                if callable(progress_refresh):
                    try:
                        progress_refresh()
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
