"""Application shell: Tk root, event queue, threading, navigation."""

from __future__ import annotations


import queue


import threading
import tkinter as tk

from collections.abc import Callable
from pathlib import Path
from tkinter import ttk
from typing import Any

from ..hardware import HardwareReport
from ..local_models import (
    LocalModel,
    discover_local_models,
    fit_entry_for_local,
    selected_fit_entry,
)
from ..logging_utils import CommandRunner, configure_logging
from ..paths import AppPaths
from ..model_manager import (
    change_context,
    register_and_switch_local,
    switch_model,
)
from ..openwebui import (
    install_open_webui,
    open_webui_status,
    restart_open_webui,
    start_open_webui,
    stop_open_webui,
)
from ..optimize import (
    DEFAULT_OPTIMIZATIONS,
    TRIMMABLE_SERVICES,
    apply_optimizations,
    kv_scale_for_settings,
    normalized_settings,
    validate_settings,
)
from ..server import (
    health_check,
    install_service,
    restart_and_wait,
    restart_service,
    service_status,
    start_service,
    stop_service,
)
from ..tailscale import (
    connect_tailscale,
    disconnect_tailscale,
    restart_tailscale,
    start_tailscale,
    stop_tailscale,
    tailscale_status,
)


class GuiBase(tk.Tk):

    def __init__(
        self,
        application,
        management: bool = False,
    ) -> None:
        super().__init__()
        # Composition authority: the window receives the composed
        # Application (paths, query layer, services). It never constructs a
        # store and never persists a whole-state dictionary.
        self.application = application
        self._paths = application.paths
        self.state_data = application.query.snapshot().data
        self._synced = dict(self.state_data)
        self.management = management
        self.title("BC250 LLM MODE")
        self.geometry("920x760")
        self.minsize(760, 620)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        legacy_step = min(max(int(self.state_data.get("setup_phase", 0)), 0), 10)
        if management and self.state_data.get("setup_complete"):
            self.current_step = 10
        else:
            from .setup_page import setup_resume_view

            stage = str(self.state_data.get("setup_stage") or "WELCOME")
            self.current_step = setup_resume_view(
                stage,
                visible_step=legacy_step,
                legacy_phase=legacy_step,
                reboot_required=bool(self.state_data.get("reboot_required")),
            ).resume_step
        if self.state_data.get("reboot_required"):
            try:
                active = "amdgpu.runpm=0" in Path("/proc/cmdline").read_text(encoding="utf-8").split()
            except OSError:
                active = False
            pending = self.state_data.get("pending_karg_mode", "enable")
            change_is_active = (pending == "enable" and active) or (pending == "disable" and not active)
            if change_is_active:
                self.state_data.update(reboot_required=False, pending_karg_mode=None)
                self.commit_narrow()
            elif pending == "enable":
                self.current_step = 2
        self.busy = False
        self.hardware_report: HardwareReport | None = None
        self.optimization_return_to_complete = False
        self._build_shell()
        self.show_step(self.current_step)
        self._schedule_lifecycle()

    def _schedule_lifecycle(self) -> None:
        """One refresh owner; task lanes are created lazily on first work."""
        from .refresh import RefreshCoordinator

        self._task_lanes = None
        self._refresh_coordinator = RefreshCoordinator(self, self._refresh_cycle)
        self._refresh_coordinator.start()

    def _refresh_cycle(self) -> None:
        self._drain_events(reschedule=False)
        lanes = self._task_lanes
        if lanes is not None:
            try:
                while True:
                    result = lanes.results.get_nowait()
                    self.busy = False
                    self._refresh_coordinator.active = False
                    self.progress.stop()
                    if result.error is not None:
                        from .view_state import Notice, sanitize_exception

                        if hasattr(self, "notice_bar"):
                            self.notice_bar.show_notice(Notice(
                                "error", "Action could not be completed",
                                sanitize_exception(result.error), dismissible=False,
                            ))
                    elif callable(result.value):
                        result.value()
            except queue.Empty:
                pass

    def _build_shell(self) -> None:
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        self.heading = ttk.Label(outer, font=("TkDefaultFont", 16, "bold"))
        self.heading.pack(anchor="w")
        self.progress = ttk.Progressbar(outer, maximum=10)
        self.progress.pack(fill="x", pady=(8, 10))
        self.content = ttk.Frame(outer)
        self.content.pack(fill="both", expand=True)
        ttk.Label(outer, text="Setup log").pack(anchor="w", pady=(8, 2))
        log_frame = ttk.Frame(outer)
        log_frame.pack(fill="both", expand=False)
        self.log_text = tk.Text(log_frame, height=9, wrap="word", state="disabled")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")
        nav = ttk.Frame(outer)
        nav.pack(fill="x", pady=(10, 0))
        self.back_button = ttk.Button(nav, text="Back", command=self.back)
        self.back_button.pack(side="left")
        self.continue_button = ttk.Button(nav, text="Continue", command=self.continue_step)
        self.continue_button.pack(side="right")

    def emit(self, line: str) -> None:
        self.events.put(("log", line))

    def _drain_events(self, *, reschedule: bool = True) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.log_text.configure(state="normal")
                    self.log_text.insert("end", str(payload) + "\n")
                    self.log_text.see("end")
                    self.log_text.configure(state="disabled")
                elif kind == "done":
                    self.busy = False
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=self.current_step)
                    self.continue_button.configure(state="normal")
                    payload()
                elif kind == "operation":
                    callback = getattr(self, "_operation_tracked", None)
                    if callback is not None:
                        callback(str(payload))
                elif kind == "error":
                    self.busy = False
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=self.current_step)
                    self.continue_button.configure(state="normal")
                    from .view_state import Notice, sanitize_exception

                    if hasattr(self, "notice_bar"):
                        self.notice_bar.show_notice(Notice(
                            "error", "Setup failed", sanitize_exception(payload),
                            dismissible=False,
                        ))
                    else:
                        self.emit("Setup failed; open the setup log for details.")
        except queue.Empty:
            pass
        if reschedule and not hasattr(self, "_refresh_coordinator"):
            self.after(100, self._drain_events)

    def runner(self) -> CommandRunner:
        return CommandRunner(configure_logging(self._paths.logs_dir), self.emit)

    def track_operation_id(self, operation_id: str | None) -> None:
        if operation_id:
            self.events.put(("operation", str(operation_id)))

    def refresh_snapshot(self) -> None:
        """Discard the draft and pull a fresh repository-native snapshot."""
        self.state_data = self.application.query.snapshot().data
        self._synced = dict(self.state_data)

    def commit_narrow(self) -> int:
        """Persist ONLY the keys this window changed since its last sync.

        This is the GUI's sole persistence primitive: a settings-scoped
        diff in one unit of work with a revision bump, so stale windows
        surface conflicts instead of overwriting newer state.
        """
        changed = self.application.commit_settings_changes(
            self._synced, self.state_data
        )
        self._synced = dict(self.state_data)
        return changed

    def _clear(self) -> None:
        for widget in self.content.winfo_children():
            widget.destroy()

    def show_step(self, step: int) -> None:
        self.current_step = max(0, min(step, 10))
        self._clear()
        self.heading.configure(text=f"Step {self.current_step}: {STEP_TITLES[self.current_step]}")
        self.progress.configure(value=self.current_step)
        self.back_button.configure(state="disabled" if self.current_step == 0 else "normal")
        self.continue_button.configure(text="Continue", state="normal")
        renderers = (
            self._hardware, self._disclaimer, self._llm_mode, self._environment, self._catalog,
            self._optimize, self._download, self._prepare, self._server, self._webui, self._setup_ready,
        )
        renderers[self.current_step]()

    def _body_label(self, text: str) -> ttk.Label:
        label = ttk.Label(self.content, text=text, wraplength=820, justify="left")
        label.pack(anchor="w", fill="x", pady=8)
        return label

    def back(self) -> None:
        if not self.busy:
            if self.current_step == 5 and self.optimization_return_to_complete:
                self.optimization_return_to_complete = False
                self.show_step(10)
            else:
                self.show_step(self.current_step - 1)

    def _work(self, action: Callable[[], None], done: Callable[[], None]) -> None:
        if self.busy:
            return
        self.busy = True
        self.continue_button.configure(state="disabled")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        from .tasks import TaskLanes

        if self._task_lanes is None:
            self._task_lanes = TaskLanes()
        self._refresh_coordinator.active = True

        def task():
            action()
            return done

        if not self._task_lanes.action.submit(self._route_generation if hasattr(self, "_route_generation") else self.current_step, task):
            self.busy = False
            self.progress.stop()
            self.continue_button.configure(state="normal")
            return
        self._refresh_coordinator.request_now()

    def _advance(self) -> None:
        self.commit_narrow()
        self.show_step(self.current_step + 1)


STEP_TITLES = (
    "Welcome & Hardware", "Safety Warning", "LLM Mode", "Inference Environment",
    "Model Selection", "Optimize", "Download", "Prepare", "Server", "Open WebUI", "Setup Complete",
)
