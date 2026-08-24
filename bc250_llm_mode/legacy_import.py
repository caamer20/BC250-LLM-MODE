"""One-time legacy JSON -> SQLite import (ADR 001).

Canonicalizes any supported JSON schema through the in-process migration,
maps fields per the ADR class table, builds a staging database, verifies
integrity + foreign keys, then publishes atomically. Failure at any phase
before publication leaves no ``state.db`` and the JSON untouched.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .db import SCHEMA_VERSION, initialize, integrity_ok, open_database
from .fsops import atomic_write_text, publish_staged
from .paths import AppPaths

# Configuration keys imported into `settings` (ADR field-mapping table).
SETTING_KEYS = frozenset({
    "disclaimer_ack", "ack_timestamp", "setup_complete", "boot_policy",
    "desktop_on_reboot", "llm_autostart", "llm_mode_done", "system_mode",
    "https_sharing_enabled", "https_webui_port", "https_api_port",
    "https_webui_url", "https_api_base_url", "server_port",
    "container_name", "service_name", "llama_cpp_path", "venv_path",
    "model_search_paths", "optimizations", "optimization_service_previous",
    "optimization_original_swappiness", "selected_local_model", "setup_phase",
})
# Observations: imported as stale evidence, reconciled by the next probe.
STALE_OBSERVATION_KEYS = frozenset({
    "env_ready", "openwebui_container", "openwebui_installed", "server_log",
    "llm_session_boot_id", "desktop_reboot_pending", "reboot_required",
    "pending_karg_mode", "download_dir",
})
# Never persisted: recomputed from AppPaths at composition.
DERIVED_KEYS = frozenset({"app_dir", "logs_dir", "state_path", "custom_paths"})
SECRET_NAME_MARKERS = ("token", "secret", "password")


class LegacyImportError(RuntimeError):
    """Raised when the legacy import cannot be completed safely."""


def is_secret_like(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SECRET_NAME_MARKERS)


def utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class LegacyImporter:
    def __init__(self, paths: AppPaths, runner: Any = None) -> None:
        self.paths = paths
        self.runner = runner
        self.counts: dict[str, int] = {}
        self.warnings: list[str] = []

    def emit(self, message: str) -> None:
        if self.runner:
            self.runner.emit(message)

    def canonicalize_raw(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Run raw JSON through the pure v1→v5 canonicalizer (no file I/O)."""
        from .legacy_schema import canonicalize_legacy_state

        return canonicalize_legacy_state(raw)

    def import_legacy(self, source: Path | None = None) -> dict[str, Any]:
        lock_path = self.paths.migration_lock_path
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Staging must exist before any connection is opened against it.
        self.paths.staging_dir.mkdir(parents=True, exist_ok=True)
        try:
            lock_handle = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise LegacyImportError(
                f"Another migration holds the import lock ({lock_path}). "
                "If no migration is running, remove the lock file."
            ) from exc
        os.close(lock_handle)

        staging_db = self.paths.staging_dir / "state.db"
        published = False
        try:
            if self.paths.database_path.exists():
                raise LegacyImportError(
                    "A published database already exists; re-import refused."
                )
            source = source or self.paths.legacy_state_path
            raw = self._read_source(source)
            migrated = self.canonicalize_raw(raw)

            if staging_db.exists():
                staging_db.unlink()
            conn = open_database(staging_db, mode="migration", journal="delete")
            # The staging file must be self-contained for atomic publication:
            # built in rollback-journal mode so all rows live in the main
            # file (no WAL sidecar to lose during os.replace). The published
            # database returns to WAL on its next production connect.
            try:
                initialize(conn)
                self._import_mapped(conn, migrated)
                # Persist imported rows: without an explicit commit the
                # connection rolls the DML transaction back on close.
                conn.commit()
                if not integrity_ok(conn):
                    raise LegacyImportError(
                        "Staged database failed integrity/foreign-key checks."
                    )
            finally:
                conn.close()

            publish_staged(staging_db, self.paths.database_path)
            published = True
            receipt = self._write_receipt(source)
            self.emit(
                f"Legacy state imported: {self.counts} (receipt {receipt.name}); "
                "JSON backup retained read-only."
            )
            return {
                "published": True,
                "schema_version": SCHEMA_VERSION,
                "counts": dict(self.counts),
                "warnings": list(self.warnings),
                "receipt": str(receipt),
            }
        except Exception:
            if not published and staging_db.exists():
                try:
                    staging_db.unlink()
                except OSError:
                    pass
            raise
        finally:
            lock_path.unlink(missing_ok=True)

    def _read_source(self, source: Path) -> dict[str, Any]:
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except OSError as exc:
            raise LegacyImportError(f"Cannot read legacy state: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LegacyImportError(
                f"Legacy state file is corrupt ({exc}); it has been left untouched."
            ) from exc
        if not isinstance(raw, dict):
            raise LegacyImportError("Legacy state must be a JSON object.")
        return raw

    def _write_receipt(self, source: Path) -> Path:
        import hashlib

        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        receipt = (
            self.paths.migration_receipts_dir
            / f"legacy-import-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
        )
        atomic_write_text(receipt, json.dumps({
            "type": "legacy-json-import",
            "source": str(source),
            "source_sha256": digest,
            "target_schema_version": SCHEMA_VERSION,
            "counts": dict(self.counts),
            "warnings": list(self.warnings),
            "created": utcnow(),
        }, indent=2))
        return receipt

    def _import_mapped(self, conn, migrated: dict[str, Any]) -> None:
        now = utcnow()
        counts = {"settings": 0, "observations": 0, "extras": 0, "models": 0}

        settings = {key: migrated[key] for key in sorted(SETTING_KEYS) if key in migrated}
        # A customized models_dir is an explicit setting; the untouched
        # default is derived and never stored.
        models_dir = migrated.get("models_dir")
        if models_dir and not str(models_dir).rstrip("/").endswith(
            "/.bc250-llm-mode/models"
        ):
            settings["models_dir_custom"] = str(models_dir)
        for key, value in sorted(settings.items()):
            if is_secret_like(key):
                self.warnings.append(f"refused secret-like setting: {key}")
                continue
            conn.execute(
                "INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), now),
            )
            counts["settings"] += 1

        conn.execute(
            "INSERT INTO runtime_config(id, model_alias, context, slots, updated_at) "
            "VALUES (1, ?, ?, ?, ?)",
            (
                migrated.get("current_model"),
                migrated.get("current_ctx") or 8192,
                int((migrated.get("optimizations") or {}).get("parallel_slots", 1)),
                now,
            ),
        )

        for model in migrated.get("installed_models") or []:
            alias = str(model.get("id") or Path(str(model.get("path", ""))).stem)
            sampling = {
                k: model[k] for k in ("temperature", "top_p", "top_k", "min_p")
                if k in model
            }
            conn.execute(
                "INSERT INTO model_installations(alias, path, quant, display_name, "
                "sampling_json, provenance, validation_status, imported_at) "
                "VALUES (?, ?, ?, ?, ?, 'legacy-import', 'unverified', ?)",
                (
                    alias, str(model.get("path", "")), str(model.get("quant", "")),
                    str(model.get("display_name") or alias),
                    json.dumps(sampling), now,
                ),
            )
            counts["models"] += 1

        for entry in migrated.get("bench_history") or []:
            conn.execute(
                "INSERT INTO bench_history(ts, payload_json) VALUES (?, ?)",
                (str(entry.get("timestamp") or now), json.dumps(entry)),
            )
        for entry in migrated.get("autotune_history") or []:
            conn.execute(
                "INSERT INTO autotune_history(payload_json) VALUES (?)",
                (json.dumps(entry),),
            )

        # Safety-authoritative: the latch survives migration.
        latch = str(migrated.get("thermal_watchdog_state") or "nominal")
        baseline = migrated.get("thermal_watchdog_baseline")
        conn.execute(
            "INSERT INTO thermal_state(id, latch_state, baseline_json, updated_at) "
            "VALUES (1, ?, ?, ?)",
            (latch, json.dumps(baseline) if baseline else None, now),
        )
        if latch == "stopped":
            self.warnings.append(
                "Thermal stop was latched before migration; it remains latched. "
                "Use 'thermals reset' after the hardware has cooled."
            )

        build = migrated.get("llamacpp_build")
        if isinstance(build, dict):
            conn.execute(
                "INSERT OR REPLACE INTO component_provenance"
                "(component, describe, commit_sha, recorded_at) VALUES (?, ?, ?, ?)",
                ("llamacpp", build.get("describe"), build.get("commit"), now),
            )

        observations = {
            key: migrated[key] for key in sorted(STALE_OBSERVATION_KEYS) if key in migrated
        }
        for key, value in sorted(observations.items()):
            conn.execute(
                "INSERT OR REPLACE INTO runtime_observations"
                "(key, payload_json, observed_at, stale) VALUES (?, ?, ?, 1)",
                (key, json.dumps(value), now),
            )
            counts["observations"] += 1

        mapped = (
            set(SETTING_KEYS) | STALE_OBSERVATION_KEYS | DERIVED_KEYS | observations.keys() | {
                "installed_models", "bench_history", "autotune_history",
                "thermal_watchdog_state", "thermal_watchdog_baseline",
                "llamacpp_build", "llamacpp_history", "current_ctx",
                "current_model", "selected_model", "selected_quant",
                "selected_source", "schema_version", "revision",
            }
        )
        for key, value in sorted(migrated.items()):
            if key in mapped:
                continue
            if is_secret_like(key):
                self.warnings.append(f"refused secret-like extra: {key}")
                continue
            conn.execute(
                "INSERT OR REPLACE INTO legacy_import_extras(key, payload_json) "
                "VALUES (?, ?)",
                (key, json.dumps(value)),
            )
            counts["extras"] += 1

        self.counts = counts


def import_legacy_state(
    paths: AppPaths,
    runner: Any = None,
    *,
    source: Path | None = None,
) -> dict[str, Any]:
    """Module-level convenience entry point."""
    return LegacyImporter(paths, runner).import_legacy(source)
