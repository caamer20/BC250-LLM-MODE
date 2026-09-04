"""Closed navigation and setup-chapter contracts for the native shell."""

from __future__ import annotations

from dataclasses import dataclass
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
    MORE = "more"
    PROFILES = "profiles"
    MAINTENANCE = "maintenance"
    REPAIR = "maintenance/repair"
    UPDATES = "maintenance/updates"
    BACKUPS = "maintenance/backups"


PRIMARY_ROUTES = (
    Route.HOME,
    Route.MODELS,
    Route.CHAT,
    Route.CONNECTIONS,
    Route.MORE,
)

MORE_ROUTES = (
    Route.ACTIVITY,
    Route.MAINTENANCE,
    Route.SYSTEM,
    Route.SETTINGS,
    Route.HELP,
)


@dataclass(frozen=True)
class MoreDestination:
    route: Route
    title: str
    summary: str


MORE_DESTINATIONS = (
    MoreDestination(
        Route.ACTIVITY, "Activity",
        "See running work, completed operations, and recovery details."),
    MoreDestination(
        Route.MAINTENANCE, "Maintenance",
        "Review prioritized checks, guided repair, cleanup, and updates."),
    MoreDestination(
        Route.SYSTEM, "System",
        "Inspect current-boot services, sharing, thermals, and host mode."),
    MoreDestination(
        Route.SETTINGS, "Settings",
        "Adjust appearance, notifications, privacy, and saved preferences."),
    MoreDestination(
        Route.HELP, "Help",
        "Open the quick start, connection guidance, and offline glossary."),
)

if tuple(item.route for item in MORE_DESTINATIONS) != MORE_ROUTES:
    raise RuntimeError("More destination inventory drifted from the route contract")

MANAGEMENT_ROUTES = (
    *PRIMARY_ROUTES,
    Route.PROFILES,
    *MORE_ROUTES,
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
    return MANAGEMENT_ROUTES
