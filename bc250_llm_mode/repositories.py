"""Typed repositories over the SQLite schema.

Repositories own persistence details. Domain services call these narrow
methods; nothing outside a repository module executes raw SQL against
durable state. Repositories never commit: their callers own the unit-of-work
transaction boundary so multi-repository invariants commit atomically.
"""

from __future__ import annotations

import json

from .legacy_import import utcnow


class SettingsRepository:
    """Validated configuration key/value storage."""

    REVISION_KEY = "__revision"

    def __init__(self, conn) -> None:
        self.conn = conn

    def all(self) -> dict:
        rows = self.conn.execute("SELECT key, value_json FROM settings")
        return {r["key"]: json.loads(r["value_json"]) for r in rows}

    def get(self, key: str, default=None):
        row = self.conn.execute(
            "SELECT value_json FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return json.loads(row["value_json"]) if row else default

    def set_many(self, values: dict) -> None:
        now = utcnow()
        for key, value in sorted(values.items()):
            self.conn.execute(
                "INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, "
                "updated_at=excluded.updated_at",
                (key, json.dumps(value), now),
            )

    def delete(self, keys) -> None:
        for key in sorted(keys):
            self.conn.execute("DELETE FROM settings WHERE key = ?", (key,))

    def revision(self) -> int:
        return int(self.get(self.REVISION_KEY, 0))

    def set_revision(self, value: int) -> None:
        self.set_many({self.REVISION_KEY: value})


class RuntimeConfigRepository:
    """Single-row desired runtime configuration."""

    def __init__(self, conn) -> None:
        self.conn = conn

    def get(self) -> dict:
        row = self.conn.execute(
            "SELECT model_alias, context, slots, profile_id, profile_revision, "
            "profile_fingerprint FROM runtime_config WHERE id = 1"
        ).fetchone()
        if row is None:
            return {}
        return {
            "model_alias": row["model_alias"],
            "context": row["context"],
            "slots": row["slots"],
            "profile_id": row["profile_id"],
            "profile_revision": row["profile_revision"],
            "profile_fingerprint": row["profile_fingerprint"],
        }

    def update(
        self,
        *,
        model_alias,
        context: int,
        slots: int,
        profile_id=None,
        profile_revision=None,
        profile_fingerprint=None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO runtime_config(id, model_alias, context, slots, profile_id, "
            "profile_revision, profile_fingerprint, updated_at) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET model_alias=excluded.model_alias, "
            "context=excluded.context, slots=excluded.slots, "
            "profile_id=excluded.profile_id, "
            "profile_revision=excluded.profile_revision, "
            "profile_fingerprint=excluded.profile_fingerprint, "
            "updated_at=excluded.updated_at",
            (
                model_alias, context, slots, profile_id, profile_revision,
                profile_fingerprint, utcnow(),
            ),
        )


class RepositoryConflict(RuntimeError):
    """Typed conflict with a stable result/error code (U1.1 §5.5)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def existing_row_id(conn, alias: str) -> int:
    row = conn.execute(
        "SELECT id FROM model_installations WHERE alias = ?", (alias,)
    ).fetchone()
    return int(row["id"])


class ModelInstallationsRepository:
    def __init__(self, conn) -> None:
        self.conn = conn

    def list(self) -> list:
        rows = self.conn.execute(
            "SELECT i.alias, i.path, i.quant, i.display_name, i.sampling_json, "
            "i.provenance, i.validation_status, i.artifact_id, "
            "a.content_digest AS content_digest, "
            "a.storage_state AS artifact_storage_state, "
            "a.trust_state AS artifact_trust_state "
            "FROM model_installations i "
            "LEFT JOIN model_artifacts a ON a.id = i.artifact_id "
            "ORDER BY i.alias"
        ).fetchall()
        models = []
        for r in rows:
            model = {
                "id": r["alias"],
                "path": r["path"],
                "quant": r["quant"],
                "display_name": r["display_name"],
                "provenance": r["provenance"],
                "validation_status": r["validation_status"],
                # U1.1 compatibility projections (bounded, read-only).
                "artifact_id": r["artifact_id"],
                "content_digest": r["content_digest"],
                "artifact_storage_state": r["artifact_storage_state"],
                "artifact_trust_state": r["artifact_trust_state"],
                "managed": (
                    r["artifact_storage_state"] == "MANAGED"
                    if r["artifact_storage_state"] is not None
                    else False
                ),
            }
            model.update(json.loads(r["sampling_json"] or "{}"))
            models.append(model)
        return models

    def get_by_alias(self, alias: str) -> dict | None:
        row = self.conn.execute(
            "SELECT alias, path, quant, display_name, sampling_json, "
            "provenance, validation_status, artifact_id "
            "FROM model_installations WHERE alias = ?",
            (alias,),
        ).fetchone()
        if row is None:
            return None
        return {
            "alias": row["alias"],
            "path": row["path"],
            "quant": row["quant"],
            "display_name": row["display_name"],
            "sampling_json": json.loads(row["sampling_json"] or "{}"),
            "provenance": row["provenance"],
            "validation_status": row["validation_status"],
            "artifact_id": row["artifact_id"],
        }

    def install_alias(
        self,
        *,
        alias: str,
        artifact_id: str,
        quant: str,
        display_name: str,
        sampling: dict | None = None,
    ) -> int:
        """Idempotent exact alias registration (U1.1 §5.2/§3.6).

        The canonical path always comes from the artifact row; no caller
        path is accepted as authority. Only ``MANAGED + VERIFIED`` (or the
        explicit legacy backfill pair) receives an alias.

        - same alias + same artifact -> no-op success;
        - same alias + different artifact -> conflict;
        - quarantined artifacts are refused an alias.
        """
        existing = self.get_by_alias(alias)
        if existing is not None:
            if existing["artifact_id"] == artifact_id:
                return existing_row_id(self.conn, alias)
            raise RepositoryConflict(
                "INSTALLATION_ALIAS_CONFLICT",
                f"alias {alias!r} already points at a different artifact",
            )
        trust = self.conn.execute(
            "SELECT storage_state, trust_state, canonical_path "
            "FROM model_artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()
        if trust is None:
            raise RepositoryConflict(
                "ARTIFACT_RECORD_CONFLICT",
                f"unknown artifact {artifact_id!r}",
            )
        allowed_pairs = {
            ("MANAGED", "VERIFIED"),
            ("LEGACY_EXTERNAL", "LEGACY_UNVERIFIED"),
        }
        if (trust["storage_state"], trust["trust_state"]) not in allowed_pairs:
            raise RepositoryConflict(
                "INSTALLATION_ALIAS_CONFLICT",
                "quarantined/unverified artifacts cannot receive an alias",
            )
        path = trust["canonical_path"]
        self.conn.execute(
            "INSERT INTO model_installations(alias, path, quant, display_name, "
            "sampling_json, provenance, validation_status, imported_at, "
            "artifact_id) VALUES (?, ?, ?, ?, ?, 'managed-import', "
            "'verified', ?, ?)",
            (
                alias,
                path,
                quant,
                display_name,
                json.dumps(sampling or {}),
                utcnow(),
                artifact_id,
            ),
        )
        return existing_row_id(self.conn, alias)

    def replace_all(self, models: list) -> None:
        self.conn.execute("DELETE FROM model_installations")
        now = utcnow()
        for model in models:
            alias = str(model.get("id"))
            sampling = {
                k: model[k]
                for k in ("temperature", "top_p", "top_k", "min_p")
                if k in model
            }
            self.conn.execute(
                "INSERT INTO model_installations(alias, path, quant, display_name, "
                "sampling_json, imported_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    alias, str(model.get("path", "")), str(model.get("quant", "")),
                    str(model.get("display_name") or alias),
                    json.dumps(sampling), now,
                ),
            )


class ModelLibraryMetaRepository:
    """P6 §12.1: per-alias library metadata (pinned, usage, benchmark).

    Never stores prompt content; only timestamps and a bounded benchmark
    summary. Cascades with the installation alias.
    """

    def __init__(self, conn) -> None:
        self.conn = conn

    def get(self, alias: str) -> dict | None:
        row = self.conn.execute(
            "SELECT alias, pinned, last_used_at, last_verified_inference_at, "
            "benchmark_summary_json, updated_at FROM model_library_meta "
            "WHERE alias = ?",
            (alias,),
        ).fetchone()
        if row is None:
            return None
        return {
            "alias": row["alias"],
            "pinned": bool(row["pinned"]),
            "last_used_at": row["last_used_at"],
            "last_verified_inference_at": row["last_verified_inference_at"],
            "benchmark_summary": json.loads(row["benchmark_summary_json"] or "{}"),
            "updated_at": row["updated_at"],
        }

    def all(self) -> dict[str, dict]:
        rows = self.conn.execute(
            "SELECT alias, pinned, last_used_at, last_verified_inference_at, "
            "benchmark_summary_json, updated_at FROM model_library_meta"
        ).fetchall()
        return {
            r["alias"]: {
                "alias": r["alias"],
                "pinned": bool(r["pinned"]),
                "last_used_at": r["last_used_at"],
                "last_verified_inference_at": r["last_verified_inference_at"],
                "benchmark_summary": json.loads(r["benchmark_summary_json"] or "{}"),
                "updated_at": r["updated_at"],
            }
            for r in rows
        }

    def set_pin(self, alias: str, pinned: bool) -> None:
        self.conn.execute(
            "INSERT INTO model_library_meta(alias, pinned, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(alias) DO UPDATE SET pinned=excluded.pinned, "
            "updated_at=excluded.updated_at",
            (alias, 1 if pinned else 0, utcnow()),
        )

    def touch_used(self, alias: str) -> None:
        self.conn.execute(
            "INSERT INTO model_library_meta(alias, pinned, last_used_at, updated_at) "
            "VALUES (?, 0, ?, ?) "
            "ON CONFLICT(alias) DO UPDATE SET last_used_at=excluded.last_used_at, "
            "updated_at=excluded.updated_at",
            (alias, utcnow(), utcnow()),
        )

    def record_verified_inference(self, alias: str,
                                  benchmark_summary: dict | None = None) -> None:
        summary_json = json.dumps(benchmark_summary or {})
        self.conn.execute(
            "INSERT INTO model_library_meta(alias, pinned, "
            "last_verified_inference_at, benchmark_summary_json, updated_at) "
            "VALUES (?, 0, ?, ?, ?) "
            "ON CONFLICT(alias) DO UPDATE SET "
            "last_verified_inference_at=excluded.last_verified_inference_at, "
            "benchmark_summary_json=excluded.benchmark_summary_json, "
            "updated_at=excluded.updated_at",
            (alias, utcnow(), summary_json, utcnow()),
        )


class BenchHistoryRepository:
    MAX_ITEMS = 20

    def __init__(self, conn) -> None:
        self.conn = conn

    def append(self, entry: dict, *, commit: bool = True) -> None:
        """Append one benchmark record and enforce retention atomically.

        The insert and the oldest-first trim share the caller's unit-of-work
        transaction; ``commit=True`` (the default) closes it for standalone
        narrow callers, while unit-of-work callers pass ``commit=False`` so
        multi-repository work stays atomic.
        """
        self.conn.execute(
            "INSERT INTO bench_history(ts, payload_json) VALUES (?, ?)",
            (str(entry.get("timestamp") or utcnow()), json.dumps(entry)),
        )
        excess = (
            self.conn.execute("SELECT COUNT(*) AS c FROM bench_history").fetchone()["c"]
            - self.MAX_ITEMS
        )
        if excess > 0:
            self.conn.execute(
                "DELETE FROM bench_history WHERE id IN "
                "(SELECT id FROM bench_history ORDER BY id LIMIT ?)",
                (excess,),
            )
        if commit:
            self.conn.commit()

    def list(self) -> list:
        rows = self.conn.execute(
            "SELECT payload_json FROM bench_history ORDER BY id"
        ).fetchall()
        return [json.loads(r["payload_json"]) for r in rows]

    def replace_all(self, entries: list) -> None:
        self.conn.execute("DELETE FROM bench_history")
        now = utcnow()
        for entry in entries[: self.MAX_ITEMS]:
            self.conn.execute(
                "INSERT INTO bench_history(ts, payload_json) VALUES (?, ?)",
                (str(entry.get("timestamp") or now), json.dumps(entry)),
            )


class AutotuneHistoryRepository:
    MAX_ITEMS = 40

    def __init__(self, conn) -> None:
        self.conn = conn

    def append(self, entry: dict, *, commit: bool = True) -> None:
        """Append one autotune result and enforce retention atomically."""
        self.conn.execute(
            "INSERT INTO autotune_history(payload_json) VALUES (?)",
            (json.dumps(entry),),
        )
        excess = (
            self.conn.execute(
                "SELECT COUNT(*) AS c FROM autotune_history"
            ).fetchone()["c"]
            - self.MAX_ITEMS
        )
        if excess > 0:
            self.conn.execute(
                "DELETE FROM autotune_history WHERE id IN "
                "(SELECT id FROM autotune_history ORDER BY id LIMIT ?)",
                (excess,),
            )
        if commit:
            self.conn.commit()

    def replace_all(self, entries: list) -> None:
        self.conn.execute("DELETE FROM autotune_history")
        for entry in entries[-self.MAX_ITEMS:]:
            self.conn.execute(
                "INSERT INTO autotune_history(payload_json) VALUES (?)",
                (json.dumps(entry),),
            )

    def list(self) -> list:
        return [
            json.loads(r["payload_json"])
            for r in self.conn.execute(
                "SELECT payload_json FROM autotune_history ORDER BY id"
            )
        ]


class ThermalStateRepository:
    """Safety-authoritative latch + baseline. Survives migration/reboot."""

    def __init__(self, conn) -> None:
        self.conn = conn

    def get(self) -> dict:
        row = self.conn.execute(
            "SELECT latch_state, baseline_json FROM thermal_state WHERE id = 1"
        ).fetchone()
        if row is None:
            return {"latch_state": "nominal", "baseline": None}
        return {
            "latch_state": row["latch_state"],
            "baseline": json.loads(row["baseline_json"])
            if row["baseline_json"]
            else None,
        }

    def set(self, latch_state: str, baseline: dict | None) -> None:
        self.conn.execute(
            "INSERT INTO thermal_state(id, latch_state, baseline_json, updated_at) "
            "VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET latch_state=excluded.latch_state, "
            "baseline_json=excluded.baseline_json, updated_at=excluded.updated_at",
            (latch_state, json.dumps(baseline) if baseline else None, utcnow()),
        )


class ComponentProvenanceRepository:
    def __init__(self, conn) -> None:
        self.conn = conn

    def set_component(self, component: str, describe: str, commit_sha=None) -> None:
        self.conn.execute(
            "INSERT INTO component_provenance(component, describe, commit_sha, "
            "recorded_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(component) DO UPDATE SET describe=excluded.describe, "
            "commit_sha=excluded.commit_sha, recorded_at=excluded.recorded_at",
            (component, describe, commit_sha, utcnow()),
        )

    def get_component(self, component: str):
        row = self.conn.execute(
            "SELECT describe, commit_sha, recorded_at FROM component_provenance "
            "WHERE component = ?",
            (component,),
        ).fetchone()
        if row is None:
            return None
        return {
            "describe": row["describe"],
            "commit_sha": row["commit_sha"],
            "recorded": row["recorded_at"],
        }


class ExtrasRepository:
    """Quarantined unknown/legacy keys, preserved outside active config."""

    def __init__(self, conn) -> None:
        self.conn = conn

    def all(self) -> dict:
        rows = self.conn.execute(
            "SELECT key, payload_json FROM legacy_import_extras"
        )
        return {r["key"]: json.loads(r["payload_json"]) for r in rows}

    def set(self, key: str, value) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO legacy_import_extras(key, payload_json) "
            "VALUES (?, ?)",
            (key, json.dumps(value)),
        )


class ObservationRepository:
    """Stale-marked runtime observations from legacy import and probes."""

    def __init__(self, conn) -> None:
        self.conn = conn

    def list(self) -> dict[str, dict]:
        rows = self.conn.execute(
            "SELECT key, payload_json, observed_at, stale "
            "FROM runtime_observations ORDER BY key"
        ).fetchall()
        return {
            r["key"]: {
                "value": json.loads(r["payload_json"]),
                "observed_at": r["observed_at"],
                "stale": bool(r["stale"]),
            }
            for r in rows
        }


class MaintenanceSnapshotRepository:
    """Bounded read model for the maintenance inbox (EXP-4).

    The repository deliberately returns only counts, stable identities, and
    timestamps needed to prioritize maintenance.  It never returns operation
    requests/details, model paths, client labels/fingerprints, or secret
    metadata.  Expensive verification (model hashing, doctor checks, network
    probes) is represented only by already-persisted observations.
    """

    _OBSERVATION_KEYS = (
        "last_doctor_report",
        "last_inference_probe",
        "last_optional_services_probe",
        "last_storage_check",
        "last_topology_probe",
        "last_update_check",
        "last_thermal_sample",
    )

    def __init__(self, conn) -> None:
        self.conn = conn

    def read(self) -> dict:
        settings_rows = self.conn.execute(
            "SELECT key, value_json, updated_at FROM settings WHERE key IN ("
            "'backup_last_completed_at', 'current_model', 'funnel_enabled', "
            "'tailscale_serving', 'https_sharing_enabled', "
            "'verified_application_update', 'host_capability_summary', "
            "'host_capability_observed_at')"
        ).fetchall()
        settings = {
            row["key"]: {
                "value": json.loads(row["value_json"]),
                "updated_at": row["updated_at"],
            }
            for row in settings_rows
        }

        thermal = self.conn.execute(
            "SELECT latch_state, updated_at FROM thermal_state WHERE id = 1"
        ).fetchone()
        runtime = self.conn.execute(
            "SELECT model_alias, context, slots, profile_id, profile_revision, "
            "profile_fingerprint, updated_at FROM runtime_config WHERE id = 1"
        ).fetchone()
        known_good = self.conn.execute(
            "SELECT model_alias, context, slots, profile_id, profile_revision, "
            "profile_fingerprint, runtime_fingerprint, "
            "runtime_component_identity, verified_at "
            "FROM known_good_runtime WHERE id = 1"
        ).fetchone()
        component = self.conn.execute(
            "SELECT promoted_build_id, generation, updated_at "
            "FROM runtime_component_state WHERE component = 'llamacpp'"
        ).fetchone()

        current_alias = (
            runtime["model_alias"] if runtime and runtime["model_alias"]
            else (settings.get("current_model") or {}).get("value")
        )
        model = None
        if isinstance(current_alias, str) and current_alias:
            model = self.conn.execute(
                "SELECT i.alias, i.validation_status, a.storage_state, "
                "a.trust_state, a.content_digest, a.validated_at "
                "FROM model_installations AS i "
                "LEFT JOIN model_artifacts AS a ON a.id = i.artifact_id "
                "WHERE i.alias = ?",
                (current_alias,),
            ).fetchone()
        artifact_counts = self.conn.execute(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(CASE WHEN storage_state = 'QUARANTINED' OR "
            "trust_state = 'QUARANTINED' THEN 1 ELSE 0 END), 0) AS quarantined, "
            "COALESCE(SUM(CASE WHEN storage_state = 'MANAGED' AND NOT EXISTS "
            "(SELECT 1 FROM model_installations i WHERE i.artifact_id = "
            "model_artifacts.id) THEN byte_size ELSE 0 END), 0) AS reclaimable "
            "FROM model_artifacts"
        ).fetchone()

        active_ops = self.conn.execute(
            "SELECT state, COUNT(*) AS n, MIN(updated_at) AS oldest_at "
            "FROM operations WHERE finished_at IS NULL "
            "GROUP BY state ORDER BY state LIMIT 16"
        ).fetchall()
        recent_failures = self.conn.execute(
            "SELECT id, operation_type, state, error_code, updated_at, finished_at "
            "FROM operations WHERE state IN "
            "('FAILED_SAFE', 'FAILED_ROLLED_BACK', 'RECOVERY_REQUIRED') "
            "ORDER BY updated_at DESC, id DESC LIMIT 20"
        ).fetchall()
        failed_restore = self.conn.execute(
            "SELECT restore_id, publish_state, post_verify_state, rollback_state, "
            "updated_at FROM restore_attempts WHERE rollback_state = 'failed' OR "
            "publish_state = 'recovery_required' ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()

        backup = self.conn.execute(
            "SELECT backup_id, verification_state, created_at, verified_at "
            "FROM backup_sets ORDER BY created_at DESC, backup_id DESC LIMIT 1"
        ).fetchone()
        credential_counts = self.conn.execute(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(CASE WHEN revoked_at IS NULL THEN 1 ELSE 0 END), 0) "
            "AS active FROM connection_clients"
        ).fetchone()
        access = self.conn.execute(
            "SELECT enabled, disabled_at FROM connection_access_state WHERE id = 1"
        ).fetchone()

        placeholders = ",".join("?" for _ in self._OBSERVATION_KEYS)
        observations = self.conn.execute(
            "SELECT key, payload_json, observed_at, stale FROM "
            f"runtime_observations WHERE key IN ({placeholders}) "
            "ORDER BY key LIMIT 16",
            self._OBSERVATION_KEYS,
        ).fetchall()
        observation_map = {
            row["key"]: {
                "value": json.loads(row["payload_json"]),
                "observed_at": row["observed_at"],
                "stale": bool(row["stale"]),
            }
            for row in observations
        }
        return {
            "settings": settings,
            "thermal": dict(thermal) if thermal else None,
            "runtime": dict(runtime) if runtime else None,
            "known_good": dict(known_good) if known_good else None,
            "component": dict(component) if component else None,
            "current_alias": current_alias,
            "model": dict(model) if model else None,
            "artifacts": dict(artifact_counts) if artifact_counts else {},
            "active_operations": [dict(row) for row in active_ops],
            "recent_failures": [dict(row) for row in recent_failures],
            "failed_restore": dict(failed_restore) if failed_restore else None,
            "backup": dict(backup) if backup else None,
            "credentials": dict(credential_counts) if credential_counts else {},
            "access": dict(access) if access else None,
            "observations": observation_map,
        }


class KnownGoodRuntimeRepository:
    """Single-row last verified-working runtime configuration (migration 002)."""

    def __init__(self, conn) -> None:
        self.conn = conn

    def get(self):
        row = self.conn.execute(
            "SELECT model_alias, context, slots, profile_id, runtime_json, "
            "runtime_fingerprint, runtime_component_identity, verified_at, "
            "profile_revision, profile_fingerprint "
            "FROM known_good_runtime WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        return {
            "model_alias": row["model_alias"],
            "context": row["context"],
            "slots": row["slots"],
            "profile_id": row["profile_id"],
            "profile_revision": row["profile_revision"],
            "profile_fingerprint": row["profile_fingerprint"],
            "runtime": json.loads(row["runtime_json"] or "{}"),
            "runtime_fingerprint": row["runtime_fingerprint"],
            "runtime_component_identity": row["runtime_component_identity"],
            "verified_at": row["verified_at"],
        }

    def set(
        self,
        *,
        model_alias,
        context: int,
        slots: int,
        profile_id=None,
        profile_revision=None,
        profile_fingerprint=None,
        runtime: dict | None = None,
        runtime_fingerprint=None,
        runtime_component_identity=None,
        verified_at: str,
    ) -> None:
        self.conn.execute(
            "INSERT INTO known_good_runtime(id, model_alias, context, slots, "
            "profile_id, runtime_json, runtime_fingerprint, "
            "runtime_component_identity, verified_at, profile_revision, "
            "profile_fingerprint) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET model_alias=excluded.model_alias, "
            "context=excluded.context, slots=excluded.slots, "
            "profile_id=excluded.profile_id, runtime_json=excluded.runtime_json, "
            "runtime_fingerprint=excluded.runtime_fingerprint, "
            "runtime_component_identity=excluded.runtime_component_identity, "
            "profile_revision=excluded.profile_revision, "
            "profile_fingerprint=excluded.profile_fingerprint, "
            "verified_at=excluded.verified_at",
            (
                model_alias, int(context), int(slots), profile_id,
                json.dumps(runtime or {}), runtime_fingerprint,
                runtime_component_identity, verified_at, profile_revision,
                profile_fingerprint,
            ),
        )

    def clear(self) -> None:
        self.conn.execute("DELETE FROM known_good_runtime WHERE id = 1")


class ModelArtifactRepository:
    """Typed access to managed/quarantined/legacy artifacts (ADR 003)."""

    _COLUMNS = (
        "id, content_digest, byte_size, canonical_path, storage_state, "
        "trust_state, format, architecture, quantization, tensor_count, "
        "validator_version, validation_detail_json, source_kind, "
        "source_repo, source_revision, source_filename, source_digest, "
        "catalog_id, license_id, provenance_json, quarantine_reason_code, "
        "created_at, validated_at"
    )

    def __init__(self, conn, *, clock=None) -> None:
        self.conn = conn
        self._clock = clock or utcnow

    def _row_to_dict(self, row) -> dict | None:
        if row is None:
            return None
        return {
            "id": row["id"],
            "content_digest": row["content_digest"],
            "byte_size": row["byte_size"],
            "canonical_path": row["canonical_path"],
            "storage_state": row["storage_state"],
            "trust_state": row["trust_state"],
            "format": row["format"],
            "architecture": row["architecture"],
            "quantization": row["quantization"],
            "tensor_count": row["tensor_count"],
            "validator_version": row["validator_version"],
            "validation_detail": json.loads(
                row["validation_detail_json"] or "{}"
            ),
            "source_kind": row["source_kind"],
            "catalog_id": row["catalog_id"],
            "provenance": json.loads(row["provenance_json"] or "{}"),
            "quarantine_reason_code": row["quarantine_reason_code"],
            "created_at": row["created_at"],
            "validated_at": row["validated_at"],
        }

    def get(self, artifact_id: str) -> dict | None:
        row = self.conn.execute(
            f"SELECT {self._COLUMNS} FROM model_artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()
        return self._row_to_dict(row)

    def require(self, artifact_id: str) -> dict:
        found = self.get(artifact_id)
        if found is None:
            raise RepositoryConflict(
                "ARTIFACT_RECORD_CONFLICT",
                f"unknown artifact {artifact_id!r}",
            )
        return found

    def get_by_digest(self, content_digest: str) -> dict | None:
        row = self.conn.execute(
            f"SELECT {self._COLUMNS} FROM model_artifacts "
            "WHERE content_digest = ? "
            "AND storage_state IN ('MANAGED', 'QUARANTINED')",
            (content_digest.lower(),),
        ).fetchone()
        return self._row_to_dict(row)

    def list_quarantined(self) -> list[dict]:
        rows = self.conn.execute(
            f"SELECT {self._COLUMNS} FROM model_artifacts "
            "WHERE storage_state = 'QUARANTINED' ORDER BY created_at DESC"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def record_verified(
        self,
        *,
        artifact_id: str,
        content_digest: str,
        byte_size: int,
        canonical_path: str,
        format: str = "GGUF",
        architecture: str | None = None,
        quantization: str | None = None,
        tensor_count: int | None = None,
        validator_version: int = 1,
        validation_detail: dict | None = None,
        source_kind: str = "hub",
        source_repo: str | None = None,
        source_revision: str | None = None,
        source_filename: str | None = None,
        catalog_id: str | None = None,
        license_id: str | None = None,
        provenance: dict | None = None,
    ) -> str:
        from .operations.validation import sanitize_payload, sanitize_summary

        now = self._clock()
        try:
            self.conn.execute(
                """
                INSERT INTO model_artifacts (
                    id, content_digest, byte_size, canonical_path,
                    storage_state, trust_state, format, architecture,
                    quantization, tensor_count, validator_version,
                    validation_detail_json, source_kind, source_repo,
                    source_revision, source_filename, catalog_id,
                    license_id, provenance_json, created_at, validated_at
                ) VALUES (?, ?, ?, ?, 'MANAGED', 'VERIFIED',
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    content_digest.lower(),
                    int(byte_size),
                    canonical_path,
                    format,
                    sanitize_summary(architecture),
                    sanitize_summary(quantization),
                    tensor_count,
                    int(validator_version),
                    sanitize_payload(validation_detail or {}),
                    source_kind,
                    sanitize_summary(source_repo),
                    sanitize_summary(source_revision),
                    sanitize_summary(source_filename),
                    sanitize_summary(catalog_id),
                    sanitize_summary(license_id),
                    sanitize_payload(provenance or {}),
                    now,
                    now,
                ),
            )
        except Exception as exc:
            raise RepositoryConflict(
                "ARTIFACT_RECORD_CONFLICT", f"cannot record artifact: {exc}"
            ) from exc
        return artifact_id

    def record_quarantine(
        self,
        *,
        artifact_id: str,
        content_digest: str,
        byte_size: int,
        canonical_path: str,
        reason_code: str,
        validation_detail: dict | None = None,
        source_kind: str = "local",
    ) -> str:
        from .operations.validation import sanitize_payload, sanitize_summary

        now = self._clock()
        try:
            self.conn.execute(
                """
                INSERT INTO model_artifacts (
                    id, content_digest, byte_size, canonical_path,
                    storage_state, trust_state, format, validator_version,
                    validation_detail_json, source_kind,
                    quarantine_reason_code, created_at, validated_at
                ) VALUES (?, ?, ?, ?, 'QUARANTINED', 'QUARANTINED', 'GGUF',
                          1, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    content_digest.lower(),
                    int(byte_size),
                    canonical_path,
                    sanitize_payload(validation_detail or {}),
                    sanitize_summary(source_kind),
                    sanitize_summary(reason_code),
                    now,
                    now,
                ),
            )
        except Exception as exc:
            raise RepositoryConflict(
                "ARTIFACT_RECORD_CONFLICT", f"cannot quarantine: {exc}"
            ) from exc
        return artifact_id


class StorageReservationRepository:
    """Durable logical reservations (U1.1 §4.6/§5.4). Release marks.

    U1.1 §3.1/§3.6: every mutation asserts the operation's current
    ``model-storage`` lease owner/revision in the SAME connection; a stale
    worker cannot reserve, grow, or release a newer owner's reservation.
    """

    def __init__(self, conn, *, clock=None) -> None:
        self.conn = conn
        self._clock = clock or utcnow

    def _assert_lease(
        self, operation_id: str, *, lease_owner: str, lease_revision: int
    ) -> None:
        row = self.conn.execute(
            """
            SELECT owner, lease_revision, expires_at
              FROM operation_leases
             WHERE resource_key = 'model-storage' AND operation_id = ?
            """,
            (operation_id,),
        ).fetchone()
        now = self._clock()
        if (
            row is None
            or row["owner"] != lease_owner
            or int(row["lease_revision"]) != int(lease_revision)
            or str(row["expires_at"]) <= now
        ):
            raise RepositoryConflict(
                "LEASE_LOST",
                f"model-storage lease fence failed for {operation_id!r}",
            )

    def reserve(
        self,
        *,
        operation_id: str,
        filesystem_identity: str,
        required_bytes: int,
        available_bytes: int,
        reserved_bytes: int,
        reclaimable_owned_bytes: int = 0,
        credited_partial_bytes: int = 0,
        lease_owner: str | None = None,
        lease_revision: int | None = None,
    ) -> dict:
        if min(required_bytes, available_bytes, reserved_bytes) < 0:
            raise ValueError("reservation bytes must be >= 0")
        if reserved_bytes > required_bytes:
            raise ValueError("reserved bytes cannot exceed required")
        if credited_partial_bytes > reclaimable_owned_bytes:
            raise ValueError("credited partials cannot exceed owned reclaimables")
        if lease_owner is not None:
            self._assert_lease(
                operation_id,
                lease_owner=lease_owner,
                lease_revision=int(lease_revision or 0),
            )
        now = self._clock()
        try:
            self.conn.execute(
                """
                INSERT INTO operation_storage_reservations (
                    operation_id, filesystem_identity, required_bytes,
                    available_bytes, reserved_bytes, reclaimable_owned_bytes,
                    credited_partial_bytes, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'RESERVED', ?, ?)
                """,
                (
                    operation_id,
                    filesystem_identity,
                    int(required_bytes),
                    int(available_bytes),
                    int(reserved_bytes),
                    int(reclaimable_owned_bytes),
                    int(credited_partial_bytes),
                    now,
                    now,
                ),
            )
        except Exception as exc:
            raise RepositoryConflict(
                "MODEL_STORAGE_BUSY",
                f"cannot reserve for {operation_id!r}: {exc}",
            ) from exc
        result = self.get(operation_id)
        assert result is not None
        return result

    def update_growth(
        self,
        operation_id: str,
        *,
        reserved_bytes: int,
        lease_owner: str | None = None,
        lease_revision: int | None = None,
    ) -> dict:
        if lease_owner is not None:
            self._assert_lease(
                operation_id,
                lease_owner=lease_owner,
                lease_revision=int(lease_revision or 0),
            )
        now = self._clock()
        cursor = self.conn.execute(
            """
            UPDATE operation_storage_reservations
               SET reserved_bytes = MAX(reserved_bytes, ?), updated_at = ?
             WHERE operation_id = ? AND state = 'RESERVED'
            """,
            (int(reserved_bytes), now, operation_id),
        )
        if cursor.rowcount != 1:
            raise RepositoryConflict(
                "STAGING_OWNERSHIP_INVALID",
                f"no active reservation for {operation_id!r}",
            )
        result = self.get(operation_id)
        assert result is not None
        return result

    def release(
        self,
        operation_id: str,
        *,
        lease_owner: str | None = None,
        lease_revision: int | None = None,
    ) -> None:
        if lease_owner is not None:
            self._assert_lease(
                operation_id,
                lease_owner=lease_owner,
                lease_revision=int(lease_revision or 0),
            )
        now = self._clock()
        cursor = self.conn.execute(
            """
            UPDATE operation_storage_reservations
               SET state = 'RELEASED', released_at = ?, updated_at = ?
             WHERE operation_id = ? AND state = 'RESERVED'
            """,
            (now, now, operation_id),
        )
        if cursor.rowcount != 1:
            raise RepositoryConflict(
                "CLEANUP_INCOMPLETE",
                f"no active reservation to release for {operation_id!r}",
            )

    def get(self, operation_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM operation_storage_reservations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "operation_id": row["operation_id"],
            "filesystem_identity": row["filesystem_identity"],
            "required_bytes": row["required_bytes"],
            "available_bytes": row["available_bytes"],
            "reserved_bytes": row["reserved_bytes"],
            "reclaimable_owned_bytes": row["reclaimable_owned_bytes"],
            "credited_partial_bytes": row["credited_partial_bytes"],
            "state": row["state"],
            "released_at": row["released_at"],
        }
