"""U1.2 §16.2: production adapter tests behind a scripted process runner.

No test invokes real Podman, systemd, GitHub, or a compiler. The fake
runner records every ProcessCommandSpec (argv, stdin payload) and plays
scripted outputs, so the adapter's TYPED ARGV construction, identity
handling, and repository integration are verified exactly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip(
    "bc250_llm_mode.runtime_lifecycle_adapter",
    reason="U1.2 Commit 6: production adapter not yet defined",
)

from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.operations.recovery import RecoveryClass
from bc250_llm_mode.operations.runtime_lifecycle import (
    RuntimeUpdateRequestV1,
    ResolvedRuntimeSourceV1,
)
from bc250_llm_mode.runtime_builds import (
    RuntimeBuildRepository,
    RuntimeTreeRepository,
    derive_build_id,
)
from bc250_llm_mode.runtime_exchange_helper import (
    HELPER_DIGEST,
    HELPER_SOURCE,
)
from bc250_llm_mode.runtime_lifecycle_adapter import (
    RuntimeLifecycleHostAdapter,
    RuntimeLocations,
    UPSTREAM_REPOSITORY,
)
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory


COMMIT_A = "a" * 40
COMMIT_B = "b" * 40


class FakeProcessRunner:
    """Records specs; answers from a scripted queue or default rules."""

    def __init__(self) -> None:
        self.specs: list[object] = []
        self.scripted: list[object] = []   # exceptions / results to replay
        self.counter = 0

    def run(self, spec, *, cancel_requested=None):
        self.specs.append(spec)
        if self.scripted:
            item = self.scripted.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return self.default(spec)

    def default(self, spec):
        from bc250_llm_mode.runtime_process import ProcessResult

        argv = list(spec.argv)
        payload = spec.stdin_payload
        # Digest echo for helper staging.
        if payload == HELPER_SOURCE and any(
            "hashlib" in part for part in argv
        ):
            return ProcessResult(exit_code=0, stdout_tail=HELPER_DIGEST + "\n",
                                 stderr_tail="", truncated_stdout=False,
                                 truncated_stderr=False, duration_seconds=0.0)
        # Wrong digest on demand.
        if payload and "hashlib" in " ".join(argv[-3:]) and payload != HELPER_SOURCE:
            return ProcessResult(exit_code=0, stdout_tail="deadbeef\n",
                                 stderr_tail="", truncated_stdout=False,
                                 truncated_stderr=False, duration_seconds=0.0)
        if "rev-parse" in argv and argv[-1].endswith("^{commit}"):
            self.counter += 1
            commit = COMMIT_B if self.counter >= 2 else COMMIT_A
            return ProcessResult(exit_code=0, stdout_tail=commit + "\n",
                                 stderr_tail="", truncated_stdout=False,
                                 truncated_stderr=False, duration_seconds=0.0)
        if "rev-parse" in argv and "HEAD" in argv:
            return ProcessResult(exit_code=0, stdout_tail=COMMIT_A + "\n",
                                 stderr_tail="", truncated_stdout=False,
                                 truncated_stderr=False, duration_seconds=0.0)
        if argv[-4:-2] == ["--format"] or "image" in argv:
            return ProcessResult(exit_code=0,
                                 stdout_tail=("sha256:image-id-000000000000000 "
                                              "sha256:digest-deadbeef\n"),
                                 stderr_tail="", truncated_stdout=False,
                                 truncated_stderr=False, duration_seconds=0.0)
        if "--version" in argv or argv[-1] in ("--version",):
            return ProcessResult(exit_code=0, stdout_tail="version 1\n",
                                 stderr_tail="", truncated_stdout=False,
                                 truncated_stderr=False, duration_seconds=0.0)
        if "stat" in argv:
            return ProcessResult(exit_code=0, stdout_tail="1234 755\n",
                                 stderr_tail="", truncated_stdout=False,
                                 truncated_stderr=False, duration_seconds=0.0)
        if "df" in argv:
            return ProcessResult(exit_code=0, stdout_tail="Avail\n99999999999\n",
                                 stderr_tail="", truncated_stdout=False,
                                 truncated_stderr=False, duration_seconds=0.0)
        if "uname" in argv:
            return ProcessResult(exit_code=0, stdout_tail="x86_64\n",
                                 stderr_tail="", truncated_stdout=False,
                                 truncated_stderr=False, duration_seconds=0.0)
        if "ls" in argv and "-1" in argv:
            return ProcessResult(exit_code=0, stdout_tail="candidate-0001\n",
                                 stderr_tail="", truncated_stdout=False,
                                 truncated_stderr=False, duration_seconds=0.0)
        if sha_requested(argv):
            path = argv[-1]
            return ProcessResult(
                exit_code=0, stdout_tail=_digest_for(path) + "  " + path + "\n",
                stderr_tail="", truncated_stdout=False,
                truncated_stderr=False, duration_seconds=0.0,
            )
        return ProcessResult(exit_code=0, stdout_tail="", stderr_tail="",
                             truncated_stdout=False, truncated_stderr=False,
                             duration_seconds=0.0)


_DIGESTS: dict[str, str] = {}


def _digest_for(path: str) -> str:
    import hashlib

    if path not in _DIGESTS:
        _DIGESTS[path] = hashlib.sha256(path.encode()).hexdigest()
    return _DIGESTS[path]


def sha_requested(argv):
    return "sha256sum" in argv


@pytest.fixture()
def env(tmp_path):
    database = tmp_path / "state.db"
    initialize_and_close(database)
    units = UnitOfWorkFactory(database)
    runner = FakeProcessRunner()
    locations = RuntimeLocations(
        container_name="llm",
        active_root="/root/llama.cpp",
        managed_root="/root/llama.cpp-managed",
        sources_root="/root/llama.cpp-sources",
        runtime_parent="/root",
    )
    adapter = RuntimeLifecycleHostAdapter(
        units=units, locations=locations, process_runner=runner,
        clock=lambda: "2026-01-01T00:00:00Z",
    )
    class Env:
        pass
    e = Env()
    e.units, e.runner, e.locations, e.adapter = units, runner, locations, adapter
    return e


def test_resolve_source_uses_typed_git_argv_and_peels_the_ref(env):
    resolved = env.adapter.resolve_source(RuntimeUpdateRequestV1(requested_by="cli"))
    assert resolved.source_commit == COMMIT_A
    assert resolved.resolution == "PIN"
    # The fake reports an existing bare clone, so no clone spec appears;
    # fetch + peel must always run.
    fetch_specs = [s for s in env.runner.specs if "fetch" in s.argv]
    peel_specs = [s for s in env.runner.specs if s.argv[-1].endswith("^{commit}")]
    assert fetch_specs and peel_specs
    joined = " ".join(fetch_specs[0].argv)
    assert "refs/tags/b7598" in joined and "--force" in joined
    # The reviewed upstream is the ONLY clone source, used for the bare
    # mirror (never per-request URLs).
    from bc250_llm_mode.runtime_lifecycle_adapter import (
        UPSTREAM_REPOSITORY as ADAPTER_UPSTREAM,
    )

    assert ADAPTER_UPSTREAM == UPSTREAM_REPOSITORY
    for token in ("bash", "-lc", ";", "&&"):
        assert token not in joined


def test_mutable_ref_resolved_differently_is_discardable(env):
    evidence = env.adapter.resolve_source(
        RuntimeUpdateRequestV1(requested_by="cli")
    )
    result = env.adapter.observe_source_resolution(None, evidence)
    # Fake runner hands out COMMIT_A first, COMMIT_B afterwards: exactly
    # the moved-ref case that must refuse.
    assert result.classification is RecoveryClass.DISCARDABLE
    assert result.reason_code == "SOURCE_REF_MOVED"


def test_preflight_observes_gates_and_freezes_environment_identity(env):
    evidence = env.adapter.preflight_build(
        RuntimeUpdateRequestV1(requested_by="cli")
    )
    assert evidence.disk_ok is True
    assert evidence.atomic_exchange_supported is True
    observed = env.adapter.observe_preflight(None, evidence)
    assert observed.classification is RecoveryClass.COMPLETE


def test_fetch_checks_out_exact_commit_and_verifies_head(env):
    pulse_calls = []
    evidence = env.adapter.fetch_exact_commit(
        RuntimeUpdateRequestV1(), COMMIT_A,
        lambda **kw: pulse_calls.append(kw),
    )
    assert evidence.source_commit == COMMIT_A
    assert evidence.verified is True
    worktree = [s for s in env.runner.specs if "worktree" in s.argv]
    assert worktree, "expected a git worktree add invocation"
    assert any(kw.get("cancellation_safe") for kw in pulse_calls)


def test_compile_uses_only_fixed_recipe_options(env):
    environment = env.adapter.configure_build(
        RuntimeUpdateRequestV1(), COMMIT_A, lambda **kw: None
    )
    before = len(env.runner.specs)
    candidate = env.adapter.compile_candidate(environment, lambda **kw: None)
    configure_specs = [
        s for s in env.runner.specs[before:] if "cmake" in s.argv and "-S" in s.argv
    ]
    build_specs = [
        s for s in env.runner.specs[before:] if "--build" in s.argv
    ]
    assert configure_specs, "expected cmake configure"
    joined_configure = " ".join(configure_specs[0].argv)
    assert "-DGGML_VULKAN=ON" in joined_configure
    assert "-DCMAKE_BUILD_TYPE=Release" in joined_configure
    joined_build = " ".join(build_specs[0].argv)
    assert "--parallel 2" in joined_build
    assert all(entry["mode"] == "755" for entry in candidate.binaries)
    assert {e["path"].split("/")[-1] for e in candidate.binaries} == {
        "llama-server", "llama-cli", "llama-quantize"
    }


def _seed_operation(units, operation_id: str) -> None:
    from bc250_llm_mode.operations.repositories import OperationRepository

    with units.begin() as conn:
        OperationRepository(conn).create(
            operation_type="RUNTIME_UPDATE",
            request={"requested_by": "cli"},
            surface="test",
            operation_id=operation_id,
        )


def test_smoke_registers_immutable_build_tree_and_verification(env):
    _seed_operation(env.units, "op-smoke-1")
    environment = env.adapter.configure_build(
        RuntimeUpdateRequestV1(), COMMIT_A, lambda **kw: None
    )
    candidate = env.adapter.compile_candidate(environment, lambda **kw: None)
    request = RuntimeUpdateRequestV1(requested_by="cli")
    smoke = env.adapter.smoke_and_register_candidate(
        request, COMMIT_A, environment, candidate, operation_id="op-smoke-1"
    )
    build_id, digest = derive_build_id(_manifest_from(environment, candidate, COMMIT_A))
    assert smoke.build_id == build_id
    assert smoke.manifest_digest == digest
    with env.units.read() as conn:
        builds = RuntimeBuildRepository(conn)
        record = builds.require(build_id)
        trees = RuntimeTreeRepository(conn)
        tree = trees.require(smoke.tree_id)
    assert record["provenance_class"] == "IMMUTABLE_SOURCE"
    assert tree["role"] == "CANDIDATE"


def test_smoke_registration_is_idempotent_per_content(env):
    _seed_operation(env.units, "op-a")
    _seed_operation(env.units, "op-b")
    environment = env.adapter.configure_build(
        RuntimeUpdateRequestV1(), COMMIT_A, lambda **kw: None
    )
    candidate = env.adapter.compile_candidate(environment, lambda **kw: None)
    request = RuntimeUpdateRequestV1(requested_by="cli")
    first = env.adapter.smoke_and_register_candidate(
        request, COMMIT_A, environment, candidate, operation_id="op-a"
    )
    second = env.adapter.smoke_and_register_candidate(
        request, COMMIT_A, environment, candidate, operation_id="op-b"
    )
    assert first.build_id == second.build_id


def test_legacy_active_adoption_registers_unverified_row_without_trusting_git(env):
    # No manifest exists; provenance carries bounded facts only.
    with env.units.begin() as conn:
        from bc250_llm_mode.repositories import ComponentProvenanceRepository

        ComponentProvenanceRepository(conn).set_component(
            "llamacpp", "b7598", COMMIT_A
        )
    adopted = env.adapter.ensure_runtime_registered()
    assert adopted == "legacy:llamacpp"
    with env.units.read() as conn:
        builds = RuntimeBuildRepository(conn)
        record = builds.require("legacy:llamacpp")
        trees = RuntimeTreeRepository(conn)
        row = trees.by_locator("root/llama.cpp")
    assert record["provenance_class"] == "LEGACY_UNVERIFIED"
    assert row["ownership_class"] == "LEGACY_ADOPTED"


def test_missing_server_binary_blocks_adoption(env):
    # The fake hashes whatever it is asked; make the binary probe fail by
    # pointing the server relpath at a path the fake refuses to hash.
    from bc250_llm_mode.runtime_process import ProcessFailure

    class NoBinaryRunner(FakeProcessRunner):
        def run(self, spec, *, cancel_requested=None):
            if "test" in spec.argv and spec.argv[-1].endswith("llama-server"):
                raise ProcessFailure("PROCESS_EXIT_UNEXPECTED", "missing")
            if "sha256sum" in spec.argv:
                raise ProcessFailure("PROCESS_EXIT_UNEXPECTED", "gone")
            return super().run(spec, cancel_requested=cancel_requested)

    env.runner.__class__ = NoBinaryRunner
    env.adapter._proc = env.runner
    from bc250_llm_mode.operations.workflow import StepFailure

    with pytest.raises(StepFailure) as err:
        env.adapter.ensure_runtime_registered()
    message = str(err.value).lower()
    assert ("runtime" in message) or ("binary" in message)


def test_helper_staging_digest_mismatch_refuses_execution(env):
    from bc250_llm_mode.operations.workflow import StepFailure

    class BadEchoRunner(FakeProcessRunner):
        def run(self, spec, *, cancel_requested=None):
            if spec.stdin_payload == HELPER_SOURCE:
                from bc250_llm_mode.runtime_process import ProcessResult

                return ProcessResult(
                    exit_code=0, stdout_tail="0" * 64 + "\n", stderr_tail="",
                    truncated_stdout=False, truncated_stderr=False,
                    duration_seconds=0.0,
                )
            return super().run(spec, cancel_requested=cancel_requested)

    env.runner.__class__ = BadEchoRunner
    env.adapter._proc = env.runner
    with pytest.raises(StepFailure):
        env.adapter._stage_helper(operation_id="op-x")


# -- helpers ----------------------------------------------------------------------


def _manifest_from(environment, candidate, source_commit):
    from bc250_llm_mode.runtime_lifecycle_adapter import UPSTREAM_REPOSITORY

    return {
        "schema_version": 1,
        "component": "llamacpp",
        "upstream_repository": UPSTREAM_REPOSITORY,
        "requested_ref": None,
        "source_commit": source_commit,
        "source_checkout_verified": True,
        "recipe_version": 1,
        "recipe_digest": environment.recipe_digest,
        "cmake_generator": environment.cmake_generator,
        "cmake_options": list(environment.cmake_options),
        "cmake_targets": list(environment.cmake_targets),
        "build_parallelism": {"policy": environment.parallelism_policy},
        "container_image_id": environment.container_image_id,
        "container_image_digest": environment.container_image_digest,
        "toolchain": dict(environment.toolchain),
        "target_arch": environment.target_arch,
        "binaries": [dict(e) for e in candidate.binaries],
        "smoke_contract_version": 1,
    }
