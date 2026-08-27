"""P6 §12.3: capacity and deduplication reporting (query-only).

``StorageCapacityService`` surfaces logical vs physical size, deduplication
savings, reserved/staging/quarantine bytes, and free space, and produces
cleanup suggestions RANKED by safety and recoverability. It NEVER deletes
anything: cleanup is a dry-run report with exact identities and reasons.
Cached and quarantined files are never silently removed.
"""

from __future__ import annotations

import os
import shutil
from typing import Any

from .paths import AppPaths
from .unit_of_work import UnitOfWorkFactory

STORAGE_CAPACITY_SCHEMA_VERSION = 1

# Default low-space warning threshold (10 GiB) — configurable per profile.
DEFAULT_LOW_SPACE_BYTES = 10 * 1024 * 1024 * 1024


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


class StorageCapacityService:
    """Query-only capacity/dedup report + ranked cleanup suggestions."""

    def __init__(
        self,
        units: UnitOfWorkFactory,
        paths: AppPaths,
        *,
        low_space_bytes: int = DEFAULT_LOW_SPACE_BYTES,
    ) -> None:
        self._units = units
        self._paths = paths
        self._low_space_bytes = int(low_space_bytes)

    # --- report ------------------------------------------------------------

    def report(self) -> dict[str, Any]:
        with self._units.read() as conn:
            unique = conn.execute(
                "SELECT COALESCE(SUM(byte_size), 0) AS n FROM model_artifacts"
            ).fetchone()
            logical_unique = int(unique["n"]) if unique else 0
            joined = conn.execute(
                "SELECT COALESCE(SUM(COALESCE(a.byte_size, 0)), 0) AS n "
                "FROM model_installations i "
                "LEFT JOIN model_artifacts a ON a.id = i.artifact_id"
            ).fetchone()
            logical_installed = int(joined["n"]) if joined else 0
        physical = _dir_stats(self._paths.models_dir)
        staging = _dir_stats(self._paths.model_staging_dir)
        quarantine = _dir_stats(self._paths.model_quarantine_dir)
        try:
            usage = shutil.disk_usage(str(self._paths.models_dir))
            free = usage.free
            total = usage.total
        except OSError:
            free = 0
            total = 0
        dedup_savings = max(0, logical_installed - logical_unique)
        low_space = free < self._low_space_bytes
        return {
            "schema_version": STORAGE_CAPACITY_SCHEMA_VERSION,
            "logical_unique_bytes": logical_unique,
            "logical_installed_bytes": logical_installed,
            "dedup_savings_bytes": dedup_savings,
            "physical_bytes": physical["bytes"],
            "physical_files": physical["files"],
            "staging_bytes": staging["bytes"],
            "staging_files": staging["files"],
            "quarantine_bytes": quarantine["bytes"],
            "quarantine_files": quarantine["files"],
            "reserved_bytes": 0,
            "free_bytes": free,
            "total_bytes": total,
            "low_space_threshold_bytes": self._low_space_bytes,
            "low_space_warning": low_space,
        }

    # --- cleanup suggestions (ranked, never deletes) -----------------------

    def cleanup_suggestions(self) -> list[dict[str, Any]]:
        """Ranked by safety and recoverability; report-only (P6 §12.3)."""
        suggestions: list[dict[str, Any]] = []

        # 1) Staging leftovers — safest: operation-owned, never installed.
        staging = self._paths.model_staging_dir
        if staging.exists():
            for entry in sorted(staging.iterdir(), key=lambda p: p.name):
                stats = _dir_stats(entry) if entry.is_dir() else {
                    "bytes": entry.stat().st_size, "files": 1}
                suggestions.append({
                    "rank": 1,
                    "kind": "staging-leftover",
                    "identity": entry.name,
                    "path": str(entry),
                    "bytes": stats["bytes"],
                    "recoverable": True,
                    "reason": "operation-owned staging; never installed",
                })

        # 2) Quarantine — recoverable while the retention window holds.
        quarantine = self._paths.model_quarantine_dir
        if quarantine.exists():
            for entry in sorted(quarantine.iterdir(), key=lambda p: p.name):
                stats = _dir_stats(entry) if entry.is_dir() else {
                    "bytes": entry.stat().st_size, "files": 1}
                suggestions.append({
                    "rank": 2,
                    "kind": "quarantine",
                    "identity": entry.name,
                    "path": str(entry),
                    "bytes": stats["bytes"],
                    "recoverable": True,
                    "reason": "quarantined; bounded undo during retention",
                })

        # 3) Unreferenced managed artifacts — no alias points at them.
        with self._units.read() as conn:
            rows = conn.execute(
                "SELECT a.id AS id, a.content_digest AS content_digest, "
                "a.byte_size AS byte_size, a.canonical_path AS canonical_path "
                "FROM model_artifacts a "
                "WHERE a.storage_state = 'MANAGED' "
                "AND NOT EXISTS (SELECT 1 FROM model_installations i "
                "                WHERE i.artifact_id = a.id)"
            ).fetchall()
        for row in rows:
            suggestions.append({
                "rank": 3,
                "kind": "unreferenced-artifact",
                "identity": row["content_digest"] or row["id"],
                "path": row["canonical_path"],
                "bytes": row["byte_size"] or 0,
                "recoverable": False,
                "reason": "no installed alias references this artifact",
            })

        suggestions.sort(key=lambda s: (s["rank"], -s["bytes"]))
        return suggestions

    def dry_run_cleanup(self) -> dict[str, Any]:
        """Exact identities + reasons; NEVER deletes (P6 §12.3)."""
        suggestions = self.cleanup_suggestions()
        report = self.report()
        return {
            "schema_version": STORAGE_CAPACITY_SCHEMA_VERSION,
            "deleted_bytes": 0,
            "deleted_anything": False,
            "reclaimable_bytes": sum(s["bytes"] for s in suggestions),
            "free_bytes": report["free_bytes"],
            "low_space_warning": report["low_space_warning"],
            "suggestions": suggestions,
            "note": "dry-run only; nothing was deleted",
        }


def open_storage_capacity_service(
    database_path, paths: AppPaths, **kwargs
) -> StorageCapacityService:
    return StorageCapacityService(
        UnitOfWorkFactory(database_path), paths, **kwargs
    )
