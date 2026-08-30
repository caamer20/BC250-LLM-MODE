"""Digest-pinned immutable release promotion and pointer publication."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .application_pointer_helper import (
    POINTER_HELPER_DIGEST,
    POINTER_HELPER_SOURCE,
    PointerObservation,
    PointerRefusal,
    classify_pointer_state,
    observe_pointers,
    publish_pointer_set,
    verify_pointer_helper_digest,
)
from .fsops import atomic_write_text, fsync_directory
from .paths import AppPaths


POINTER_RECEIPT_SCHEMA_VERSION = 1
_DIGEST = re.compile(r"[0-9a-f]{64}")
_STAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _rename_no_replace(source: Path, target: Path) -> None:
    """Atomically publish a directory without replacing an existing slot.

    BC250 production hosts are Linux and use ``renameat2(RENAME_NOREPLACE)``.
    The guarded POSIX rename fallback exists only for non-Linux developer
    tests where the private root is single-process.
    """
    if sys.platform.startswith("linux"):
        import ctypes
        import ctypes.util
        import errno

        libc = ctypes.CDLL(
            ctypes.util.find_library("c") or "libc.so.6", use_errno=True
        )
        if not hasattr(libc, "renameat2"):
            raise PointerRefusal("ATOMIC_NOREPLACE_UNSUPPORTED")
        libc.renameat2.argtypes = [
            ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
            ctypes.c_uint,
        ]
        result = libc.renameat2(
            -100, os.fsencode(source), -100, os.fsencode(target), 1
        )
        if result == 0:
            return
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise PointerRefusal("RELEASE_DIRECTORY_COLLISION")
        if error in {errno.ENOSYS, errno.EINVAL, errno.EPERM}:
            raise PointerRefusal("ATOMIC_NOREPLACE_UNSUPPORTED")
        raise PointerRefusal("RELEASE_PROMOTION_FAILED")
    if os.path.lexists(target):
        raise PointerRefusal("RELEASE_DIRECTORY_COLLISION")
    os.rename(source, target)


@dataclass(frozen=True)
class PointerPublishRequest:
    operation_id: str
    candidate_installation_id: str
    expected_current_installation_id: str
    expected_previous_installation_id: str | None
    expected_pointer_generation: int
    stage_identity: str | None = None


@dataclass(frozen=True)
class PointerPublishResult:
    candidate_installation_id: str
    previous_installation_id: str
    pointer_generation: int
    receipt_digest: str
    recovered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_installation_id": self.candidate_installation_id,
            "previous_installation_id": self.previous_installation_id,
            "pointer_generation": self.pointer_generation,
            "receipt_digest": self.receipt_digest,
            "recovered": self.recovered,
        }


class ApplicationPointerPublisher:
    def __init__(
        self,
        paths: AppPaths,
        *,
        crash_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.paths = paths
        self.crash_hook = crash_hook

    def _receipt_path(self, operation_id: str) -> Path:
        if not _STAGE.fullmatch(operation_id):
            raise PointerRefusal("OPERATION_IDENTITY_REFUSED")
        return self.paths.migration_receipts_dir / f"application-pointer-{operation_id}.json"

    @staticmethod
    def _load_release_receipt(path: Path, digest: str) -> bool:
        receipt = path / ".bc250-release.json"
        if receipt.is_symlink() or not receipt.is_file():
            return False
        try:
            raw = receipt.read_bytes()
            if len(raw) > 32 * 1024:
                return False
            value = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        return (
            isinstance(value, dict)
            and value.get("schema_version") == 1
            and value.get("release_set_digest") == digest
        )

    def promote_stage(self, request: PointerPublishRequest) -> Path:
        digest = request.candidate_installation_id
        if not _DIGEST.fullmatch(digest):
            raise PointerRefusal("RELEASE_IDENTITY_REFUSED")
        final = self.paths.application_releases_dir / digest
        if final.exists():
            if final.is_symlink() or not self._load_release_receipt(final, digest):
                raise PointerRefusal("RELEASE_DIRECTORY_REFUSED")
            return final
        if request.stage_identity is None or not _STAGE.fullmatch(request.stage_identity):
            raise PointerRefusal("STAGING_IDENTITY_MISMATCH")
        stage = self.paths.application_release_staging_dir / request.stage_identity
        if (
            stage.is_symlink()
            or not stage.is_dir()
            or not self._load_release_receipt(stage, digest)
            or stage.parent.resolve(strict=True)
            != self.paths.application_release_staging_dir.resolve(strict=True)
        ):
            raise PointerRefusal("STAGING_IDENTITY_MISMATCH")
        # The application-installation lease is authoritative against another
        # app process; the private 0700 root excludes other users. Recheck just
        # before same-filesystem rename and never replace an existing target.
        if os.path.lexists(final):
            raise PointerRefusal("RELEASE_DIRECTORY_COLLISION")
        if stage.stat().st_dev != final.parent.stat().st_dev:
            raise PointerRefusal("POINTER_CROSS_DEVICE")
        _rename_no_replace(stage, final)
        fsync_directory(final.parent)
        return final

    def _receipt_body(self, request: PointerPublishRequest) -> dict[str, Any]:
        return {
            "schema_version": POINTER_RECEIPT_SCHEMA_VERSION,
            "operation_id": request.operation_id,
            "candidate_installation_id": request.candidate_installation_id,
            "prior_current_installation_id": (
                request.expected_current_installation_id
            ),
            "prior_previous_installation_id": (
                request.expected_previous_installation_id
            ),
            "pointer_generation": request.expected_pointer_generation + 1,
            "helper_digest": POINTER_HELPER_DIGEST,
        }

    def publish(self, request: PointerPublishRequest) -> PointerPublishResult:
        self.promote_stage(request)
        verify_pointer_helper_digest(
            hashlib.sha256(POINTER_HELPER_SOURCE.encode("utf-8")).hexdigest()
        )
        before = observe_pointers(self.paths.app_dir)
        state = classify_pointer_state(
            before,
            candidate=request.candidate_installation_id,
            expected_current=request.expected_current_installation_id,
            expected_previous=request.expected_previous_installation_id,
        )
        recovered = state in {"PREPARED", "COMPLETE"}
        publish_pointer_set(
            self.paths.app_dir,
            candidate=request.candidate_installation_id,
            expected_current=request.expected_current_installation_id,
            expected_previous=request.expected_previous_installation_id,
            operation_id=request.operation_id,
            crash_hook=self.crash_hook,
        )
        body = self._receipt_body(request)
        text = json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n"
        atomic_write_text(self._receipt_path(request.operation_id), text)
        return PointerPublishResult(
            candidate_installation_id=request.candidate_installation_id,
            previous_installation_id=request.expected_current_installation_id,
            pointer_generation=request.expected_pointer_generation + 1,
            receipt_digest=hashlib.sha256(text.encode()).hexdigest(),
            recovered=recovered,
        )

    def probe(self, request: PointerPublishRequest) -> tuple[str, PointerObservation]:
        observation = observe_pointers(self.paths.app_dir)
        return classify_pointer_state(
            observation,
            candidate=request.candidate_installation_id,
            expected_current=request.expected_current_installation_id,
            expected_previous=request.expected_previous_installation_id,
        ), observation

    def restore(self, request: PointerPublishRequest) -> PointerPublishResult:
        state, _ = self.probe(request)
        if state == "ABSENT":
            return PointerPublishResult(
                candidate_installation_id=request.expected_current_installation_id,
                previous_installation_id=request.expected_previous_installation_id
                or request.candidate_installation_id,
                pointer_generation=request.expected_pointer_generation,
                receipt_digest="0" * 64,
                recovered=True,
            )
        if state != "COMPLETE":
            raise PointerRefusal("POINTER_ROLLBACK_UNCERTAIN")
        rollback = PointerPublishRequest(
            operation_id=f"{request.operation_id}.rollback",
            candidate_installation_id=request.expected_current_installation_id,
            expected_current_installation_id=request.candidate_installation_id,
            expected_previous_installation_id=request.expected_current_installation_id,
            expected_pointer_generation=request.expected_pointer_generation + 1,
        )
        return self.publish(rollback)

    def probe_restored(self, request: PointerPublishRequest) -> bool:
        observed = observe_pointers(self.paths.app_dir)
        return (
            observed.current == request.expected_current_installation_id
            and observed.previous == request.candidate_installation_id
        ) or (
            observed.current == request.expected_current_installation_id
            and observed.previous == request.expected_previous_installation_id
        )


__all__ = [
    "ApplicationPointerPublisher", "POINTER_RECEIPT_SCHEMA_VERSION",
    "PointerPublishRequest", "PointerPublishResult",
]
