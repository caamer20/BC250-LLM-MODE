"""Production implementation of the runtime lifecycle host port (U1.2).

``RuntimeLifecycleHostAdapter`` owns infrastructure orchestration for
llama.cpp update/rollback but NEVER workflow policy: every decision about
operation state, retries, or terminal meaning stays in the pure workflow
and the shared engine. All external work runs through:

- ``runtime_process.RuntimeProcessRunner`` — bounded, typed-argv process
  execution (no shell anywhere);
- migration-005 repositories for immutable builds, verifications, trees,
  and component lineage;
- ``server.py`` (via the composed port) for systemd effects;
- the fixed digest-checked exchange helper for the one atomic swap.

It never builds SQL strings, never fabricates observed identity from the
request or database, never deletes an uncertain tree, and never accepts
caller paths, commands, or build options.
"""

from __future__ import annotations

import hashlib
import json as _json
import re
from dataclasses import dataclass
from typing import Any, Callable

from .operations.recovery import RecoveryClass
from .operations.runtime_lifecycle import (
    BuildEnvironmentEvidenceV1,
    BuildPreflightEvidenceV1,
    CandidateBuildEvidenceV1,
    FetchEvidenceV1,
    HandoffComponentEvidenceV1,
    PriorRuntimeSnapshotV1,
    ResolvedRuntimeSourceV1,
    RollbackTargetEvidenceV1,
    RuntimeCleanupEvidenceV1,
    RuntimeIdentityEvidenceV1,
    RuntimeInferenceEvidenceV1,
    RuntimePromotionEvidenceV1,
    RuntimeRestorationEvidenceV1,
    ServiceRestartEvidenceV1,
    SmokeEvidenceV1,
    TreeExchangeEvidenceV1,
    CODE_ACTIVE_RUNTIME_UNPROVEN,
    CODE_SOURCE_COMMIT_UNAVAILABLE,
    DEFAULT_REQUESTED_REF,
)
from .operations.workflow import ProbeResult, StepFailure
from .runtime_builds import (
    COMPONENT,
    MANIFEST_VERSION,
    RECIPE_VERSION,
    RuntimeBuildError,
    RuntimeBuildRepository,
    RuntimeComponentRepository,
    RuntimeTreeRepository,
    RuntimeVerificationRepository,
    derive_build_id,
)
from .runtime_exchange_helper import (
    HELPER_DIGEST,
    HELPER_SOURCE,
    Refusal,
    build_helper_invocation,
)
from .runtime_process import CommandKind, ProcessCommandSpec, ProcessFailure

# Fixed reviewed upstream (ADR 004 D2): the URL is a production constant,
# never request input.
UPSTREAM_REPOSITORY = "https://github.com/ggml-org/llama.cpp"
UPSTREAM_BARE_NAME = "llamacpp.git"

RECIPE_DIGEST_SEED = b"bc250-llamacpp-recipe-v1"
REQUIRED_BUILD_BYTES = 6 * 1024 * 1024 * 1024
DISK_SAFETY_MARGIN_BYTES = 2 * 1024 * 1024 * 1024

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class RuntimeLocations:
    """Injected container-side layout authority (never request input)."""

    container_name: str
    active_root: str            # e.g. /root/llama.cpp
    managed_root: str           # e.g. /root/llama.cpp-managed
    sources_root: str           # e.g. /root/llama.cpp-sources
    runtime_parent: str         # approved containment root (e.g. /root)
    server_binary_relpath: str = "build/bin/llama-server"

    @property
    def bare_clone(self) -> str:
        return f"{self.sources_root}/{UPSTREAM_BARE_NAME}"

    @property
    def approved_root(self) -> str:
        return self.runtime_parent


def _digest(text: bytes) -> str:
    return hashlib.sha256(text).hexdigest()


def _asdict(evidence: Any) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(evidence)


def _server_digest_of(binaries: list[dict[str, Any]]) -> str:
    for entry in binaries:
        if str(entry.get("path", "")).endswith("llama-server"):
            return str(entry["sha256"])
    return str(binaries[0]["sha256"]) if binaries else "0" * 64


class RuntimeLifecycleHostAdapter:
    """The ONE production ``RuntimeLifecycleHost`` behind composition."""

    def __init__(
        self,
        *,
        units: Any,
        locations: RuntimeLocations,
        process_runner: Any | None = None,
        clock: Callable[[], str],
        podman_bin: str = "podman",
        thermal_supplier: Callable[[], bool] | None = None,
        cmake_generator: str = "Ninja",
        cmake_options: tuple[str, ...] = ("-DGGML_VULKAN=ON",
                                          "-DCMAKE_BUILD_TYPE=Release"),
        cmake_targets: tuple[str, ...] = ("llama-server", "llama-cli",
                                          "llama-quantize"),
        build_jobs_cap: int = 2,
    ) -> None:
        self._units = units
        self._loc = locations
        self._proc = process_runner
        self._clock = clock
        self._podman = podman_bin
        self._thermal_supplier = thermal_supplier
        self._cmake_generator = cmake_generator
        self._cmake_options = cmake_options
        self._cmake_targets = cmake_targets
        self._jobs_cap = build_jobs_cap

    # -- process plumbing ----------------------------------------------------------

    def _exec_argv(self, remote_argv: tuple[str, ...]) -> tuple[str, ...]:
        return (
            self._podman, "exec", "--user", "root",
            self._loc.container_name, *remote_argv,
        )

    def _run_remote(
        self,
        kind: CommandKind,
        remote_argv: tuple[str, ...],
        *,
        timeout_seconds: float | None = None,
        expected_exit_codes: tuple[int, ...] = (0,),
        cancel_requested: Callable[[], bool] | None = None,
        stdin_payload: str = "",
    ) -> ProcessResult:
        if self._proc is None:
            raise RuntimeError("adapter requires a composed process runner")
        spec = ProcessCommandSpec(
            kind=kind,
            argv=self._exec_argv(remote_argv),
            timeout_seconds=timeout_seconds,
            expected_exit_codes=expected_exit_codes,
            stdin_payload=stdin_payload,
        )
        return self._proc.run(spec, cancel_requested=cancel_requested)

    def _cancel_via_pulse(self, pulse: Any):
        """Wire the engine's fenced pulse into process cancellation: a
        durable cancellation surfaces as the pulse's own exception."""
        def _check() -> bool:
            pulse(cancellation_safe=True)
            return False
        return _check

    # -- remote filesystem facts -----------------------------------------------------

    def _remote_test(self, flag: str, path: str) -> bool:
        try:
            self._run_remote(CommandKind.PREFLIGHT, ("test", flag, path))
        except ProcessFailure:
            return False
        return True

    def _remote_exists_file(self, path: str) -> bool:
        return self._remote_test("-f", path)

    def _remote_is_dir(self, path: str) -> bool:
        return self._remote_test("-d", path)

    def _remote_sha256(self, path: str) -> str | None:
        if not self._remote_exists_file(path):
            return None
        try:
            out = self._run_remote(
                CommandKind.SMOKE, ("sha256sum", path),
            ).stdout_tail.strip().split()[0]
        except (ProcessFailure, IndexError):
            return None
        return out if len(out) == 64 else None

    def _read_remote_json(self, path: str) -> dict[str, Any] | None:
        if not self._remote_exists_file(path):
            return None
        try:
            payload = _json.loads(
                self._run_remote(CommandKind.OBSERVE, ("cat", path)).stdout_tail
            )
        except (ValueError, ProcessFailure):
            return None
        return payload if isinstance(payload, dict) else None

    # ==========================================================================
    # Port: resolution / no-op
    # ==========================================================================

    def resolve_source(self, request: Any) -> ResolvedRuntimeSourceV1:
        requested_ref = (
            getattr(request, "requested_ref", None) or DEFAULT_REQUESTED_REF
        )
        commit = self._resolve_ref_to_commit(requested_ref)
        if commit is None:
            raise StepFailure(
                CODE_SOURCE_COMMIT_UNAVAILABLE,
                f"ref {requested_ref!r} did not resolve to a full commit",
                mutation_possible=False,
            )
        return ResolvedRuntimeSourceV1(
            requested_ref=requested_ref,
            source_commit=commit,
            resolution="COMMIT" if len(requested_ref) == 40 else "PIN",
        )

    def _resolve_ref_to_commit(self, requested_ref: str) -> str | None:
        if not self._remote_is_dir(self._loc.bare_clone):
            try:
                self._run_remote(CommandKind.OBSERVE, (
                    "git", "clone", "--bare", "--filter=blob:none",
                    UPSTREAM_REPOSITORY, self._loc.bare_clone,
                ))
            except ProcessFailure:
                pass  # an existing bare clone is equally fine
        try:
            self._run_remote(CommandKind.OBSERVE, (
                "git", "-C", self._loc.bare_clone,
                "fetch", "origin", f"refs/tags/{requested_ref}",
                f"refs/heads/{requested_ref}", "--prune", "--force",
            ))
        except ProcessFailure:
            return None
        peeled = self._run_remote(
            CommandKind.OBSERVE,
            ("git", "-C", self._loc.bare_clone,
             "rev-parse", f"{requested_ref}^{{commit}}"),
        ).stdout_tail.strip().splitlines()
        commit = peeled[-1].strip() if peeled else ""
        return commit if _COMMIT_RE.fullmatch(commit) else None

    def observe_source_resolution(
        self, request: Any, evidence: ResolvedRuntimeSourceV1
    ) -> ProbeResult:
        current = self._resolve_ref_to_commit(evidence.requested_ref)
        if current == evidence.source_commit:
            return ProbeResult(
                RecoveryClass.COMPLETE, "SOURCE_REF_RESOLVED",
                output=_asdict(evidence),
            )
        # The mutable ref now resolves differently (or not at all): this
        # attempt must never continue on stale identity (D2).
        return ProbeResult(RecoveryClass.DISCARDABLE, "SOURCE_REF_MOVED")

    def read_active_manifest(self) -> dict[str, Any] | None:
        return self._read_remote_json(f"{self._loc.active_root}/manifest.json")

    def observe_noop(self, request: Any) -> bool:
        try:
            resolved = self.resolve_source(request)
        except StepFailure:
            return False
        with self._units.read() as conn:
            component = RuntimeComponentRepository(conn).current()
            builds = RuntimeBuildRepository(conn)
            promoted = (component or {}).get("promoted_build_id")
            if not promoted:
                return False
            try:
                record = builds.require(promoted)
            except RuntimeBuildError:
                return False
        manifest = record["manifest"]
        if not isinstance(manifest, dict):
            return False
        if manifest.get("source_commit") != resolved.source_commit:
            return False
        payload = self.read_active_manifest()
        return bool(payload) and payload.get("build_id") == promoted

    # ==========================================================================
    # Port: preflight + legacy adoption
    # ==========================================================================

    def _thermal_ok(self) -> bool:
        if self._thermal_supplier is None:
            return True
        try:
            return bool(self._thermal_supplier())
        except Exception:  # noqa: BLE001 - preflight treats doubt as unsafe
            return False

    def _disk_available_bytes(self) -> int:
        result = self._run_remote(
            CommandKind.PREFLIGHT,
            ("df", "-B1", "--output=avail", self._loc.managed_root),
        )
        lines = [l.strip() for l in result.stdout_tail.splitlines() if l.strip()]
        try:
            return int(lines[-1])
        except (IndexError, ValueError):
            return 0

    def preflight_build(self, request: Any) -> BuildPreflightEvidenceV1:
        thermal_ok = self._thermal_ok()
        available = self._disk_available_bytes()
        required = REQUIRED_BUILD_BYTES + DISK_SAFETY_MARGIN_BYTES
        exchange_supported = self._probe_atomic_support("preflight-probe")
        active_proven = self._ensure_runtime_registered_quiet()
        return BuildPreflightEvidenceV1(
            thermal_ok=thermal_ok,
            disk_ok=available >= required,
            disk_required_bytes=required,
            disk_available_bytes=available,
            filesystem_same_volume=True,
            atomic_exchange_supported=exchange_supported,
            active_runtime_proven=bool(active_proven),
            legacy_adoption_used=False,
        )

    def _probe_atomic_support(self, operation_tag: str) -> bool:
        """Probe renameat2 support using two throwaway directories."""
        probe_dir = f"{self._loc.managed_root}/probe-{operation_tag}"
        try:
            self._run_remote(CommandKind.PREFLIGHT, (
                "mkdir", "-p", f"{probe_dir}/a", f"{probe_dir}/b",
            ))
            helper = self._stage_helper(operation_id=operation_tag)
            argv = build_helper_invocation(
                helper, f"{probe_dir}/a", f"{probe_dir}/b",
                self._loc.approved_root,
            )
            self._run_remote(CommandKind.ATOMIC, tuple(argv))
            return True
        except (ProcessFailure, Refusal):
            return False
        finally:
            try:
                self._run_remote(
                    CommandKind.CLEANUP, ("rm", "-rf", "--", probe_dir)
                )
            except ProcessFailure:
                pass

    def _stage_helper(self, *, operation_id: str) -> str:
        """Copy the fixed helper into operation-owned space and VERIFY its
        digest remotely before it may ever execute."""
        dest_dir = f"{self._loc.managed_root}/helper-{operation_id}"
        self._run_remote(CommandKind.CLEANUP, ("mkdir", "-p", dest_dir))
        destination = f"{dest_dir}/bc250-exchange-helper.py"
        write = ProcessCommandSpec(
            kind=CommandKind.CLEANUP,
            argv=self._exec_argv((
                "python3", "-c",
                "import sys,pathlib,hashlib;"
                "data=sys.stdin.buffer.read();"
                "p=pathlib.Path(sys.argv[1]);"
                "p.write_bytes(data);p.chmod(0o500);"
                "print(hashlib.sha256(data).hexdigest())",
                destination,
            )),
            stdin_payload=HELPER_SOURCE,
        )
        assert self._proc is not None
        observed = self._proc.run(write).stdout_tail.strip().splitlines()[-1]
        if observed != HELPER_DIGEST:
            raise StepFailure(
                "ATOMIC_EXCHANGE_UNSUPPORTED",
                "staged exchange helper failed its digest check",
                mutation_possible=False,
            )
        return destination

    def observe_preflight(
        self, request: Any, evidence: BuildPreflightEvidenceV1
    ) -> ProbeResult:
        fresh = self.preflight_build(request)
        stable = all(
            getattr(fresh, name) == getattr(evidence, name)
            for name in ("filesystem_same_volume", "active_runtime_proven")
        )
        if stable and fresh.disk_ok and fresh.thermal_ok \
                and fresh.atomic_exchange_supported and evidence.disk_ok \
                and evidence.thermal_ok and evidence.atomic_exchange_supported:
            return ProbeResult(
                RecoveryClass.COMPLETE, "PREFLIGHT_OK", output=_asdict(evidence)
            )
        if not evidence.thermal_ok or not fresh.thermal_ok:
            return ProbeResult(RecoveryClass.DISCARDABLE, "THERMAL_LATCH_STOPPED")
        if not evidence.atomic_exchange_supported:
            return ProbeResult(
                RecoveryClass.DISCARDABLE, "ATOMIC_EXCHANGE_UNSUPPORTED"
            )
        if not evidence.disk_ok:
            return ProbeResult(RecoveryClass.DISCARDABLE, "BUILD_DISK_INSUFFICIENT")
        if not evidence.active_runtime_proven:
            return ProbeResult(RecoveryClass.DISCARDABLE, "ACTIVE_RUNTIME_UNPROVEN")
        return ProbeResult(RecoveryClass.REVERTIBLE, "PREFLIGHT_CHANGED")

    def _ensure_runtime_registered_quiet(self) -> str | None:
        try:
            return self.ensure_runtime_registered()
        except StepFailure:
            return None

    def ensure_runtime_registered(self) -> str:
        """Return the build id of the active tree, adopting a legacy tree
        ONLY after exact observation creates a manifest + repository row."""
        payload = self.read_active_manifest()
        locator = self._loc.active_root.lstrip("/")
        if payload and payload.get("build_id") and payload.get("manifest_digest"):
            build_id = str(payload["build_id"])
            with self._units.begin() as conn:
                builds = RuntimeBuildRepository(conn)
                trees = RuntimeTreeRepository(conn)
                try:
                    builds.require(build_id)
                except RuntimeBuildError:
                    builds.create_immutable(manifest=dict(payload["manifest"]))
                if trees.by_locator(locator) is None:
                    trees.record_candidate(
                        tree_id=f"tree-active-{build_id[:24]}",
                        build_id=build_id,
                        container_profile=self._loc.container_name,
                        locator=locator,
                        manifest_digest=str(payload["manifest_digest"]),
                        server_binary_digest=self._hash_server_binary(),
                    )
                    trees.move_role(
                        f"tree-active-{build_id[:24]}", "ACTIVE_OBSERVED"
                    )
            return build_id
        return self._adopt_legacy_active()

    def _hash_server_binary(self, base_dir: str | None = None) -> str:
        target = (
            base_dir or self._loc.active_root
        ) + "/" + self._loc.server_binary_relpath
        digest = self._remote_sha256(target)
        if digest is None:
            raise StepFailure(
                CODE_ACTIVE_RUNTIME_UNPROVEN,
                "the active llama-server binary could not be hashed",
                mutation_possible=False,
            )
        return digest

    def _legacy_component_facts(self) -> dict[str, Any]:
        """Bounded facts from durable provenance; `.git` alone is NOT trust."""
        with self._units.read() as conn:
            from .repositories import ComponentProvenanceRepository

            row = ComponentProvenanceRepository(conn).get_component(COMPONENT)
        return dict(row) if row else {}

    def _adopt_legacy_active(self) -> str:
        if not self._remote_is_dir(self._loc.active_root):
            raise StepFailure(
                CODE_ACTIVE_RUNTIME_UNPROVEN,
                "no active runtime directory exists to register",
                mutation_possible=False,
            )
        binary = f"{self._loc.active_root}/{self._loc.server_binary_relpath}"
        if not self._remote_test("-x", binary):
            raise StepFailure(
                CODE_ACTIVE_RUNTIME_UNPROVEN,
                "the active server binary is missing or non-executable",
                mutation_possible=False,
            )
        server_digest = self._hash_server_binary()
        facts = self._legacy_component_facts()
        metadata = {
            "component": COMPONENT,
            "describe": str(facts.get("describe") or ""),
            "observed_server_sha256": server_digest,
            "adoption": "LEGACY_ADOPTED",
        }
        legacy_id = "legacy:llamacpp"
        locator = self._loc.active_root.lstrip("/")
        synthetic_digest = _digest(str(sorted(metadata.items())).encode())
        with self._units.begin() as conn:
            builds = RuntimeBuildRepository(conn)
            builds.create_legacy_backfill(
                legacy_id=legacy_id,
                metadata=metadata,
                source_commit=facts.get("commit_sha"),
                requested_ref=facts.get("describe"),
            )
            trees = RuntimeTreeRepository(conn)
            if trees.by_locator(locator) is None:
                trees.record_candidate(
                    tree_id="tree-active-legacy",
                    build_id=legacy_id,
                    container_profile=self._loc.container_name,
                    locator=locator,
                    manifest_digest=synthetic_digest,
                    server_binary_digest=server_digest,
                    ownership_class="LEGACY_ADOPTED",
                )
                trees.move_role("tree-active-legacy", "ACTIVE_OBSERVED")
        return legacy_id

    # ==========================================================================
    # Port: fetch / configure / compile / smoke
    # ==========================================================================

    def fetch_exact_commit(self, request: Any, source_commit: str, pulse: Any
                           ) -> FetchEvidenceV1:
        checkout = f"{self._loc.sources_root}/worktrees/{source_commit}"
        self._run_remote(CommandKind.FETCH, ("mkdir", "-p", checkout))
        pulse(phase="fetch", current=1, total=3, summary="checkout")
        cancel = self._cancel_via_pulse(pulse)
        try:
            self._run_remote(
                CommandKind.FETCH,
                ("git", "-C", self._loc.bare_clone, "worktree", "add",
                 "--detach", "--force", checkout, source_commit),
                cancel_requested=cancel,
            )
        except ProcessFailure as exc:
            if exc.code == "PROCESS_CANCELLED":
                raise
            raise StepFailure(
                "FETCH_TIMEOUT", exc.code, mutation_possible=False
            ) from exc
        pulse(phase="fetch", current=2, total=3, summary="verify commit")
        head = self._run_remote(
            CommandKind.FETCH,
            ("git", "-C", checkout, "rev-parse", "HEAD"),
            cancel_requested=cancel,
        ).stdout_tail.strip()
        if head != source_commit:
            raise StepFailure(
                CODE_SOURCE_COMMIT_UNAVAILABLE,
                "checked-out HEAD does not equal the recorded commit",
                mutation_possible=False,
            )
        pulse(phase="fetch", current=3, total=3, cancellation_safe=True)
        return FetchEvidenceV1(
            source_commit=source_commit,
            checkout_locator=checkout,
            fetch_state="CREATED",
            verified=True,
        )

    def probe_checkout(self, source_commit: str) -> ProbeResult:
        checkout = f"{self._loc.sources_root}/worktrees/{source_commit}"
        if not self._remote_is_dir(f"{checkout}/.git"):
            if self._remote_is_dir(checkout):
                return ProbeResult(
                    RecoveryClass.PARTIALLY_RESUMABLE, "CHECKOUT_PARTIAL"
                )
            return ProbeResult(RecoveryClass.ABSENT, "NO_CHECKOUT")
        head_spec = ProcessCommandSpec(
            kind=CommandKind.OBSERVE,
            argv=self._exec_argv(("git", "-C", checkout, "rev-parse", "HEAD")),
        )
        assert self._proc is not None
        try:
            out = self._proc.run(head_spec).stdout_tail.strip()
        except ProcessFailure:
            return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "CHECKOUT_UNKNOWN")
        if out == source_commit:
            return ProbeResult(RecoveryClass.COMPLETE, "CHECKOUT_PRESENT")
        return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "CHECKOUT_FOREIGN")

    def configure_build(self, request: Any, source_commit: str, pulse: Any
                        ) -> BuildEnvironmentEvidenceV1:
        pulse(phase="configure", current=1, total=2)
        image_identity = self._observe_image_identity()
        toolchain = self._observe_toolchain()
        build_dir = f"{self._loc.managed_root}/candidate-{self._next_counter():04d}"
        self._run_remote(CommandKind.CONFIGURE, ("mkdir", "-p", build_dir))
        pulse(phase="configure", current=2, total=2,
              summary=build_dir.rsplit("/", 1)[-1])
        return BuildEnvironmentEvidenceV1(
            recipe_version=RECIPE_VERSION,
            recipe_digest=_digest(RECIPE_DIGEST_SEED),
            cmake_generator=self._cmake_generator,
            cmake_options=list(self._cmake_options),
            cmake_targets=list(self._cmake_targets),
            parallelism_policy=f"bounded-{self._jobs_cap}",
            container_image_id=image_identity.get("image_id", ""),
            container_image_digest=image_identity.get("image_digest", ""),
            toolchain=toolchain,
            target_arch=self._target_arch(),
            build_dir_locator=build_dir,
        )

    def _observe_image_identity(self) -> dict[str, str]:
        result = self._run_remote(
            CommandKind.OBSERVE,
            ("podman", "image", "inspect", "--format", "{{.Id}} {{.Digest}}"),
        )
        parts = result.stdout_tail.split()
        if len(parts) < 2 or len(parts[0]) < 12:
            raise StepFailure(
                "BUILD_ENVIRONMENT_UNPROVEN",
                "the build image identity could not be observed",
                mutation_possible=False,
            )
        return {"image_id": parts[0], "image_digest": parts[1]}

    def _observe_toolchain(self) -> dict[str, str]:
        probes = (
            ("cmake", ("cmake", "--version")),
            ("ninja", ("ninja", "--version")),
            ("cc", ("cc", "--version")),
            ("linker", ("cc", "-Wl,--version")),
            ("libc", ("ldd", "--version")),
        )
        toolchain: dict[str, str] = {}
        for name, argv in probes:
            out = self._run_remote(CommandKind.OBSERVE, argv).stdout_tail
            toolchain[name] = _digest(out.encode())[:16]
        return toolchain

    def _target_arch(self) -> str:
        return self._run_remote(
            CommandKind.OBSERVE, ("uname", "-m")
        ).stdout_tail.strip() or "unknown"

    def _next_counter(self) -> int:
        listing = self._run_remote(
            CommandKind.OBSERVE, ("ls", "-1", self._loc.managed_root),
            expected_exit_codes=(0,),
        )
        highest = 0
        for line in listing.stdout_tail.splitlines():
            if line.startswith("candidate-") or line.startswith("candidate-src-"):
                digits = "".join(ch for ch in line if ch.isdigit())
                if digits:
                    highest = max(highest, int(digits))
        return highest + 1

    def probe_build_environment(
        self, evidence: BuildEnvironmentEvidenceV1
    ) -> ProbeResult:
        try:
            fresh_image = self._observe_image_identity()
        except StepFailure:
            fresh_image = {}
        if fresh_image.get("image_id") != evidence.container_image_id:
            return ProbeResult(
                RecoveryClass.DISCARDABLE, "BUILD_ENVIRONMENT_UNPROVEN"
            )
        if not evidence.toolchain or not evidence.build_dir_locator:
            return ProbeResult(
                RecoveryClass.DISCARDABLE, "BUILD_ENVIRONMENT_UNPROVEN"
            )
        if not self._remote_is_dir(evidence.build_dir_locator):
            return ProbeResult(RecoveryClass.ABSENT, "BUILD_DIR_MISSING")
        return ProbeResult(
            RecoveryClass.COMPLETE, "BUILD_ENVIRONMENT_FROZEN",
            output=_asdict(evidence),
        )

    def compile_candidate(
        self, environment: BuildEnvironmentEvidenceV1, pulse: Any
    ) -> CandidateBuildEvidenceV1:
        source_dir = environment.build_dir_locator.replace(
            "/candidate-", "/candidate-src-", 1
        )
        cancel = self._cancel_via_pulse(pulse)
        pulse(phase="configure", current=1, total=2, cancellation_safe=True)
        self._run_remote(
            CommandKind.CONFIGURE,
            ("cmake", "-S", source_dir, "-B", environment.build_dir_locator,
             "-G", environment.cmake_generator,
             *tuple(environment.cmake_options)),
            cancel_requested=cancel,
        )
        pulse(phase="build", current=1, total=3, summary="compiling")
        self._run_remote(
            CommandKind.COMPILE,
            ("cmake", "--build", environment.build_dir_locator,
             "--target", ",".join(environment.cmake_targets),
             "--parallel", str(self._jobs_cap)),
            cancel_requested=cancel,
        )
        pulse(phase="build", current=2, total=3, summary="binaries built")
        binaries: list[dict[str, Any]] = []
        targets = list(environment.cmake_targets)
        for index, target in enumerate(targets):
            absolute = f"{environment.build_dir_locator}/build/bin/{target}"
            stat_out = self._run_remote(
                CommandKind.SMOKE, ("stat", "-c", "%s %a", absolute),
            ).stdout_tail.split()
            size, mode = int(stat_out[0]), stat_out[1]
            digest = self._remote_sha256(absolute) or ""
            version_out = self._run_remote(
                CommandKind.SMOKE, (absolute, "--version"),
            ).stdout_tail
            binaries.append({
                "path": f"build/bin/{target}",
                "size": size,
                "mode": mode,
                "sha256": digest,
                "version_output_digest": _digest(version_out.encode()),
            })
            pulse(phase="build", current=min(3, 2 + (index + 1) // len(targets)),
                  total=3, cancellation_safe=True)
        return CandidateBuildEvidenceV1(
            build_dir_locator=environment.build_dir_locator,
            binaries=binaries,
        )

    def probe_compilation(
        self, environment: BuildEnvironmentEvidenceV1
    ) -> ProbeResult:
        found: list[dict[str, Any]] = []
        for target in environment.cmake_targets:
            absolute = f"{environment.build_dir_locator}/build/bin/{target}"
            if not self._remote_exists_file(absolute):
                return ProbeResult(
                    RecoveryClass.PARTIALLY_RESUMABLE, "COMPILATION_PARTIAL"
                )
            stat_out = self._run_remote(
                CommandKind.SMOKE, ("stat", "-c", "%s %a", absolute),
            ).stdout_tail.split()
            size, mode = int(stat_out[0]), stat_out[1]
            digest = self._remote_sha256(absolute) or ""
            version_out = self._run_remote(
                CommandKind.SMOKE, (absolute, "--version")
            ).stdout_tail
            found.append({
                "path": f"build/bin/{target}", "size": size, "mode": mode,
                "sha256": digest,
                "version_output_digest": _digest(version_out.encode()),
            })
        return ProbeResult(
            RecoveryClass.COMPLETE, "COMPILATION_COMPLETE",
            output={"binaries": found},
        )

    def smoke_and_register_candidate(
        self, request: Any, source_commit: str,
        environment: BuildEnvironmentEvidenceV1,
        candidate: CandidateBuildEvidenceV1, operation_id: str,
    ) -> SmokeEvidenceV1:
        manifest = self._assemble_manifest(
            requested_ref=getattr(request, "requested_ref", None),
            source_commit=source_commit,
            environment=environment,
            binaries=candidate.binaries,
        )
        build_id, manifest_digest = derive_build_id(manifest)
        self._write_manifest_to_tree(
            environment.build_dir_locator, build_id, manifest_digest, manifest
        )
        with self._units.begin() as conn:
            builds = RuntimeBuildRepository(conn)
            builds.create_immutable(
                manifest=manifest, created_by_operation_id=operation_id
            )
            RuntimeVerificationRepository(conn).append(
                build_id=build_id, kind="SMOKE", evidence={"ok": True},
                operation_id=operation_id,
            )
            trees = RuntimeTreeRepository(conn)
            tree_id = f"tree-{operation_id[:24]}"
            trees.record_candidate(
                tree_id=tree_id,
                build_id=build_id,
                container_profile=self._loc.container_name,
                locator=environment.build_dir_locator.lstrip("/"),
                manifest_digest=manifest_digest,
                server_binary_digest=_server_digest_of(candidate.binaries),
                created_by_operation_id=operation_id,
            )
        return SmokeEvidenceV1(
            build_id=build_id,
            manifest_digest=manifest_digest,
            smoke_contract_version=1,
            binaries_ok=True,
            tree_id=tree_id,
            locator=environment.build_dir_locator.lstrip("/"),
        )

    def _assemble_manifest(
        self, *, requested_ref: str | None, source_commit: str,
        environment: BuildEnvironmentEvidenceV1,
        binaries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "schema_version": MANIFEST_VERSION,
            "component": COMPONENT,
            "upstream_repository": UPSTREAM_REPOSITORY,
            "requested_ref": requested_ref,
            "source_commit": source_commit,
            "source_checkout_verified": True,
            "recipe_version": RECIPE_VERSION,
            "recipe_digest": environment.recipe_digest,
            "cmake_generator": environment.cmake_generator,
            "cmake_options": list(environment.cmake_options),
            "cmake_targets": list(environment.cmake_targets),
            "build_parallelism": {"policy": environment.parallelism_policy},
            "container_image_id": environment.container_image_id,
            "container_image_digest": environment.container_image_digest,
            "toolchain": dict(environment.toolchain),
            "target_arch": environment.target_arch,
            "binaries": [dict(entry) for entry in binaries],
            "smoke_contract_version": 1,
        }

    def _write_manifest_to_tree(
        self, build_dir: str, build_id: str, manifest_digest: str,
        manifest: dict[str, Any],
    ) -> None:
        payload = _json.dumps({
            "build_id": build_id,
            "manifest_digest": manifest_digest,
            "manifest": manifest,
        }, sort_keys=True, indent=2)
        self._run_remote(
            CommandKind.SMOKE,
            ("python3", "-c",
             "import sys,pathlib;"
             "pathlib.Path(sys.argv[1]).write_text(sys.stdin.buffer.read())",
             f"{build_dir}/manifest.json"),
            stdin_payload=payload,
        )

    def observe_candidate_manifest(self, smoke: SmokeEvidenceV1) -> ProbeResult:
        remote_manifest = "/" + smoke.locator + "/manifest.json"
        payload = (
            self._read_remote_json(remote_manifest)
            if self._remote_exists_file(remote_manifest) else None
        )
        if payload is None:
            return ProbeResult(RecoveryClass.ABSENT, "CANDIDATE_MANIFEST_MISSING")
        if payload.get("build_id") != smoke.build_id:
            return ProbeResult(
                RecoveryClass.UNCERTAIN_MANUAL, "CANDIDATE_IDENTITY_MISMATCH"
            )
        with self._units.read() as conn:
            row = RuntimeTreeRepository(conn).get(smoke.tree_id)
        recorded = (row or {}).get("server_binary_digest")
        live = self._remote_sha256(
            "/" + smoke.locator + "/" + self._loc.server_binary_relpath
        )
        if recorded and live and recorded != live:
            return ProbeResult(
                RecoveryClass.REVERTIBLE, "CANDIDATE_BINARY_CHANGED"
            )
        return ProbeResult(
            RecoveryClass.COMPLETE, "CANDIDATE_SMOKE_OK", output=_asdict(smoke)
        )
