"""P5 §11.1: the unified appliance home snapshot.

``HomeQueryService`` assembles ONE bounded, query-only snapshot that
explains the appliance's state card by card. Every card carries:

- its data;
- ``as_of`` — the timestamp of the underlying reading;
- ``stale`` / ``stale_reason`` — a stale reading is labeled, never
  rendered as a current green status;
- ``health`` — a typed :class:`~bc250_llm_mode.health.HealthDimension`.

The snapshot is QUERY-ONLY: it opens read units exclusively (pinned by
an AST guard in the tests), never writes durable state, and never runs
subprocesses or network I/O. Live probing belongs to ``doctor`` (P5
§11.3); the home snapshot reports only durable, bounded evidence, so a
green readiness claim always names the evidence that established it.
"""

from __future__ import annotations

import datetime
import os
import shutil
from dataclasses import dataclass, field
from typing import Any, Callable

from . import __version__
from . import health as H
from .db import SCHEMA_VERSION
from .paths import AppPaths
from .repositories import (
    ModelInstallationsRepository,
    ObservationRepository,
    SettingsRepository,
    ThermalStateRepository,
)
from .unit_of_work import UnitOfWorkFactory

HOME_SNAPSHOT_SCHEMA_VERSION = 1

# Storage pressure bound: above this fraction of the filesystem the
# storage dimension reports DEGRADED (bounded evidence, no deletion).
STORAGE_PRESSURE_FRACTION = 0.90

# A durable inference probe reading older than this is stale.
INFERENCE_PROBE_STALE_AFTER = datetime.timedelta(hours=24)

# Backup freshness bound (P8 will produce the durable rows; until then
# the card reports UNAVAILABLE honestly).
BACKUP_STALE_AFTER = datetime.timedelta(days=7)


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _parse_ts(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value)
    except ValueError:
        return None


def _dir_stats(root) -> dict[str, int]:
    """Bounded recursive size/count for one directory (missing -> zeros)."""
    total_bytes = 0
    file_count = 0
    try:
        stack = [os.scandir(root)]
    except OSError:
        return {"bytes": 0, "files": 0}
    while stack:
        try:
            entries = list(stack.pop())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(os.scandir(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    file_count += 1
                    total_bytes += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return {"bytes": total_bytes, "files": file_count}


def _count_entries(root) -> int:
    try:
        with os.scandir(root) as it:
            return sum(1 for _ in it)
    except OSError:
        return 0


@dataclass(frozen=True)
class ApplianceHomeSnapshot:
    """Bounded, disposable, query-only view of the whole appliance."""

    schema_version: int
    generated_at: str
    cards: dict[str, dict[str, Any]]
    overall: H.HealthDimension
    dimension_names: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "overall": self.overall.to_dict(),
            "cards": self.cards,
        }


class HomeQueryService:
    """Assembles the home snapshot from read units + AppPaths only."""

    def __init__(
        self,
        units: UnitOfWorkFactory,
        paths: AppPaths,
        *,
        clock: Callable[[], str] | None = None,
        platform: Any = None,
    ) -> None:
        self._units = units
        self._paths = paths
        self._clock = clock or _utcnow
        self._platform = platform

    # --- cards ---------------------------------------------------------------

    def snapshot(self) -> ApplianceHomeSnapshot:
        generated_at = self._clock()
        with self._units.read() as conn:
            settings = SettingsRepository(conn).all()
            settings.pop("__revision", None)
            thermal = ThermalStateRepository(conn).get()
            models = ModelInstallationsRepository(conn).list()
            observations = ObservationRepository(conn).list()
            runtime = self._runtime_card(conn, settings, generated_at)
            gateway_row = conn.execute(
                "SELECT fingerprint, scopes, created_at, rotated_at, revoked_at "
                "FROM gateway_credentials WHERE id = 1"
            ).fetchone()
            connection_rows = conn.execute(
                "SELECT client_id, scopes, active_fingerprint, active_generation, "
                "secret_storage, rotated_at, revoked_at FROM connection_clients "
                "ORDER BY client_id LIMIT 32"
            ).fetchall()
            connection_access = conn.execute(
                "SELECT enabled, disabled_at FROM connection_access_state WHERE id = 1"
            ).fetchone()

        cards: dict[str, dict[str, Any]] = {}
        dimensions: list[H.HealthDimension] = []

        for name, (card, dim) in (
            ("identity", self._identity_card(settings, generated_at)),
            ("runtime", runtime),
            ("model", self._model_card(settings, models, generated_at)),
            ("inference", self._inference_card(observations, generated_at)),
            ("thermal", self._thermal_card(thermal, generated_at)),
            ("operations", self._operations_card(generated_at)),
            ("storage", self._storage_card(generated_at)),
            ("integrations", self._integrations_card(
                settings, gateway_row, connection_rows, connection_access,
                generated_at)),
            ("backup", self._backup_card(settings, generated_at)),
            ("host", self._host_card(settings, generated_at)),
        ):
            cards[name] = card
            dimensions.append(dim)

        overall = H.compose_overall(dimensions)
        return ApplianceHomeSnapshot(
            schema_version=HOME_SNAPSHOT_SCHEMA_VERSION,
            generated_at=generated_at,
            cards=cards,
            overall=overall,
            dimension_names=tuple(d.name for d in dimensions),
        )

    def _identity_card(self, settings: dict, now: str):
        setup_complete = bool(settings.get("setup_complete"))
        dim = H.dimension(
            "identity",
            H.READY if setup_complete else H.UNAVAILABLE,
            basis=H.BASIS_OBSERVED,
            evidence=("installation complete" if setup_complete
                      else "setup not complete"),
            as_of=now,
        )
        card = {
            "version": __version__,
            "schema_version": SCHEMA_VERSION,
            "setup_complete": setup_complete,
            "profile": self._paths.app_dir.name,
            "as_of": now,
            "stale": False,
            "stale_reason": None,
            "health": dim.to_dict(),
        }
        return card, dim

    def _runtime_card(self, conn, settings: dict, now: str):
        from .runtime_builds import (
            RuntimeBuildRepository,
            RuntimeComponentRepository,
            RuntimeTreeRepository,
        )

        component = RuntimeComponentRepository(conn).current()
        promoted = None
        tree_row = None
        if component and component.get("promoted_build_id"):
            try:
                promoted = RuntimeBuildRepository(conn).require(
                    component["promoted_build_id"])
                if component.get("promoted_tree_id"):
                    tree_row = RuntimeTreeRepository(conn).get(
                        component["promoted_tree_id"])
            except KeyError:
                promoted = None

        known_good_row = conn.execute(
            "SELECT runtime_component_identity, verified_at "
            "FROM known_good_runtime WHERE id = 1"
        ).fetchone()
        known_good_identity = (
            known_good_row["runtime_component_identity"]
            if known_good_row else None
        )
        verified_at = known_good_row["verified_at"] if known_good_row else None

        desired = settings.get("llamacpp_pin") or (promoted or {}).get(
            "requested_ref")
        observed = (promoted or {}).get("build_id")
        verified = bool(promoted and known_good_identity
                        and promoted["build_id"] == known_good_identity)

        if promoted is None:
            state, evidence = H.UNAVAILABLE, "no runtime published"
        elif verified:
            state, evidence = H.READY, f"promoted build verified ({observed})"
        else:
            state, evidence = H.DEGRADED, (
                f"promoted build {observed} not in known-good identity")

        basis = H.BASIS_VERIFIED if verified else H.BASIS_OBSERVED
        dim = H.dimension(
            "runtime", state, basis=basis, evidence=evidence,
            as_of=verified_at or now,
        )
        card = {
            "desired": desired,
            "observed": observed,
            "verified": known_good_identity if verified else None,
            "generation": (component or {}).get("generation"),
            "manifest_digest": (promoted or {}).get("manifest_digest"),
            "server_binary_digest": (tree_row or {}).get("server_binary_digest"),
            "as_of": verified_at or now,
            "stale": False,
            "stale_reason": None,
            "health": dim.to_dict(),
        }
        return card, dim

    def _model_card(self, settings: dict, models: list, now: str):
        desired = settings.get("current_model")
        installed = {m["id"]: m for m in models}
        row = installed.get(desired) if desired else None

        if not desired:
            state, evidence = H.UNAVAILABLE, "no model selected"
        elif row is None:
            state, evidence = H.UNAVAILABLE, (
                f"selected model {desired!r} is not installed")
        elif row.get("artifact_trust_state") == "QUARANTINED":
            state, evidence = H.DEGRADED, (
                f"model {desired!r} installed but quarantined")
        elif row.get("content_digest"):
            state, evidence = H.READY, (
                f"model {desired!r} installed with digest "
                f"{row['content_digest'][:12]}")
        else:
            state, evidence = H.DEGRADED, (
                f"model {desired!r} installed without artifact digest")

        dim = H.dimension(
            "model", state,
            basis=H.BASIS_VERIFIED if (row and row.get("content_digest"))
            else H.BASIS_OBSERVED,
            evidence=evidence, as_of=now,
        )
        card = {
            "desired": desired,
            "observed": row["id"] if row else None,
            "verified": bool(row and row.get("content_digest")),
            "quant": (row or {}).get("quant"),
            "content_digest": (row or {}).get("content_digest"),
            "trust_state": (row or {}).get("artifact_trust_state"),
            "installed_count": len(models),
            "as_of": now,
            "stale": False,
            "stale_reason": None,
            "health": dim.to_dict(),
        }
        return card, dim

    def _inference_card(self, observations: dict, now: str):
        probe = observations.get("last_inference_probe")
        if probe is None:
            dim = H.dimension(
                "inference", H.UNVERIFIED, basis=H.BASIS_INFERRED,
                evidence="no durable inference probe recorded; "
                         "run 'bc250-llm-mode doctor' for a live probe",
                as_of=None,
            )
            card = {
                "ready": False,
                "last_probe_at": None,
                "as_of": None,
                "stale": False,
                "stale_reason": "never probed",
                "health": dim.to_dict(),
            }
            return card, dim

        observed_at = probe.get("observed_at")
        stale = bool(probe.get("stale"))
        if not stale and observed_at:
            ts = _parse_ts(observed_at)
            now_ts = _parse_ts(now)
            if ts and now_ts and now_ts - ts > INFERENCE_PROBE_STALE_AFTER:
                stale = True
        value = probe.get("value") or {}
        ok = bool(value.get("ok")) and not stale
        dim = H.dimension(
            "inference",
            H.READY if ok else H.UNVERIFIED,
            basis=H.BASIS_OBSERVED,
            evidence=("last bounded probe succeeded" if ok
                      else "no current successful probe"),
            as_of=observed_at,
            stale=stale,
            stale_reason="probe older than bound" if stale else None,
        )
        card = {
            "ready": ok,
            "last_probe_at": observed_at,
            "as_of": observed_at,
            "stale": stale,
            "stale_reason": "probe older than bound" if stale else None,
            "health": dim.to_dict(),
        }
        return card, dim

    def _thermal_card(self, thermal: dict, now: str):
        latch = thermal.get("latch_state") or "nominal"
        latched = latch != "nominal"
        dim = H.dimension(
            "thermal",
            H.BLOCKED if latched else H.READY,
            basis=H.BASIS_OBSERVED,
            evidence=(f"thermal latch engaged ({latch})" if latched
                      else "thermal latch nominal"),
            as_of=now,
        )
        card = {
            "latch_state": latch,
            "baseline": thermal.get("baseline"),
            # The latch is a durable safety condition, not a metered
            # reading: it persists until an explicit safe-temperature
            # 'thermals reset' clears it.
            "safe_reset_eligible": latched,
            "reset_guidance": (
                "cool the hardware, then run 'bc250-llm-mode thermals reset'"
                if latched else None),
            "as_of": now,
            "stale": False,
            "stale_reason": None,
            "health": dim.to_dict(),
        }
        return card, dim

    def _operations_card(self, now: str):
        from .operations.query_service import OperationQueryService

        summary = OperationQueryService(self._units, clock=self._clock
                                        ).active_summary()
        if summary.recovery_required_count:
            state = H.RECOVERY_REQUIRED
            evidence = (f"{summary.recovery_required_count} operation(s) "
                        "require recovery")
        elif summary.active_count:
            state = H.BUSY
            evidence = (f"{summary.running_count} running, "
                        f"{summary.queued_count} queued, "
                        f"{summary.paused_count} paused")
        else:
            state = H.READY
            evidence = "no active operations"
        dim = H.dimension("operations", state, basis=H.BASIS_OBSERVED,
                          evidence=evidence, as_of=now)
        card = {
            "summary": summary.to_dict(),
            "as_of": now,
            "stale": False,
            "stale_reason": None,
            "health": dim.to_dict(),
        }
        return card, dim

    def _storage_card(self, now: str):
        models_stats = _dir_stats(self._paths.models_dir)
        staging_count = _count_entries(self._paths.model_staging_dir)
        quarantine_count = _count_entries(self._paths.model_quarantine_dir)
        try:
            usage = shutil.disk_usage(str(self._paths.models_dir))
            free = usage.free
            total = usage.total
            fraction = (usage.total - usage.free) / usage.total
        except OSError:
            free = total = None
            fraction = 0.0
        pressured = fraction >= STORAGE_PRESSURE_FRACTION
        dim = H.dimension(
            "storage",
            H.DEGRADED if pressured else H.READY,
            basis=H.BASIS_OBSERVED,
            evidence=(f"filesystem {fraction:.0%} used"
                      if total else "filesystem usage unreadable"),
            as_of=now,
        )
        card = {
            "models_bytes": models_stats["bytes"],
            "models_files": models_stats["files"],
            "staging_count": staging_count,
            "quarantine_count": quarantine_count,
            "available_bytes": free,
            "total_bytes": total,
            "pressure_fraction": round(fraction, 4),
            "as_of": now,
            "stale": False,
            "stale_reason": None,
            "health": dim.to_dict(),
        }
        return card, dim

    def _integrations_card(
        self, settings: dict, gateway_row, connection_rows,
        connection_access, now: str,
    ):
        access_enabled = bool(
            connection_access is None or connection_access["enabled"])
        active = [row for row in connection_rows if row["revoked_at"] is None]

        def client_file(row):
            if row["secret_storage"] == "legacy-singleton":
                return self._paths.app_dir / "gateway-credential"
            return (
                self._paths.app_dir / "connection-secrets" /
                f"{row['client_id']}-{int(row['active_generation'])}.secret")

        ready = [row for row in active if client_file(row).is_file()]
        legacy_revoked = bool(gateway_row and gateway_row["revoked_at"])
        legacy_ready = bool(
            gateway_row and not legacy_revoked
            and (self._paths.app_dir / "gateway-credential").is_file())
        provisioned = bool(active) or (
            not connection_rows and bool(gateway_row) and not legacy_revoked)
        verified = access_enabled and (bool(ready) or (
            not connection_rows and legacy_ready))
        revoked = bool(connection_rows and not active) or legacy_revoked

        if not access_enabled:
            state, evidence = H.BLOCKED, "remote API access disabled"
        elif revoked:
            state, evidence = H.BLOCKED, "gateway credential revoked"
        elif verified:
            state, evidence = H.READY, (
                f"{len(ready) if connection_rows else 1} client credential(s) ready")
        elif provisioned:
            state, evidence = H.DEGRADED, (
                "gateway credential provisioned but secret file missing")
        else:
            state, evidence = H.UNAVAILABLE, "gateway credential not provisioned"

        dim = H.dimension(
            "integrations", state, basis=H.BASIS_OBSERVED,
            evidence=evidence, as_of=now,
        )
        card = {
            "gateway": {
                "provisioned": provisioned,
                "revoked": revoked,
                "verified": verified,
                "access_enabled": access_enabled,
                "active_clients": len(active),
                "ready_clients": len(ready),
                "fingerprint_prefix": (
                    active[0]["active_fingerprint"][:8] if active
                    else gateway_row["fingerprint"][:8] if gateway_row else None),
                "scopes": active[0]["scopes"] if active
                else gateway_row["scopes"] if gateway_row else None,
                "rotated_at": active[0]["rotated_at"] if active
                else gateway_row["rotated_at"] if gateway_row else None,
            },
            "topology": {
                "gateway_port": settings.get("gateway_port", 9071),
                "webui_publish": settings.get("openwebui_publish",
                                              "127.0.0.1:3000:8080"),
                "tailscale_serving": bool(settings.get("tailscale_serving")),
                "funnel_enabled": bool(settings.get("funnel_enabled")),
            },
            "as_of": now,
            "stale": False,
            "stale_reason": None,
            "health": dim.to_dict(),
        }
        return card, dim

    def _backup_card(self, settings: dict, now: str):
        last_backup_at = settings.get("backup_last_completed_at")
        ts = _parse_ts(last_backup_at)
        if last_backup_at is None:
            state = H.UNAVAILABLE
            evidence = "no backup recorded (backup lifecycle lands in P8)"
            stale_reason = "never backed up"
            stale = False
        else:
            stale = False
            if ts:
                now_ts = _parse_ts(now)
                if now_ts and now_ts - ts > BACKUP_STALE_AFTER:
                    stale = True
            state = H.DEGRADED if stale else H.READY
            evidence = ("last backup is stale" if stale
                        else f"last backup {last_backup_at}")
            stale_reason = "backup older than bound" if stale else None
        dim = H.dimension(
            "backup", state,
            basis=H.BASIS_OBSERVED if last_backup_at else H.BASIS_INFERRED,
            evidence=evidence, as_of=last_backup_at,
            stale=stale, stale_reason=stale_reason,
        )
        card = {
            "last_backup_at": last_backup_at,
            "restore_ready": bool(settings.get("backup_restore_ready")),
            "as_of": last_backup_at,
            "stale": stale,
            "stale_reason": stale_reason,
            "health": dim.to_dict(),
        }
        return card, dim

    def _host_card(self, settings: dict, now: str):
        # The composed platform detector is bounded and read-only (os-release,
        # command/path presence). Hardware subprocess probes remain in doctor.
        # Legacy durable summaries are accepted only when no detector was
        # injected; current observations are never persisted as truth.
        capability = (
            self._platform.status() if self._platform is not None
            else settings.get("host_capability_summary")
        )
        observed_at = now if self._platform is not None else settings.get(
            "host_capability_observed_at"
        )
        if capability:
            tier = str(capability.get("integration_tier") or "")
            state = (
                H.READY if self._platform is None
                else H.UNAVAILABLE if tier == "unsupported"
                else H.READY if tier == "native"
                else H.UNVERIFIED
            )
            dim = H.dimension(
                "host", state, basis=H.BASIS_OBSERVED,
                evidence=(
                    f"host integration tier {tier}" if tier
                    else "host capability summary recorded"
                ),
                as_of=observed_at or now,
            )
        else:
            dim = H.dimension(
                "host", H.UNVERIFIED, basis=H.BASIS_INFERRED,
                evidence="host capability not probed; run doctor",
                as_of=None,
            )
        card = {
            "capability": capability,
            "unsupported_reasons": (
                capability.get("blockers", []) if self._platform is not None
                else settings.get("host_unsupported_reasons") or []
            ),
            "as_of": observed_at,
            "stale": False,
            "stale_reason": None if capability else "never probed",
            "health": dim.to_dict(),
        }
        return card, dim


def open_home_service(database_path, paths: AppPaths) -> HomeQueryService:
    return HomeQueryService(UnitOfWorkFactory(database_path), paths)
