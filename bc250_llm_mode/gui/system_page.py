"""System controls grouped by operator task and composed service state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import tkinter as tk
from tkinter import ttk

from .routes import Route
from .view_state import Notice


@dataclass(frozen=True)
class ServiceCardView:
    key: str
    title: str
    state: str
    detail: str
    primary_code: str | None = None
    primary_label: str | None = None
    more_code: str | None = None
    more_label: str | None = None


def llamacpp_button_states(report: Mapping[str, Any]) -> dict[str, str]:
    blocked = bool(report.get("recovery_barrier") or report.get("active_operation"))
    return {
        "update": "disabled" if blocked else "normal",
        "rollback": "normal" if report.get("rollback_available") and not blocked else "disabled",
    }


def llamacpp_card_text(report: Mapping[str, Any]) -> str:
    promoted = report.get("promoted") or {}
    barrier = report.get("recovery_barrier")
    if barrier:
        text = f"RECOVERY REQUIRED (operation {str(barrier.get('operation_id'))[:12]})"
    elif promoted.get("short"):
        text = f"promoted build {promoted.get('short')}"
    else:
        text = "not recorded yet; run setup or update"
    if report.get("rollback_available"):
        text += "; prior build retained for rollback"
    active = report.get("active_operation")
    if active:
        progress = (
            f" {active.get('phase')} {active.get('current')}/{active.get('total')}"
            if active.get("total") else ""
        )
        text += (
            f" | {active.get('type')} {active.get('state')}"
            f"{progress} (foreground only)"
        )
    return f"llama.cpp: {text}"


def system_card_views(
    *,
    home: Mapping[str, Any],
    server: Mapping[str, Any],
    webui: Mapping[str, Any],
    tailscale: Mapping[str, Any],
    sharing: Mapping[str, Any],
    runtime: Mapping[str, Any],
    backups: int,
    platform_label: str,
) -> tuple[ServiceCardView, ...]:
    cards = home.get("cards") if isinstance(home.get("cards"), Mapping) else {}
    thermal = cards.get("thermal") if isinstance(cards.get("thermal"), Mapping) else {}
    thermal_health = thermal.get("health") if isinstance(thermal.get("health"), Mapping) else {}
    server_active = bool(server.get("active"))
    webui_running = bool(webui.get("running"))
    daemon = bool(tailscale.get("daemon_active"))
    sharing_on = str(sharing.get("status") or "").lower() not in {"", "off", "disabled"}
    return (
        ServiceCardView(
            "server", "Model server", "Service active" if server_active else "Stopped",
            "Single-owner llama.cpp service; model auto-start remains off for reboot.",
            "stop-server" if server_active else "start-server",
            "Stop" if server_active else "Start current model",
            "restart-server" if server_active else None,
            "Restart" if server_active else None,
        ),
        ServiceCardView(
            "webui", "Web interface", "Running" if webui_running else (
                "Installed" if webui.get("installed") else "Not installed"
            ),
            "Open WebUI remains optional and binds through the authenticated local gateway.",
            "stop-webui" if webui_running else "start-webui" if webui.get("installed") else "install-webui",
            "Stop" if webui_running else "Start" if webui.get("installed") else "Install",
        ),
        ServiceCardView(
            "remote", "Remote access", "Connected" if tailscale.get("connected") else (
                "Daemon ready" if daemon else "Off"
            ),
            f"Tailscale daemon {'active' if daemon else 'stopped'} · HTTPS sharing {'on' if sharing_on else 'off'}.",
            "stop-tailscale" if daemon else "start-tailscale",
            "Stop Tailscale" if daemon else "Start Tailscale",
            "stop-sharing" if sharing_on else "start-sharing",
            "Disable HTTPS" if sharing_on else "Enable HTTPS",
        ),
        ServiceCardView(
            "host", "Host mode", str(platform_label),
            "LLM Mode affects this boot only; the next boot remains graphical with model auto-start off.",
            "desktop" if home.get("system_mode") == "llm-session" else "llm-mode",
            "Return to desktop" if home.get("system_mode") == "llm-session" else "Enter LLM Mode",
        ),
        ServiceCardView(
            "thermal", "Thermal safety",
            str(thermal_health.get("effective_state") or thermal_health.get("state") or "UNVERIFIED"),
            str(thermal_health.get("evidence") or "No current thermal evidence."),
        ),
        ServiceCardView(
            "runtime", "llama.cpp runtime", "Recovery required" if runtime.get("recovery_barrier") else "Managed",
            llamacpp_card_text(runtime), "runtime-update", "Update pinned runtime",
            "runtime-rollback" if runtime.get("rollback_available") else None,
            "Roll back" if runtime.get("rollback_available") else None,
        ),
        ServiceCardView(
            "backup", "Backups & recovery", f"{backups} backup(s)",
            "Backup and restore are durable, verified operations; restore retains the prior profile.",
            None, None,
        ),
    )


class SystemPage(ttk.Frame):
    def __init__(self, parent, shell, application) -> None:
        super().__init__(parent)
        self.shell = shell
        self.application = application
        self._disposed = False
        self._last_cards: tuple[ServiceCardView, ...] = ()
        self._has_observation = False
        self._cards_frame = ttk.Frame(self)
        self._cards_frame.pack(fill="both", expand=True)
        self._render_cards((ServiceCardView(
            "loading", "System status", "Checking…",
            "Reading services, thermal safety, runtime, and recovery state.",
        ),))
        self.refresh()

    def mount(self, parent=None):
        del parent
        self.pack(fill="both", expand=True)
        return self

    def enter(self, route_context=None) -> None:
        del route_context
        self.refresh()

    def refresh(self, snapshot=None) -> None:
        if self._disposed:
            return
        if snapshot is not None:
            self._render_cards(snapshot)
            return

        def observe():
            state = self.application.read_model()
            runner = self.shell.runner()

            def safe(call, fallback):
                try:
                    return call()
                except Exception:
                    return fallback

            home = self.application.home.snapshot().to_dict()
            home["system_mode"] = state.get("system_mode")
            server = safe(
                lambda: self.application.model_server.status(state, runner), {}
            )
            webui = safe(
                lambda: self.application.openwebui.status(state, runner), {}
            )
            tailscale = safe(
                lambda: self.application.tailscale.status(runner), {}
            )
            sharing = safe(
                lambda: self.application.sharing.status(state, runner), {}
            )
            runtime = self.application.runtime_lifecycle.status()
            backups = self.application.backup.list_backups()
            return system_card_views(
                home=home, server=server, webui=webui, tailscale=tailscale,
                sharing=sharing, runtime=runtime, backups=len(backups),
                platform_label=self.application.platform.profile.label,
            )

        def apply(cards) -> None:
            self._has_observation = True
            self._render_cards(cards)

        self.shell.request_observation(observe, apply)

    def _render_cards(self, cards) -> None:
        if self._disposed:
            return
        cards = tuple(cards)
        if cards == self._last_cards:
            return
        self._last_cards = cards
        for child in self._cards_frame.winfo_children():
            child.destroy()
        for index, card in enumerate(cards):
            frame = ttk.LabelFrame(self._cards_frame, text=card.title, padding=7)
            frame.grid(row=index // 2, column=index % 2, sticky="nsew", padx=4, pady=4)
            ttk.Label(frame, text=card.state).pack(anchor="w")
            ttk.Label(frame, text=card.detail, wraplength=330).pack(anchor="w", fill="x", pady=(2, 5))
            actions = ttk.Frame(frame)
            actions.pack(fill="x")
            if card.primary_code and card.primary_label:
                ttk.Button(actions, text=card.primary_label, command=lambda code=card.primary_code: self._act(code)).pack(side="left")
            if card.more_code and card.more_label:
                ttk.Button(actions, text=card.more_label, command=lambda code=card.more_code: self._act(code)).pack(side="left", padx=5)
        self._cards_frame.columnconfigure(0, weight=1)
        self._cards_frame.columnconfigure(1, weight=1)

    def focus_primary(self) -> None:
        self._cards_frame.focus_set()

    def observation_failed(self, _error: BaseException) -> None:
        if not self._has_observation:
            self._render_cards((ServiceCardView(
                "unavailable", "System status", "Retrying…",
                "The first status check did not complete. This page will retry automatically.",
            ),))
        self.shell.notice_bar.show_notice(Notice(
            "warning", "System status is stale",
            "One or more read-only probes failed; no ready state was inferred.",
            dismissible=False,
        ))

    def _act(self, code: str) -> None:
        result_box: dict[str, Any] = {}
        state = self.application.read_model()
        runner = self.shell.runner()

        def action() -> None:
            if code == "start-server":
                current = self.application.runtime_config.current()
                result = self.application.activation.activate({
                    "model_alias": current.get("model_alias"),
                    "context_per_slot": current.get("context"),
                    "parallel_slots": current.get("slots"),
                    "requested_by": "gui",
                })
                self.shell.track_operation_id(result.operation_id)
            elif code == "stop-server": result = self.application.model_server.stop(state, runner)
            elif code == "restart-server": result = self.application.model_server.restart(state, runner)
            elif code == "install-webui": result = self.application.openwebui.install(state, runner)
            elif code == "start-webui": result = self.application.openwebui.start(state, runner)
            elif code == "stop-webui": result = self.application.openwebui.stop(state, runner)
            elif code == "start-tailscale": result = self.application.tailscale.start(state, runner)
            elif code == "stop-tailscale": result = self.application.tailscale.stop(state, runner)
            elif code == "start-sharing": result = self.application.sharing.start(state, runner)
            elif code == "stop-sharing": result = self.application.sharing.stop(state, runner)
            elif code == "llm-mode": result = self.application.host_mode.enter_llm_mode(state, runner)
            elif code == "desktop": result = self.application.host_mode.return_to_desktop(state, runner)
            elif code == "runtime-update":
                result = self.application.runtime_lifecycle.update(requested_by="gui")
                self.shell.track_operation_id(result.operation_id)
            elif code == "runtime-rollback":
                result = self.application.runtime_lifecycle.rollback(requested_by="gui")
                self.shell.track_operation_id(result.operation_id)
            else:
                raise ValueError(f"unknown system action {code!r}")
            result_box["result"] = result

        def done() -> None:
            result = result_box.get("result")
            ok = bool(getattr(result, "ok", True))
            self.shell.notice_bar.show_notice(Notice(
                "success" if ok else "error",
                "System action completed" if ok else "System action needs attention",
                "Observed state was refreshed."
                if ok else f"The action ended in {getattr(result, 'status', 'UNKNOWN')}. Open Activity for details.",
                dismissible=ok,
            ))
            self.refresh()

        self.shell._work(action, done)

    def leave(self) -> None:
        return None

    def dispose(self) -> None:
        self._disposed = True


__all__ = [
    "ServiceCardView", "SystemPage", "llamacpp_button_states",
    "llamacpp_card_text", "system_card_views",
]
