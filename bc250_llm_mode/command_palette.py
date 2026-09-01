"""Bounded, local-only command-palette contracts.

The palette is a discoverability surface, never an execution authority.
Protected entries route to their ordinary preview page and cannot carry a
callable or shell command.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


MAX_PALETTE_COMMANDS = 32
MAX_PALETTE_RESULTS = 12
MAX_PALETTE_QUERY = 128
_TOKENS = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class PaletteCommand:
    command_id: str
    label: str
    description: str
    route: str
    keywords: tuple[str, ...] = ()
    context: tuple[tuple[str, str], ...] = ()
    protected: bool = False
    enabled: bool = True
    blocked_reason: str | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", self.command_id):
            raise ValueError("palette command id is invalid")
        if not self.label.strip() or not self.description.strip():
            raise ValueError("palette command copy is required")
        if len(self.label) > 80 or len(self.description) > 240:
            raise ValueError("palette command copy exceeds its bound")
        if not self.route or len(self.route) > 64:
            raise ValueError("palette command route is invalid")
        if not self.enabled and not self.blocked_reason:
            raise ValueError("disabled palette commands require a reason")
        if self.blocked_reason and len(self.blocked_reason) > 240:
            raise ValueError("palette blocked reason exceeds its bound")

    def route_context(self) -> dict[str, str]:
        return dict(self.context)


def palette_commands(
    *, setup_complete: bool, operational: bool,
) -> tuple[PaletteCommand, ...]:
    """Build the closed command set from already-observed application state."""
    if not operational or not setup_complete:
        reason = (
            "Application repair must finish before management pages are available."
            if not operational
            else "Finish the saved setup chapters before management pages are available."
        )
        return (
            PaletteCommand(
                "continue-setup", "Continue setup", reason, "setup",
                ("resume", "safety", "install"),
            ),
        )

    navigation = (
        ("open-home", "Open Home", "Review the appliance summary and primary safe action.", "home", ("status", "start", "stop")),
        ("open-models", "Open Models", "Browse, import, install, and select verified model files.", "models", ("library", "gguf", "quantization")),
        ("open-profiles", "Open Profiles", "Preview named context and concurrency workloads.", "profiles", ("context", "slots", "users", "performance")),
        ("open-chat", "Open Chat", "Use the bounded native chat with the active model.", "chat", ("conversation", "prompt")),
        ("open-connections", "Open Connections", "Set up Open WebUI, PocketPal, or another authenticated client.", "connections", ("endpoint", "phone", "api", "credential")),
        ("open-more", "Open More", "Browse Activity, Maintenance, System, Settings, and Help.", "more", ("tools", "details", "advanced")),
        ("open-activity", "Open Activity", "Review durable operations, progress, and recovery evidence.", "activity", ("operation", "recovery", "history")),
        ("open-maintenance", "Open Maintenance", "Review the prioritized maintenance inbox.", "maintenance", ("health", "storage", "notification")),
        ("open-system", "Open System", "Manage model, Open WebUI, Tailscale, sharing, and host mode.", "system", ("service", "desktop", "llm")),
        ("open-settings", "Open Settings", "Edit workload, appearance, and notification preferences.", "settings", ("context", "slots", "theme")),
        ("open-help", "Open Help", "Read quick start and the bundled offline glossary.", "help", ("glossary", "doctor", "support")),
    )
    commands = [
        PaletteCommand(command_id, label, description, route, keywords)
        for command_id, label, description, route, keywords in navigation
    ]
    commands.extend((
        PaletteCommand(
            "start-stop-model", "Preview model start or stop",
            "Open Home to review current evidence before changing the server.",
            "home", ("llm", "server", "run"), (("action", "model-lifecycle"),),
            protected=True,
        ),
        PaletteCommand(
            "install-model", "Preview model installation",
            "Open Models to select an exact artifact, quantization, and fit preview.",
            "models", ("download", "import", "huggingface"),
            (("action", "install"),), protected=True,
        ),
        PaletteCommand(
            "guided-repair", "Preview a guided repair",
            "Open the Repair Center; the palette cannot run a repair.",
            "maintenance/repair", ("fix", "doctor", "recovery"),
            (("section", "repairs"),), protected=True,
        ),
        PaletteCommand(
            "storage-cleanup", "Preview storage cleanup",
            "Open the cleanup dry run; nothing is removed by the palette.",
            "maintenance/repair", ("disk", "quarantine", "purge"),
            (("section", "cleanup"),), protected=True,
        ),
        PaletteCommand(
            "application-update", "Preview an application update",
            "Open Updates to check eligibility and preview a signed bundle.",
            "maintenance/updates", ("upgrade", "rollback", "bundle"),
            protected=True,
        ),
        PaletteCommand(
            "privacy-center", "Open Privacy Center",
            "Review what is retained, where it lives, and what can leave the machine.",
            "settings", ("data", "retention", "telemetry", "credentials"),
            (("section", "privacy"),),
        ),
    ))
    if len(commands) > MAX_PALETTE_COMMANDS:
        raise RuntimeError("palette command inventory exceeds its hard bound")
    return tuple(commands)


def match_palette(
    commands: tuple[PaletteCommand, ...], query: str,
    *, limit: int = MAX_PALETTE_RESULTS,
) -> tuple[PaletteCommand, ...]:
    """Use deterministic token containment; no fuzzy library or network."""
    if len(commands) > MAX_PALETTE_COMMANDS:
        raise ValueError("palette command input exceeds its hard bound")
    if not 1 <= int(limit) <= MAX_PALETTE_RESULTS:
        raise ValueError(f"palette result limit must be 1..{MAX_PALETTE_RESULTS}")
    tokens = tuple(_TOKENS.findall(str(query)[:MAX_PALETTE_QUERY].casefold())[:8])
    if not tokens:
        return commands[:limit]
    matched = []
    for command in commands:
        haystack = " ".join((
            command.label, command.description, command.route, *command.keywords,
        )).casefold()
        if all(token in haystack for token in tokens):
            matched.append(command)
            if len(matched) == limit:
                break
    return tuple(matched)


__all__ = [
    "MAX_PALETTE_COMMANDS", "MAX_PALETTE_QUERY", "MAX_PALETTE_RESULTS",
    "PaletteCommand", "match_palette", "palette_commands",
]
