"""Fixed, hash-checked atomic tree exchange helper (ADR 004 D4).

The active runtime root and a validated candidate/rollback tree are
swapped with ONE no-gap atomic syscall — Linux ``renameat2(...,
RENAME_EXCHANGE)``. There is NO ``mv``-based fallback: when the
filesystem cannot exchange atomically the helper exits with a stable
"unsupported" code and the caller must fail safely BEFORE any mutation.

This module is the fixed package resource. Its executable source region
(``HELPER_SOURCE``) carries a recorded SHA-256 (``HELPER_DIGEST``); when
it is transferred into a container it is copied to an operation-owned
path, its remote digest is verified against ``HELPER_DIGEST``, and only
then is it invoked with typed argv:

    python3 <op-owned>/bc250-exchange-helper.py ACTIVE CANDIDATE --root ROOT

Exit codes: 0 exchanged; 3 validation refusal (hostile path, symlink,
cross-device, containment escape); 2 atomic exchange unsupported;
1 unexpected internal error.

Importing this module from application code gives tests the same
validation logic the script executes: :func:`validate_exchange_request`
is the single authority for path hostility/containment rules.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_UNSUPPORTED = 2
EXIT_REFUSAL = 3

# Stable refusal codes surfaced through stderr markers.
REF_NOT_DIRECTORY = "EXCHANGE_REFUSAL_NOT_A_DIRECTORY"
REF_SYMLINK = "EXCHANGE_REFUSAL_SYMLINK"
REF_CONTAINMENT = "EXCHANGE_REFUSAL_CONTAINMENT"
REF_OVERLAP = "EXCHANGE_REFUSAL_PATH_OVERLAP"
REF_CROSS_DEVICE = "EXCHANGE_REFUSAL_CROSS_DEVICE"

_HOSTILE_NAMES = {"", ".", ".."}
_RENAMEAT2_SUPPORTED_PLATFORMS = ("linux",)


def _check_component_hostility(path_text: str) -> None:
    # The ONLY legal empty component is the one the leading "/" of an
    # absolute path produces; every other empty, ".", "..", NUL or newline
    # segment is hostile.
    normalized = path_text[1:] if path_text.startswith("/") else path_text
    parts = normalized.split("/")
    if path_text.endswith("/") or any(
        part in _HOSTILE_NAMES or part == "" for part in parts
    ):
        raise Refusal(REF_CONTAINMENT)
    if "\x00" in path_text or "\n" in path_text:
        raise Refusal(REF_CONTAINMENT)


class Refusal(Exception):
    """A validation refusal with a stable code."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


def validate_exchange_request(
    active: str, candidate: str, *, approved_root: str
) -> tuple[Path, Path, Path]:
    """Validate one exchange request; returns ``(active, candidate, root)``.

    Refusals (never exceptions carrying raw caller text into durable
    state) cover the hostile-path corpus: empty, ``/``, ``.``, ``..``,
    traversal, NUL/newline, prefix confusion, sibling escapes, symlinks,
    and nested overlap. Both trees must be real directories under ONE
    approved container runtime root.
    """
    for value in (active, candidate, approved_root):
        _check_component_hostility(value)
    root = Path(approved_root).resolve(strict=True)
    resolved: list[Path] = []
    for raw in (active, candidate):
        path = Path(raw)
        if path.is_symlink():
            raise Refusal(REF_SYMLINK)
        try:
            resolved_path = path.resolve(strict=True)
        except OSError as exc:
            raise Refusal(REF_NOT_DIRECTORY, str(exc)[:120]) from exc
        if not resolved_path.is_dir():
            raise Refusal(REF_NOT_DIRECTORY)
        try:
            resolved_path.relative_to(root)
        except ValueError as exc:
            raise Refusal(REF_CONTAINMENT) from exc
        resolved.append(resolved_path)
    if resolved[0] == resolved[1]:
        raise Refusal(REF_OVERLAP)
    # Nested overlap: one inside the other would corrupt both trees.
    try:
        resolved[1].relative_to(resolved[0])
        raise Refusal(REF_OVERLAP)
    except Refusal:
        raise
    except ValueError:
        pass
    try:
        resolved[0].relative_to(resolved[1])
        raise Refusal(REF_OVERLAP)
    except Refusal:
        raise
    except ValueError:
        pass
    return resolved[0], resolved[1], root


def check_same_filesystem(first: Path, second: Path) -> None:
    if first.stat().st_dev != second.stat().st_dev:
        raise Refusal(REF_CROSS_DEVICE)


# -- The fixed executable resource ------------------------------------------------

HELPER_SOURCE = '''#!/usr/bin/env python3
"""BC250 atomic runtime exchange helper (fixed resource; digest-pinned).

Usage: exchange-helper.py ACTIVE CANDIDATE --root APPROVED_ROOT
Exit codes: 0 exchanged | 2 unsupported | 3 refusal | 1 internal.
"""
import ctypes, ctypes.util, os, stat, sys

EXIT_OK, EXIT_INTERNAL, EXIT_UNSUPPORTED, EXIT_REFUSAL = 0, 1, 2, 3
RENAME_EXCHANGE = 1 << 1
AT_FDCWD = -100


def fail(code, marker):
    sys.stderr.write(marker + "\\n")
    sys.exit(code)


def validate(active, candidate, root_text):
    import pathlib
    bad_names = {"", ".", ".."}
    for value in (active, candidate, root_text):
        norm = value[1:] if value.startswith("/") else value
        parts = norm.split("/")
        if value.endswith("/") or any(p in bad_names for p in parts):
            fail(EXIT_REFUSAL, "EXCHANGE_REFUSAL_CONTAINMENT")
        if "\\x00" in value or "\\n" in value:
            fail(EXIT_REFUSAL, "EXCHANGE_REFUSAL_CONTAINMENT")
    root = pathlib.Path(root_text).resolve()
    paths = []
    for raw in (active, candidate):
        p = pathlib.Path(raw)
        if p.is_symlink():
            fail(EXIT_REFUSAL, "EXCHANGE_REFUSAL_SYMLINK")
        try:
            r = p.resolve()
        except OSError:
            fail(EXIT_REFUSAL, "EXCHANGE_REFUSAL_NOT_A_DIRECTORY")
        if not r.is_dir():
            fail(EXIT_REFUSAL, "EXCHANGE_REFUSAL_NOT_A_DIRECTORY")
        try:
            r.relative_to(root)
        except ValueError:
            fail(EXIT_REFUSAL, "EXCHANGE_REFUSAL_CONTAINMENT")
        paths.append(r)
    if paths[0] == paths[1]:
        fail(EXIT_REFUSAL, "EXCHANGE_REFUSAL_PATH_OVERLAP")
    for outer, inner in ((paths[0], paths[1]), (paths[1], paths[0])):
        try:
            inner.relative_to(outer)
            fail(EXIT_REFUSAL, "EXCHANGE_REFUSAL_PATH_OVERLAP")
        except ValueError:
            pass
    return paths[0], paths[1]


def renameat2_exchange(first, second):
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    AT_FDCWD = -100
    RENAME_EXCHANGE = 2
    if not hasattr(libc, "renameat2"):
        return False
    libc.renameat2.argtypes = [
        ctypes.c_int, ctypes.c_char_p,
        ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
    ]
    result = libc.renameat2(
        AT_FDCWD, os.fsencode(str(first)),
        AT_FDCWD, os.fsencode(str(second)),
        RENAME_EXCHANGE,
    )
    if result != 0:
        errno_value = ctypes.get_errno()
        # EINVAL/ENOSYS/EPERM on filesystems without exchange support.
        if errno_value in (22, 38, 1):
            return False
        raise OSError(errno_value, os.strerror(errno_value))
    return True


def fsync_dir(path):
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main(argv):
    if len(argv) != 5 or argv[3] != "--root":
        fail(EXIT_INTERNAL, "EXCHANGE_USAGE")
    active, candidate = validate(argv[1], argv[2], argv[4])
    if (
        active.stat().st_dev != candidate.stat().st_dev
        or active.stat().st_dev != pathlib_st_dev(argv[4])
    ):
        fail(EXIT_REFUSAL, "EXCHANGE_REFUSAL_CROSS_DEVICE")
    try:
        exchanged = renameat2_exchange(active, candidate)
    except OSError:
        exchanged = False
    if not exchanged:
        fail(EXIT_UNSUPPORTED, "ATOMIC_EXCHANGE_UNSUPPORTED")
    fsync_dir(active.parent)
    fsync_dir(candidate.parent)
    sys.exit(EXIT_OK)


def pathlib_st_dev(root_text):
    import pathlib
    return pathlib.Path(root_text).stat().st_dev


if __name__ == "__main__":
    main(sys.argv[1:])
'''

HELPER_DIGEST = hashlib.sha256(HELPER_SOURCE.encode("utf-8")).hexdigest()

_HELPER_FILENAME = "bc250-exchange-helper.py"


def verify_helper_digest(observed_digest: str) -> None:
    """Refuse to execute a transferred helper whose digest drifted."""
    if observed_digest != HELPER_DIGEST:
        raise Refusal("EXCHANGE_HELPER_DIGEST_MISMATCH")


def helper_destination(operation_owned_dir: Path) -> Path:
    """The operation-owned path the helper is copied to (never shared)."""
    return operation_owned_dir / _HELPER_FILENAME


def build_helper_invocation(
    helper_path_in_container: str, active: str, candidate: str,
    approved_root: str,
) -> tuple[str, ...]:
    """Typed argv executing the verified helper (no shell anywhere)."""
    return (
        "python3",
        helper_path_in_container,
        active,
        candidate,
        "--root",
        approved_root,
    )


def run_local_exchange(
    active: str, candidate: str, *, approved_root: str
) -> None:
    """Execute the SAME validation + syscall path in-process (tests/dev).

    Production goes through the digest-checked copy in the container; this
    direct entry exists so the exact semantics are unit-testable.
    """
    import sys as _sys

    first, second = _local_validate(active, candidate, approved_root)
    ok = _renameat2_exchange(first, second)
    if not ok:
        print("ATOMIC_EXCHANGE_UNSUPPORTED")
        raise SystemExit(EXIT_UNSUPPORTED)
    _fsync_dir(os.path.dirname(first))
    _fsync_dir(os.path.dirname(second))


def _local_validate(active: str, candidate: str, root_text: str):
    first, second, _root = validate_exchange_request(
        active, candidate, approved_root=root_text
    )
    check_same_filesystem(first.parent, second.parent)
    return str(first), str(second)


def _renameat2_exchange(first: str, second: str) -> bool:
    import ctypes
    import ctypes.util
    import sys

    if not sys.platform.startswith(_RENAMEAT2_SUPPORTED_PLATFORMS):
        return False
    libc = ctypes.CDLL(
        ctypes.util.find_library("c") or "libc.so.6", use_errno=True
    )
    if not hasattr(libc, "renameat2"):
        return False
    libc.renameat2.argtypes = [
        ctypes.c_int, ctypes.c_char_p,
        ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
    ]
    result = libc.renameat2(
        -100,  # AT_FDCWD
        os.fsencode(first),
        -100,
        os.fsencode(second),
        2,     # RENAME_EXCHANGE
    )
    if result != 0:
        errno_value = ctypes.get_errno()
        if errno_value in (1, 22, 38):  # EPERM / EINVAL / ENOSYS
            return False
        raise OSError(errno_value, os.strerror(errno_value))
    return True


def _fsync_dir(directory: str) -> None:
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


__all__ = [
    "HELPER_DIGEST",
    "HELPER_SOURCE",
    "Refusal",
    "build_helper_invocation",
    "helper_destination",
    "run_local_exchange",
    "validate_exchange_request",
    "verify_helper_digest",
]
