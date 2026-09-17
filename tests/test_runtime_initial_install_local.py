"""Mac-only production-adapter journeys with explicit platform fixtures.

Git, compilation, hashes, SQLite and file effects are real. These tests replace
CMake with the system C compiler and the Linux helper syscall with Darwin's
renamex_np. They prove setup/recovery logic, not the Linux helper or toolchain.
The separate *_real module retains the actual Linux/CMake checks.
"""

import ctypes
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import test_runtime_initial_install_real as journeys
from test_runtime_adapter_real import real_adapter
from bc250_llm_mode.operations.recovery import RecoveryClass
from bc250_llm_mode.operations.runtime_lifecycle import CandidateBuildEvidenceV1
from bc250_llm_mode.operations.workflow import ProbeResult
from bc250_llm_mode.runtime_exchange_helper import validate_exchange_request
from bc250_llm_mode.runtime_process import CommandKind, ProcessResult


pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="explicit Darwin platform fixture")


@pytest.fixture
def local_world(real_adapter, tmp_path):
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("native C compiler required for local fixture")
    adapter, _, _ = real_adapter
    # This fixture manifest identifies its native compiler; it does not claim
    # the production Linux CMake environment was observed on macOS.
    version = subprocess.check_output([compiler, "--version"], text=True)
    adapter._observe_toolchain = lambda: {"fixture-native-cc": hashlib.sha256(version.encode()).hexdigest()}

    def binaries(environment):
        result = []
        for name in environment.cmake_targets:
            path = Path(environment.build_dir_locator, "build/bin", name)
            info = path.stat()
            text = adapter._binary_smoke_text(str(path), name)
            result.append({"path": "build/bin/" + name, "size": info.st_size,
                           "mode": format(info.st_mode & 0o777, "o"),
                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                           "version_output_digest": hashlib.sha256(text.encode()).hexdigest()})
        return CandidateBuildEvidenceV1(environment.build_dir_locator, result)

    def compile_candidate(environment, pulse):
        assert adapter.probe_build_environment(environment).classification is RecoveryClass.COMPLETE
        source = Path(adapter._loc.sources_root, "worktrees", environment.source_commit)
        output = Path(environment.build_dir_locator, "build/bin")
        output.mkdir(parents=True)
        for name in environment.cmake_targets:
            pulse(cancellation_safe=True)
            subprocess.run([compiler, str(source / "main.c"), str(source / "helper.c"),
                            "-o", str(output / name)], check=True, capture_output=True, timeout=20)
        return binaries(environment)

    def compilation(environment):
        try:
            from dataclasses import asdict
            return ProbeResult(RecoveryClass.COMPLETE, "LOCAL_COMPILE_FIXTURE", output=asdict(binaries(environment)))
        except FileNotFoundError:
            return ProbeResult(RecoveryClass.PARTIALLY_RESUMABLE, "LOCAL_BUILD_ABSENT")

    adapter.compile_candidate = compile_candidate
    adapter.probe_compilation = compilation
    remote = adapter._run_remote
    libc = ctypes.CDLL(None, use_errno=True)
    libc.renamex_np.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)

    def run_remote(kind, argv, **kwargs):
        if kind is not CommandKind.ATOMIC:
            return remote(kind, argv, **kwargs)
        active, candidate, root = map(Path, (argv[2], argv[3], argv[5]))
        assert argv[4] == "--root"
        if argv[-1] == "--publish-initial":
            assert root in active.parents and root in candidate.parents
            assert not active.is_symlink() and active.parent.resolve() == active.parent
            assert candidate.resolve() == candidate and candidate.is_dir()
            source, destination, flags = candidate, active, 0x4  # SDK RENAME_EXCL
        else:
            validate_exchange_request(str(active), str(candidate), approved_root=str(root))
            source, destination, flags = active, candidate, 0x2  # SDK RENAME_SWAP
        if libc.renamex_np(os.fsencode(source), os.fsencode(destination), flags):
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
        for parent in {source.parent, destination.parent}:
            descriptor = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return ProcessResult(0, "fixture rename completed\n", "", False, False, 0)

    adapter._run_remote = run_remote
    return journeys.make_initial_world(real_adapter, tmp_path)


def test_local_initial_install_repeat_activation_and_promotion(local_world):
    journeys.test_initial_install_repeat_model_activation_and_exact_promotion(local_world)


def test_local_verification_failure_restores_the_prepared_runtime(local_world):
    journeys.test_first_verification_failure_restores_unpromoted_runtime_without_rebuild(local_world)


def test_local_death_after_first_publication_resumes_without_a_model(local_world):
    journeys.test_death_after_initial_publication_recovers_without_starting_a_service(local_world)


def test_local_changed_prepared_binary_is_refused(local_world):
    journeys.test_prepared_binary_change_is_refused_before_reuse(local_world)


def test_local_no_model_verification_remains_pending(local_world):
    journeys.test_verification_without_a_model_remains_pending(local_world)


def test_local_first_model_failure_restores_empty_selection_and_allows_retry(local_world):
    journeys.test_first_model_inference_failure_restores_empty_selection_and_allows_retry(local_world)
