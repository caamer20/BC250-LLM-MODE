"""Production filesystem adapter for durable ``STORAGE_CLEANUP v1``.

Only app-owned model staging and app-owned cleanup quarantine are reachable.
Every identity is streamed in bounded chunks; symlinks, special files, mount
crossings, unknown receipts, and external model paths fail closed.
"""

from __future__ import annotations

import datetime as _datetime
import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

from .artifact_storage import read_receipt, write_receipt
from .legacy_import import utcnow
from .operations.model import OperationState
from .operations.recovery import RecoveryClass
from .operations.repositories import OperationRepository
from .operations.storage_cleanup import StorageCleanupHost
from .operations.validation import OperationValidationError
from .operations.workflow import EffectContext, ProbeResult
from .paths import AppPaths
from .unit_of_work import UnitOfWorkFactory

IDENTITY_CHUNK_BYTES = 1024 * 1024
MAX_TREE_FILES = 4096
MAX_TREE_ENTRIES = 8192
MAX_DISCOVERY_ENTRIES = 128
RETENTION_DAYS = 7
RECEIPT_VERSION = 1
FREE_SPACE_TOLERANCE_BYTES = 64 * 1024 * 1024


class CleanupRefusal(OperationValidationError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CleanupRecoveryRequired(CleanupRefusal):
    requires_recovery = True


def _parse_time(value: str) -> _datetime.datetime:
    try:
        parsed = _datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise CleanupRefusal("CLEANUP_TIMESTAMP_INVALID") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_datetime.timezone.utc)
    return parsed.astimezone(_datetime.timezone.utc)


def _format_time(value: _datetime.datetime) -> str:
    return value.astimezone(_datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tree_identity(root: Path) -> dict[str, Any]:
    """Stream a real directory tree into one stable identity."""
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise CleanupRefusal("CLEANUP_TARGET_MISSING") from exc
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise CleanupRefusal("CLEANUP_TARGET_NOT_DIRECTORY")
    device = root_stat.st_dev
    digest = hashlib.sha256()
    total_bytes = 0
    file_count = 0
    entry_count = 0
    stack: list[tuple[Path, str]] = [(root, "")]
    while stack:
        directory, relative = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise CleanupRefusal("CLEANUP_TARGET_UNREADABLE") from exc
        digest.update(b"D\0" + relative.encode("utf-8") + b"\0")
        directories: list[tuple[Path, str]] = []
        for entry in entries:
            entry_count += 1
            if entry_count > MAX_TREE_ENTRIES:
                raise CleanupRefusal("CLEANUP_TREE_TOO_LARGE")
            rel = f"{relative}/{entry.name}" if relative else entry.name
            if len(rel) > 1024 or "\n" in rel or "\x00" in rel:
                raise CleanupRefusal("CLEANUP_PATH_INVALID")
            try:
                observed = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise CleanupRefusal("CLEANUP_TARGET_UNREADABLE") from exc
            if observed.st_dev != device:
                raise CleanupRefusal("CLEANUP_MOUNT_CROSSING")
            if stat.S_ISLNK(observed.st_mode):
                raise CleanupRefusal("CLEANUP_SYMLINK_REFUSED")
            if stat.S_ISDIR(observed.st_mode):
                directories.append((Path(entry.path), rel))
                continue
            if not stat.S_ISREG(observed.st_mode):
                raise CleanupRefusal("CLEANUP_SPECIAL_FILE_REFUSED")
            file_count += 1
            if file_count > MAX_TREE_FILES:
                raise CleanupRefusal("CLEANUP_TREE_TOO_LARGE")
            digest.update(
                b"F\0" + rel.encode("utf-8") + b"\0"
                + str(observed.st_size).encode("ascii") + b"\0"
            )
            try:
                with open(entry.path, "rb") as handle:
                    while True:
                        chunk = handle.read(IDENTITY_CHUNK_BYTES)
                        if not chunk:
                            break
                        digest.update(chunk)
                        total_bytes += len(chunk)
            except OSError as exc:
                raise CleanupRefusal("CLEANUP_TARGET_UNREADABLE") from exc
        stack.extend(reversed(directories))
    return {
        "tree_digest": digest.hexdigest(),
        "bytes": total_bytes,
        "files": file_count,
        "device": int(device),
    }


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rename_no_replace(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise CleanupRefusal("CLEANUP_DESTINATION_COLLISION")
    if source.stat().st_dev != destination.parent.stat().st_dev:
        raise CleanupRefusal("CLEANUP_CROSS_DEVICE")
    if sys.platform.startswith("linux"):
        import ctypes
        import ctypes.util

        library = ctypes.CDLL(
            ctypes.util.find_library("c") or "libc.so.6", use_errno=True
        )
        if not hasattr(library, "renameat2"):
            raise CleanupRefusal("CLEANUP_NOREPLACE_UNSUPPORTED")
        library.renameat2.argtypes = [
            ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
            ctypes.c_uint,
        ]
        result = library.renameat2(
            -100, os.fsencode(source), -100, os.fsencode(destination), 1,
        )
        if result != 0:
            error = ctypes.get_errno()
            if error in (17,):
                raise CleanupRefusal("CLEANUP_DESTINATION_COLLISION")
            if error in (1, 22, 38, 95):
                raise CleanupRefusal("CLEANUP_NOREPLACE_UNSUPPORTED")
            raise OSError(error, os.strerror(error))
    else:
        # Developer-test fallback. Production hosts are Linux and use the
        # atomic RENAME_NOREPLACE primitive above.
        os.rename(source, destination)
    _fsync_dir(source.parent)
    _fsync_dir(destination.parent)


class StorageCleanupHostAdapter(StorageCleanupHost):
    def __init__(
        self, units: UnitOfWorkFactory, paths: AppPaths, *, clock=utcnow
    ) -> None:
        self._units = units
        self._paths = paths
        self._clock = clock

    @property
    def cleanup_root(self) -> Path:
        return self._paths.model_quarantine_dir / "cleanup"

    # -- discovery / command-service input --------------------------------

    def discover(self) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        staging = self._paths.model_staging_dir
        for entry in self._bounded_children(staging):
            if len(candidates) >= MAX_DISCOVERY_ENTRIES:
                break
            with self._units.read() as conn:
                operation = OperationRepository(conn).get(entry.name)
            if operation is None or operation.state not in {
                OperationState.SUCCEEDED, OperationState.CANCELLED,
                OperationState.FAILED_SAFE, OperationState.FAILED_ROLLED_BACK,
            }:
                continue
            try:
                identity = _tree_identity(entry)
            except CleanupRefusal:
                continue
            target_id = self._target_id("ABANDONED_STAGING", entry.name,
                                        identity["tree_digest"])
            candidates.append({
                "target_id": target_id,
                "kind": "ABANDONED_STAGING",
                "relative_name": entry.name,
                "expected_tree_digest": identity["tree_digest"],
                "expected_bytes": identity["bytes"],
                "expected_files": identity["files"],
                "owner_operation_id": operation.id,
                "quarantine_operation_id": None,
                "retention_until": _format_time(
                    _parse_time(self._clock())
                    + _datetime.timedelta(days=RETENTION_DAYS)
                ),
                "eligible_modes": ["QUARANTINE"],
                "default_selected": True,
                "reason_code": "TERMINAL_OPERATION_STAGING",
            })
        for operation_dir in self._bounded_children(self.cleanup_root):
            for receipt_path in self._bounded_receipts(operation_dir):
                if len(candidates) >= MAX_DISCOVERY_ENTRIES:
                    break
                receipt = read_receipt(receipt_path) or {}
                if (receipt.get("receipt_version") != RECEIPT_VERSION
                        or receipt.get("state") != "QUARANTINED"):
                    continue
                target_id = str(receipt.get("target_id") or "")
                target_path = operation_dir / target_id
                try:
                    identity = _tree_identity(target_path)
                except CleanupRefusal:
                    continue
                if identity["tree_digest"] != receipt.get("expected_tree_digest"):
                    continue
                retention = str(receipt.get("retention_until") or "")
                expired = False
                try:
                    expired = _parse_time(retention) <= _parse_time(self._clock())
                except CleanupRefusal:
                    continue
                candidates.append({
                    "target_id": target_id,
                    "kind": "CLEANUP_QUARANTINE",
                    "relative_name": str(receipt.get("relative_name") or ""),
                    "expected_tree_digest": identity["tree_digest"],
                    "expected_bytes": identity["bytes"],
                    "expected_files": identity["files"],
                    "owner_operation_id": str(
                        receipt.get("owner_operation_id") or ""),
                    "quarantine_operation_id": operation_dir.name,
                    "retention_until": retention,
                    "eligible_modes": ["PURGE"] if expired else ["RESTORE"],
                    "default_selected": False,
                    "reason_code": (
                        "RETENTION_EXPIRED" if expired else "UNDO_RETAINED"
                    ),
                })
        return sorted(candidates, key=lambda item: (
            item["kind"], item["target_id"]
        ))[:MAX_DISCOVERY_ENTRIES]

    def _bounded_children(self, root: Path) -> list[Path]:
        try:
            with os.scandir(root) as stream:
                entries = []
                for item in stream:
                    if len(entries) >= MAX_DISCOVERY_ENTRIES:
                        break
                    if item.is_dir(follow_symlinks=False):
                        entries.append(Path(item.path))
        except OSError:
            return []
        return sorted(entries, key=lambda item: item.name)

    @staticmethod
    def _bounded_receipts(root: Path) -> list[Path]:
        try:
            with os.scandir(root) as stream:
                entries = [
                    Path(item.path) for item in stream
                    if item.is_file(follow_symlinks=False)
                    and item.name.endswith(".cleanup.json")
                ][:MAX_DISCOVERY_ENTRIES]
        except OSError:
            return []
        return sorted(entries, key=lambda item: item.name)

    @staticmethod
    def _target_id(kind: str, name: str, digest: str) -> str:
        raw = json.dumps([kind, name, digest], separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()[:32]

    # -- durable host port -------------------------------------------------

    def resolve(self, ctx: EffectContext) -> dict[str, Any]:
        for target in ctx.request.targets:
            self._validate_target(target, ctx.request.mode)
        free = shutil.disk_usage(self._paths.models_dir).free
        return {
            "mode": ctx.request.mode,
            "target_count": len(ctx.request.targets),
            "bytes_selected": sum(item.expected_bytes for item in ctx.request.targets),
            "free_before": int(free),
        }

    def probe_resolved(self, ctx: EffectContext) -> ProbeResult:
        try:
            output = self.resolve(ctx)
        except CleanupRefusal as exc:
            return ProbeResult(RecoveryClass.ABSENT, exc.code)
        return ProbeResult(RecoveryClass.COMPLETE, "CLEANUP_TARGETS_RESOLVED",
                           {"resolution": output})

    def apply_mode(self, ctx: EffectContext, mode: str) -> dict[str, Any]:
        if ctx.request.mode != mode:
            return {"mode": mode, "completed_count": 0, "retained_count": 0,
                    "not_applicable": True}
        completed = 0
        retained = 0
        for index, target in enumerate(ctx.request.targets, start=1):
            ctx.pulse(
                phase=mode.lower(), current=index - 1,
                total=len(ctx.request.targets), unit="targets",
                cancellation_safe=False,
            )
            if mode == "QUARANTINE":
                self._quarantine_one(ctx.operation_id, target)
                completed += 1
            elif mode == "RESTORE":
                self._restore_one(target)
                completed += 1
            else:
                if self._purge_one(target):
                    completed += 1
                else:
                    retained += 1
        free_after = shutil.disk_usage(self._paths.models_dir).free
        return {
            "mode": mode,
            "completed_count": completed,
            "retained_count": retained,
            "bytes_selected": sum(item.expected_bytes for item in ctx.request.targets),
            "free_after": int(free_after),
        }

    def probe_mode(self, ctx: EffectContext, mode: str) -> ProbeResult:
        if ctx.request.mode != mode:
            return ProbeResult(
                RecoveryClass.COMPLETE, f"{mode}_NOT_APPLICABLE",
                {"effect": {"mode": mode, "completed_count": 0,
                            "retained_count": 0, "not_applicable": True}},
            )
        states: list[str] = []
        for target in ctx.request.targets:
            states.append(self._target_effect_state(
                target, mode, operation_id=ctx.operation_id))
        if "UNCERTAIN" in states:
            return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL,
                               f"{mode}_IDENTITY_UNCERTAIN")
        completed = states.count("COMPLETE")
        retained = states.count("RETAINED")
        free_after = int(shutil.disk_usage(self._paths.models_dir).free)
        output = {"effect": {
            "mode": mode,
            "completed_count": completed,
            "retained_count": retained,
            "bytes_selected": sum(item.expected_bytes for item in ctx.request.targets),
            "free_after": free_after,
        }}
        if completed + retained == len(states):
            if mode == "PURGE":
                before = int(
                    ((ctx.prior_outputs.get("resolve_targets") or {})
                     .get("resolution") or {}).get("free_before") or 0
                )
                if before and free_after + FREE_SPACE_TOLERANCE_BYTES < before:
                    return ProbeResult(
                        RecoveryClass.UNCERTAIN_MANUAL,
                        "PURGE_FREE_SPACE_NOT_CONFIRMED",
                    )
            return ProbeResult(RecoveryClass.COMPLETE, f"{mode}_COMPLETE", output)
        if completed:
            return ProbeResult(RecoveryClass.PARTIALLY_RESUMABLE,
                               f"{mode}_PARTIAL", output)
        return ProbeResult(RecoveryClass.ABSENT, f"{mode}_ABSENT")

    def compensate_mode(self, ctx: EffectContext, mode: str) -> dict[str, Any]:
        if ctx.request.mode != mode:
            return {"mode": mode, "restored_count": 0, "not_applicable": True}
        restored = 0
        for target in reversed(ctx.request.targets):
            if mode == "QUARANTINE":
                source = self._staging_path(target)
                quarantine = self._quarantine_path(ctx.operation_id, target)
                if quarantine.exists() and not source.exists():
                    _rename_no_replace(quarantine, source)
                    receipt = self._receipt(ctx.operation_id, target)
                    self._write_state(receipt, target, ctx.operation_id,
                                      "RESTORED_BY_COMPENSATION")
                    restored += 1
            elif mode == "RESTORE":
                source = self._staging_path(target)
                quarantine = self._quarantine_path(
                    str(target.quarantine_operation_id), target)
                if source.exists() and not quarantine.exists():
                    _rename_no_replace(source, quarantine)
                    receipt = self._receipt(
                        str(target.quarantine_operation_id), target)
                    self._write_state(
                        receipt, target, str(target.quarantine_operation_id),
                        "QUARANTINED")
                    restored += 1
        return {"mode": mode, "restored_count": restored}

    def probe_compensation(self, ctx: EffectContext, mode: str) -> ProbeResult:
        if ctx.request.mode != mode:
            return ProbeResult(RecoveryClass.COMPLETE,
                               f"{mode}_RESTORATION_NOT_APPLICABLE")
        if mode == "PURGE":
            return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL,
                               "PURGE_HAS_NO_INVERSE")
        states = []
        for target in ctx.request.targets:
            source = self._staging_path(target)
            qop = ctx.operation_id if mode == "QUARANTINE" \
                else str(target.quarantine_operation_id)
            quarantine = self._quarantine_path(qop, target)
            expected_source = mode == "QUARANTINE"
            complete = source.exists() == expected_source \
                and quarantine.exists() != expected_source
            identity_path = source if expected_source else quarantine
            if complete:
                try:
                    complete = _tree_identity(identity_path)["tree_digest"] \
                        == target.expected_tree_digest
                except CleanupRefusal:
                    complete = False
            states.append(complete)
        if all(states):
            return ProbeResult(RecoveryClass.COMPLETE,
                               f"{mode}_RESTORATION_COMPLETE")
        if any(states):
            return ProbeResult(RecoveryClass.PARTIALLY_RESUMABLE,
                               f"{mode}_RESTORATION_PARTIAL")
        return ProbeResult(RecoveryClass.ABSENT, f"{mode}_RESTORATION_ABSENT")

    # -- exact target operations -----------------------------------------

    def _validate_target(self, target: Any, mode: str) -> None:
        if mode == "QUARANTINE":
            with self._units.read() as conn:
                operation = OperationRepository(conn).get(target.owner_operation_id)
            if operation is None or operation.state not in {
                OperationState.SUCCEEDED, OperationState.CANCELLED,
                OperationState.FAILED_SAFE, OperationState.FAILED_ROLLED_BACK,
            }:
                raise CleanupRefusal("CLEANUP_OPERATION_NOT_ABANDONED")
            identity = _tree_identity(self._staging_path(target))
        else:
            receipt = read_receipt(self._receipt(
                str(target.quarantine_operation_id), target)) or {}
            if (receipt.get("receipt_version") != RECEIPT_VERSION
                    or receipt.get("target_id") != target.target_id
                    or receipt.get("expected_tree_digest")
                    != target.expected_tree_digest
                    or receipt.get("state") != "QUARANTINED"):
                raise CleanupRefusal("CLEANUP_RECEIPT_INVALID")
            expired = _parse_time(str(receipt.get("retention_until"))) \
                <= _parse_time(self._clock())
            if mode == "RESTORE" and expired:
                raise CleanupRefusal("CLEANUP_UNDO_EXPIRED")
            if mode == "PURGE" and not expired:
                raise CleanupRefusal("CLEANUP_RETENTION_ACTIVE")
            if mode == "RESTORE" and self._staging_path(target).exists():
                raise CleanupRefusal("CLEANUP_RESTORE_COLLISION")
            identity = _tree_identity(self._quarantine_path(
                str(target.quarantine_operation_id), target))
        if (
            identity["tree_digest"] != target.expected_tree_digest
            or identity["bytes"] != target.expected_bytes
            or identity["files"] != target.expected_files
        ):
            raise CleanupRefusal("CLEANUP_IDENTITY_MISMATCH")

    def _quarantine_one(self, operation_id: str, target: Any) -> None:
        source = self._staging_path(target)
        destination = self._quarantine_path(operation_id, target)
        receipt = self._receipt(operation_id, target)
        if destination.exists() and not source.exists():
            identity = _tree_identity(destination)
            if identity["tree_digest"] != target.expected_tree_digest:
                raise CleanupRefusal("CLEANUP_IDENTITY_MISMATCH")
            self._write_state(receipt, target, operation_id, "QUARANTINED")
            return
        self._validate_target(target, "QUARANTINE")
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(destination.parent, 0o700)
        _rename_no_replace(source, destination)
        self._write_state(receipt, target, operation_id, "QUARANTINED")

    def _restore_one(self, target: Any) -> None:
        qop = str(target.quarantine_operation_id)
        source = self._quarantine_path(qop, target)
        destination = self._staging_path(target)
        receipt = self._receipt(qop, target)
        if destination.exists() and not source.exists():
            identity = _tree_identity(destination)
            if identity["tree_digest"] != target.expected_tree_digest:
                raise CleanupRefusal("CLEANUP_IDENTITY_MISMATCH")
            self._write_state(receipt, target, qop, "RESTORED")
            return
        self._validate_target(target, "RESTORE")
        destination.parent.mkdir(parents=True, exist_ok=True)
        _rename_no_replace(source, destination)
        self._write_state(receipt, target, qop, "RESTORED")

    def _purge_one(self, target: Any) -> bool:
        qop = str(target.quarantine_operation_id)
        source = self._quarantine_path(qop, target)
        receipt = self._receipt(qop, target)
        if not source.exists():
            self._write_state(receipt, target, qop, "PURGED")
            return True
        try:
            self._validate_target(target, "PURGE")
            if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
                raise CleanupRefusal("CLEANUP_SAFE_DELETE_UNAVAILABLE")
            try:
                shutil.rmtree(source)
            except OSError as exc:
                raise CleanupRecoveryRequired(
                    "CLEANUP_PURGE_PARTIAL_UNCERTAIN") from exc
            _fsync_dir(source.parent)
            try:
                self._write_state(receipt, target, qop, "PURGED")
            except OSError as exc:
                raise CleanupRecoveryRequired(
                    "CLEANUP_PURGE_RECEIPT_UNCERTAIN") from exc
            return True
        except CleanupRecoveryRequired:
            raise
        except CleanupRefusal:
            try:
                self._write_state(receipt, target, qop, "PURGE_RETAINED")
            except OSError:
                pass
            return False

    def _target_effect_state(
        self, target: Any, mode: str, *, operation_id: str
    ) -> str:
        source = self._staging_path(target)
        qop = str(target.quarantine_operation_id or "")
        if mode == "QUARANTINE":
            qop = operation_id
        quarantine = self._quarantine_path(qop, target)
        if mode == "QUARANTINE":
            if quarantine.exists() and not source.exists():
                try:
                    identity = _tree_identity(quarantine)
                except CleanupRefusal:
                    return "UNCERTAIN"
                receipt = read_receipt(self._receipt(qop, target)) or {}
                if identity["tree_digest"] != target.expected_tree_digest:
                    return "UNCERTAIN"
                return "COMPLETE" if receipt.get("state") == "QUARANTINED" \
                    else "PENDING"
            if source.exists() and not quarantine.exists():
                try:
                    self._validate_target(target, "QUARANTINE")
                except CleanupRefusal:
                    return "UNCERTAIN"
                return "PENDING"
            return "UNCERTAIN"
        if mode == "RESTORE":
            if source.exists() and not quarantine.exists():
                try:
                    identity = _tree_identity(source)
                except CleanupRefusal:
                    return "UNCERTAIN"
                receipt = read_receipt(self._receipt(qop, target)) or {}
                return "COMPLETE" if (
                    identity["tree_digest"] == target.expected_tree_digest
                    and receipt.get("state") == "RESTORED"
                ) else "PENDING"
            if quarantine.exists() and not source.exists():
                try:
                    self._validate_target(target, "RESTORE")
                except CleanupRefusal:
                    return "UNCERTAIN"
                return "PENDING"
            return "UNCERTAIN"
        if not quarantine.exists():
            receipt = read_receipt(self._receipt(qop, target)) or {}
            return "COMPLETE" if receipt.get("state") == "PURGED" else "PENDING"
        receipt = read_receipt(self._receipt(qop, target)) or {}
        if receipt.get("state") == "PURGE_RETAINED":
            try:
                identity = _tree_identity(quarantine)
            except CleanupRefusal:
                return "UNCERTAIN"
            return "RETAINED" if identity["tree_digest"] \
                == target.expected_tree_digest else "UNCERTAIN"
        try:
            self._validate_target(target, "PURGE")
        except CleanupRefusal:
            return "UNCERTAIN"
        return "PENDING"

    def _staging_path(self, target: Any) -> Path:
        path = self._paths.model_staging_dir / target.relative_name
        if path.parent != self._paths.model_staging_dir:
            raise CleanupRefusal("CLEANUP_CONTAINMENT_REFUSED")
        return path

    def _quarantine_path(self, operation_id: str, target: Any) -> Path:
        path = self.cleanup_root / operation_id / target.target_id
        if path.parent.parent != self.cleanup_root:
            raise CleanupRefusal("CLEANUP_CONTAINMENT_REFUSED")
        return path

    def _receipt(self, operation_id: str, target: Any) -> Path:
        return self.cleanup_root / operation_id / f"{target.target_id}.cleanup.json"

    def _write_state(
        self, path: Path, target: Any, quarantine_operation_id: str, state: str
    ) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        existing = read_receipt(path) or {}
        created = existing.get("quarantined_at") or self._clock()
        write_receipt(path, {
            "receipt_version": RECEIPT_VERSION,
            "target_id": target.target_id,
            "relative_name": target.relative_name,
            "expected_tree_digest": target.expected_tree_digest,
            "expected_bytes": target.expected_bytes,
            "expected_files": target.expected_files,
            "owner_operation_id": target.owner_operation_id,
            "quarantine_operation_id": quarantine_operation_id,
            "retention_until": target.retention_until,
            "quarantined_at": created,
            "updated_at": self._clock(),
            "state": state,
        })


__all__ = [
    "CleanupRecoveryRequired", "CleanupRefusal", "MAX_DISCOVERY_ENTRIES", "RETENTION_DAYS",
    "StorageCleanupHostAdapter", "_tree_identity",
]
