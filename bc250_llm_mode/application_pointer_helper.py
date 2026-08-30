"""Minimal digest-pinned application pointer publication helper.

``current`` and ``previous`` are app-owned relative symlinks. Publication is
two ordered same-directory atomic replacements: first ``previous`` becomes the
old current slot, then ``current`` becomes the candidate. A death between them
leaves a uniquely recognizable prepared state and never loses the working
current slot (ADR 013 D5/D7).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .fsops import fsync_directory


POINTER_HELPER_SCHEMA_VERSION = 1
_DIGEST = re.compile(r"[0-9a-f]{64}")
_OPERATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_LINK_NAMES = frozenset({"current", "previous"})


class PointerRefusal(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PointerObservation:
    current: str | None
    previous: str | None


def _validate_root(root: Path) -> tuple[Path, Path]:
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise PointerRefusal("POINTER_ROOT_REFUSED")
    resolved = root.resolve(strict=True)
    releases = root / "releases"
    if releases.is_symlink() or not releases.is_dir():
        raise PointerRefusal("RELEASE_ROOT_REFUSED")
    if releases.resolve(strict=True) != resolved / "releases":
        raise PointerRefusal("RELEASE_ROOT_REFUSED")
    if releases.stat().st_dev != root.stat().st_dev:
        raise PointerRefusal("POINTER_CROSS_DEVICE")
    return resolved, releases


def _verify_release(releases: Path, identity: str) -> Path:
    if not isinstance(identity, str) or not _DIGEST.fullmatch(identity):
        raise PointerRefusal("RELEASE_IDENTITY_REFUSED")
    release = releases / identity
    if release.is_symlink() or not release.is_dir():
        raise PointerRefusal("RELEASE_DIRECTORY_REFUSED")
    if release.resolve(strict=True) != releases.resolve(strict=True) / identity:
        raise PointerRefusal("RELEASE_DIRECTORY_REFUSED")
    receipt = release / ".bc250-release.json"
    if receipt.is_symlink() or not receipt.is_file():
        raise PointerRefusal("RELEASE_RECEIPT_INVALID")
    try:
        raw = receipt.read_bytes()
        if len(raw) > 32 * 1024:
            raise PointerRefusal("RELEASE_RECEIPT_INVALID")
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PointerRefusal("RELEASE_RECEIPT_INVALID") from exc
    if (
        not isinstance(value, dict)
        or value.get("release_set_digest") != identity
        or value.get("schema_version") != 1
    ):
        raise PointerRefusal("RELEASE_RECEIPT_INVALID")
    return release


def _read_link(root: Path, name: str, releases: Path) -> str | None:
    if name not in _LINK_NAMES:
        raise PointerRefusal("POINTER_NAME_REFUSED")
    link = root / name
    if not os.path.lexists(link):
        return None
    if not link.is_symlink():
        raise PointerRefusal("POINTER_NOT_SYMLINK")
    target = os.readlink(link)
    match = re.fullmatch(r"releases/([0-9a-f]{64})", target)
    if match is None:
        raise PointerRefusal("POINTER_TARGET_REFUSED")
    identity = match.group(1)
    _verify_release(releases, identity)
    return identity


def observe_pointers(root: Path) -> PointerObservation:
    resolved, releases = _validate_root(root)
    return PointerObservation(
        _read_link(resolved, "current", releases),
        _read_link(resolved, "previous", releases),
    )


def classify_pointer_state(
    observation: PointerObservation,
    *,
    candidate: str,
    expected_current: str,
    expected_previous: str | None,
) -> str:
    if (
        observation.current == candidate
        and observation.previous == expected_current
    ):
        return "COMPLETE"
    if (
        observation.current == expected_current
        and observation.previous == expected_current
    ):
        return "PREPARED"
    if (
        observation.current == expected_current
        and observation.previous == expected_previous
    ):
        return "ABSENT"
    return "UNCERTAIN"


def publish_pointer_set(
    root: Path,
    *,
    candidate: str,
    expected_current: str,
    expected_previous: str | None,
    operation_id: str,
    crash_hook: Callable[[str], None] | None = None,
) -> PointerObservation:
    if not _OPERATION.fullmatch(operation_id):
        raise PointerRefusal("OPERATION_IDENTITY_REFUSED")
    resolved, releases = _validate_root(root)
    _verify_release(releases, candidate)
    _verify_release(releases, expected_current)
    if expected_previous is not None:
        _verify_release(releases, expected_previous)
    observed = observe_pointers(resolved)
    state = classify_pointer_state(
        observed,
        candidate=candidate,
        expected_current=expected_current,
        expected_previous=expected_previous,
    )
    if state == "COMPLETE":
        return observed
    if state not in {"ABSENT", "PREPARED"}:
        raise PointerRefusal("POINTER_IDENTITY_MISMATCH")

    hook = crash_hook or (lambda point: None)
    previous_tmp = resolved / f".previous.{operation_id}.tmp"
    current_tmp = resolved / f".current.{operation_id}.tmp"
    for temp in (previous_tmp, current_tmp):
        if os.path.lexists(temp):
            if not temp.is_symlink():
                raise PointerRefusal("POINTER_TEMP_REFUSED")
            temp.unlink()
    try:
        if state == "ABSENT":
            previous_tmp.symlink_to(f"releases/{expected_current}")
            os.replace(previous_tmp, resolved / "previous")
            fsync_directory(resolved)
            hook("after_previous_replace")
        current_tmp.symlink_to(f"releases/{candidate}")
        os.replace(current_tmp, resolved / "current")
        fsync_directory(resolved)
        hook("after_current_replace")
    finally:
        for temp in (previous_tmp, current_tmp):
            if temp.is_symlink():
                try:
                    temp.unlink()
                except OSError:
                    pass
    final = observe_pointers(resolved)
    if classify_pointer_state(
        final,
        candidate=candidate,
        expected_current=expected_current,
        expected_previous=expected_previous,
    ) != "COMPLETE":
        raise PointerRefusal("POINTER_PUBLICATION_UNCERTAIN")
    return final


# The fixed executable artifact is deliberately self-contained only as a
# wrapper around the package's reviewed helper entry. Production verifies this
# exact source digest before invoking it from an operation-owned location; the
# imported package is the already verified candidate/current slot.
POINTER_HELPER_SOURCE = '''#!/usr/bin/env python3
import pathlib, sys
from bc250_llm_mode.application_pointer_helper import publish_pointer_set

def main(argv):
    if len(argv) != 6:
        raise SystemExit(2)
    root, candidate, current, previous, operation = argv[1:]
    publish_pointer_set(
        pathlib.Path(root), candidate=candidate, expected_current=current,
        expected_previous=None if previous == "-" else previous,
        operation_id=operation,
    )

if __name__ == "__main__":
    main(sys.argv)
'''
POINTER_HELPER_DIGEST = hashlib.sha256(
    POINTER_HELPER_SOURCE.encode("utf-8")
).hexdigest()


def verify_pointer_helper_digest(observed: str) -> None:
    if observed != POINTER_HELPER_DIGEST:
        raise PointerRefusal("POINTER_HELPER_DIGEST_MISMATCH")


def build_pointer_helper_invocation(
    helper_path: str,
    *,
    root: str,
    candidate: str,
    expected_current: str,
    expected_previous: str | None,
    operation_id: str,
) -> tuple[str, ...]:
    return (
        "python3", helper_path, root, candidate, expected_current,
        expected_previous or "-", operation_id,
    )


__all__ = [
    "POINTER_HELPER_DIGEST", "POINTER_HELPER_SCHEMA_VERSION",
    "POINTER_HELPER_SOURCE", "PointerObservation", "PointerRefusal",
    "build_pointer_helper_invocation", "classify_pointer_state",
    "observe_pointers", "publish_pointer_set", "verify_pointer_helper_digest",
]
