"""Task-oriented Home page over the composed appliance snapshot."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

import tkinter as tk
from tkinter import ttk

from ..presentation import format_bytes
from ..ux_guidance import friendly_state
from .routes import Route
from .view_state import Notice, PrimaryAction


@dataclass(frozen=True)
class HomeCardView:
    key: str
    title: str
    state: str
    summary: str
    stale: bool = False
    age: str | None = None


@dataclass(frozen=True)
class HomeView:
    headline: str
    explanation: str
    primary: PrimaryAction
    cards: tuple[HomeCardView, ...]
    shortcuts: tuple[tuple[str, str], ...]


def _card(snapshot: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    cards = snapshot.get("cards")
    if not isinstance(cards, Mapping):
        return {}
    item = cards.get(name)
    return item if isinstance(item, Mapping) else {}


def _health(card: Mapping[str, Any]) -> str:
    health = card.get("health")
    if not isinstance(health, Mapping):
        return "UNVERIFIED"
    if card.get("stale"):
        return "STALE"
    return str(health.get("effective_state") or health.get("state") or "UNVERIFIED")


def _maintenance_item(snapshot: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not isinstance(snapshot, Mapping):
        return None
    items = snapshot.get("items")
    if not isinstance(items, (list, tuple)):
        return None
    candidates = [
        item for item in items
        if isinstance(item, Mapping)
        and str(item.get("category")) != "INFORMATION"
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (int(item.get("priority") or 99), str(item.get("code") or "")),
    )


def build_home_view(
    snapshot: Mapping[str, Any],
    maintenance: Mapping[str, Any] | None = None,
    readiness: Mapping[str, Any] | None = None,
) -> HomeView:
    """Choose one safety-first next action and five bounded health cards."""
    identity = _card(snapshot, "identity")
    runtime = _card(snapshot, "runtime")
    model = _card(snapshot, "model")
    inference = _card(snapshot, "inference")
    thermal = _card(snapshot, "thermal")
    operations = _card(snapshot, "operations")
    storage = _card(snapshot, "storage")
    integrations = _card(snapshot, "integrations")

    model_name = str(model.get("desired") or model.get("observed") or "your model")
    operation_summary = operations.get("summary")
    if not isinstance(operation_summary, Mapping):
        operation_summary = {}
    native_ready = (
        bool(readiness.get("native_chat_ready"))
        if isinstance(readiness, Mapping) else
        (_health(model) == "READY" and _health(inference) == "READY")
    )
    readiness_problem = (
        str(readiness.get("primary_problem_code") or "")
        if isinstance(readiness, Mapping) else ""
    )

    priority_maintenance = _maintenance_item(maintenance)
    if _health(thermal) in {"BLOCKED", "RECOVERY_REQUIRED", "REPAIR_REQUIRED"}:
        headline = "Cooling required"
        explanation = "A thermal safety latch blocks model work until the hardware is cool and explicitly reset."
        primary = PrimaryAction("thermal", "View thermal safety", Route.SYSTEM.value, explanation)
    elif _health(operations) in {"RECOVERY_REQUIRED", "REPAIR_REQUIRED"}:
        headline = "This appliance needs attention"
        explanation = "Durable work needs a reviewed recovery action before another model change."
        primary = PrimaryAction("recovery", "Open recovery activity", Route.ACTIVITY.value, explanation)
    elif not bool(identity.get("setup_complete")):
        headline = "Finish setting up this BC-250"
        explanation = "Setup progress is saved and can be resumed safely."
        primary = PrimaryAction("setup", "Resume setup", Route.SETUP.value, explanation)
    elif _health(operations) == "BUSY" or int(operation_summary.get("active_count") or 0):
        headline = "Work is in progress"
        explanation = "One durable operation owns the appliance resources it needs."
        primary = PrimaryAction("activity", "View activity", Route.ACTIVITY.value, explanation)
    elif priority_maintenance is not None:
        headline = str(priority_maintenance.get("title") or "Maintenance needs attention")
        explanation = str(
            priority_maintenance.get("impact")
            or "Review the prioritized maintenance evidence before starting more model work."
        )
        primary = PrimaryAction(
            "maintenance",
            "Open Maintenance",
            Route.MAINTENANCE.value,
            explanation,
        )
    elif native_ready:
        headline = f"Ready to chat with {model_name}"
        explanation = "The selected model identity and a current local completion are verified."
        primary = PrimaryAction("chat", "Start chatting", Route.CHAT.value, explanation)
    elif readiness_problem == "NATIVE_CHAT_UNVERIFIED":
        headline = f"Check {model_name} before chatting"
        explanation = "The model protocol is ready, but its completion evidence is missing or stale."
        primary = PrimaryAction("checks", "Run checks", Route.SYSTEM.value, explanation)
    elif _health(model) == "READY" and _health(runtime) == "READY":
        headline = f"{model_name} is ready but stopped"
        explanation = "The model and runtime are verified; start them when you are ready."
        primary = PrimaryAction("start", "Start model", Route.HOME.value, explanation)
    elif int(model.get("installed_count") or 0) == 0:
        headline = "Choose your first model"
        explanation = "The library shows only standard-layout choices with live BC-250 fit evidence."
        primary = PrimaryAction("models", "Browse models", Route.MODELS.value, explanation)
    else:
        headline = "Status needs refreshing"
        explanation = "Current readiness evidence is missing or stale; no green state is inferred."
        primary = PrimaryAction("checks", "Run checks", Route.SYSTEM.value, explanation)

    def evidence(card: Mapping[str, Any]) -> str:
        health = card.get("health")
        if isinstance(health, Mapping):
            return str(health.get("evidence") or health.get("reason") or "No current evidence")
        return "No current evidence"

    available = storage.get("available_bytes")
    storage_summary = (
        f"{format_bytes(available)} available"
        if isinstance(available, (int, float)) else evidence(storage)
    )
    readiness_components = (
        readiness.get("components")
        if isinstance(readiness, Mapping)
        and isinstance(readiness.get("components"), Mapping) else {}
    )
    remote_component = (
        readiness_components.get("client_verification")
        if isinstance(readiness_components.get("client_verification"), Mapping)
        else None
    )
    gateway = integrations.get("gateway")
    if remote_component is not None:
        remote_summary = str(remote_component.get("summary") or "Remote access is not verified.")
        remote_state = (
            "READY" if readiness.get("remote_client_ready")
            else str(remote_component.get("state") or "UNKNOWN")
        )
        remote_stale = remote_component.get("problem_code") in {
            "CLIENT_VERIFICATION_STALE", "CLIENT_VERIFICATION_INVALIDATED"
        }
        remote_age = str(remote_component.get("observed_at") or "") or None
    else:
        remote_summary = (
            "Authenticated gateway ready"
            if isinstance(gateway, Mapping) and gateway.get("verified")
            else evidence(integrations)
        )
        remote_state = _health(integrations)
        remote_stale = bool(integrations.get("stale"))
        remote_age = str(integrations.get("as_of") or "") or None
    if native_ready:
        chat_state = "READY"
        chat_summary = "A current local completion is verified."
    else:
        chat_state = _health(inference)
        chat_summary = evidence(inference)
    if priority_maintenance is not None:
        priority = int(priority_maintenance.get("priority") or 9)
        maintenance_state = "BLOCKED" if priority <= 3 else "DEGRADED"
        maintenance_summary = str(
            priority_maintenance.get("title")
            or priority_maintenance.get("impact")
            or "A maintenance item needs review.")
    elif _health(operations) not in {"READY", "UNAVAILABLE"}:
        maintenance_state = _health(operations)
        maintenance_summary = evidence(operations)
    elif _health(storage) not in {"READY", "UNAVAILABLE"}:
        maintenance_state = _health(storage)
        maintenance_summary = storage_summary
    else:
        maintenance_state = "READY"
        maintenance_summary = f"No prioritized items · {storage_summary}"
    cards = (
        HomeCardView(
            "model", "Model", _health(model), model_name,
            bool(model.get("stale")),
            str(model.get("as_of") or "") or None,
        ),
        HomeCardView(
            "chat", "Chat", chat_state, chat_summary,
            bool(inference.get("stale")), str(inference.get("as_of") or "") or None,
        ),
        HomeCardView(
            "connections", "Connections", remote_state, remote_summary,
            remote_stale, remote_age,
        ),
        HomeCardView(
            "safety", "Safety", _health(thermal), evidence(thermal),
            bool(thermal.get("stale")), str(thermal.get("as_of") or "") or None,
        ),
        HomeCardView(
            "maintenance", "Maintenance", maintenance_state, maintenance_summary,
            bool(operations.get("stale") or storage.get("stale")),
            str(operations.get("as_of") or storage.get("as_of") or "") or None,
        ),
    )
    shortcuts = (
        ("Browse models", Route.MODELS.value),
        ("Connect another device", Route.CONNECTIONS.value),
        ("Maintenance", Route.MAINTENANCE.value),
        ("System details", Route.SYSTEM.value),
    )
    return HomeView(headline, explanation, primary, cards, shortcuts)


class HomePage(ttk.Frame):
    """One decision, five health cards, and at most four shortcuts."""

    def __init__(self, parent, shell, application) -> None:
        super().__init__(parent)
        self.shell = shell
        self.application = application
        self._disposed = False
        self._view: HomeView | None = None
        self._headline = tk.StringVar(value="Loading appliance status…")
        self._explanation = tk.StringVar(value="")
        self._primary_text = tk.StringVar(value="Refresh")
        ttk.Label(self, textvariable=self._headline, font=("TkDefaultFont", 18, "bold")).pack(anchor="w")
        ttk.Label(self, textvariable=self._explanation, wraplength=720).pack(anchor="w", fill="x", pady=(3, 8))
        self._primary_button = ttk.Button(self, textvariable=self._primary_text, command=self._run_primary)
        self._primary_button.pack(anchor="w", pady=(0, 12))
        self._cards = ttk.Frame(self)
        self._cards.pack(fill="x")
        self._card_vars: dict[str, tuple[tk.StringVar, tk.StringVar]] = {}
        for index, key in enumerate((
            "model", "chat", "connections", "safety", "maintenance",
        )):
            frame = ttk.LabelFrame(self._cards, text="", padding=7)
            frame.grid(row=index // 3, column=index % 3, sticky="nsew", padx=3, pady=3)
            state_var = tk.StringVar(value="Checking")
            summary_var = tk.StringVar(value="")
            ttk.Label(frame, textvariable=state_var).pack(anchor="w")
            ttk.Label(frame, textvariable=summary_var, wraplength=220).pack(anchor="w", fill="x")
            self._card_vars[key] = (state_var, summary_var)
            setattr(self, f"_{key}_frame", frame)
        for column in range(3):
            self._cards.columnconfigure(column, weight=1)
        self._shortcuts = ttk.Frame(self)
        self._shortcuts.pack(fill="x", pady=(10, 0))
        self._shortcut_routes: list[str | None] = [None] * 4
        self._shortcut_buttons: list[ttk.Button] = []
        for index in range(4):
            button = ttk.Button(
                self._shortcuts,
                command=lambda slot=index: self._run_shortcut(slot),
            )
            button.pack(side="left", padx=(0, 5))
            self._shortcut_buttons.append(button)
        self._apply_snapshot({})
        self.refresh()

    def mount(self, parent=None):
        del parent
        self.pack(fill="both", expand=True)
        return self

    def enter(self, route_context=None) -> None:
        del route_context
        self.refresh()

    def refresh(self, snapshot: Mapping[str, Any] | tuple[Any, Any] | None = None) -> None:
        if self._disposed:
            return
        if snapshot is None:
            self.shell.request_observation(
                lambda: (
                    self.application.home.snapshot().to_dict(),
                    self.application.maintenance_snapshot.snapshot().to_dict(),
                    self.application.readiness.snapshot(
                        target_journey="native_chat").to_dict(),
                    self._monitoring(),
                ),
                self._apply_snapshot,
            )
            return
        self._apply_snapshot(snapshot)

    def _monitoring(self):
        from ..runtime_policy import observed_policy
        return observed_policy(self.application.paths.app_dir)

    def _apply_snapshot(self, snapshot: Mapping[str, Any] | tuple[Any, Any]) -> None:
        if self._disposed:
            return
        if isinstance(snapshot, tuple):
            home_source = snapshot[0]
            maintenance_source = snapshot[1] if len(snapshot) > 1 else None
            readiness_source = snapshot[2] if len(snapshot) > 2 else None
        else:
            home_source, maintenance_source, readiness_source = snapshot, None, None
        source = dict(home_source)
        maintenance = (
            dict(maintenance_source)
            if isinstance(maintenance_source, Mapping)
            else None
        )
        readiness = (
            dict(readiness_source)
            if isinstance(readiness_source, Mapping) else None
        )
        view = build_home_view(source, maintenance, readiness)
        if isinstance(snapshot, tuple) and len(snapshot) > 3:
            policy = snapshot[3]
            view = replace(view, cards=tuple(replace(card, summary=card.summary +
                f" Monitoring: {policy.get('monitoring', 'unavailable')}. Stop: {policy.get('stop_outcome') or 'not requested'}. See System for the last poll and idle policy.")
                if card.key == "safety" else card for card in view.cards))
        self._view = view
        self._headline.set(view.headline)
        self._explanation.set(f"Why this is next: {view.explanation}")
        self._primary_text.set(view.primary.label)
        for card in view.cards:
            frame = getattr(self, f"_{card.key}_frame")
            frame.configure(text=card.title)
            state_var, summary_var = self._card_vars[card.key]
            state_var.set(
                ("Needs a fresh check · " if card.stale else "")
                + friendly_state(card.state)
            )
            summary_var.set(card.summary)
        shortcuts = view.shortcuts[:4]
        for index, button in enumerate(self._shortcut_buttons):
            if index < len(shortcuts):
                label, route = shortcuts[index]
                self._shortcut_routes[index] = route
                button.configure(text=label)
                if not button.winfo_manager():
                    button.pack(side="left", padx=(0, 5))
            else:
                self._shortcut_routes[index] = None
                button.pack_forget()

    def focus_primary(self) -> None:
        self._primary_button.focus_set()

    def observation_failed(self, _error: BaseException) -> None:
        self._explanation.set(
            "Status could not be refreshed; the last bounded view remains visible."
        )

    def _run_shortcut(self, index: int) -> None:
        target = self._shortcut_routes[index]
        if target is not None:
            self.shell.navigate(target)

    def _run_primary(self) -> None:
        if self._view is None:
            self.refresh()
            return
        action = self._view.primary
        if action.code != "start":
            self.shell.navigate(action.route)
            return
        result_box: dict[str, Any] = {}

        def action_fn() -> None:
            current = self.application.runtime_config.current()
            payload = {
                "model_alias": current.get("model_alias"),
                "context_per_slot": current.get("context"),
                "parallel_slots": current.get("slots"),
                "requested_by": "gui",
            }
            revision = int(current.get("revision") or 0)
            if revision > 0:
                payload["expected_runtime_revision"] = revision
            outcome = self.application.activation.activate(payload)
            result_box["outcome"] = outcome
            self.shell.track_operation_id(outcome.operation_id)

        def done() -> None:
            outcome = result_box.get("outcome")
            if outcome is not None and outcome.ok:
                self.shell.notice_bar.show_notice(Notice(
                    "success", "Model started", "Live verification completed successfully."
                ))
            else:
                status = getattr(outcome, "status", "UNKNOWN")
                self.shell.notice_bar.show_notice(Notice(
                    "error", "Model did not start",
                    f"Activation ended in {status}. Open Activity for durable details.",
                    action_label="View activity", action_route=Route.ACTIVITY.value,
                    dismissible=False,
                ))
            self.refresh()

        self.shell._work(action_fn, done)

    def leave(self) -> None:
        return None

    def dispose(self) -> None:
        self._disposed = True


__all__ = ["HomeCardView", "HomePage", "HomeView", "build_home_view"]
