"""C2 §C2.4/§C2.7 (V1_0_RELEASE_CLOSURE plan): profile exchange helper.

Validates the fixed, digest-pinned atomic profile exchange: profile-marker
refusal, hostile-path/containment/symlink refusal, cross-device refusal, the
stable helper digest, typed argv, and — on Linux only — a real same-filesystem
atomic exchange.
"""

from __future__ import annotations

import os
import sys

import pytest

from bc250_llm_mode.profile_exchange_helper import (
    PROFILE_HELPER_DIGEST,
    PROFILE_MARKER,
    REF_NOT_A_PROFILE,
    Refusal,
    build_profile_helper_invocation,
    profile_helper_destination,
    run_local_profile_exchange,
    validate_profile_exchange,
    verify_profile_helper_digest,
)
from bc250_llm_mode.runtime_exchange_helper import (
    EXIT_UNSUPPORTED,
    REF_CONTAINMENT,
    REF_SYMLINK,
)

IS_LINUX = sys.platform.startswith("linux")


def _make_profile(root, name):
    d = root / name
    d.mkdir()
    (d / PROFILE_MARKER).write_bytes(b"db")
    return d


def test_valid_profile_exchange_validates(tmp_path):
    active = _make_profile(tmp_path, "active")
    candidate = _make_profile(tmp_path, "candidate")
    first, second, root = validate_profile_exchange(
        str(active), str(candidate), approved_root=str(tmp_path))
    assert first == active.resolve() and second == candidate.resolve()


def test_missing_profile_marker_refused(tmp_path):
    active = _make_profile(tmp_path, "active")
    not_profile = tmp_path / "stray"  # no state.db
    not_profile.mkdir()
    candidate = _make_profile(tmp_path, "candidate")
    with pytest.raises(Refusal) as exc:
        validate_profile_exchange(
            str(not_profile), str(candidate), approved_root=str(tmp_path))
    assert exc.value.code == REF_NOT_A_PROFILE
    with pytest.raises(Refusal) as exc2:
        validate_profile_exchange(
            str(active), str(not_profile), approved_root=str(tmp_path))
    assert exc2.value.code == REF_NOT_A_PROFILE


def test_hostile_paths_and_symlink_refused(tmp_path):
    active = _make_profile(tmp_path, "active")
    candidate = _make_profile(tmp_path, "candidate")
    with pytest.raises(Refusal) as exc:
        validate_profile_exchange(
            str(active) + "/../escape", str(candidate),
            approved_root=str(tmp_path))
    assert exc.value.code == REF_CONTAINMENT

    link = tmp_path / "link"
    os.symlink(str(active), str(link))
    with pytest.raises(Refusal) as exc2:
        validate_profile_exchange(
            str(link), str(candidate), approved_root=str(tmp_path))
    assert exc2.value.code == REF_SYMLINK


def test_cross_device_refused(tmp_path, monkeypatch):
    import bc250_llm_mode.profile_exchange_helper as mod
    from bc250_llm_mode.runtime_exchange_helper import (
        REF_CROSS_DEVICE, check_same_filesystem,
    )

    def _raise_cross_device(first, second):
        raise Refusal(REF_CROSS_DEVICE)

    monkeypatch.setattr(mod, "check_same_filesystem", _raise_cross_device)
    active = _make_profile(tmp_path, "active")
    candidate = _make_profile(tmp_path, "candidate")
    with pytest.raises(Refusal) as exc:
        validate_profile_exchange(
            str(active), str(candidate), approved_root=str(tmp_path))
    assert exc.value.code == REF_CROSS_DEVICE
    monkeypatch.setattr(mod, "check_same_filesystem", check_same_filesystem)


def test_helper_digest_is_stable_and_mismatch_refused(tmp_path):
    # Digest is deterministic across imports.
    import importlib
    from bc250_llm_mode import profile_exchange_helper as mod
    importlib.reload(mod)
    assert mod.PROFILE_HELPER_DIGEST == PROFILE_HELPER_DIGEST
    verify_profile_helper_digest(PROFILE_HELPER_DIGEST)  # no raise
    with pytest.raises(Refusal):
        verify_profile_helper_digest("0" * 64)


def test_helper_invocation_is_typed_argv(tmp_path):
    dest = profile_helper_destination(tmp_path)
    argv = build_profile_helper_invocation(
        str(dest), "/root/active", "/root/candidate", "/root")
    assert argv[0] == "python3"
    assert argv[1] == str(dest)
    assert argv[3] == "/root/candidate" and argv[4] == "--root"
    assert dest.name == "bc250-profile-exchange-helper.py"


def test_local_exchange_unsupported_off_linux(tmp_path):
    if IS_LINUX:
        pytest.skip("off-Linux unsupported path only")
    active = _make_profile(tmp_path, "active")
    candidate = _make_profile(tmp_path, "candidate")
    with pytest.raises(SystemExit) as exc:
        run_local_profile_exchange(
            str(active), str(candidate), approved_root=str(tmp_path))
    assert exc.value.code == EXIT_UNSUPPORTED


@pytest.mark.skipif(not IS_LINUX, reason="real exchange requires Linux")
def test_real_same_filesystem_exchange_on_linux(tmp_path):
    active = _make_profile(tmp_path, "active")
    candidate = _make_profile(tmp_path, "candidate")
    (active / "marker.txt").write_text("was-active")
    (candidate / "marker.txt").write_text("was-candidate")
    run_local_profile_exchange(
        str(active), str(candidate), approved_root=str(tmp_path))
    # After exchange, the directory NAMES now hold the swapped content.
    assert (active / "marker.txt").read_text() == "was-candidate"
    assert (candidate / "marker.txt").read_text() == "was-active"
