"""System controls grouped by operator task and composed service state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import tkinter as tk
from tkinter import ttk

from .routes import Route
from .view_state import Confirmation, Notice
from .widgets import VerticalScrollFrame


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
    readiness: Mapping[str, Any] | None = None,
) -> tuple[ServiceCardView, ...]:
    cards = home.get("cards") if isinstance(home.get("cards"), Mapping) else {}
    thermal = cards.get("thermal") if isinstance(cards.get("thermal"), Mapping) else {}
    thermal_health = thermal.get("health") if isinstance(thermal.get("health"), Mapping) else {}
    server_active = bool(server.get("active"))
    webui_running = bool(webui.get("running"))
    daemon = bool(tailscale.get("daemon_active"))
    webui_shared = bool(sharing.get("webui_enabled"))
    sharing_on = bool(webui_shared or sharing.get("api_enabled"))
    local_webui_url = str(webui.get("url") or "http://127.0.0.1:3000")
    tailnet_webui_url = str(sharing.get("webui_url") or "")
    tailnet_addresses = tailscale.get("addresses")
    tailnet_ip = next((
        str(address) for address in (
            tailnet_addresses if isinstance(tailnet_addresses, (list, tuple)) else ()
        ) if ":" not in str(address)
    ), "")
    readiness_components = (
        readiness.get("components")
        if isinstance(readiness, Mapping)
        and isinstance(readiness.get("components"), Mapping) else {}
    )

    def observed_state(component_id: str, fallback: str) -> str:
        component = readiness_components.get(component_id)
        if isinstance(component, Mapping):
            return str(component.get("state") or fallback)
        return fallback

    def observed_summary(component_id: str) -> str:
        component = readiness_components.get(component_id)
        return (
            str(component.get("summary") or "")
            if isinstance(component, Mapping) else ""
        )
    webui_detail = f"Local: {local_webui_url} (this BC250 only)."
    if webui_shared and tailnet_webui_url:
        webui_detail += f" Tailnet HTTPS: {tailnet_webui_url}"
    else:
        webui_detail += " Tailnet HTTPS: not enabled."
    if tailnet_ip:
        webui_detail += f" Device Tailscale IP: {tailnet_ip}."
    desktop_active = home.get("desktop_active")
    llm_console_active = (
        home.get("system_mode") == "llm-session" and desktop_active is not True
    )
    return (
        ServiceCardView(
            "server", "Model server", observed_state(
                "model", "Service active" if server_active else "Stopped"),
            observed_summary("model") or
            "Single-owner llama.cpp service; model auto-start remains off for reboot.",
            "stop-server" if server_active else "start-server",
            "Stop" if server_active else "Start current model",
            "restart-server" if server_active else None,
            "Restart" if server_active else None,
        ),
        ServiceCardView(
            "webui", "Web interface", observed_state("openwebui", "Running" if webui_running else (
                "Installed" if webui.get("installed") else "Not installed"
            )),
            ((observed_summary("openwebui") + " ") if observed_summary("openwebui") else "")
            + webui_detail,
            "stop-webui" if webui_running else "start-webui" if webui.get("installed") else "install-webui",
            "Stop" if webui_running else "Start" if webui.get("installed") else "Install",
            "update-webui" if webui.get("installed") else None,
            "Update pinned Open WebUI" if webui.get("installed") else None,
        ),
        ServiceCardView(
            "remote", "Remote access", (
                "READY" if isinstance(readiness, Mapping)
                and readiness.get("remote_client_ready") else
                observed_state("client_verification", "Connected" if tailscale.get("connected") else (
                "Daemon ready" if daemon else "Off"
            ))),
            (
                ((observed_summary("client_verification") + " ")
                 if observed_summary("client_verification") else "") +
                f"Tailscale daemon {'active' if daemon else 'stopped'} · "
                f"IP {tailnet_ip or 'not assigned'} · HTTPS sharing "
                f"{'on' if sharing_on else 'off'}."
            ),
            "stop-tailscale" if daemon else "start-tailscale",
            "Stop Tailscale" if daemon else "Start Tailscale",
            "stop-sharing" if sharing_on else "start-sharing",
            "Disable HTTPS" if sharing_on else "Enable HTTPS",
        ),
        ServiceCardView(
            "host", "Host mode",
            "Text console active" if llm_console_active else str(platform_label),
            (
                "LLM Mode is using the full-screen text console for this boot; "
                "the next boot remains graphical with model auto-start off."
                if llm_console_active else
                "Entering LLM Mode closes graphical desktop apps and opens a "
                "full-screen tty1 login. Save desktop work first. The next boot remains graphical."
            ),
            "desktop" if llm_console_active else "llm-mode",
            "Return to desktop" if llm_console_active else "Enter LLM Mode…",
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
            "backups", "Open Backups",
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
        self._card_widgets: dict[str, dict[str, Any]] = {}
        scroll = VerticalScrollFrame(self)
        scroll.pack(fill="both", expand=True)
        self._cards_frame = scroll.inner
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
            home.update(safe(
                lambda: self.application.host_mode.status(state, runner),
                {"system_mode": state.get("system_mode"), "desktop_active": True},
            ))
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
            readiness = safe(
                lambda: self.application.readiness.snapshot(
                    target_journey="remote_client").to_dict(), {})
            runtime = self.application.runtime_lifecycle.status()
            backups = self.application.backup.list_backups()
            from ..runtime_policy import observed_policy
            policy = observed_policy(self.application.paths.app_dir)
            cards = system_card_views(
                home=home, server=server, webui=webui, tailscale=tailscale,
                sharing=sharing, runtime=runtime, backups=len(backups),
                platform_label=self.application.platform.profile.label,
                readiness=readiness,
            )
            return cards + (ServiceCardView(
                "monitoring", "Monitoring & idle policy", str(policy["monitoring"]).capitalize(),
                f"Last poll: {policy.get('observed_at', 'unavailable')}. "
                f"Stop: {policy.get('stop_outcome') or 'not requested'}. "
                f"Active requests: {policy.get('active_requests', 'unknown')}. "
                f"Idle policy: {policy.get('idle_policy', 'unknown')}. "
                f"{policy.get('reason', '')}"),)

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
        wanted = {card.key for card in cards}
        for key in tuple(self._card_widgets):
            if key not in wanted:
                self._card_widgets[key]["frame"].destroy()
                del self._card_widgets[key]
        for index, card in enumerate(cards):
            widgets = self._card_widgets.get(card.key)
            if widgets is None:
                frame = ttk.LabelFrame(self._cards_frame, padding=7)
                state_var = tk.StringVar(value="")
                detail_var = tk.StringVar(value="")
                ttk.Label(frame, textvariable=state_var).pack(anchor="w")
                ttk.Label(
                    frame, textvariable=detail_var, wraplength=330,
                ).pack(anchor="w", fill="x", pady=(2, 5))
                actions = ttk.Frame(frame)
                actions.pack(fill="x")
                widgets = {
                    "frame": frame,
                    "state": state_var,
                    "detail": detail_var,
                    "primary": ttk.Button(actions),
                    "more": ttk.Button(actions),
                }
                self._card_widgets[card.key] = widgets
            frame = widgets["frame"]
            frame.configure(text=card.title)
            frame.grid(row=index, column=0, sticky="nsew", padx=4, pady=4)
            widgets["state"].set(card.state)
            widgets["detail"].set(card.detail)
            primary = widgets["primary"]
            more = widgets["more"]
            if card.primary_code and card.primary_label:
                primary.configure(
                    text=card.primary_label,
                    command=lambda code=card.primary_code: self._act(code),
                )
                if not primary.winfo_manager():
                    primary.pack(side="left")
            else:
                primary.pack_forget()
            if card.more_code and card.more_label:
                more.configure(
                    text=card.more_label,
                    command=lambda code=card.more_code: self._act(code),
                )
                if not more.winfo_manager():
                    more.pack(side="left", padx=5)
            else:
                more.pack_forget()
        self._cards_frame.columnconfigure(0, weight=1)
        self._cards_frame.columnconfigure(1, weight=0)

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
        if code == "backups":
            self.shell.navigate("maintenance/backups")
            return
        if code == "llm-mode":
            self.shell.drawer.show_confirmation(
                Confirmation(
                    "Enter LLM Mode?",
                    "The graphical desktop and all desktop applications will close. "
                    "A full-screen tty1 login will replace them for this boot.",
                    "Save work in other applications first. Reboot returns to the "
                    "graphical desktop, or sign in on tty1 and run "
                    "~/.bc250-llm-mode/app-venv/bin/bc250-llm-mode desktop --now.",
                    "Leave desktop",
                ),
                lambda: self._run_action(code),
            )
            return
        self._run_action(code)

    def _run_action(self, code: str) -> None:
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
            elif code == "update-webui": result = self.application.openwebui.update(state, runner)
            elif code == "start-tailscale": result = self.application.tailscale.start(state, runner)
            elif code == "stop-tailscale": result = self.application.tailscale.stop(state, runner)
            elif code == "start-sharing": result = self.application.sharing.start(state, runner)
            elif code == "stop-sharing": result = self.application.sharing.stop(state, runner)
            elif code == "llm-mode":
                result = self.application.host_mode.enter_llm_mode(
                    state, runner, activate_console_now=True,
                )
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
