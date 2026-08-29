"""Closed navigation and setup-chapter contracts for the native shell."""

from __future__ import annotations

from enum import StrEnum


class Route(StrEnum):
    SETUP = "setup"
    HOME = "home"
    MODELS = "models"
    CHAT = "chat"
    ACTIVITY = "activity"
    SYSTEM = "system"
    SETTINGS = "settings"
    HELP = "help"
    CONNECTIONS = "connections"
    PROFILES = "profiles"
    MAINTENANCE = "maintenance"
    REPAIR = "maintenance/repair"
    UPDATES = "maintenance/updates"


PRIMARY_ROUTES = (
    Route.HOME,
    Route.MODELS,
    Route.CHAT,
    Route.CONNECTIONS,
    Route.ACTIVITY,
    Route.SYSTEM,
    Route.SETTINGS,
    Route.HELP,
)

SAFE_EXTERNAL_ROUTES = frozenset(Route)

SETUP_CHAPTERS = (
    "Machine check",
    "Safety",
    "Prepare system",
    "Choose workload",
    "Install and verify",
)

_STAGE_TO_CHAPTER = {
    "WELCOME": 0,
    "SAFETY_ACKNOWLEDGED": 1,
    "HARDWARE_VALIDATED": 2,
    "TKINTER_READY": 2,
    "LLM_MODE_CONFIGURED": 2,
    "RUNTIME_READY": 2,
    "MODEL_SELECTED": 3,
    "MODEL_PREPARED": 4,
    "PROFILE_APPLIED": 4,
    "SERVICE_INSTALLED": 4,
    "OPTIONALS_CONFIGURED": 4,
    "VERIFIED": 4,
    "COMPLETE": 4,
}


def setup_chapter_for(stage: str) -> int:
    """Map every canonical setup stage to exactly one user chapter."""
    try:
        return _STAGE_TO_CHAPTER[stage]
    except KeyError:
        raise ValueError(f"unknown setup stage {stage!r}") from None


def parse_route(value: str | Route) -> Route:
    if isinstance(value, Route):
        return value
    try:
        return Route(value)
    except ValueError:
        raise ValueError(f"unknown application route {value!r}") from None


def available_routes(*, setup_complete: bool, operational: bool = True) -> tuple[Route, ...]:
    if not operational or not setup_complete:
        return (Route.SETUP,)
    return PRIMARY_ROUTES
