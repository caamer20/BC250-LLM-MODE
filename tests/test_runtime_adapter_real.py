"""Real Git, subprocess and filesystem regression coverage for the host adapter.

Only the Podman transport/image observation is substituted. All guest commands
execute locally in disposable directories; no installed runtime is touched.
"""
from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.operations.recovery import RecoveryClass
from bc250_llm_mode.operations.runtime_lifecycle import RuntimeUpdateRequestV1
from bc250_llm_mode.operations.workflow import StepFailure
from bc250_llm_mode.runtime_lifecycle_adapter import RuntimeLifecycleHostAdapter, RuntimeLocations
from bc250_llm_mode.runtime_process import RuntimeProcessRunner, ProcessResult
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory


class LocalGuestRunner:
    def __init__(self):
        self.specs = []
        self.runner = RuntimeProcessRunner()

    def run(self, spec, **kwargs):
        self.specs.append(spec)
        if spec.argv[:2] == ("podman", "exec"):
            offset = 3 if spec.argv[2] == "--interactive" else 2
            assert spec.argv[offset:offset + 3] == ("--user", "root", "fixture")
            if spec.stdin_payload or spec.stdin_heartbeat:
                assert offset == 3, "Podman must keep stdin open for transferred payloads"
            argv = spec.argv[offset + 3:]
            if argv[0] == "python3":
                argv = (sys.executable, *argv[1:])
            return self.runner.run(replace(spec, argv=argv), **kwargs)
        if spec.argv[:3] == ("podman", "container", "inspect"):
            assert spec.argv[-1] == "fixture"
            output = "sha256:" + "a" * 64
        elif spec.argv[:3] == ("podman", "image", "inspect"):
            assert spec.argv[-1] == "sha256:" + "a" * 64
            output = "sha256:" + "a" * 64 + " sha256:" + "b" * 64
        else:
            raise AssertionError(spec.argv)
        return ProcessResult(0, output + "\n", "", False, False, 0)


def git(repo, *args):
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], stderr=subprocess.PIPE, text=True,
    ).strip()


@pytest.fixture
def real_adapter(tmp_path, monkeypatch):
    if not shutil.which("git"):
        pytest.skip("real Git required")
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(upstream, "init", "-b", "main")
    git(upstream, "config", "user.name", "Runtime fixture")
    git(upstream, "config", "user.email", "fixture@example.invalid")
    (upstream / "main.c").write_text('#include <stdio.h>\n#include <string.h>\nconst char *message(void);\nint main(int argc, char **argv) { if (argc > 1 && strcmp(argv[1], "--help") == 0) { puts("usage: llama-quantize fixture"); return 1; } puts(message()); return 0; }\n')
    (upstream / "helper.c").write_text('const char *message(void) { return "fixture 1"; }\n')
    (upstream / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\nproject(fixture C)\n"
        "option(BUILD_SHARED_LIBS \"Shared libraries\" ON)\nadd_library(fixturelib helper.c)\n"
        "set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)\n"
        "foreach(name llama-server llama-cli llama-quantize)\n"
        "add_executable(${name} main.c)\ntarget_link_libraries(${name} fixturelib)\nendforeach()\n"
    )
    git(upstream, "add", ".")
    git(upstream, "commit", "-m", "fixture")
    git(upstream, "tag", "b1")
    git(upstream, "tag", "-a", "b2", "-m", "annotated")
    monkeypatch.setattr("bc250_llm_mode.runtime_lifecycle_adapter.UPSTREAM_REPOSITORY", str(upstream))
    # These fixtures compile three tiny C programs, often on a small /tmp
    # filesystem. Production llama.cpp keeps its full 8 GiB preflight budget.
    monkeypatch.setattr("bc250_llm_mode.runtime_lifecycle_adapter.REQUIRED_BUILD_BYTES", 1024 * 1024)
    monkeypatch.setattr("bc250_llm_mode.runtime_lifecycle_adapter.DISK_SAFETY_MARGIN_BYTES", 1024 * 1024)
    database = tmp_path / "state.db"
    initialize_and_close(database)
    locations = RuntimeLocations(
        container_name="fixture", active_root=str(tmp_path / "active"),
        managed_root=str(tmp_path / "managed"), sources_root=str(tmp_path / "sources"),
        runtime_parent=str(tmp_path),
    )
    adapter = RuntimeLifecycleHostAdapter(
        units=UnitOfWorkFactory(database), locations=locations,
        process_runner=LocalGuestRunner(), clock=lambda: "2026-09-17T00:00:00Z",
        cmake_generator="Unix Makefiles", cmake_options=("-DCMAKE_BUILD_TYPE=Release", "-DBUILD_SHARED_LIBS=OFF"),
    )
    return adapter, upstream, git(upstream, "rev-parse", "HEAD")


@pytest.mark.parametrize("ref", ["b1", "b2", "main", "refs/tags/b1", "commit"])
def test_real_git_ref_resolution_and_worktree_recovery(real_adapter, ref):
    adapter, upstream, commit = real_adapter
    requested = commit if ref == "commit" else ref
    resolved = adapter.resolve_source(RuntimeUpdateRequestV1(requested_ref=requested))
    assert resolved.source_commit == commit
    fetched = adapter.fetch_exact_commit(None, commit, lambda **kw: None)
    assert Path(fetched.checkout_locator, ".git").is_file()
    assert adapter.probe_checkout(commit).classification is RecoveryClass.COMPLETE
    # Resuming a completed checkout must not attempt a second worktree add.
    assert adapter.fetch_exact_commit(None, commit, lambda **kw: None).verified
    Path(fetched.checkout_locator, "main.c").write_text("changed source")
    assert adapter.probe_checkout(commit).classification is RecoveryClass.UNCERTAIN_MANUAL


def test_real_mutable_ref_moves_and_ambiguous_name_refuses(real_adapter):
    adapter, upstream, commit = real_adapter
    evidence = adapter.resolve_source(RuntimeUpdateRequestV1(requested_ref="b1"))
    (upstream / "new.txt").write_text("new")
    git(upstream, "add", ".")
    git(upstream, "commit", "-m", "second")
    git(upstream, "tag", "-f", "b1")
    assert adapter.observe_source_resolution(None, evidence).classification is RecoveryClass.DISCARDABLE
    git(upstream, "branch", "b1")
    with pytest.raises(StepFailure):
        adapter.resolve_source(RuntimeUpdateRequestV1(requested_ref="b1"))


def test_fresh_disk_preflight_and_real_manifest_payload(real_adapter):
    adapter, upstream, commit = real_adapter
    assert not Path(adapter._loc.managed_root).exists()
    assert adapter._disk_available_bytes() > 0
    tree = Path(adapter._loc.active_root)
    tree.mkdir()
    adapter._write_manifest_to_tree(str(tree), "build-id", "digest", {"source_commit": commit})
    assert json.loads((tree / "manifest.json").read_text())["manifest"]["source_commit"] == commit


def test_image_identity_is_observed_on_host_for_actual_container(real_adapter):
    adapter, _, _ = real_adapter
    assert adapter._observe_image_identity() == {
        "image_id": "sha256:" + "a" * 64, "image_digest": "sha256:" + "b" * 64,
    }


def test_exchange_helper_refresh_is_atomic_and_never_follows_destination_symlink(real_adapter, tmp_path):
    from bc250_llm_mode.runtime_exchange_helper import HELPER_SOURCE

    adapter, _, _ = real_adapter
    path = Path(adapter._stage_helper(operation_id="repeat-helper"))
    assert path.read_bytes() == HELPER_SOURCE.encode()
    assert path.stat().st_mode & 0o777 == 0o500
    assert adapter._stage_helper(operation_id="repeat-helper") == str(path)
    outside = tmp_path / "preserve.txt"
    outside.write_text("owner data")
    path.unlink()
    path.symlink_to(outside)
    adapter._stage_helper(operation_id="repeat-helper")
    assert not path.is_symlink() and path.read_bytes() == HELPER_SOURCE.encode()
    assert outside.read_text() == "owner data"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux compiler and binary-stat recipe")
def test_real_cmake_builds_fetched_source_and_recovers_manifest(real_adapter):
    if not all(shutil.which(tool) for tool in ("cmake", "make", "cc")):
        pytest.skip("CMake and C toolchain required")
    adapter, _, commit = real_adapter
    adapter.fetch_exact_commit(None, commit, lambda **kw: None)
    environment = adapter.configure_build(None, commit, lambda **kw: None)
    candidate = adapter.compile_candidate(environment, lambda **kw: None)
    assert len(candidate.binaries) == 3
    assert all(Path(candidate.build_dir_locator, item["path"]).is_file() for item in candidate.binaries)
    assert adapter.probe_compilation(environment).classification is RecoveryClass.COMPLETE
    from bc250_llm_mode.operations.repositories import OperationRepository
    with adapter._units.begin() as conn:
        OperationRepository(conn).create(operation_type="RUNTIME_UPDATE", request={}, surface="test", operation_id="real-build")
    smoke = adapter.smoke_and_register_candidate(None, commit, environment, candidate, "real-build")
    assert adapter.observe_candidate_manifest(smoke).classification is RecoveryClass.COMPLETE
    Path(candidate.build_dir_locator, "build/bin/llama-server").unlink()
    assert adapter.observe_candidate_manifest(smoke).classification is not RecoveryClass.COMPLETE
