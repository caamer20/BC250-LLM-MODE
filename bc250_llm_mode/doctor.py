"""P5 §11.3: the read-only doctor.

``DoctorService`` runs a bounded battery of READ-ONLY checks and returns a
``DoctorReport`` of ``Finding`` records. Every finding carries:

- a STABLE ``id`` (operators and support tooling rely on it);
- a bounded ``severity`` (FAIL > WARN > INFO > PASS);
- bounded ``evidence`` (what was observed, never a secret);
- an optional ``recommended_command`` (the exact next action).

The doctor never mutates durable state, never deletes anything, and never
runs unbounded work. Live probes (inference, binary digest recompute) are
injectable so the read-only core stays deterministic and testable; the
durable checks run entirely from the database and ``AppPaths``.

The P5 exit gate requires the doctor to catch these seeded failures:
DB corruption, stale lease, mismatched handoff, bad digest, thermal latch,
low disk, insecure topology, and stale backup. Each maps to a stable
finding id below and is pinned by ``tests/test_doctor.py``.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .db import SCHEMA_VERSION
from .paths import AppPaths
from .repositories import (
    ModelInstallationsRepository,
    SettingsRepository,
    ThermalStateRepository,
)
from .runtime_handoff import RuntimeHandoffRenderer
from .unit_of_work import UnitOfWorkFactory

DOCTOR_SCHEMA_VERSION = 1

# --- severity vocabulary (bounded, deterministically ordered) -----------------
SEVERITY_PASS = "PASS"
SEVERITY_INFO = "INFO"
SEVERITY_WARN = "WARN"
SEVERITY_FAIL = "FAIL"
SEVERITIES = (SEVERITY_PASS, SEVERITY_INFO, SEVERITY_WARN, SEVERITY_FAIL)
# Most severe first, mirroring health.SEVERITY_ORDER semantics.
SEVERITY_ORDER = (SEVERITY_FAIL, SEVERITY_WARN, SEVERITY_INFO, SEVERITY_PASS)

# --- stable finding ids -------------------------------------------------------
DB_INTEGRITY = "DB_INTEGRITY"
DB_SCHEMA = "DB_SCHEMA"
DIR_PERMS = "DIR_PERMS"
SECRET_PERMS = "SECRET_PERMS"
STALE_LEASE = "STALE_LEASE"
HANDOFF_MISMATCH = "HANDOFF_MISMATCH"
RUNTIME_DIGEST = "RUNTIME_DIGEST"
MODEL_DIGEST = "MODEL_DIGEST"
THERMAL_LATCH = "THERMAL_LATCH"
LOW_DISK = "LOW_DISK"
ORPHAN_CANDIDATE = "ORPHAN_CANDIDATE"
INSECURE_TOPOLOGY = "INSECURE_TOPOLOGY"
STALE_BACKUP = "STALE_BACKUP"
INFERENCE = "INFERENCE"

FINDING_IDS = (
    DB_INTEGRITY, DB_SCHEMA, DIR_PERMS, SECRET_PERMS, STALE_LEASE,
    HANDOFF_MISMATCH, RUNTIME_DIGEST, MODEL_DIGEST, THERMAL_LATCH,
    LOW_DISK, ORPHAN_CANDIDATE, INSECURE_TOPOLOGY, STALE_BACKUP, INFERENCE,
)

# Storage pressure bound (fraction of the filesystem used).
LOW_DISK_FRACTION = 0.90
# Absolute free-space floor for model work.
LOW_DISK_FREE_BYTES = 1 << 30  # 1 GiB
# Backup freshness bound.
BACKUP_STALE_AFTER = datetime.timedelta(days=7)
# Digest recompute read chunk.
_DIGEST_CHUNK = 1 << 20


def severity_rank(severity: str) -> int:
    try:
        return SEVERITY_ORDER.index(severity)
    except ValueError:
        raise ValueError(f"unknown severity {severity!r}") from None


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _parse_ts(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value)
    except ValueError:
        return None


def _file_sha256(path, chunk: int = _DIGEST_CHUNK) -> str | None:
    """Bounded chunked sha256 of one file; None when unreadable."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while True:
                block = handle.read(chunk)
                if not block:
                    break
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


@dataclass(frozen=True)
class Finding:
    id: str
    severity: str
    title: str
    evidence: str
    recommended_command: str | None = None

    def __post_init__(self) -> None:
        if self.id not in FINDING_IDS:
            raise ValueError(f"unknown finding id {self.id!r}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "title": self.title,
            "evidence": self.evidence,
            "recommended_command": self.recommended_command,
        }


@dataclass(frozen=True)
class DoctorReport:
    schema_version: int
    generated_at: str
    findings: tuple[Finding, ...]
    overall: str
    worst_ids: tuple[str, ...] = field(default_factory=tuple)
    readiness: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "overall": self.overall,
            "worst_ids": list(self.worst_ids),
            "findings": [f.to_dict() for f in self.findings],
        }
        if self.readiness is not None:
            value["readiness"] = self.readiness
        return value


def _compose(findings: list[Finding], generated_at: str) -> DoctorReport:
    if not findings:
        findings = [Finding(
            id=DB_INTEGRITY, severity=SEVERITY_PASS,
            title="No findings", evidence="doctor ran no checks",
        )]
    worst_rank = min(severity_rank(f.severity) for f in findings)
    overall = SEVERITY_ORDER[worst_rank]
    worst_ids = tuple(
        f.id for f in findings if severity_rank(f.severity) == worst_rank
    )
    return DoctorReport(
        schema_version=DOCTOR_SCHEMA_VERSION,
        generated_at=generated_at,
        findings=tuple(findings),
        overall=overall,
        worst_ids=worst_ids,
    )


class DoctorService:
    """Read-only diagnostics over the durable state + AppPaths."""

    def __init__(
        self,
        units: UnitOfWorkFactory,
        paths: AppPaths,
        *,
        clock: Callable[[], str] | None = None,
        inference_probe: Callable[[], dict[str, Any]] | None = None,
        file_digest: Callable[[Any], str | None] | None = None,
        readiness: Any = None,
    ) -> None:
        self._units = units
        self._paths = paths
        self._clock = clock or _utcnow
        self._inference_probe = inference_probe
        self._file_digest = file_digest or _file_sha256
        self._readiness = readiness

    def attach_readiness_source(self, readiness: Any) -> None:
        """Composition hook; does not mutate durable appliance state."""
        self._readiness = readiness

    # --- entry point ----------------------------------------------------------

    def run(self) -> DoctorReport:
        generated_at = self._clock()
        findings: list[Finding] = []
        settings: dict[str, Any] = {}
        try:
            with self._units.read() as conn:
                settings = SettingsRepository(conn).all()
                settings.pop("__revision", None)
                findings.extend(self._check_database(conn))
                findings.extend(self._check_leases(conn, generated_at))
                findings.extend(self._check_handoff(conn))
                findings.extend(self._check_runtime_digest(conn))
                findings.extend(self._check_model_digests(conn))
                findings.extend(self._check_thermal(conn))
                findings.extend(self._check_backup(settings, generated_at))
        except Exception as exc:  # noqa: BLE001 - an unreadable DB is a finding
            findings.append(Finding(
                id=DB_INTEGRITY, severity=SEVERITY_FAIL,
                title="Database unreadable",
                evidence=f"opening the database raised {exc!r}",
                recommended_command="bc250-llm-mode repair",
            ))
        findings.extend(self._check_directories())
        findings.extend(self._check_topology(settings))
        findings.extend(self._check_storage())
        findings.extend(self._check_inference())
        report = _compose(findings, generated_at)
        if self._readiness is None:
            return report
        try:
            readiness = self._readiness.snapshot(
                target_journey="native_chat").to_dict()
        except Exception:  # noqa: BLE001 - readiness is UNKNOWN, not exception text
            readiness = {
                "overall_state": "UNKNOWN",
                "primary_problem_code": "READINESS_OBSERVATION_UNAVAILABLE",
            }
        return DoctorReport(
            schema_version=report.schema_version,
            generated_at=report.generated_at,
            findings=report.findings,
            overall=report.overall,
            worst_ids=report.worst_ids,
            readiness=readiness,
        )

    # --- checks ---------------------------------------------------------------

    def _check_database(self, conn) -> list[Finding]:
        out: list[Finding] = []
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
            result = rows[0][0] if rows else "unknown"
        except Exception as exc:  # noqa: BLE001 - corruption is a finding
            out.append(Finding(
                id=DB_INTEGRITY, severity=SEVERITY_FAIL,
                title="Database integrity check failed",
                evidence=f"integrity_check raised {exc!r}",
                recommended_command="bc250-llm-mode repair",
            ))
            return out
        if result == "ok":
            out.append(Finding(
                id=DB_INTEGRITY, severity=SEVERITY_PASS,
                title="Database integrity ok",
                evidence="PRAGMA integrity_check = ok",
            ))
        else:
            out.append(Finding(
                id=DB_INTEGRITY, severity=SEVERITY_FAIL,
                title="Database corruption detected",
                evidence=f"integrity_check = {result!r}",
                recommended_command="bc250-llm-mode repair",
            ))
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        except Exception:  # noqa: BLE001
            version = None
        if version == SCHEMA_VERSION:
            out.append(Finding(
                id=DB_SCHEMA, severity=SEVERITY_PASS,
                title="Schema version current",
                evidence=f"user_version = {version}",
            ))
        else:
            out.append(Finding(
                id=DB_SCHEMA, severity=SEVERITY_WARN,
                title="Schema version differs from expected",
                evidence=f"user_version = {version!r}, expected "
                         f"{SCHEMA_VERSION}",
                recommended_command="bc250-llm-mode repair",
            ))
        return out

    def _check_leases(self, conn, now: str) -> list[Finding]:
        from .operations.repositories import (
            LeaseRepository,
            WorkerLockRepository,
        )

        out: list[Finding] = []
        expired = LeaseRepository(conn).list_expired(now)
        lock = WorkerLockRepository(conn, clock=self._clock).get()
        lock_expired = bool(lock and lock.get("expires_at") and
                            lock["expires_at"] <= now)
        problems = []
        if expired:
            keys = ", ".join(l.resource_key for l in expired[:5])
            problems.append(f"{len(expired)} expired lease(s): {keys}")
        if lock_expired:
            problems.append(
                f"worker lock held by {lock.get('owner')!r} expired at "
                f"{lock.get('expires_at')}")
        if problems:
            out.append(Finding(
                id=STALE_LEASE, severity=SEVERITY_WARN,
                title="Stale lease or worker lock detected",
                evidence="; ".join(problems),
                recommended_command="bc250 operations recover --confirm",
            ))
        else:
            out.append(Finding(
                id=STALE_LEASE, severity=SEVERITY_PASS,
                title="No stale leases",
                evidence="no expired operation leases or worker locks",
            ))
        return out

    def _check_handoff(self, conn) -> list[Finding]:
        known_good = conn.execute(
            "SELECT runtime_fingerprint, runtime_component_identity "
            "FROM known_good_runtime WHERE id = 1"
        ).fetchone()
        stored = RuntimeHandoffRenderer(self._paths.app_dir
                                        ).stored_fingerprint()
        if known_good is None and stored is None:
            return [Finding(
                id=HANDOFF_MISMATCH, severity=SEVERITY_INFO,
                title="No handoff recorded",
                evidence="no known-good runtime and no handoff file",
            )]
        expected = known_good["runtime_fingerprint"] if known_good else None
        if expected and stored and stored != expected:
            return [Finding(
                id=HANDOFF_MISMATCH, severity=SEVERITY_FAIL,
                title="Handoff fingerprint does not match known-good runtime",
                evidence=f"handoff={stored[:12]}… known-good={expected[:12]}…",
                recommended_command="bc250-llm-mode llamacpp resume",
            )]
        if expected and stored is None:
            return [Finding(
                id=HANDOFF_MISMATCH, severity=SEVERITY_WARN,
                title="Handoff file missing for known-good runtime",
                evidence="known-good fingerprint recorded but no handoff file",
                recommended_command="bc250-llm-mode llamacpp resume",
            )]
        return [Finding(
            id=HANDOFF_MISMATCH, severity=SEVERITY_PASS,
            title="Handoff consistent",
            evidence="handoff fingerprint matches known-good runtime"
                     if expected else "no known-good runtime recorded",
        )]

    def _check_runtime_digest(self, conn) -> list[Finding]:
        component = conn.execute(
            "SELECT promoted_build_id, promoted_tree_id "
            "FROM runtime_component_state WHERE component = 'llamacpp'"
        ).fetchone()
        if component is None or not component["promoted_build_id"]:
            return [Finding(
                id=RUNTIME_DIGEST, severity=SEVERITY_INFO,
                title="No runtime published",
                evidence="runtime_component_state has no promoted build",
            )]
        build = conn.execute(
            "SELECT manifest_digest FROM runtime_builds WHERE build_id = ?",
            (component["promoted_build_id"],),
        ).fetchone()
        tree = None
        if component["promoted_tree_id"]:
            tree = conn.execute(
                "SELECT manifest_digest, server_binary_digest "
                "FROM runtime_trees WHERE tree_id = ?",
                (component["promoted_tree_id"],),
            ).fetchone()
        if build and tree and build["manifest_digest"] != tree["manifest_digest"]:
            return [Finding(
                id=RUNTIME_DIGEST, severity=SEVERITY_FAIL,
                title="Active tree manifest digest does not match build",
                evidence=f"build={build['manifest_digest'][:12]}… "
                         f"tree={tree['manifest_digest'][:12]}…",
                recommended_command="bc250-llm-mode llamacpp rollback",
            )]
        return [Finding(
            id=RUNTIME_DIGEST, severity=SEVERITY_PASS,
            title="Runtime manifest digest consistent",
            evidence="promoted build and active tree manifest digests agree"
                     if (build and tree) else "no active tree recorded",
        )]

    def _check_model_digests(self, conn) -> list[Finding]:
        out: list[Finding] = []
        models = ModelInstallationsRepository(conn).list()
        checked = 0
        for model in models:
            digest = model.get("content_digest")
            path = model.get("path")
            if not digest:
                continue
            if not path or not os.path.exists(path):
                out.append(Finding(
                    id=MODEL_DIGEST, severity=SEVERITY_WARN,
                    title=f"Model file missing for {model.get('id')!r}",
                    evidence=f"expected at {path!r}",
                    recommended_command="bc250 models list",
                ))
                continue
            actual = self._file_digest(path)
            checked += 1
            if actual is None:
                out.append(Finding(
                    id=MODEL_DIGEST, severity=SEVERITY_WARN,
                    title=f"Model file unreadable for {model.get('id')!r}",
                    evidence=f"could not digest {path!r}",
                ))
            elif actual != digest:
                out.append(Finding(
                    id=MODEL_DIGEST, severity=SEVERITY_FAIL,
                    title=f"Model digest mismatch for {model.get('id')!r}",
                    evidence=f"stored={digest[:12]}… actual={actual[:12]}…",
                    recommended_command="bc250 models verify",
                ))
        if not out:
            out.append(Finding(
                id=MODEL_DIGEST, severity=SEVERITY_PASS,
                title="Model artifact digests verified",
                evidence=f"{checked} model file(s) re-digested cleanly"
                         if checked else "no digest-pinned models installed",
            ))
        return out

    def _check_thermal(self, conn) -> list[Finding]:
        thermal = ThermalStateRepository(conn).get()
        latch = thermal.get("latch_state") or "nominal"
        if latch != "nominal":
            return [Finding(
                id=THERMAL_LATCH, severity=SEVERITY_FAIL,
                title="Thermal latch engaged",
                evidence=f"latch_state={latch!r} "
                         f"baseline={thermal.get('baseline')!r}",
                recommended_command="bc250-llm-mode thermals reset",
            )]
        return [Finding(
            id=THERMAL_LATCH, severity=SEVERITY_PASS,
            title="Thermal latch nominal",
            evidence="latch_state='nominal'",
        )]

    def _check_backup(self, settings: dict, now: str) -> list[Finding]:
        last = settings.get("backup_last_completed_at")
        if not last:
            return [Finding(
                id=STALE_BACKUP, severity=SEVERITY_WARN,
                title="No backup recorded",
                evidence="backup_last_completed_at is unset",
                recommended_command="bc250-llm-mode backup run",
            )]
        ts = _parse_ts(last)
        now_ts = _parse_ts(now)
        if ts and now_ts and now_ts - ts > BACKUP_STALE_AFTER:
            return [Finding(
                id=STALE_BACKUP, severity=SEVERITY_WARN,
                title="Backup is stale",
                evidence=f"last backup {last} older than "
                         f"{BACKUP_STALE_AFTER.days} days",
                recommended_command="bc250-llm-mode backup run",
            )]
        return [Finding(
            id=STALE_BACKUP, severity=SEVERITY_PASS,
            title="Backup fresh",
            evidence=f"last backup {last}",
        )]

    def _check_directories(self) -> list[Finding]:
        out: list[Finding] = []
        app_dir = self._paths.app_dir
        if not os.access(app_dir, os.W_OK):
            out.append(Finding(
                id=DIR_PERMS, severity=SEVERITY_FAIL,
                title="Profile directory not writable",
                evidence=f"{app_dir} is not writable by this user",
                recommended_command="check profile ownership",
            ))
        else:
            out.append(Finding(
                id=DIR_PERMS, severity=SEVERITY_PASS,
                title="Profile directory writable",
                evidence=f"{app_dir.name} is writable",
            ))
        secret_files = []
        legacy_secret = app_dir / "gateway-credential"
        if legacy_secret.exists():
            secret_files.append(legacy_secret)
        managed = app_dir / "connection-secrets"
        try:
            secret_files.extend(
                path for path in sorted(managed.iterdir(), key=lambda item: item.name)[:32]
                if path.is_file() and not path.is_symlink())
        except OSError:
            pass
        modes = []
        for secret in secret_files:
            try:
                modes.append(stat.S_IMODE(secret.stat().st_mode))
            except OSError:
                modes.append(-1)
        if any(mode != 0o600 for mode in modes):
            out.append(Finding(
                id=SECRET_PERMS, severity=SEVERITY_FAIL,
                title="Client credential file permissions too open",
                evidence="one or more client credential files are not mode 0o600",
                recommended_command="bc250-llm-mode connections clients",
            ))
        elif modes:
            out.append(Finding(
                id=SECRET_PERMS, severity=SEVERITY_PASS,
                title="Client credential file permissions ok",
                evidence=f"{len(modes)} credential file(s), all mode 0o600",
            ))
        return out

    def _check_topology(self, settings: dict) -> list[Finding]:
        out: list[Finding] = []
        funnel = bool(settings.get("funnel_enabled"))
        serving = bool(settings.get("tailscale_serving"))
        if funnel:
            out.append(Finding(
                id=INSECURE_TOPOLOGY, severity=SEVERITY_FAIL,
                title="Public funnel exposure enabled",
                evidence="funnel_enabled is set; the appliance is reachable "
                         "from the public internet",
                recommended_command="bc250-llm-mode share disable --funnel",
            ))
        elif serving:
            out.append(Finding(
                id=INSECURE_TOPOLOGY, severity=SEVERITY_INFO,
                title="Tailscale serving enabled",
                evidence="sharing is routed through the authenticated "
                         "gateway over the tailnet",
            ))
        else:
            out.append(Finding(
                id=INSECURE_TOPOLOGY, severity=SEVERITY_PASS,
                title="Topology loopback-only",
                evidence="no public sharing enabled",
            ))
        return out

    def _check_storage(self) -> list[Finding]:
        out: list[Finding] = []
        # Walk to the nearest existing ancestor so a not-yet-created
        # models_dir still yields a real filesystem reading.
        probe = Path(self._paths.models_dir)
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            usage = shutil.disk_usage(str(probe))
            fraction = (usage.total - usage.free) / usage.total
        except OSError as exc:
            return [Finding(
                id=LOW_DISK, severity=SEVERITY_WARN,
                title="Could not read filesystem usage",
                evidence=repr(exc),
            )]
        if usage.free < LOW_DISK_FREE_BYTES or fraction >= LOW_DISK_FRACTION:
            out.append(Finding(
                id=LOW_DISK, severity=SEVERITY_WARN,
                title="Low disk space for models",
                evidence=f"free={usage.free} bytes, used "
                         f"{fraction:.0%} of filesystem",
                recommended_command="free space or move the models directory",
            ))
        else:
            out.append(Finding(
                id=LOW_DISK, severity=SEVERITY_PASS,
                title="Disk space adequate",
                evidence=f"free={usage.free} bytes ({fraction:.0%} used)",
            ))
        # Orphan candidates: staging/quarantine entries are reported, never
        # deleted, by the doctor.
        orphans = 0
        for root in (self._paths.model_staging_dir,
                     self._paths.model_quarantine_dir):
            try:
                with os.scandir(root) as it:
                    orphans += sum(1 for _ in it)
            except OSError:
                continue
        if orphans:
            out.append(Finding(
                id=ORPHAN_CANDIDATE, severity=SEVERITY_INFO,
                title="Staging/quarantine candidates present",
                evidence=f"{orphans} entr(ies) retained; doctor never "
                         "deletes them",
                recommended_command="bc250 models list",
            ))
        return out

    def _check_inference(self) -> list[Finding]:
        if self._inference_probe is None:
            return [Finding(
                id=INFERENCE, severity=SEVERITY_INFO,
                title="Inference not probed",
                evidence="read-only doctor did not run a live inference "
                         "probe",
            )]
        try:
            probe = self._inference_probe()
        except Exception as exc:  # noqa: BLE001 - a failed probe is a finding
            return [Finding(
                id=INFERENCE, severity=SEVERITY_FAIL,
                title="Inference probe failed",
                evidence=repr(exc),
                recommended_command="bc250-llm-mode serve status",
            )]
        if isinstance(probe, dict) and probe.get("ok"):
            return [Finding(
                id=INFERENCE, severity=SEVERITY_PASS,
                title="Inference probe succeeded",
                evidence="bounded live probe returned ok",
            )]
        return [Finding(
            id=INFERENCE, severity=SEVERITY_FAIL,
            title="Inference probe not ok",
            evidence=f"probe returned {probe!r}",
            recommended_command="bc250-llm-mode serve status",
        )]


def open_doctor_service(database_path, paths: AppPaths) -> DoctorService:
    return DoctorService(UnitOfWorkFactory(database_path), paths)
