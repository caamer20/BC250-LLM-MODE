"""Query-only privacy inventory for the native and CLI experience."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import AppPaths


PRIVACY_SNAPSHOT_SCHEMA_VERSION = 1
MAX_PRIVACY_ITEMS = 16


@dataclass(frozen=True)
class PrivacyItem:
    item_id: str
    label: str
    contents: str
    location: str
    retention: str
    leaves_machine: str
    manage_label: str | None
    manage_route: str | None
    manage_context: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict:
        value = asdict(self)
        value["manage_context"] = dict(self.manage_context)
        return value


@dataclass(frozen=True)
class PrivacySnapshot:
    schema_version: int
    telemetry: str
    network_summary: str
    items: tuple[PrivacyItem, ...]

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "telemetry": self.telemetry,
            "network_summary": self.network_summary,
            "items": [item.to_dict() for item in self.items],
        }


class PrivacyCenterQueryService:
    """Describe real owners and locations without reading or changing them."""

    def __init__(self, paths: AppPaths) -> None:
        if not isinstance(paths, AppPaths):
            raise TypeError("PrivacyCenterQueryService requires AppPaths")
        self._paths = paths

    def snapshot(self) -> PrivacySnapshot:
        paths = self._paths
        database = str(paths.database_path)
        items = (
            PrivacyItem(
                "conversations", "Saved conversations",
                "Conversation titles, roles, prompts, and responses saved explicitly from Chat.",
                str(paths.conversations_dir),
                "Creation stops at 200 files; existing larger histories are retained and indexed up to 10,000 files. Each conversation is bounded to 2,000 messages and 8 MiB. Private titles are indexed locally. Nothing is automatically deleted.",
                "Sent only to the selected model endpoint while chatting; never included in logs, events, notices, or support bundles.",
                "Manage conversations", "chat",
            ),
            PrivacyItem(
                "logs", "Application logs",
                "Setup, worker, and model-server diagnostics. Prompt and completion content is excluded by policy.",
                str(paths.logs_dir),
                "Local files remain until app-owned cleanup, uninstall selection, or manual removal.",
                "Not transmitted automatically. A bounded redacted subset may enter a support bundle the user creates.",
                "Open bounded logs", "help",
            ),
            PrivacyItem(
                "operation-events", "Operations and events",
                "Stable action states, progress, result codes, and redacted bounded evidence.",
                database,
                "Durable appliance history; events are retained with their operation until explicit domain cleanup.",
                "Never transmitted automatically.",
                "Review Activity", "activity",
            ),
            PrivacyItem(
                "benchmarks", "Benchmarks and calibration",
                "Local performance measurements and profile fingerprints; no conversation text.",
                database,
                "The newest 20 benchmark records are retained; calibration evidence follows durable operation retention.",
                "Never transmitted automatically.",
                "Manage profiles", "profiles",
            ),
            PrivacyItem(
                "credentials", "Client credentials",
                "Purpose-scoped secret files plus redacted fingerprints, scopes, and lifecycle metadata.",
                f"{paths.app_dir / 'connection-secrets'} and {database}",
                "Mode-0600 secret files remain only while active or during a requested short rotation overlap; revocation removes them.",
                "Presented only to the local authenticated gateway when the named client connects.",
                "Manage credentials", "connections",
            ),
            PrivacyItem(
                "openwebui", "Open WebUI data",
                "Open WebUI accounts, preferences, and chat data owned by the optional container.",
                "Podman named volume bc250-open-webui (outside the application profile)",
                "Preserved when the container is stopped or recreated. Removed only by an explicit volume-removal action outside current safe GUI ownership.",
                "Open WebUI may send chat content to the configured local authenticated gateway; no cloud destination is configured by this app.",
                "Manage service", "system",
            ),
            PrivacyItem(
                "backups", "Profile backups",
                "SQLite profile snapshot, bounded manifest, settings, and selected app data. Model/runtime bytes are excluded unless explicitly requested.",
                str(paths.backups_dir),
                "Kept until an explicit cleanup decision. Encryption is unavailable in this build and is refused rather than implied.",
                "Not transmitted automatically.",
                "Manage backups", "system",
            ),
            PrivacyItem(
                "support-bundles", "Support bundles",
                "A bounded redacted diagnostic export created only on request.",
                "A folder chosen by the user at export time",
                "The user owns the exported archive and decides when to remove it.",
                "Never uploaded by BC250 LLM MODE. The user decides whether and how to share it.",
                "Create or review bundle", "help",
            ),
            PrivacyItem(
                "update-bundles", "Application update bundles",
                "Verified release artifacts, manifests, signatures, SBOM, and release notes; no user conversations.",
                str(paths.application_update_bundles_dir),
                "Content-addressed bundles remain until an explicit update cleanup preview is applied.",
                "Production has no available online update channel in this build. Offline imports read only the selected local archive.",
                "Manage updates", "maintenance/updates",
            ),
            PrivacyItem(
                "notices", "Desktop notification preferences and receipts",
                "Enabled categories and bounded redacted delivery identities; notification bodies are not persisted.",
                database,
                "At most 256 receipt identities are retained; oldest receipts are pruned automatically.",
                "Fixed privacy-safe notification text is sent only to the local desktop notification service when enabled.",
                "Manage notifications", "settings", (("section", "basic"),),
            ),
            PrivacyItem(
                "update-network", "Update network behavior",
                "Signed-channel availability and verified release identity only.",
                "No remote cache is created by a status view",
                "Read-only status creates no check receipt. Explicit verified imports retain their bundle identity.",
                "No automatic update check occurs. Production online updates remain unavailable until a qualified trust root and channel are shipped.",
                "Review Updates", "maintenance/updates",
            ),
        )
        if len(items) > MAX_PRIVACY_ITEMS:
            raise RuntimeError("privacy inventory exceeds its hard bound")
        return PrivacySnapshot(
            PRIVACY_SNAPSHOT_SCHEMA_VERSION,
            "BC250 LLM MODE has no telemetry and sends no usage analytics.",
            "Network activity is action-driven: model downloads, explicit connection probes, chat requests, and any future eligible update check are never hidden background telemetry.",
            items,
        )


__all__ = [
    "MAX_PRIVACY_ITEMS", "PRIVACY_SNAPSHOT_SCHEMA_VERSION", "PrivacyCenterQueryService",
    "PrivacyItem", "PrivacySnapshot",
]
