"""Bounded, prioritized, query-only appliance maintenance snapshot (EXP-4).

Normal refresh consumes durable/cached truth, a constant-time filesystem
capacity reading, and the already-composed platform observation.  It never
runs Doctor, hashes a model, walks model directories, calls HTTP, starts a
service, checks for updates, or writes a refresh receipt.
"""

from __future__ import annotations

import datetime as _datetime
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .legacy_import import utcnow
from .repositories import MaintenanceSnapshotRepository


MAINTENANCE_SNAPSHOT_SCHEMA_VERSION = 1
MAX_MAINTENANCE_ITEMS = 5
BACKUP_STALE_SECONDS = 7 * 24 * 60 * 60
CACHE_STALE_SECONDS = 24 * 60 * 60
CRITICAL_FREE_BYTES = 2 * 1024 * 1024 * 1024

LIVE = "live"
CACHED = "cached"
STALE = "stale"
NOT_CHECKED = "not_checked"
FRESHNESS_STATES = (LIVE, CACHED, STALE, NOT_CHECKED)

PRIORITY_POLICY = (
    "SAFETY",
    "RECOVERY",
    "SECURITY",
    "INTEGRITY",
    "STORAGE",
    "BACKUP",
    "OPERATION",
    "UPDATE",
    "INFORMATION",
)
_PRIORITY = {category: index + 1 for index, category in enumerate(PRIORITY_POLICY)}
_NONDISMISSIBLE = frozenset({"SAFETY", "RECOVERY", "SECURITY", "INTEGRITY"})

SOURCE_NAMES = (
    "doctor",
    "operations",
    "thermal",
    "server",
    "model_runtime",
    "storage",
    "backup",
    "credentials_topology",
    "optional_services",
    "application_update",
    "platform",
)


def _parse_timestamp(value: Any) -> _datetime.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = _datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_datetime.timezone.utc)
    return parsed.astimezone(_datetime.timezone.utc)


def _age_seconds(now: str, timestamp: str | None) -> int | None:
    current = _parse_timestamp(now)
    observed = _parse_timestamp(timestamp)
    if current is None or observed is None:
        return None
    return max(0, int((current - observed).total_seconds()))


@dataclass(frozen=True)
class EvidenceSource:
    name: str
    freshness: str
    observed_at: str | None
    age_seconds: int | None
    summary: str

    def __post_init__(self) -> None:
        if self.name not in SOURCE_NAMES:
            raise ValueError(f"unknown maintenance source: {self.name!r}")
        if self.freshness not in FRESHNESS_STATES:
            raise ValueError(f"unknown evidence freshness: {self.freshness!r}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MaintenanceItem:
    code: str
    category: str
    priority: int
    title: str
    impact: str
    evidence_age_seconds: int | None
    evidence_freshness: str
    resource: str
    primary_action: str
    details_action: str
    dismissible: bool

    def __post_init__(self) -> None:
        if self.category not in PRIORITY_POLICY:
            raise ValueError(f"unknown maintenance category: {self.category!r}")
        if self.priority != _PRIORITY[self.category]:
            raise ValueError("maintenance priority does not match closed policy")
        if self.evidence_freshness not in FRESHNESS_STATES:
            raise ValueError("unknown maintenance evidence freshness")
        if self.category in _NONDISMISSIBLE and self.dismissible:
            raise ValueError("safety/recovery/security/integrity cannot be dismissed")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MaintenanceSnapshot:
    schema_version: int
    generated_at: str
    items: tuple[MaintenanceItem, ...]
    sources: tuple[EvidenceSource, ...]
    checked_live: bool

    def __post_init__(self) -> None:
        if len(self.items) > MAX_MAINTENANCE_ITEMS:
            raise ValueError("maintenance snapshot exceeds item bound")
        priorities = [item.priority for item in self.items]
        if priorities != sorted(priorities):
            raise ValueError("maintenance items are not priority ordered")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "checked_live": self.checked_live,
            "items": [item.to_dict() for item in self.items],
            "sources": [source.to_dict() for source in self.sources],
        }


class MaintenanceCenterQueryService:
    """Build the at-most-five maintenance inbox without mutation/probes."""

    def __init__(
        self,
        units: Any,
        models_dir: str | Path,
        *,
        platform: Any = None,
        clock: Callable[[], str] = utcnow,
        disk_usage: Callable[[str], Any] = shutil.disk_usage,
    ) -> None:
        self._units = units
        self._models_dir = Path(models_dir)
        self._platform = platform
        self._clock = clock
        self._disk_usage = disk_usage

    @staticmethod
    def _setting(data: dict[str, Any], key: str, default: Any = None) -> Any:
        return (data.get("settings", {}).get(key) or {}).get("value", default)

    @staticmethod
    def _source(
        name: str,
        now: str,
        observed_at: str | None,
        summary: str,
        *,
        live: bool = False,
        stale: bool = False,
        stale_after: int = CACHE_STALE_SECONDS,
    ) -> EvidenceSource:
        age = _age_seconds(now, observed_at)
        if live:
            freshness = LIVE
            age = 0
        elif observed_at is None:
            freshness = NOT_CHECKED
        elif stale or age is None or age > stale_after:
            freshness = STALE
        else:
            freshness = CACHED
        return EvidenceSource(name, freshness, observed_at, age, summary[:240])

    @staticmethod
    def _item(
        category: str,
        code: str,
        title: str,
        impact: str,
        resource: str,
        source: EvidenceSource,
        primary_action: str,
        details_action: str = "maintenance/details",
        *,
        dismissible: bool | None = None,
    ) -> MaintenanceItem:
        allowed = category not in _NONDISMISSIBLE
        if dismissible is None:
            dismissible = allowed
        return MaintenanceItem(
            code=code,
            category=category,
            priority=_PRIORITY[category],
            title=title[:120],
            impact=impact[:300],
            evidence_age_seconds=source.age_seconds,
            evidence_freshness=source.freshness,
            resource=resource[:120],
            primary_action=primary_action[:160],
            details_action=details_action[:160],
            dismissible=bool(dismissible),
        )

    def snapshot(self) -> MaintenanceSnapshot:
        now = self._clock()
        with self._units.read() as conn:
            data = MaintenanceSnapshotRepository(conn).read()

        observations = data["observations"]
        doctor_obs = observations.get("last_doctor_report") or {}
        server_obs = observations.get("last_inference_probe") or {}
        service_obs = observations.get("last_optional_services_probe") or {}
        topology_obs = observations.get("last_topology_probe") or {}
        update_obs = observations.get("last_update_check") or {}
        thermal_obs = observations.get("last_thermal_sample") or {}

        thermal_row = data.get("thermal") or {}
        operation_times = [
            row.get("oldest_at") for row in data["active_operations"]
            if row.get("oldest_at")
        ]
        operation_at = min(operation_times) if operation_times else now
        runtime_at = (
            (data.get("known_good") or {}).get("verified_at")
            or (data.get("runtime") or {}).get("updated_at")
            or (data.get("component") or {}).get("updated_at")
        )
        backup_row = data.get("backup") or {}
        backup_setting = self._setting(data, "backup_last_completed_at")
        backup_at = backup_row.get("verified_at") or backup_row.get("created_at")
        if not backup_at and isinstance(backup_setting, str):
            backup_at = backup_setting

        try:
            usage = self._disk_usage(str(self._models_dir))
            storage_free = max(0, int(usage.free))
            storage_summary = "filesystem capacity observed"
            storage_live = True
        except OSError:
            storage_free = None
            storage_summary = "filesystem capacity unavailable"
            storage_live = False

        platform_value = None
        if self._platform is not None:
            try:
                platform_value = self._platform.status()
            except (OSError, RuntimeError, ValueError):
                platform_value = None
        platform_setting = self._setting(data, "host_capability_summary")
        platform_at = (
            now if platform_value is not None
            else self._setting(data, "host_capability_observed_at")
        )

        sources = {
            "doctor": self._source(
                "doctor", now, doctor_obs.get("observed_at"),
                "cached full doctor result" if doctor_obs else "full doctor not run",
                stale=bool(doctor_obs.get("stale")),
            ),
            "operations": self._source(
                "operations", now, operation_at,
                "durable operation state read", live=True,
            ),
            "thermal": self._source(
                "thermal", now,
                thermal_obs.get("observed_at") or thermal_row.get("updated_at"),
                "thermal sample cached" if thermal_obs else "durable thermal latch read",
                stale=bool(thermal_obs.get("stale")),
            ),
            "server": self._source(
                "server", now, server_obs.get("observed_at"),
                "cached inference health probe" if server_obs else "server not probed",
                stale=bool(server_obs.get("stale")),
            ),
            "model_runtime": self._source(
                "model_runtime", now, runtime_at,
                "durable model/runtime evidence read",
            ),
            "storage": self._source(
                "storage", now, now if storage_live else None,
                storage_summary, live=storage_live,
            ),
            "backup": self._source(
                "backup", now, backup_at,
                "latest durable backup evidence" if backup_at else "no backup verified",
                stale_after=BACKUP_STALE_SECONDS,
            ),
            "credentials_topology": self._source(
                "credentials_topology", now,
                topology_obs.get("observed_at"),
                "cached topology probe" if topology_obs else "durable topology only",
                stale=bool(topology_obs.get("stale")),
            ),
            "optional_services": self._source(
                "optional_services", now, service_obs.get("observed_at"),
                "cached optional-service probe" if service_obs else "optional services not probed",
                stale=bool(service_obs.get("stale")),
            ),
            "application_update": self._source(
                "application_update", now, update_obs.get("observed_at"),
                "cached signed update metadata" if update_obs else "update channel not checked",
                stale=bool(update_obs.get("stale")),
            ),
            "platform": self._source(
                "platform", now, platform_at,
                "platform capability observed" if (platform_value or platform_setting)
                else "platform qualification unavailable",
                live=platform_value is not None,
            ),
        }

        candidates: list[MaintenanceItem] = []

        latch = str(thermal_row.get("latch_state") or "nominal")
        thermal_value = thermal_obs.get("value") or {}
        sensor_lost = bool(thermal_obs) and not bool(
            thermal_value.get("sensor_available", True)
        )
        if latch != "nominal" or sensor_lost:
            candidates.append(self._item(
                "SAFETY", "SAFETY_THERMAL_STOP" if latch == "stopped"
                else "SAFETY_THERMAL_SENSOR_LOST" if sensor_lost
                else "SAFETY_THERMAL_THROTTLED",
                "Thermal safety needs attention",
                "Unsafe work remains blocked until cooling and sensor evidence are safe.",
                "gpu-thermal", sources["thermal"], "system/thermals",
            ))

        active_counts = {
            row["state"]: int(row["n"]) for row in data["active_operations"]
        }
        failed_restore = data.get("failed_restore")
        if active_counts.get("RECOVERY_REQUIRED", 0) or failed_restore:
            candidates.append(self._item(
                "RECOVERY", "OPERATION_RECOVERY_REQUIRED",
                "Durable recovery is required",
                "A protected change could not prove completion or restoration; conflicting work is fenced.",
                "durable-operations", sources["operations"], "activity/recovery",
            ))

        funnel = bool(self._setting(data, "funnel_enabled", False))
        sharing = bool(
            self._setting(data, "tailscale_serving", False)
            or self._setting(data, "https_sharing_enabled", False)
        )
        credentials = data.get("credentials") or {}
        access = data.get("access") or {"enabled": 1}
        no_remote_auth = sharing and (
            int(credentials.get("active") or 0) == 0 or not bool(access["enabled"])
        )
        topology_value = topology_obs.get("value") or {}
        topology_failed = topology_value.get("safe") is False
        if funnel or no_remote_auth or topology_failed:
            candidates.append(self._item(
                "SECURITY", "REMOTE_TOPOLOGY_UNSAFE" if funnel or topology_failed
                else "REMOTE_CREDENTIAL_UNAVAILABLE",
                "Remote access is not safely configured",
                "Remote clients may be exposed incorrectly or unable to authenticate.",
                "remote-access", sources["credentials_topology"], "connections",
            ))

        model = data.get("model")
        runtime = data.get("runtime") or {}
        known_good = data.get("known_good") or {}
        component = data.get("component") or {}
        model_bad = bool(data.get("current_alias")) and (
            model is None
            or model.get("storage_state") != "MANAGED"
            or model.get("trust_state") != "VERIFIED"
            or not model.get("content_digest")
        )
        runtime_bad = bool(runtime) and bool(known_good) and (
            runtime.get("model_alias") != known_good.get("model_alias")
            or runtime.get("profile_fingerprint") != known_good.get("profile_fingerprint")
            or component.get("promoted_build_id")
            != known_good.get("runtime_component_identity")
        )
        quarantined = int((data.get("artifacts") or {}).get("quarantined") or 0)
        if model_bad or runtime_bad or quarantined:
            candidates.append(self._item(
                "INTEGRITY", "MODEL_RUNTIME_EVIDENCE_MISMATCH",
                "Model or runtime evidence needs verification",
                "The selected model/runtime cannot be treated as known-good until its durable identity matches.",
                "model-runtime", sources["model_runtime"], "maintenance/repair",
            ))

        if storage_free is not None and storage_free < CRITICAL_FREE_BYTES:
            candidates.append(self._item(
                "STORAGE", "STORAGE_CRITICALLY_LOW",
                "Model storage is critically low",
                "Downloads, checkpoints, and rollback staging may fail without free space.",
                "model-storage", sources["storage"], "storage/cleanup",
            ))

        backup_state = backup_row.get("verification_state")
        backup_age = sources["backup"].age_seconds
        if (
            not backup_at or backup_state in {"pending", "failed"}
            or (backup_age is not None and backup_age > BACKUP_STALE_SECONDS)
        ):
            candidates.append(self._item(
                "BACKUP", "BACKUP_UNVERIFIED" if backup_state in {"pending", "failed"}
                else "BACKUP_STALE",
                "A current verified backup is unavailable",
                "Recovery may not include recent appliance configuration and metadata.",
                "profile-backup", sources["backup"], "backup/create",
            ))

        paused = int(active_counts.get("PAUSED", 0))
        recent = data.get("recent_failures") or []
        nonrecovery_failures = [
            row for row in recent if row.get("state") != "RECOVERY_REQUIRED"
        ]
        if paused or nonrecovery_failures:
            operation_source = sources["operations"]
            candidates.append(self._item(
                "OPERATION", "OPERATION_PAUSED" if paused else "OPERATION_FAILED",
                "An operation needs review",
                "Requested work is paused or ended safely without reaching its intended result.",
                "durable-operations", operation_source, "activity",
            ))

        update_setting = self._setting(data, "verified_application_update")
        update_value = update_obs.get("value") or update_setting
        if isinstance(update_value, dict) and update_value.get("verified") is True:
            candidates.append(self._item(
                "UPDATE", "VERIFIED_APPLICATION_UPDATE_AVAILABLE",
                "A verified application update is available",
                "Review the signed release and rollback plan before installing it.",
                "application", sources["application_update"], "maintenance/updates",
            ))

        if not candidates:
            candidates.append(self._item(
                "INFORMATION", "MAINTENANCE_NO_URGENT_ITEMS",
                "No urgent maintenance items",
                "Run the full check when you want fresh server, topology, and integrity evidence.",
                "appliance", sources["doctor"], "maintenance/check",
            ))

        candidates.sort(key=lambda item: (item.priority, item.code))
        return MaintenanceSnapshot(
            schema_version=MAINTENANCE_SNAPSHOT_SCHEMA_VERSION,
            generated_at=now,
            items=tuple(candidates[:MAX_MAINTENANCE_ITEMS]),
            sources=tuple(sources[name] for name in SOURCE_NAMES),
            checked_live=False,
        )


__all__ = [
    "BACKUP_STALE_SECONDS",
    "CACHE_STALE_SECONDS",
    "CACHED",
    "CRITICAL_FREE_BYTES",
    "EvidenceSource",
    "FRESHNESS_STATES",
    "LIVE",
    "MAINTENANCE_SNAPSHOT_SCHEMA_VERSION",
    "MAX_MAINTENANCE_ITEMS",
    "MaintenanceCenterQueryService",
    "MaintenanceItem",
    "MaintenanceSnapshot",
    "NOT_CHECKED",
    "PRIORITY_POLICY",
    "SOURCE_NAMES",
    "STALE",
]
