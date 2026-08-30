"""Migration-012 workload profile contracts and repositories.

This module is deliberately free of Tk, subprocess, host, service, and network
imports. It owns stable built-in identities, bounded profile validation,
canonical resolution fingerprints, and revision-fenced persistence only.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from typing import Callable

from .legacy_import import utcnow
from .repositories import RepositoryConflict


PROFILE_SCHEMA_VERSION = 1
RESOLUTION_POLICY_VERSION = 1
MAX_ACTIVE_CUSTOM_PROFILES = 32
CONTEXT_RANGE = (512, 262144)
SLOT_RANGE = (1, 8)
BATCH_RANGE = (128, 2048)
UBATCH_RANGE = (64, 512)

OWNERS = frozenset({"builtin", "user"})
PURPOSES = frozenset(
    {"interactive", "long_context", "shared", "cool", "throughput", "custom"}
)
RESOLUTION_POLICIES = frozenset(
    {
        "interactive-v1",
        "long-context-v1",
        "shared-v1",
        "cool-v1",
        "throughput-v1",
        "custom-v1",
    }
)
KV_CACHE_TYPES = frozenset({"q8_0", "q4_0"})
FLASH_PREFERENCES = frozenset({"auto", "on", "off"})
OPTIMIZATION_PRESETS = frozenset({"custom", "cool-quiet", "balanced", "maximum"})
THERMAL_POLICIES = frozenset({"standard", "cool", "throughput-guarded"})
IDLE_POLICIES = frozenset({"KEEP_LOADED", "STOP_AFTER", "STOP_ON_DESKTOP"})
EVIDENCE_CLASSES = frozenset({"MEASURED_LOCAL", "HARDWARE_VALIDATED", "ESTIMATED"})

_CUSTOM_ID = re.compile(r"[0-9a-f]{32}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class WorkloadProfileError(ValueError):
    """Bounded validation failure before profile persistence."""


@dataclass(frozen=True)
class BuiltinProfileSpec:
    profile_id: str
    name: str
    purpose: str
    resolution_policy: str
    context_per_slot: int | None
    slots: int
    kv_cache_type: str
    batch_size: int
    ubatch_size: int
    flash_attention: str
    optimization_preset_id: str
    thermal_policy: str
    idle_policy: str
    stop_after_minutes: int | None = None


BUILTIN_PROFILES: tuple[BuiltinProfileSpec, ...] = (
    BuiltinProfileSpec(
        "builtin-interactive", "Interactive", "interactive", "interactive-v1",
        None, 1, "q8_0", 1024, 256, "auto", "balanced", "standard",
        "KEEP_LOADED",
    ),
    BuiltinProfileSpec(
        "builtin-long-context", "Long context", "long_context", "long-context-v1",
        None, 1, "q8_0", 512, 128, "auto", "balanced", "standard",
        "KEEP_LOADED",
    ),
    BuiltinProfileSpec(
        "builtin-shared", "Shared", "shared", "shared-v1",
        None, 2, "q4_0", 512, 128, "auto", "balanced", "standard",
        "KEEP_LOADED",
    ),
    BuiltinProfileSpec(
        "builtin-cool", "Cool", "cool", "cool-v1",
        None, 1, "q8_0", 512, 128, "auto", "cool-quiet", "cool",
        "STOP_AFTER", 30,
    ),
    BuiltinProfileSpec(
        "builtin-throughput", "Throughput", "throughput", "throughput-v1",
        None, 1, "q8_0", 1024, 256, "auto", "balanced", "throughput-guarded",
        "KEEP_LOADED",
    ),
)
BUILTIN_PROFILE_IDS = frozenset(item.profile_id for item in BUILTIN_PROFILES)


@dataclass(frozen=True)
class WorkloadProfileRecord:
    profile_id: str
    owner: str
    name: str
    purpose: str
    resolution_policy: str
    schema_version: int
    revision: int
    context_per_slot: int | None
    slots: int | None
    kv_cache_type: str
    batch_size: int
    ubatch_size: int
    flash_attention: str
    optimization_preset_id: str
    thermal_policy: str
    idle_policy: str
    stop_after_minutes: int | None
    evidence_class: str
    evidence_fingerprint: str | None
    evidence_recorded_at: str | None
    created_at: str
    updated_at: str
    deleted_at: str | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ProfileResolutionIdentity:
    """Exact non-secret inputs bound by an applied-profile fingerprint."""

    profile_id: str
    profile_revision: int
    model_artifact_digest: str
    quant: str
    context_per_slot: int
    slots: int
    kv_cache_type: str
    batch_size: int
    ubatch_size: int
    flash_attention: str
    optimization_preset_id: str
    thermal_policy: str
    idle_policy: str
    stop_after_minutes: int | None
    resolution_policy_version: int = RESOLUTION_POLICY_VERSION

    def fingerprint(self) -> str:
        validate_resolution_identity(self)
        payload = json.dumps(
            asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def normalize_profile_name(value: str) -> str:
    name = " ".join(str(value).strip().split())
    if not 1 <= len(name) <= 80:
        raise WorkloadProfileError("profile name must be 1-80 characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise WorkloadProfileError("profile name contains control characters")
    return name


def validate_custom_profile_id(value: str) -> str:
    profile_id = str(value)
    if not _CUSTOM_ID.fullmatch(profile_id):
        raise WorkloadProfileError("custom profile id must be generated lowercase UUID hex")
    return profile_id


def _bounded_int(value: object, *, name: str, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise WorkloadProfileError(f"{name} must be an integer from {low} to {high}")
    return value


def _validate_profile_values(record: WorkloadProfileRecord) -> WorkloadProfileRecord:
    if record.owner not in OWNERS or record.purpose not in PURPOSES:
        raise WorkloadProfileError("unknown profile owner or purpose")
    if record.resolution_policy not in RESOLUTION_POLICIES:
        raise WorkloadProfileError("unknown profile resolution policy")
    if record.schema_version != PROFILE_SCHEMA_VERSION:
        raise WorkloadProfileError("unknown profile schema version")
    if record.revision < 1:
        raise WorkloadProfileError("profile revision must be positive")
    if record.owner == "builtin":
        if record.profile_id not in BUILTIN_PROFILE_IDS or record.purpose == "custom":
            raise WorkloadProfileError("invalid built-in profile identity")
    elif record.purpose != "custom":
        raise WorkloadProfileError("user profiles must use custom purpose")
    else:
        validate_custom_profile_id(record.profile_id)
    normalize_profile_name(record.name)
    if record.context_per_slot is not None:
        _bounded_int(record.context_per_slot, name="context_per_slot", low=CONTEXT_RANGE[0], high=CONTEXT_RANGE[1])
    if record.slots is not None:
        _bounded_int(record.slots, name="slots", low=SLOT_RANGE[0], high=SLOT_RANGE[1])
    if record.kv_cache_type not in KV_CACHE_TYPES:
        raise WorkloadProfileError("unknown KV cache type")
    batch = _bounded_int(record.batch_size, name="batch_size", low=BATCH_RANGE[0], high=BATCH_RANGE[1])
    ubatch = _bounded_int(record.ubatch_size, name="ubatch_size", low=UBATCH_RANGE[0], high=UBATCH_RANGE[1])
    if batch % 64 or ubatch % 64 or ubatch > batch:
        raise WorkloadProfileError("batch and ubatch must be multiples of 64 and ubatch cannot exceed batch")
    if record.flash_attention not in FLASH_PREFERENCES:
        raise WorkloadProfileError("unknown flash-attention preference")
    if record.optimization_preset_id not in OPTIMIZATION_PRESETS:
        raise WorkloadProfileError("unknown optimization preset")
    if record.thermal_policy not in THERMAL_POLICIES:
        raise WorkloadProfileError("unknown thermal policy")
    if record.idle_policy not in IDLE_POLICIES:
        raise WorkloadProfileError("unknown idle policy")
    if record.idle_policy == "STOP_AFTER":
        _bounded_int(record.stop_after_minutes, name="stop_after_minutes", low=5, high=240)
    elif record.stop_after_minutes is not None:
        raise WorkloadProfileError("stop_after_minutes is valid only with STOP_AFTER")
    if record.evidence_class not in EVIDENCE_CLASSES:
        raise WorkloadProfileError("unknown evidence class")
    if record.evidence_fingerprint is not None and not _SHA256.fullmatch(record.evidence_fingerprint):
        raise WorkloadProfileError("evidence fingerprint must be sha256 hex")
    if bool(record.evidence_fingerprint) != bool(record.evidence_recorded_at):
        raise WorkloadProfileError("evidence fingerprint and timestamp must be set together")
    return replace(record, name=normalize_profile_name(record.name))


def validate_resolution_identity(identity: ProfileResolutionIdentity) -> None:
    if identity.profile_id not in BUILTIN_PROFILE_IDS and not _CUSTOM_ID.fullmatch(identity.profile_id):
        raise WorkloadProfileError("unknown profile identity shape")
    _bounded_int(identity.profile_revision, name="profile_revision", low=1, high=2**63 - 1)
    if not _SHA256.fullmatch(identity.model_artifact_digest):
        raise WorkloadProfileError("model artifact digest must be sha256 hex")
    if not identity.quant or len(identity.quant) > 32:
        raise WorkloadProfileError("quant must be a short non-empty value")
    _bounded_int(identity.context_per_slot, name="context_per_slot", low=CONTEXT_RANGE[0], high=CONTEXT_RANGE[1])
    _bounded_int(identity.slots, name="slots", low=SLOT_RANGE[0], high=SLOT_RANGE[1])
    if identity.kv_cache_type not in KV_CACHE_TYPES:
        raise WorkloadProfileError("unknown KV cache type")
    batch = _bounded_int(identity.batch_size, name="batch_size", low=BATCH_RANGE[0], high=BATCH_RANGE[1])
    ubatch = _bounded_int(identity.ubatch_size, name="ubatch_size", low=UBATCH_RANGE[0], high=UBATCH_RANGE[1])
    if batch % 64 or ubatch % 64 or ubatch > batch:
        raise WorkloadProfileError("invalid batch relationship")
    if identity.flash_attention not in FLASH_PREFERENCES:
        raise WorkloadProfileError("unknown flash preference")
    if identity.optimization_preset_id not in OPTIMIZATION_PRESETS:
        raise WorkloadProfileError("unknown optimization preset")
    if identity.thermal_policy not in THERMAL_POLICIES:
        raise WorkloadProfileError("unknown thermal policy")
    if identity.idle_policy not in IDLE_POLICIES:
        raise WorkloadProfileError("unknown idle policy")
    if identity.idle_policy == "STOP_AFTER":
        _bounded_int(identity.stop_after_minutes, name="stop_after_minutes", low=5, high=240)
    elif identity.stop_after_minutes is not None:
        raise WorkloadProfileError("stop-after value without STOP_AFTER policy")
    if identity.resolution_policy_version != RESOLUTION_POLICY_VERSION:
        raise WorkloadProfileError("unknown resolution policy version")


class WorkloadProfileRepository:
    """Revision-fenced profile persistence. Callers own transactions."""

    _COLUMNS = (
        "profile_id, owner, name, purpose, resolution_policy, schema_version, "
        "revision, context_per_slot, slots, kv_cache_type, batch_size, "
        "ubatch_size, flash_attention, optimization_preset_id, thermal_policy, "
        "idle_policy, stop_after_minutes, evidence_class, evidence_fingerprint, "
        "evidence_recorded_at, created_at, updated_at, deleted_at"
    )

    def __init__(self, conn, *, clock: Callable[[], str] = utcnow) -> None:
        self.conn = conn
        self._clock = clock

    @staticmethod
    def _record(row) -> WorkloadProfileRecord:
        return WorkloadProfileRecord(**{key: row[key] for key in row.keys()})

    def get(self, profile_id: str, *, include_deleted: bool = True) -> WorkloadProfileRecord | None:
        where = "" if include_deleted else " AND deleted_at IS NULL"
        row = self.conn.execute(
            f"SELECT {self._COLUMNS} FROM workload_profiles WHERE profile_id = ?{where}",
            (profile_id,),
        ).fetchone()
        return self._record(row) if row else None

    def list(self, *, include_deleted: bool = False) -> tuple[WorkloadProfileRecord, ...]:
        where = "" if include_deleted else "WHERE deleted_at IS NULL"
        rows = self.conn.execute(
            f"SELECT {self._COLUMNS} FROM workload_profiles {where} "
            "ORDER BY CASE owner WHEN 'builtin' THEN 0 ELSE 1 END, lower(name), profile_id"
        ).fetchall()
        return tuple(self._record(row) for row in rows)

    def create_custom(
        self,
        *,
        profile_id: str,
        name: str,
        context_per_slot: int,
        slots: int,
        kv_cache_type: str = "q8_0",
        batch_size: int = 1024,
        ubatch_size: int = 256,
        flash_attention: str = "auto",
        optimization_preset_id: str = "balanced",
        thermal_policy: str = "standard",
        idle_policy: str = "KEEP_LOADED",
        stop_after_minutes: int | None = None,
    ) -> WorkloadProfileRecord:
        now = self._clock()
        candidate = _validate_profile_values(WorkloadProfileRecord(
            profile_id=validate_custom_profile_id(profile_id), owner="user",
            name=name, purpose="custom", resolution_policy="custom-v1",
            schema_version=PROFILE_SCHEMA_VERSION, revision=1,
            context_per_slot=context_per_slot, slots=slots,
            kv_cache_type=kv_cache_type, batch_size=batch_size,
            ubatch_size=ubatch_size, flash_attention=flash_attention,
            optimization_preset_id=optimization_preset_id,
            thermal_policy=thermal_policy, idle_policy=idle_policy,
            stop_after_minutes=stop_after_minutes, evidence_class="ESTIMATED",
            evidence_fingerprint=None, evidence_recorded_at=None,
            created_at=now, updated_at=now, deleted_at=None,
        ))
        active_count = self.conn.execute(
            "SELECT COUNT(*) AS n FROM workload_profiles "
            "WHERE owner = 'user' AND deleted_at IS NULL"
        ).fetchone()["n"]
        if int(active_count) >= MAX_ACTIVE_CUSTOM_PROFILES:
            raise WorkloadProfileError("active custom profile limit reached")
        values = asdict(candidate)
        columns = tuple(values)
        try:
            self.conn.execute(
                f"INSERT INTO workload_profiles ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                tuple(values[column] for column in columns),
            )
        except Exception as exc:
            raise RepositoryConflict(
                "PROFILE_CREATE_CONFLICT", "profile identity or active name conflicts"
            ) from exc
        result = self.get(candidate.profile_id)
        assert result is not None
        return result

    def replace_custom(
        self,
        profile_id: str,
        *,
        expected_revision: int,
        name: str,
        context_per_slot: int,
        slots: int,
        kv_cache_type: str,
        batch_size: int,
        ubatch_size: int,
        flash_attention: str,
        optimization_preset_id: str,
        thermal_policy: str,
        idle_policy: str,
        stop_after_minutes: int | None,
    ) -> WorkloadProfileRecord:
        current = self.get(profile_id)
        if current is None or current.deleted_at is not None:
            raise RepositoryConflict("PROFILE_NOT_FOUND", "active profile does not exist")
        if current.owner != "user":
            raise RepositoryConflict("PROFILE_BUILTIN_IMMUTABLE", "built-in profiles cannot be edited")
        if current.revision != expected_revision:
            raise RepositoryConflict("PROFILE_REVISION_CONFLICT", "stale profile revision")
        candidate = _validate_profile_values(replace(
            current, name=name, context_per_slot=context_per_slot, slots=slots,
            kv_cache_type=kv_cache_type, batch_size=batch_size,
            ubatch_size=ubatch_size, flash_attention=flash_attention,
            optimization_preset_id=optimization_preset_id,
            thermal_policy=thermal_policy, idle_policy=idle_policy,
            stop_after_minutes=stop_after_minutes,
            evidence_class="ESTIMATED", evidence_fingerprint=None,
            evidence_recorded_at=None, updated_at=self._clock(),
            revision=expected_revision + 1,
        ))
        cursor = self.conn.execute(
            "UPDATE workload_profiles SET name = ?, context_per_slot = ?, slots = ?, "
            "kv_cache_type = ?, batch_size = ?, ubatch_size = ?, flash_attention = ?, "
            "optimization_preset_id = ?, thermal_policy = ?, idle_policy = ?, "
            "stop_after_minutes = ?, evidence_class = 'ESTIMATED', "
            "evidence_fingerprint = NULL, evidence_recorded_at = NULL, "
            "updated_at = ?, revision = revision + 1 "
            "WHERE profile_id = ? AND owner = 'user' AND deleted_at IS NULL "
            "AND revision = ?",
            (
                candidate.name, candidate.context_per_slot, candidate.slots,
                candidate.kv_cache_type, candidate.batch_size,
                candidate.ubatch_size, candidate.flash_attention,
                candidate.optimization_preset_id, candidate.thermal_policy,
                candidate.idle_policy, candidate.stop_after_minutes,
                candidate.updated_at, profile_id, expected_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise RepositoryConflict("PROFILE_REVISION_CONFLICT", "stale profile revision")
        result = self.get(profile_id)
        assert result is not None
        return result

    def soft_delete(self, profile_id: str, *, expected_revision: int) -> WorkloadProfileRecord:
        current = self.get(profile_id)
        if current is None or current.deleted_at is not None:
            raise RepositoryConflict("PROFILE_NOT_FOUND", "active profile does not exist")
        if current.owner != "user":
            raise RepositoryConflict("PROFILE_BUILTIN_IMMUTABLE", "built-in profiles cannot be deleted")
        if current.revision != expected_revision:
            raise RepositoryConflict("PROFILE_REVISION_CONFLICT", "stale profile revision")
        now = self._clock()
        cursor = self.conn.execute(
            "UPDATE workload_profiles SET deleted_at = ?, updated_at = ?, "
            "revision = revision + 1 WHERE profile_id = ? AND owner = 'user' "
            "AND deleted_at IS NULL AND revision = ?",
            (now, now, profile_id, expected_revision),
        )
        if cursor.rowcount != 1:
            raise RepositoryConflict("PROFILE_REVISION_CONFLICT", "stale profile revision")
        result = self.get(profile_id)
        assert result is not None
        return result

    def record_evidence(
        self,
        profile_id: str,
        *,
        expected_revision: int,
        evidence_class: str,
        evidence_fingerprint: str,
        evidence_recorded_at: str,
    ) -> WorkloadProfileRecord:
        if evidence_class not in {"MEASURED_LOCAL", "HARDWARE_VALIDATED"}:
            raise WorkloadProfileError("recorded evidence must be measured or hardware-validated")
        if not _SHA256.fullmatch(str(evidence_fingerprint)):
            raise WorkloadProfileError("evidence fingerprint must be sha256 hex")
        current = self.get(profile_id, include_deleted=False)
        if current is None:
            raise RepositoryConflict("PROFILE_NOT_FOUND", "active profile does not exist")
        if current.owner != "user":
            raise RepositoryConflict(
                "PROFILE_BUILTIN_IMMUTABLE",
                "built-in profiles cannot store mutable evidence",
            )
        if current.revision != expected_revision:
            raise RepositoryConflict("PROFILE_REVISION_CONFLICT", "stale profile revision")
        cursor = self.conn.execute(
            "UPDATE workload_profiles SET evidence_class = ?, evidence_fingerprint = ?, "
            "evidence_recorded_at = ?, updated_at = ?, revision = revision + 1 "
            "WHERE profile_id = ? AND deleted_at IS NULL AND revision = ?",
            (
                evidence_class, evidence_fingerprint, evidence_recorded_at,
                self._clock(), profile_id, expected_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise RepositoryConflict("PROFILE_REVISION_CONFLICT", "stale profile revision")
        result = self.get(profile_id)
        assert result is not None
        return result
