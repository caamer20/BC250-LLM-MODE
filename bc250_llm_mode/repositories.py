"""Typed repositories over the SQLite schema (migration 001).

Repositories own persistence details. Domain code and the compatibility
facade call these narrow methods; nothing outside this module executes raw
SQL against durable state.
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


class ModelInstallationsRepository:
    def __init__(self, conn) -> None:
        self.conn = conn

    def list(self) -> list:
        rows = self.conn.execute(
            "SELECT alias, path, quant, display_name, sampling_json, "
            "provenance, validation_status FROM model_installations ORDER BY alias"
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
            }
            model.update(json.loads(r["sampling_json"] or "{}"))
            models.append(model)
        return models

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

        The insert and the oldest-first trim share one implicit transaction,
        closed by this method's commit when ``commit`` is true (the default
        for standalone/narrow callers; the facade passes commit=False inside
        its own whole-state unit of work).
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