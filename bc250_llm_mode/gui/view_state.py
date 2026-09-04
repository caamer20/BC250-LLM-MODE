"""Pure presentation records shared by native pages and headless tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

LEVELS = frozenset({"info", "success", "warning", "error"})


@dataclass(frozen=True)
class Notice:
    level: str
    title: str
    message: str
    action_label: str | None = None
    action_route: str | None = None
    details: str | None = None
    dismissible: bool = True

    def __post_init__(self) -> None:
        if self.level not in LEVELS:
            raise ValueError(f"unknown notice level {self.level!r}")
        if not self.title.strip() or not self.message.strip():
            raise ValueError("notice title and message are required")
        if len(self.title) > 160 or len(self.message) > 2048:
            raise ValueError("notice text exceeds its presentation bound")
        if bool(self.action_label) != bool(self.action_route):
            raise ValueError("notice action label and route must be supplied together")
        if self.details is not None and len(self.details) > 8192:
            raise ValueError("notice details exceed 8 KiB")


@dataclass(frozen=True)
class Confirmation:
    title: str
    consequence: str
    recovery: str
    confirm_label: str
    destructive: bool = False
    typed_phrase: str | None = None

    def __post_init__(self) -> None:
        for value in (self.title, self.consequence, self.recovery, self.confirm_label):
            if not value.strip():
                raise ValueError("confirmation fields may not be blank")
        if self.typed_phrase and len(self.typed_phrase) > 64:
            raise ValueError("typed confirmation phrase is too long")


@dataclass(frozen=True)
class PrimaryAction:
    code: str
    label: str
    route: str
    reason: str


def home_primary_action(home: dict[str, Any]) -> PrimaryAction:
    """Choose exactly one next action, with safety/recovery always first."""
    cards = home.get("cards") if isinstance(home.get("cards"), dict) else {}

    def state(name: str) -> str:
        card = cards.get(name) if isinstance(cards.get(name), dict) else {}
        health = card.get("health") if isinstance(card.get("health"), dict) else {}
        return str(health.get("effective_state") or health.get("state") or "UNVERIFIED")

    if state("thermal") in {"BLOCKED", "RECOVERY_REQUIRED", "REPAIR_REQUIRED"}:
        return PrimaryAction("thermal", "Review thermal safety", "system", "A thermal safety condition blocks model work.")
    if state("operations") in {"RECOVERY_REQUIRED", "REPAIR_REQUIRED"}:
        return PrimaryAction("recovery", "Resume recovery", "activity", "Durable work needs attention before a new action starts.")
    if state("model") in {"UNAVAILABLE", "UNVERIFIED"}:
        return PrimaryAction("choose-model", "Choose a model", "models", "No verified model is ready to use.")
    if state("runtime") in {"UNAVAILABLE", "BLOCKED"}:
        return PrimaryAction("runtime", "Review runtime", "system", "The inference runtime is not ready.")
    if state("inference") in {"READY", "DEGRADED"}:
        return PrimaryAction("chat", "Start chatting", "chat", "The selected model is ready.")
    return PrimaryAction("start", "Start current model", "home", "A verified model is available but inference is stopped.")


def sanitize_exception(exc: BaseException) -> str:
    """Never surface arbitrary exception text as the user-facing headline."""
    return f"{exc.__class__.__name__}: the action could not be completed. Open details for the operation or setup log."
