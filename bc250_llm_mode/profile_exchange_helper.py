"""Fixed, digest-checked atomic profile exchange helper (ADR 006 D5).

Restore publication swaps the ACTIVE profile directory and a fully validated
STAGED candidate directory with ONE no-gap atomic syscall — Linux
``renameat2(..., RENAME_EXCHANGE)`` — exactly following the runtime exchange
precedent (:mod:`bc250_llm_mode.runtime_exchange_helper`). There is NO
cross-filesystem "copy then replace" path: when the two profiles are not on the
same device, or the filesystem cannot exchange atomically, the helper refuses
with a stable code and the caller fails safely BEFORE any mutation.

A profile directory is identified by its ``state.db`` marker; both the active
and candidate must be real profile directories under ONE approved root. The
path hostility / containment / symlink / cross-device rules are reused from the
runtime helper (single authority), and a profile-marker check is layered on top.

Importing this module gives tests the same validation logic the fixed script
executes. The executable source region (``PROFILE_HELPER_SOURCE``) carries a
recorded SHA-256 (``PROFILE_HELPER_DIGEST``); production copies it to an
operation-owned path, verifies the digest, and only then invokes it with typed
argv (no shell):

    python3 <op-owned>/bc250-profile-exchange-helper.py ACTIVE CANDIDATE \
        --root ROOT

Exit codes: 0 exchanged; 3 validation refusal; 2 atomic exchange unsupported;
1 unexpected internal error.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .runtime_exchange_helper import (
    EXIT_INTERNAL,
    EXIT_OK,
    EXIT_REFUSAL,
    EXIT_UNSUPPORTED,
    Refusal,
    _fsync_dir,
    _renameat2_exchange,
    check_same_filesystem,
    validate_exchange_request,
)

PROFILE_MARKER = "state.db"

REF_NOT_A_PROFILE = "PROFILE_EXCHANGE_REFUSAL_NOT_A_PROFILE"


def _require_profile_marker(directory: Path) -> None:
    if not (directory / PROFILE_MARKER).is_file():
        raise Refusal(REF_NOT_A_PROFILE, str(directory)[-80:])


def validate_profile_exchange(
    active: str, candidate: str, *, approved_root: str
) -> tuple[Path, Path, Path]:
    """Validate one profile exchange; returns ``(active, candidate, root)``.

    Reuses the runtime helper's hostile-path/containment/symlink/cross-device
    rules, then requires BOTH trees to be profile directories (a ``state.db``
    marker) so a stray or partially staged tree can never be exchanged in.
    """
    first, second, root = validate_exchange_request(
        active, candidate, approved_root=approved_root)
    _require_profile_marker(first)
    _require_profile_marker(second)
    check_same_filesystem(first, second)
    return first, second, root


def run_local_profile_exchange(
    active: str, candidate: str, *, approved_root: str
) -> None:
    """Execute the SAME validation + syscall path in-process (tests/dev)."""
    import os

    first, second, _root = validate_profile_exchange(
        active, candidate, approved_root=approved_root)
    ok = _renameat2_exchange(str(first), str(second))
    if not ok:
        print("ATOMIC_EXCHANGE_UNSUPPORTED")
        raise SystemExit(EXIT_UNSUPPORTED)
    _fsync_dir(os.path.dirname(str(first)))
    _fsync_dir(os.path.dirname(str(second)))


# -- The fixed executable resource ------------------------------------------------

PROFILE_HELPER_SOURCE = '''#!/usr/bin/env python3
"""BC250 atomic profile exchange helper (fixed resource; digest-pinned).

Usage: bc250-profile-exchange-helper.py ACTIVE CANDIDATE --root APPROVED_ROOT
Exit codes: 0 exchanged | 2 unsupported | 3 refusal | 1 internal.
Both ACTIVE and CANDIDATE must be profile directories (contain state.db).
"""
import ctypes, ctypes.util, os, pathlib, sys

EXIT_OK, EXIT_INTERNAL, EXIT_UNSUPPORTED, EXIT_REFUSAL = 0, 1, 2, 3
PROFILE_MARKER = "state.db"


def fail(code, marker):
    sys.stderr.write(marker + "\\n")
    sys.exit(code)


def validate(active, candidate, root_text):
    bad_names = {"", ".", ".."}
    for value in (active, candidate, root_text):
        norm = value[1:] if value.startswith("/") else value
        parts = norm.split("/")
        if value.endswith("/") or any(p in bad_names or p == "" for p in parts):
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
        if not (r / PROFILE_MARKER).is_file():
            fail(EXIT_REFUSAL, "PROFILE_EXCHANGE_REFUSAL_NOT_A_PROFILE")
        paths.append(r)
    if paths[0] == paths[1]:
        fail(EXIT_REFUSAL, "EXCHANGE_REFUSAL_PATH_OVERLAP")
    for outer, inner in ((paths[0], paths[1]), (paths[1], paths[0])):
        try:
            inner.relative_to(outer)
            fail(EXIT_REFUSAL, "EXCHANGE_REFUSAL_PATH_OVERLAP")
        except ValueError:
            pass
    if paths[0].stat().st_dev != paths[1].stat().st_dev:
        fail(EXIT_REFUSAL, "EXCHANGE_REFUSAL_CROSS_DEVICE")
    return paths[0], paths[1]


def renameat2_exchange(first, second):
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    if not hasattr(libc, "renameat2"):
        return False
    libc.renameat2.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
        ctypes.c_uint,
    ]
    result = libc.renameat2(
        -100, os.fsencode(str(first)), -100, os.fsencode(str(second)), 2,
    )
    if result != 0:
        errno_value = ctypes.get_errno()
        if errno_value in (1, 22, 38):
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
        fail(EXIT_INTERNAL, "PROFILE_EXCHANGE_USAGE")
    active, candidate = validate(argv[1], argv[2], argv[4])
    try:
        exchanged = renameat2_exchange(active, candidate)
    except OSError:
        exchanged = False
    if not exchanged:
        fail(EXIT_UNSUPPORTED, "ATOMIC_EXCHANGE_UNSUPPORTED")
    fsync_dir(active.parent)
    fsync_dir(candidate.parent)
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main(sys.argv[1:])
'''

PROFILE_HELPER_DIGEST = hashlib.sha256(
    PROFILE_HELPER_SOURCE.encode("utf-8")).hexdigest()

_PROFILE_HELPER_FILENAME = "bc250-profile-exchange-helper.py"


def verify_profile_helper_digest(observed_digest: str) -> None:
    if observed_digest != PROFILE_HELPER_DIGEST:
        raise Refusal("PROFILE_EXCHANGE_HELPER_DIGEST_MISMATCH")


def profile_helper_destination(operation_owned_dir: Path) -> Path:
    return operation_owned_dir / _PROFILE_HELPER_FILENAME


def build_profile_helper_invocation(
    helper_path: str, active: str, candidate: str, approved_root: str,
) -> tuple[str, ...]:
    """Typed argv executing the verified helper (no shell anywhere)."""
    return ("python3", helper_path, active, candidate, "--root", approved_root)


__all__ = [
    "EXIT_INTERNAL",
    "EXIT_OK",
    "EXIT_REFUSAL",
    "EXIT_UNSUPPORTED",
    "PROFILE_HELPER_DIGEST",
    "PROFILE_HELPER_SOURCE",
    "PROFILE_MARKER",
    "REF_NOT_A_PROFILE",
    "Refusal",
    "build_profile_helper_invocation",
    "profile_helper_destination",
    "run_local_profile_exchange",
    "validate_profile_exchange",
    "verify_profile_helper_digest",
]
