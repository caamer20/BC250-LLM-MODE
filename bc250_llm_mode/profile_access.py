"""Cross-process profile exclusion whose lock inode survives profile exchange."""
from __future__ import annotations

import fcntl
import json
import os
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_local = threading.local()


class ProfileBusy(RuntimeError):
    pass


def receipt_path(profile: Path) -> Path:
    return profile.parent / ("." + profile.name + "-restore-receipt.json")


@contextmanager
def profile_access(profile: Path, *, exclusive=False, timeout=10.0):
    profile = Path(profile).absolute()
    key = str(profile)
    held = getattr(_local, "held", None)
    if held is None:
        held = _local.held = {}
    if key in held:
        if exclusive and not held[key]:
            raise ProfileBusy("Cannot upgrade a live profile read to a restore")
        yield
        return
    profile.parent.mkdir(parents=True, exist_ok=True)
    lock_path = profile.parent / ("." + profile.name + "-access.lock")
    # Shared flock needs only a readable descriptor. The gateway's strict
    # systemd sandbox deliberately keeps the profile parent read-only; its
    # installer prepares this stable inode before starting the process.
    flags = (os.O_RDWR if exclusive else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(lock_path, flags)
    except FileNotFoundError:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ProfileBusy("Profile lock is not a regular file")
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ProfileBusy("Profile is busy; retry after the active operation finishes") from None
                time.sleep(0.025)
        held[key] = exclusive
        yield
    finally:
        held.pop(key, None)
        os.close(fd)


def require_writable_profile(profile: Path) -> None:
    """A dead restore owner must be recovered before other writers resume."""
    if getattr(_local, "held", {}).get(str(Path(profile).absolute())):
        return
    path = receipt_path(Path(profile))
    if path.exists():
        try:
            with path.open("rb") as source:
                raw = source.read(8193)
            if len(raw) > 8192 or json.loads(raw).get("phase") not in {"promoted", "rolled_back"}:
                raise ProfileBusy("Restore recovery required before changing this profile")
        except (ValueError, OSError) as exc:
            raise ProfileBusy("Restore receipt requires Repair") from exc
