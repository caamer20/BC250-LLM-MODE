"""Local preservation and exact identity receipts for profile exchange."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from contextlib import closing
import stat
from pathlib import Path

from .fsops import atomic_write_text, fsync_directory
from .operations.validation import OperationValidationError
from .profile_access import receipt_path

# Historical configuration may be restored, but operational authority must
# never rewind. Keep the coherent current graph, including events and revocations.
CURRENT_TABLES = (
    "runtime_config", "model_installations", "thermal_state", "component_provenance",
    "known_good_runtime", "operations", "operation_steps", "operation_events",
    "operation_leases", "model_artifacts", "operation_storage_reservations",
    "runtime_builds", "runtime_build_verifications", "runtime_trees",
    "runtime_component_state", "worker_locks", "gateway_credentials",
    "model_library_meta", "backup_sets", "restore_attempts", "connection_clients",
    "connection_client_secrets", "connection_access_state", "workload_profiles",
    "notification_preferences", "notification_receipts", "application_installations",
    "application_installation_state", "application_update_imports",
)
EXCLUDED_FILES = {"state.db", "state.db-wal", "state.db-shm", "backup-manifest.json",
                  "gui.sock", "gui.lock", "gui.nonce", "runtime-policy.json", ".restore-staged.json"}
MAX_FILES = 250_000
MAX_BYTES = 256 * 1024**3


def directory_identity(path: Path) -> list[int]:
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode):
        raise OperationValidationError("RESTORE_IDENTITY: expected a directory")
    return [st.st_dev, st.st_ino]


def inventory_local(profile: Path):
    """Bounded walk, never following symlinks, without reading file contents."""
    total = 0
    count = 0
    entries = []
    for root, dirs, files in os.walk(profile, followlinks=False):
        for name in dirs + files:
            source = Path(root) / name
            relative = source.relative_to(profile)
            if len(relative.parts) == 1 and name in EXCLUDED_FILES:
                continue
            st = source.lstat()
            if not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):
                raise OperationValidationError("RESTORE_PRESERVATION: unsupported local special file")
            count += 1
            total += st.st_size if stat.S_ISREG(st.st_mode) else 0
            if count > MAX_FILES or total > MAX_BYTES:
                raise OperationValidationError("RESTORE_PRESERVATION: local inventory exceeds bounds")
            entries.append((relative, st))
    return entries, total


def preserve_local(profile: Path, candidate: Path, *, heartbeat=lambda: None):
    entries, total = inventory_local(profile)
    if shutil.disk_usage(candidate.parent).free < total + 64 * 1024**2:
        raise OperationValidationError("LOW_SPACE: insufficient space to preserve the current profile")
    for relative, before in entries:
        source, target = profile / relative, candidate / relative
        heartbeat()
        if stat.S_ISLNK(before.st_mode):
            # Preserve local venv/interpreter and slot links literally. Archive
            # links are forbidden; these are existing local installation assets.
            target.symlink_to(os.readlink(source))
        elif stat.S_ISDIR(before.st_mode):
            target.mkdir(exist_ok=True)
            target.chmod(stat.S_IMODE(before.st_mode))
        else:
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as src, target.open("xb") as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
                    heartbeat()
                dst.flush()
                os.fsync(dst.fileno())
                after = os.fstat(src.fileno())
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise OperationValidationError("RESTORE_PRESERVATION: local file changed during staging")
            target.chmod(stat.S_IMODE(before.st_mode))
    fsync_directory(candidate)
    return len(entries)


def preserve_current_database(current: Path, staged: Path):
    with closing(sqlite3.connect(staged)) as dst:
        dst.execute("ATTACH DATABASE ? AS current", (str(current),))
        dst.execute("PRAGMA foreign_keys=OFF")
        dst.execute("BEGIN IMMEDIATE")
        for table in CURRENT_TABLES:
            dst.execute(f'DELETE FROM "{table}"')
            dst.execute(f'INSERT INTO "{table}" SELECT * FROM current."{table}"')
        # Historical observations can never become fresh after restoration.
        dst.execute("DELETE FROM runtime_observations")
        # Retain current security/integration/runtime settings and revisions.
        dst.execute("INSERT OR REPLACE INTO settings SELECT * FROM current.settings WHERE "
                    "key LIKE '%gateway%' OR key LIKE '%sharing%' OR key LIKE '%webui%' "
                    "OR key LIKE '%revision%' OR key IN ('optimizations', 'server_port', 'service_name', 'container_name', 'llama_cpp_path', 'runtime_component_id', 'runtime_manifest_digest', 'system_mode', 'boot_policy', 'desktop_on_reboot', 'llm_autostart')")
        if dst.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise OperationValidationError("RESTORE_PRESERVATION: inconsistent current graph")
        dst.commit()
        dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def read_receipt(profile: Path, operation_id: str):
    path = receipt_path(profile)
    if not path.exists():
        return None
    with path.open("rb") as source:
        raw = source.read(8193)
    if len(raw) > 8192:
        raise OperationValidationError("RESTORE_IDENTITY: oversized receipt")
    value = json.loads(raw)
    if value.get("operation_id") != operation_id:
        if value.get("phase") in {"promoted", "rolled_back"}:
            return None
        raise OperationValidationError("RESTORE_IDENTITY: another restore needs recovery")
    return value


def write_receipt(profile: Path, receipt):
    atomic_write_text(receipt_path(profile), json.dumps(receipt, sort_keys=True), mode=0o600)
