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
            "SELECT model_alias, context, slots FROM runtime_config WHERE id = 1"
        ).fetchone()
        if row is None:
            return {}
        return {
            "model_alias": row["model_alias"],
            "context": row["context"],
            "slots": row["slots"],
        }

    def update(self, *, model_alias, context: int, slots: int) -> None:
        self.conn.execute(
            "INSERT INTO runtime_config(id, model_alias, context, slots, updated_at) "
            "VALUES (1, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET model_alias=excluded.model_alias, "
            "context=excluded.context, slots=excluded.slots, "
            "updated_at=excluded.updated_at",
            (model_alias, context, slots, utcnow()),
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
        path: str,
        quant: str,
        display_name: str,
        sampling: dict | None = None,
    ) -> int:
        """Idempotent exact alias registration (U1.1 §5.2).

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
            "SELECT storage_state, trust_state FROM model_artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()
        if trust is None:
            raise RepositoryConflict(
                "ARTIFACT_RECORD_CONFLICT",
                f"unknown artifact {artifact_id!r}",
            )
        if trust["storage_state"] != "MANAGED" or trust["trust_state"] not in (
            "VERIFIED",
            "LEGACY_UNVERIFIED",
        ):
            raise RepositoryConflict(
                "INSTALLATION_ALIAS_CONFLICT",
                "quarantined/unverified artifacts cannot receive an alias",
            )
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


class KnownGoodRuntimeRepository:
    """Single-row last verified-working runtime configuration (migration 002)."""

    def __init__(self, conn) -> None:
        self.conn = conn

    def get(self):
        row = self.conn.execute(
            "SELECT model_alias, context, slots, profile_id, runtime_json, "
            "runtime_fingerprint, runtime_component_identity, verified_at "
            "FROM known_good_runtime WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        return {
            "model_alias": row["model_alias"],
            "context": row["context"],
            "slots": row["slots"],
            "profile_id": row["profile_id"],
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
        runtime: dict | None = None,
        runtime_fingerprint=None,
        runtime_component_identity=None,
        verified_at: str,
    ) -> None:
        self.conn.execute(
            "INSERT INTO known_good_runtime(id, model_alias, context, slots, "
            "profile_id, runtime_json, runtime_fingerprint, "
            "runtime_component_identity, verified_at) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET model_alias=excluded.model_alias, "
            "context=excluded.context, slots=excluded.slots, "
            "profile_id=excluded.profile_id, runtime_json=excluded.runtime_json, "
            "runtime_fingerprint=excluded.runtime_fingerprint, "
            "runtime_component_identity=excluded.runtime_component_identity, "
            "verified_at=excluded.verified_at",
            (
                model_alias, int(context), int(slots), profile_id,
                json.dumps(runtime or {}), runtime_fingerprint,
                runtime_component_identity, verified_at,
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
    """Durable logical reservations (U1.1 §4.6/§5.4). Release marks."""

    def __init__(self, conn, *, clock=None) -> None:
        self.conn = conn
        self._clock = clock or utcnow

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
    ) -> dict:
        if min(required_bytes, available_bytes, reserved_bytes) < 0:
            raise ValueError("reservation bytes must be >= 0")
        if reserved_bytes > required_bytes:
            raise ValueError("reserved bytes cannot exceed required")
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

    def update_growth(self, operation_id: str, *, reserved_bytes: int) -> dict:
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

    def release(self, operation_id: str) -> None:
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