"""Task-oriented Home page over the composed appliance snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import tkinter as tk
from tkinter import ttk

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


def build_home_view(snapshot: Mapping[str, Any]) -> HomeView:
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
    elif _health(model) == "READY" and _health(inference) == "READY":
        headline = f"Ready to chat with {model_name}"
        explanation = "The selected artifact and a current inference probe are verified."
        primary = PrimaryAction("chat", "Start chatting", Route.CHAT.value, explanation)
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
        f"{float(available) / 1024**3:.1f} GiB available"
        if isinstance(available, (int, float)) else evidence(storage)
    )
    gateway = integrations.get("gateway")
    remote_summary = (
        "Authenticated gateway ready"
        if isinstance(gateway, Mapping) and gateway.get("verified")
        else evidence(integrations)
    )
    cards = (
        HomeCardView(
            "model", "Model & inference",
            _health(inference) if _health(model) == "READY" else _health(model),
            f"{model_name} · {evidence(inference)}",
            bool(model.get("stale") or inference.get("stale")),
            str(inference.get("as_of") or model.get("as_of") or "") or None,
        ),
        HomeCardView(
            "thermal", "Temperature & memory", _health(thermal), evidence(thermal),
            bool(thermal.get("stale")), str(thermal.get("as_of") or "") or None,
        ),
        HomeCardView(
            "operations", "Active work", _health(operations), evidence(operations),
            bool(operations.get("stale")), str(operations.get("as_of") or "") or None,
        ),
        HomeCardView(
            "storage", "Storage", _health(storage), storage_summary,
            bool(storage.get("stale")), str(storage.get("as_of") or "") or None,
        ),
        HomeCardView(
            "remote", "Remote access", _health(integrations), remote_summary,
            bool(integrations.get("stale")), str(integrations.get("as_of") or "") or None,
        ),
    )
    shortcuts = (
        ("Browse models", Route.MODELS.value),
        ("View activity", Route.ACTIVITY.value),
        ("System details", Route.SYSTEM.value),
        ("Help & checks", Route.HELP.value),
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
        for index, key in enumerate(("model", "thermal", "operations", "storage", "remote")):
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
        self._apply_snapshot({})
        self.refresh()

    def mount(self, parent=None):
        del parent
        self.pack(fill="both", expand=True)
        return self

    def enter(self, route_context=None) -> None:
        del route_context
        self.refresh()

    def refresh(self, snapshot: Mapping[str, Any] | None = None) -> None:
        if self._disposed:
            return
        if snapshot is None:
            self.shell.request_observation(
                lambda: self.application.home.snapshot().to_dict(),
                self._apply_snapshot,
            )
            return
        self._apply_snapshot(snapshot)

    def _apply_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        if self._disposed:
            return
        source = dict(snapshot)
        view = build_home_view(source)
        self._view = view
        self._headline.set(view.headline)
        self._explanation.set(view.explanation)
        self._primary_text.set(view.primary.label)
        for card in view.cards:
            frame = getattr(self, f"_{card.key}_frame")
            frame.configure(text=card.title)
            state_var, summary_var = self._card_vars[card.key]
            state_var.set(("STALE · " if card.stale else "") + card.state)
            summary_var.set(card.summary)
        for child in self._shortcuts.winfo_children():
            child.destroy()
        for label, route in view.shortcuts[:4]:
            ttk.Button(
                self._shortcuts, text=label,
                command=lambda target=route: self.shell.navigate(target),
            ).pack(side="left", padx=(0, 5))

    def focus_primary(self) -> None:
        self._primary_button.focus_set()

    def observation_failed(self, _error: BaseException) -> None:
        self._explanation.set(
            "Status could not be refreshed; the last bounded view remains visible."
        )

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
