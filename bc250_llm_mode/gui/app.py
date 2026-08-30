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
from ..logging_utils import CommandRunner, configure_logging

MAX_GUI_EVENTS = 512


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
        self.geometry("1000x700")
        self.minsize(760, 560)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue(MAX_GUI_EVENTS)
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
        try:
            self.gui_preferences = application.preferences.current()
            self.reduced_motion = bool(
                self.gui_preferences.get("reduced_motion", False)
            )
        except Exception:
            self.gui_preferences = {
                "appearance": "system", "ui_scale_percent": 100,
                "reduced_motion": False,
                "notifications_enabled": False,
            }
            self.reduced_motion = False
        from .theme import apply_theme

        apply_theme(
            self, str(self.gui_preferences.get("appearance") or "system"),
            scale_percent=int(
                self.gui_preferences.get("ui_scale_percent") or 100
            ),
        )
        self._task_lanes = None
        self._build_shell()
        self._schedule_lifecycle()
        self.show_setup_screen(self.current_step)
        self._refresh_coordinator.start()

    def _schedule_lifecycle(self) -> None:
        """Create the one refresh owner before any page submits observations."""
        from .refresh import RefreshCoordinator

        self._refresh_coordinator = RefreshCoordinator(self, self._refresh_cycle)

    def _refresh_cycle(self) -> None:
        self._drain_events(reschedule=False)
        lanes = self._task_lanes
        if lanes is not None:
            try:
                while True:
                    result = lanes.results.get_nowait()
                    current_generation = (
                        self._route_generation
                        if hasattr(self, "_route_generation") else self.current_step
                    )
                    if result.lane == "action":
                        self.busy = False
                        self.progress.stop()
                    if result.lane in {"action", "chat"}:
                        self._refresh_coordinator.active = False
                    if result.generation != current_generation:
                        continue
                    if result.error is not None:
                        if result.lane == "observation":
                            page = getattr(self, "_page", None)
                            failed = getattr(page, "observation_failed", None)
                            if callable(failed):
                                failed(result.error)
                            continue
                        from .view_state import Notice, sanitize_exception

                        if hasattr(self, "notice_bar"):
                            self.notice_bar.show_notice(Notice(
                                "error", "Action could not be completed",
                                sanitize_exception(result.error), dismissible=False,
                            ))
                    elif callable(result.value):
                        try:
                            result.value()
                        except Exception as exc:
                            from .view_state import Notice, sanitize_exception

                            if hasattr(self, "notice_bar"):
                                self.notice_bar.show_notice(Notice(
                                    "error", "View could not be refreshed",
                                    sanitize_exception(exc), dismissible=False,
                                ))
            except queue.Empty:
                pass

    def _build_shell(self) -> None:
        """Build the concrete shell supplied by :class:`ApplicationWindow`."""
        raise NotImplementedError

    def emit(self, line: str) -> None:
        self._queue_event("log", line)

    def _queue_event(self, kind: str, payload: Any) -> bool:
        """Non-blocking bounded bridge; log bursts may be safely coalesced."""
        try:
            self.events.put_nowait((kind, payload))
            return True
        except queue.Full:
            if kind == "log":
                return False
            try:
                self.events.get_nowait()
            except queue.Empty:
                return False
            try:
                self.events.put_nowait((kind, payload))
                return True
            except queue.Full:
                return False

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
        del reschedule

    def runner(self) -> CommandRunner:
        return CommandRunner(configure_logging(self._paths.logs_dir), self.emit)

    def track_operation_id(self, operation_id: str | None) -> None:
        if operation_id:
            self._queue_event("operation", str(operation_id))

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

    def _work(self, action: Callable[[], None], done: Callable[[], None]) -> None:
        if self.busy:
            return
        self.busy = True
        self.continue_button.configure(state="disabled")
        self.progress.configure(mode="indeterminate")
        if not self.reduced_motion:
            self.progress.start(12)
        self._ensure_task_lanes()
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

    def _ensure_task_lanes(self) -> None:
        if self._task_lanes is None:
            from .tasks import TaskLanes

            self._task_lanes = TaskLanes()

    def submit_chat(self, task: Callable[[], Any]) -> bool:
        """Submit one stream to the dedicated bounded chat lane."""
        self._ensure_task_lanes()
        generation = (
            self._route_generation
            if hasattr(self, "_route_generation") else self.current_step
        )
        accepted = self._task_lanes.chat.submit(generation, task)
        if accepted:
            self._refresh_coordinator.active = True
            self._refresh_coordinator.request_now()
        return accepted

    def request_observation(
        self, work: Callable[[], Any], apply: Callable[[Any], None]
    ) -> bool:
        """Coalesce read-only probes and apply only current-page results."""
        self._ensure_task_lanes()
        generation = (
            self._route_generation
            if hasattr(self, "_route_generation") else self.current_step
        )

        def task():
            value = work()
            return lambda: apply(value)

        accepted = self._task_lanes.observation.submit(generation, task)
        if accepted and not self._refresh_coordinator.in_callback:
            self._refresh_coordinator.request_now()
        return accepted
