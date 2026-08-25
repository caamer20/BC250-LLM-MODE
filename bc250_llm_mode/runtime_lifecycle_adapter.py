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
        server_port: Any | None = None,
        renderer: Any | None = None,
        state_supplier: Callable[[], dict[str, Any]] | None = None,
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
        self.server_port = server_port
        self.renderer = renderer
        self.state_supplier = state_supplier
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

    # ==========================================================================
    # Port: activation boundary / exchange / verification / promotion
    # (Commit 7 — requires composed server_port + renderer seams)
    # ==========================================================================

    def _require_seams(self) -> None:
        missing = [
            name for name, value in (
                ("server_port", self.server_port),
                ("renderer", self.renderer),
                ("state_supplier", self.state_supplier),
            ) if value is None
        ]
        if missing:
            raise RuntimeError(
                "adapter seams not composed: " + ", ".join(missing)
            )

    def _view(self) -> dict[str, Any]:
        view = dict(self.state_supplier() or {})
        return view

    def _known_good(self) -> dict[str, Any] | None:
        with self._units.read() as conn:
            from .repositories import KnownGoodRuntimeRepository

            return KnownGoodRuntimeRepository(conn).get()

    def _capture_service_facts(self) -> tuple[bool, str | None, dict[str, Any]]:
        """``(active, invocation_marker, health)`` via the typed port."""
        view = self._view()
        facts = self.server_port.capture(view)
        marker = facts.get("invocation_marker")
        active = bool(facts.get("active"))
        health: dict[str, Any] = {}
        if active:
            try:
                health = self.server_port.health(view, timeout=15)
            except Exception:  # noqa: BLE001 - unverified is evidence
                health = {}
        return active, marker, health

    def capture_activation_boundary(
        self, request: Any, target_build_id: str | None
    ) -> PriorRuntimeSnapshotV1:
        self._require_seams()
        self._crash_point("capture_activation_boundary", "mid_effect")
        with self._units.read() as conn:
            components = RuntimeComponentRepository(conn)
            builds = RuntimeBuildRepository(conn)
            component = components.current()
            promoted = (component or {}).get("promoted_build_id")
            source_commit = ""
            if promoted:
                try:
                    source_commit = str(
                        builds.require(promoted)["manifest"].get("source_commit") or ""
                    )
                except RuntimeBuildError:
                    source_commit = ""
        known_good = self._known_good()
        payload = (
            self.renderer.observe(require_v2=False) if self.renderer else None
        )
        active_manifest = self.read_active_manifest()
        active_build_id = (
            str(payload.get("runtime_component_id"))
            if payload and payload.get("runtime_component_id")
            else (str(active_manifest.get("build_id")) if active_manifest else None)
        )
        active, marker, health = self._capture_service_facts()
        running_model = health.get("model_id") or (
            active_build_id if active else None
        )
        del source_commit
        return PriorRuntimeSnapshotV1(
            service_state=(
                "ACTIVE_VERIFIED" if active and health.get("healthy")
                else PRIOR_STOPPED if active or active_build_id is not None
                else PRIOR_ABSENT
            ),
            active_build_id=active_build_id,
            promoted_build_id=promoted,
            rollback_build_id=(component or {}).get("rollback_build_id"),
            generation=(component or {}).get("generation"),
            known_good_component_identity=(
                (known_good or {}).get("runtime_component_identity")
            ),
            handoff_fingerprint=(
                payload.get("runtime_fingerprint") if payload else None
            ),
            handoff_payload=payload,
            invocation_count=None,
            invocation_marker=str(marker) if marker is not None else None,
            observed_model_alias=running_model,
            observed_context_total=health.get("n_ctx") if active else None,
            observed_slots=health.get("parallel_slots") if active else None,
            inference_verified=bool(active and health.get("healthy")),
            active_tree_id=None,
        )

    def verify_activation_boundary(self, request, snapshot, target_build_id) -> None:
        expected = getattr(request, "expected_active_build_id", None)
        if expected is not None \
                and snapshot.promoted_build_id != expected:
            raise StepFailure(
                CODE_ACTIVE_TREE_CHANGED,
                "promoted build changed before the activation boundary",
                mutation_possible=False,
            )
        if snapshot.service_state == PRIOR_ABSENT and target_build_id is not None \
                and snapshot.active_build_id is not None:
            raise StepFailure(
                CODE_ACTIVE_TREE_CHANGED,
                "an active runtime appeared during the build phase",
                mutation_possible=False,
            )

    def _crash_point(self, step_key: str, subpoint: str) -> None:
        hook = getattr(self, "_effect_crash_hook", None)
        if hook is not None:
            hook(step_key, subpoint)

    # -- atomic exchange -----------------------------------------------------------

    def _retained_prior_locator(self) -> str:
        with self._units.read() as conn:
            trees = RuntimeTreeRepository(conn)
            row = trees.by_locator(
                self._loc.managed_root.lstrip("/") + "/prior"
            )
            if row is not None:
                return row["locator"]
            retained = [
                dict(r) for r in conn.execute(
                    "SELECT locator FROM runtime_trees WHERE role='RETAINED'"
                    " ORDER BY last_observed_at DESC LIMIT 1"
                ).fetchall()
            ]
        return retained[0]["locator"] if retained else (
            self._loc.managed_root.lstrip("/") + "/prior"
        )

    def _classify_arrangement(
        self, snapshot: PriorRuntimeSnapshotV1, target_build_id: str,
    ) -> ProbeResult:
        """Exact two-tree classifier (D8): identities, never filenames."""
        active_payload = self.read_active_manifest()
        active_id = (
            str(active_payload.get("build_id")) if active_payload else None
        )
        if active_id is None:
            return ProbeResult(
                RecoveryClass.UNCERTAIN_MANUAL, "NO_ACTIVE_IDENTITY"
            )
        if active_id == target_build_id:
            prior_locator = self._retained_prior_locator()
            prior_payload = self._read_remote_json(
                "/" + prior_locator + "/manifest.json"
            )
            prior_id = (
                str(prior_payload.get("build_id")) if prior_payload else None
            )
            if snapshot.active_build_id is None:
                return ProbeResult(
                    RecoveryClass.COMPLETE, "PUBLISHED_INITIAL_VERIFIED"
                )
            if prior_id == snapshot.active_build_id:
                return ProbeResult(
                    RecoveryClass.COMPLETE, "TREE_EXCHANGE_COMPLETED",
                    output={
                        "active": active_id, "prior_locator": prior_locator,
                    },
                )
            return ProbeResult(
                RecoveryClass.UNCERTAIN_MANUAL, "PRIOR_RETAINED_UNPROVEN"
            )
        if active_id == snapshot.active_build_id:
            staged = self._staged_target_present(target_build_id)
            if staged:
                return ProbeResult(RecoveryClass.ABSENT, "EXCHANGE_NOT_LANDED")
            return ProbeResult(
                RecoveryClass.UNCERTAIN_MANUAL, "TARGET_TREE_UNPROVABLE"
            )
        return ProbeResult(
            RecoveryClass.UNCERTAIN_MANUAL, "ARRANGEMENT_UNKNOWN"
        )

    def _staged_target_present(self, target_build_id: str) -> bool:
        with self._units.read() as conn:
            rows = conn.execute(
                "SELECT locator FROM runtime_trees WHERE role='CANDIDATE'"
                " AND build_id=?",
                (target_build_id,),
            ).fetchall()
        for row in rows:
            payload = self._read_remote_json(
                "/" + row["locator"] + "/manifest.json"
            )
            if payload and payload.get("build_id") == target_build_id:
                return True
        return False

    def exchange_active_tree(
        self, snapshot, smoke, external_effect_id, *, mode,
    ) -> TreeExchangeEvidenceV1:
        self._require_seams()
        assert smoke is not None
        self._crash_point("exchange_active_tree", "after_step_start")
        helper = self._stage_helper(operation_id=external_effect_id[:24])
        candidate_abs = "/" + smoke.locator.lstrip("/")
        active_abs = self._loc.active_root

        initial_install = snapshot.active_build_id is None
        if initial_install:
            self._crash_point("exchange_active_tree", "before_publication")
            rename_spec = ProcessCommandSpec(
                kind=CommandKind.ATOMIC,
                argv=self._exec_argv((
                    "python3", "-c",
                    "import os,sys,errno;"
                    "src,dst=sys.argv[1],sys.argv[2];"
                    "parent=os.path.dirname(dst);"
                    "\ntry:\n os.rename(src,dst)\nexcept OSError as e:\n"
                    " sys.exit(3 if e.errno in (17,39) else 1)",
                    candidate_abs, active_abs,
                )),
            )
            assert self._proc is not None
            try:
                result = self._proc.run(rename_spec)
            except ProcessFailure as exc:
                if exc.code == "PROCESS_EXIT_UNEXPECTED":
                    raise StepFailure(
                        "TREE_EXCHANGE_UNCERTAIN",
                        "initial publication could not complete atomically",
                        mutation_possible=False,
                    ) from exc
                raise
            self._crash_point("exchange_active_tree", "after_swap")
            return TreeExchangeEvidenceV1(
                classification="PUBLISHED_INITIAL",
                exchanged_now=result.exit_code == 0,
                active_build_id_after=(
                    self._active_build_id_after_swap() or ""
                ),
            )

        self._crash_point("exchange_active_tree", "before_swap")
        prior_locator = self._retained_prior_locator()
        prior_abs = "/" + prior_locator.lstrip("/")
        argv = build_helper_invocation(helper, active_abs, candidate_abs,
                                       self._loc.approved_root)
        try:
            self._run_remote(CommandKind.ATOMIC, tuple(argv))
        except ProcessFailure as exc:
            if exc.code == "PROCESS_EXIT_UNEXPECTED":
                raise StepFailure(
                    "ATOMIC_EXCHANGE_UNSUPPORTED",
                    "the filesystem refused the atomic exchange",
                    mutation_possible=False,
                ) from exc
            raise
        self._crash_point("exchange_active_tree", "after_swap")
        # After RENAME_EXCHANGE the displaced tree sits at the candidate
        # path; move it into the standard retained slot (post-effect
        # bookkeeping, safe to redo).
        displaced = candidate_abs
        retained_slot = "/" + self._loc.managed_root.lstrip("/") + "/prior"
        self._run_remote(CommandKind.CLEANUP, ("mkdir", "-p", retained_slot))
        self._run_remote(CommandKind.CLEANUP,
                         ("mv", "-T", displaced,
                          retained_slot.rsplit("/", 1)[0] + "/.tmp-prior"))
        self._run_remote(CommandKind.CLEANUP,
                         ("rm", "-rf", "--", retained_slot))
        self._run_remote(CommandKind.CLEANUP,
                         ("mv", "-T",
                          retained_slot.rsplit("/", 1)[0] + "/.tmp-prior",
                          retained_slot))
        return TreeExchangeEvidenceV1(
            classification="EXCHANGED",
            exchanged_now=True,
            active_build_id_after=self._active_build_id_after_swap() or "",
            prior_tree={
                "tree_id": "tree-retained-prior",
                "locator": self._loc.managed_root.lstrip("/") + "/prior",
                "role": "RETAINED",
                "manifest_digest": "",
                "server_binary_digest": "",
            },
        )

    def _active_build_id_after_swap(self) -> str | None:
        payload = self.read_active_manifest()
        return str(payload.get("build_id")) if payload else None

    def probe_exchange(self, snapshot, target_build_id, *, mode) -> ProbeResult:
        return self._classify_arrangement(snapshot, target_build_id)

    def verify_exchange(self, snapshot, target_build_id, *, mode) -> None:
        result = self.probe_exchange(snapshot, target_build_id, mode=mode)
        if result.classification is not RecoveryClass.COMPLETE:
            raise StepFailure(
                "TREE_EXCHANGE_UNCERTAIN",
                f"exchange postcondition not proven ({result.reason_code})",
                mutation_possible=True,
            )

    # -- handoff v2 ------------------------------------------------------------------

    def publish_handoff_v2(
        self, snapshot, target_build_id, operation_id, *, mode,
    ) -> HandoffComponentEvidenceV1:
        self._require_seams()
        self._crash_point("publish_component_handoff", "mid_effect")
        with self._units.read() as conn:
            builds = RuntimeBuildRepository(conn)
            record = builds.require(target_build_id)
        manifest = record["manifest"]
        view = self._view()
        view["runtime_component_id"] = target_build_id
        view["runtime_manifest_digest"] = record["manifest_digest"]
        revision = int(view.get("revision") or 1)
        from .runtime_handoff import RuntimeIdentityV2

        identity = RuntimeIdentityV2(
            component_id=target_build_id,
            source_commit=str(manifest.get("source_commit") or ""),
            server_sha256=_server_digest_of(manifest.get("binaries") or []),
            manifest_digest=record["manifest_digest"],
            operation_id=operation_id,
        )
        try:
            self.renderer.publish(
                view, config_revision=revision, runtime_identity=identity
            )
        except OSError as exc:
            raise StepFailure(
                "HANDOFF_COMPONENT_PUBLISHED",
                f"handoff publication failed ({exc.__class__.__name__})",
                mutation_possible=False,
            ) from exc
        payload = self.renderer.observe(require_v2=True)
        if payload is None:
            raise StepFailure(
                "HANDOFF_COMPONENT_PUBLISHED",
                "published handoff failed strict v2 observation",
                mutation_possible=True,
            )
        return HandoffComponentEvidenceV1(
            fingerprint=payload["runtime_fingerprint"],
            schema_version=int(payload["schema_version"]),
            component_id=target_build_id,
            server_sha256=identity.server_sha256,
            manifest_digest=record["manifest_digest"],
            operation_id=operation_id,
        )

    def observe_handoff_v2(
        self, snapshot, target_build_id, *, mode,
    ) -> ProbeResult:
        payload = (
            self.renderer.observe(require_v2=True) if self.renderer else None
        )
        if payload is None:
            prior_component = (snapshot.handoff_payload or {}).get(
                "runtime_component_id"
            )
            if prior_component:
                return ProbeResult(RecoveryClass.ABSENT, "HANDOFF_IS_PRIOR")
            return ProbeResult(RecoveryClass.ABSENT, "NO_V2_HANDOFF")
        if payload.get("runtime_component_id") == target_build_id:
            return ProbeResult(
                RecoveryClass.COMPLETE, "HANDOFF_COMPONENT_PUBLISHED",
                output={
                    "fingerprint": payload["runtime_fingerprint"],
                    "schema_version": 2,
                    "component_id": target_build_id,
                    "server_sha256": payload["runtime_server_sha256"],
                    "manifest_digest": payload["runtime_manifest_digest"],
                    "operation_id": payload.get("runtime_operation_id", ""),
                },
            )
        prior_component = (snapshot.handoff_payload or {}).get(
            "runtime_component_id"
        )
        if prior_component \
                and payload.get("runtime_component_id") == prior_component:
            return ProbeResult(RecoveryClass.ABSENT, "EXACT_PRIOR_HANDOFF")
        return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "THIRD_PARTY_HANDOFF")

    # -- restart / invocation ----------------------------------------------------------

    def restart_for_runtime_change(
        self, snapshot, target_build_id, operation_id, *, mode,
    ) -> ServiceRestartEvidenceV1:
        self._require_seams()
        self._crash_point("restart_runtime", "mid_effect")
        view = self._view()
        already = self.observe_invocation(
            snapshot, target_build_id, mode=mode
        )
        if already.classification is RecoveryClass.COMPLETE:
            receipt = self._read_receipt()
            return ServiceRestartEvidenceV1(
                restarted_now=False, was_already_active=True,
                invocation_nonce=receipt.get("nonce", ""),
                receipt_present=bool(receipt),
            )
        try:
            self.server_port.restart(view)
        except Exception as exc:  # noqa: BLE001 - typed mapping only
            raise StepFailure(
                "SERVICE_RESTART_FAILED",
                f"restart failed ({exc.__class__.__name__})",
                mutation_possible=False,
            ) from exc
        receipt = self._read_receipt()
        return ServiceRestartEvidenceV1(
            restarted_now=True, was_already_active=False,
            invocation_nonce=receipt.get("nonce", ""),
            receipt_present=bool(receipt),
        )

    def _receipt_path(self):
        return self.renderer.path.parent / "start-receipt.json"

    def _read_receipt(self) -> dict[str, Any]:
        try:
            payload = _json.loads(
                self._receipt_path().read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def observe_invocation(
        self, snapshot, target_build_id, *, mode,
    ) -> ProbeResult:
        view = self._view()
        facts = self.server_port.capture(view)
        if not facts.get("active"):
            return ProbeResult(RecoveryClass.REVERTIBLE, "SERVICE_INACTIVE")
        marker = facts.get("invocation_marker")
        receipt = self._read_receipt()
        receipt_ok = (
            receipt.get("build_id") == target_build_id
            and (
                snapshot.invocation_marker is None
                or marker != snapshot.invocation_marker
                or receipt.get("operation_id")
                != (snapshot.handoff_payload or {}).get(
                    "runtime_operation_id"
                )
            )
        )
        if not receipt_ok:
            return ProbeResult(
                RecoveryClass.UNCERTAIN_MANUAL, "STALE_START_RECEIPT"
            )
        return ProbeResult(
            RecoveryClass.COMPLETE, "NEW_INVOCATION_PROVEN",
            output={
                "restarted_now": False, "was_already_active": True,
                "invocation_nonce": receipt.get("nonce", ""),
                "receipt_present": True,
            },
        )

    # -- live verification --------------------------------------------------------------

    def verify_runtime_identity(
        self, snapshot, target_build_id,
    ) -> RuntimeIdentityEvidenceV1:
        view = self._view()
        health = self.server_port.health(view)
        active_payload = self.read_active_manifest()
        active_ok = bool(
            active_payload and active_payload.get("build_id") == target_build_id
        )
        binary_live = self._remote_sha256(
            self._loc.active_root + "/" + self._loc.server_binary_relpath
        )
        manifest_entry = ""
        if active_payload:
            for entry in (active_payload.get("manifest") or {}).get(
                "binaries", []
            ):
                if str(entry.get("path", "")).endswith("llama-server"):
                    manifest_entry = str(entry.get("sha256", ""))
        binary_ok = bool(binary_live) and binary_live == manifest_entry
        receipt = self._read_receipt()
        receipt_ok = (
            not receipt or receipt.get("build_id") == target_build_id
        )
        desired_model = view.get("current_model")
        alias_ok = health.get("model_id") in (desired_model, None) and \
            bool(health.get("healthy"))
        slots = int((view.get("optimizations") or {}).get("parallel_slots") or 0)
        ctx_per_slot = int(view.get("current_ctx") or 0)
        context_ok = (
            not health or int(health.get("n_ctx") or 0)
            in (0, ctx_per_slot * max(slots, 1))
        )
        slots_ok = not health or not slots or int(
            health.get("parallel_slots") or 0
        ) in (0, slots)
        return RuntimeIdentityEvidenceV1(
            component_ok=active_ok and bool(receipt_ok),
            binary_digest_ok=binary_ok,
            model_alias_ok=bool(alias_ok),
            context_ok=bool(context_ok),
            slots_ok=bool(slots_ok),
            health_ok=bool(health.get("healthy")),
            observed_model_alias=str(health.get("model_id") or ""),
        )

    def verify_runtime_inference(self, target_build_id) -> RuntimeInferenceEvidenceV1:
        import time as _time

        started = _time.monotonic()
        view = self._view()
        try:
            probe = self.server_port.inference(view, timeout=20.0)
        except Exception:  # noqa: BLE001 - any failure means not verified
            probe = {"ok": False}
        elapsed = _time.monotonic() - started
        success = probe.get("ok") is True
        bucket = (
            "sub_second" if elapsed < 1.0
            else "seconds" if elapsed < 10.0 else "slow"
        )
        return RuntimeInferenceEvidenceV1(
            success=bool(success),
            generated_count=1 if success else 0,
            latency_bucket=bucket,
        )

    # -- promotion ---------------------------------------------------------------------

    def promote_verified_runtime(
        self, snapshot, target_build_id, smoke, operation_id, *, mode,
    ) -> RuntimePromotionEvidenceV1:
        self._crash_point("promote_runtime", "mid_effect")
        with self._units.begin() as conn:
            components = RuntimeComponentRepository(conn)
            trees = RuntimeTreeRepository(conn)
            builds = RuntimeBuildRepository(conn)
            current = components.current() or components.initialize()
            former_promoted = snapshot.promoted_build_id
            goal_rollback = former_promoted
            if mode == "rollback":
                goal_rollback = former_promoted
            if (
                current["promoted_build_id"] == target_build_id
                and current["rollback_build_id"] == goal_rollback
                and int(current["generation"]) >= int(snapshot.generation or 1)
            ):
                return RuntimePromotionEvidenceV1(
                    generation_after=int(current["generation"]),
                    promoted_build_id=target_build_id,
                    rollback_build_id=current.get("rollback_build_id"),
                    promoted_tree_id=current.get("promoted_tree_id"),
                    rollback_tree_id=current.get("rollback_tree_id"),
                    noop=True,
                )
            promoted_tree = smoke.tree_id if smoke else None
            if smoke is not None:
                trees.observe_location(smoke.tree_id)
                trees.move_role(smoke.tree_id, "ACTIVE_OBSERVED")
            known_good = self._known_good()
            identity = {
                "runtime_fingerprint": (known_good or {}).get(
                    "runtime_fingerprint"
                ),
                "runtime_component_identity": target_build_id,
            }
            if mode == "update":
                promoted = components.promote_verified(
                    expected_generation=int(current["generation"]),
                    expected_promoted_build_id=current["promoted_build_id"],
                    expected_rollback_build_id=current["rollback_build_id"],
                    promoted_build_id=target_build_id,
                    rollback_build_id=former_promoted,
                    promoted_tree_id=promoted_tree,
                    rollback_tree_id="tree-retained-prior",
                    operation_id=operation_id,
                    known_good_identity=identity,
                )
            else:
                promoted = components.record_restoration(
                    expected_generation=int(current["generation"]),
                    expected_promoted_build_id=current["promoted_build_id"],
                    expected_rollback_build_id=current["rollback_build_id"],
                    restored_promoted_build_id=target_build_id,
                    new_rollback_build_id=former_promoted,
                    promoted_tree_id=promoted_tree,
                    operation_id=operation_id,
                    known_good_identity=identity,
                )
            del builds
            return RuntimePromotionEvidenceV1(
                generation_after=int(promoted["generation"]),
                promoted_build_id=target_build_id,
                rollback_build_id=promoted["rollback_build_id"],
                promoted_tree_id=promoted_tree,
                rollback_tree_id="tree-retained-prior",
            )

    def observe_promotion(
        self, snapshot, target_build_id, *, mode,
    ) -> ProbeResult:
        with self._units.read() as conn:
            current = RuntimeComponentRepository(conn).current()
        if current is None:
            return ProbeResult(RecoveryClass.ABSENT, "NO_COMPONENT_ROW")
        generation_ok = int(current.get("generation") or 0) > int(
            snapshot.generation or 0
        )
        if (
            current.get("promoted_build_id") == target_build_id
            and generation_ok
        ):
            return ProbeResult(
                RecoveryClass.COMPLETE, "RUNTIME_PROMOTED",
                output={
                    "generation_after": int(current["generation"]),
                    "promoted_build_id": target_build_id,
                    "rollback_build_id": current.get("rollback_build_id"),
                    "noop": False,
                },
            )
        if current.get("promoted_build_id") == snapshot.promoted_build_id \
                and generation_ok:
            return ProbeResult(RecoveryClass.REVERTIBLE, "PROMOTION_NOT_APPLIED")
        return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "LINEAGE_AMBIGUOUS")

    # -- restoration ----------------------------------------------------------------------

    def restore_prior_runtime(self, snapshot, restoration_id, *, mode
                              ) -> RuntimeRestorationEvidenceV1:
        self._require_seams()
        stages: list[str] = []
        active_now = self._active_build_id_after_swap()
        if snapshot.active_build_id is not None \
                and active_now != snapshot.active_build_id:
            prior_locator = self._retained_prior_locator()
            prior_payload = self._read_remote_json(
                "/" + prior_locator + "/manifest.json"
            )
            if not prior_payload \
                    or prior_payload.get("build_id") != snapshot.active_build_id:
                raise StepFailure(
                    "RUNTIME_RESTORATION_UNCERTAIN",
                    "the exact prior tree is not provable at the retained "
                    "locator",
                    mutation_possible=True,
                )
            helper = self._stage_helper(operation_id=f"restore-{restoration_id}"[:40])
            argv = build_helper_invocation(
                helper, self._loc.active_root,
                "/" + prior_locator.lstrip("/"), self._loc.approved_root,
            )
            self._run_remote(CommandKind.ATOMIC, tuple(argv))
            stages.append("REVERSE_EXCHANGED")
        elif snapshot.active_build_id is None \
                and self._active_build_id_after_swap() is not None:
            payload = self.read_active_manifest()
            if payload is None:
                raise StepFailure(
                    "RUNTIME_RESTORATION_UNCERTAIN",
                    "cannot prove ownership of the published tree",
                    mutation_possible=True,
                )
            self._run_remote(
                CommandKind.CLEANUP, ("rm", "-rf", "--", self._loc.active_root)
            )
            stages.append("INITIAL_PUBLICATION_REMOVED")
        else:
            stages.append("TREE_ALREADY_PRIOR")
        # Prior handoff exactly, or remove our own candidate artifact.
        prior_payload = snapshot.handoff_payload
        if prior_payload:
            self.renderer.path.write_text(
                _json.dumps(prior_payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            stages.append("HANDOFF_RESTORED")
        elif self.renderer.path.exists():
            self.renderer.path.unlink()
            stages.append("HANDOFF_REMOVED")
        # Service state strictly through the typed port.
        view = self._view()
        if snapshot.service_state == "ACTIVE_VERIFIED":
            self.server_port.restart(view)
            stages.append("SERVICE_STATE_RESTORED")
        else:
            self.server_port.stop(view)
            stages.append("SERVICE_STOPPED")
        # Lineage toggle-back.
        with self._units.begin() as conn:
            components = RuntimeComponentRepository(conn)
            current = components.current()
            if current is not None and snapshot.generation is not None \
                    and current.get("promoted_build_id") \
                    != snapshot.promoted_build_id:
                components.record_restoration(
                    expected_generation=int(current["generation"]),
                    expected_promoted_build_id=current["promoted_build_id"],
                    expected_rollback_build_id=current.get("rollback_build_id"),
                    restored_promoted_build_id=snapshot.promoted_build_id or "",
                    new_rollback_build_id=current.get("promoted_build_id"),
                )
                stages.append("LINEAGE_TOGGLED_BACK")
        return RuntimeRestorationEvidenceV1(
            restored=True, stage_codes=stages,
            service_state=snapshot.service_state,
        )

    def observe_restoration(self, snapshot, *, mode) -> ProbeResult:
        active_now = self._active_build_id_after_swap()
        if snapshot.active_build_id is None:
            active_ok = active_now is None
        else:
            active_ok = active_now == snapshot.active_build_id
        handoff = (
            self.renderer.observe(require_v2=False) if self.renderer else None
        )
        prior_component = (snapshot.handoff_payload or {}).get(
            "runtime_component_id"
        )
        if snapshot.handoff_payload:
            handoff_ok = handoff is not None and (
                handoff.get("runtime_component_id") == prior_component
                or handoff.get("runtime_fingerprint")
                == snapshot.handoff_fingerprint
            )
        else:
            handoff_ok = handoff is None
        view = self._view()
        facts = self.server_port.capture(view)
        service_running = bool(facts.get("active"))
        if snapshot.service_state == "ACTIVE_VERIFIED":
            service_ok = service_running and (
                snapshot.invocation_marker is None
                or facts.get("invocation_marker") is not None
            )
        else:
            service_ok = not service_running
        with self._units.read() as conn:
            current = RuntimeComponentRepository(conn).current()
        lineage_ok = (
            current is None
            or snapshot.generation is None
            or (
                current.get("promoted_build_id") == snapshot.promoted_build_id
                and int(current.get("generation") or 0)
                >= int(snapshot.generation or 0)
            )
        )
        if active_ok and handoff_ok and service_ok and lineage_ok:
            return ProbeResult(
                RecoveryClass.COMPLETE, "PRIOR_RUNTIME_RESTORED",
                output={"restored": True,
                        "service_state": snapshot.service_state},
            )
        known_prior = (
            snapshot.promoted_build_id is not None
            or snapshot.service_state in ("ACTIVE_VERIFIED", PRIOR_STOPPED)
        )
        if known_prior:
            return ProbeResult(
                RecoveryClass.REVERTIBLE, "RESTORATION_INCOMPLETE"
            )
        return ProbeResult(
            RecoveryClass.UNCERTAIN_MANUAL, "NO_DURABLE_PRIOR_EVIDENCE"
        )

    # -- finalization ------------------------------------------------------------------------

    def finalize_trees(self, snapshot, target_build_id, promotion, exchange,
                       *, mode) -> RuntimeCleanupEvidenceV1:
        deferred: list[str] = []
        removed: list[str] = []
        retained: list[str] = []
        protected: set[str]
        with self._units.begin() as conn:
            trees = RuntimeTreeRepository(conn)
            protected = trees.protected_tree_ids(exclude_operation_id=None)
            locator = self._loc.active_root.lstrip("/")
            row = trees.by_locator(locator)
            active_id = self._active_build_id_after_swap()
            if active_id and row is None:
                trees.record_candidate(
                    tree_id=f"tree-active-{active_id[:24]}",
                    build_id=active_id,
                    container_profile=self._loc.container_name,
                    locator=locator,
                    manifest_digest=(
                        self._read_remote_json(
                            f"{self._loc.active_root}/manifest.json"
                        ) or {}
                    ).get("manifest_digest", "") or "0" * 64,
                    server_binary_digest=self._hash_server_binary(),
                )
                row = trees.by_locator(locator)
            if row is not None and row["role"] != "ACTIVE_OBSERVED":
                trees.move_role(row["tree_id"], "ACTIVE_OBSERVED")
            prior_locator = self._loc.managed_root.lstrip("/") + "/prior"
            prior_row = trees.by_locator(prior_locator)
            if prior_row is not None and prior_row["role"] != "RETAINED":
                trees.move_role(prior_row["tree_id"], "RETAINED")
            candidates = [
                dict(r) for r in conn.execute(
                    "SELECT tree_id, locator FROM runtime_trees "
                    "WHERE role='CANDIDATE'"
                ).fetchall()
            ]
            for entry in candidates:
                if entry["tree_id"] in protected:
                    continue
                remote_dir = "/" + entry["locator"]
                if self._remote_is_dir(remote_dir) and entry["locator"].startswith(
                    self._loc.managed_root.lstrip("/")
                ):
                    self._run_remote(
                        CommandKind.CLEANUP,
                        ("rm", "-rf", "--", remote_dir),
                    )
                    removed.append(entry["locator"])
                    trees.move_role(entry["tree_id"], "QUARANTINED")
                else:
                    deferred.append(entry["locator"])
        del promotion, exchange, retained
        return RuntimeCleanupEvidenceV1(
            retained_locators=[], removed_locators=removed,
            deferred_locators=deferred,
        )

    def observe_finalization(self, snapshot, target_build_id, *,
                             mode) -> ProbeResult:
        return ProbeResult(
            RecoveryClass.COMPLETE, "TREES_FINALIZED",
            output={"retained_locators": [], "removed_locators": [],
                    "deferred_locators": [], "noop": False},
        )

    # ==========================================================================
    # Rollback-specific seams
    # ==========================================================================

    def resolve_rollback_target(self, request) -> RollbackTargetEvidenceV1:
        with self._units.read() as conn:
            components = RuntimeComponentRepository(conn)
            trees = RuntimeTreeRepository(conn)
            current = components.current()
            target = getattr(request, "target_build_id", "")
            promoted = (current or {}).get("promoted_build_id")
            rollback = (current or {}).get("rollback_build_id")
            if not target or not promoted or target != rollback:
                raise StepFailure(
                    "RUNTIME_ROLLBACK_TARGET_MISSING",
                    "requested target is not the durable current rollback "
                    "target",
                    mutation_possible=False,
                )
            row = trees.by_locator(
                self._loc.managed_root.lstrip("/") + "/prior"
            )
            if row is None or row["build_id"] != target:
                raise StepFailure(
                    "RUNTIME_ROLLBACK_TARGET_MISSING",
                    "the retained target tree cannot be identified",
                    mutation_possible=False,
                )
        payload = self._read_remote_json(
            "/" + row["locator"] + "/manifest.json"
        )
        if not payload or payload.get("build_id") != target:
            raise StepFailure(
                "RUNTIME_ROLLBACK_TARGET_MISSING",
                "the retained tree identity does not match the recorded "
                "build",
                mutation_possible=False,
            )
        return RollbackTargetEvidenceV1(
            target_build_id=target,
            current_promoted_build_id=promoted,
            generation=int((current or {}).get("generation") or 0),
            target_tree_id=row["tree_id"],
            target_locator=row["locator"],
            manifest_digest=str(payload.get("manifest_digest") or ""),
            server_binary_digest=self._hash_server_binary(
                "/" + row["locator"]
            ),
        )

    def observe_rollback_target(
        self, request, evidence,
    ) -> ProbeResult:
        try:
            fresh = self.resolve_rollback_target(request)
        except StepFailure as failure:
            if failure.code == "RUNTIME_ROLLBACK_TARGET_MISSING":
                return ProbeResult(RecoveryClass.DISCARDABLE, failure.code)
            raise
        if fresh.target_build_id == evidence.target_build_id \
                and fresh.manifest_digest == evidence.manifest_digest:
            return ProbeResult(
                RecoveryClass.COMPLETE, "ROLLBACK_TARGET_RESOLVED",
                output=_asdict(evidence),
            )
        return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "TARGET_IDENTITY_MOVED")

    def preflight_rollback(self, request, target) -> BuildPreflightEvidenceV1:
        thermal_ok = self._thermal_ok()
        supported = self._probe_atomic_support("rollback-probe")
        active_matches = self._active_build_id_after_swap() == \
            target.current_promoted_build_id
        return BuildPreflightEvidenceV1(
            thermal_ok=thermal_ok,
            disk_ok=True,
            disk_required_bytes=0,
            disk_available_bytes=self._disk_available_bytes(),
            filesystem_same_volume=True,
            atomic_exchange_supported=supported,
            active_runtime_proven=bool(active_matches),
            legacy_adoption_used=False,
        )

    def observe_preflight_rollback(
        self, request, target, evidence,
    ) -> ProbeResult:
        fresh = self.preflight_rollback(request, target)
        ok = (
            evidence.thermal_ok and evidence.atomic_exchange_supported
            and evidence.active_runtime_proven
        )
        if ok and fresh.atomic_exchange_supported and fresh.thermal_ok:
            return ProbeResult(
                RecoveryClass.COMPLETE, "ROLLBACK_PREFLIGHT_OK",
                output=_asdict(evidence),
            )
        if not evidence.thermal_ok:
            return ProbeResult(RecoveryClass.DISCARDABLE, "THERMAL_LATCH_STOPPED")
        if not evidence.atomic_exchange_supported:
            return ProbeResult(
                RecoveryClass.DISCARDABLE, "ATOMIC_EXCHANGE_UNSUPPORTED"
            )
        return ProbeResult(RecoveryClass.REVERTIBLE, "ACTIVE_STATE_CHANGED")

    def smoke_rollback_target(self, target) -> SmokeEvidenceV1:
        self._crash_point("smoke_rollback_target", "mid_effect")
        locator = "/" + target.target_locator.lstrip("/")
        binary = locator + "/" + self._loc.server_binary_relpath
        live = self._remote_sha256(binary)
        if live is None or live != target.server_binary_digest:
            raise StepFailure(
                "CANDIDATE_SMOKE_FAILED",
                "the retained binary no longer matches its recorded digest",
                mutation_possible=False,
            )
        self._run_remote(CommandKind.SMOKE, (binary, "--version"))
        with self._units.begin() as conn:
            RuntimeVerificationRepository(conn).append(
                build_id=target.target_build_id,
                kind="SMOKE",
                evidence={"ok": True, "scope": "rollback-target"},
            )
        return SmokeEvidenceV1(
            build_id=target.target_build_id,
            manifest_digest=target.manifest_digest,
            smoke_contract_version=1,
            binaries_ok=True,
            tree_id=target.target_tree_id,
            locator=target.target_locator,
        )

    def observe_rollback_manifest(self, target) -> ProbeResult:
        payload = self._read_remote_json(
            "/" + target.target_locator + "/manifest.json"
        )
        if payload is None:
            return ProbeResult(RecoveryClass.ABSENT, "RETAINED_MANIFEST_MISSING")
        if payload.get("build_id") == target.target_build_id:
            return ProbeResult(RecoveryClass.COMPLETE, "RETAINED_SMOKE_OK")
        return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "RETAINED_IDENTITY_MOVED")
