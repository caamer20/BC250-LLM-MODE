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
    CODE_THERMAL_LATCH_STOPPED,
    CODE_ACTIVE_TREE_CHANGED,
    CODE_SOURCE_COMMIT_UNAVAILABLE,
    DEFAULT_REQUESTED_REF,
    PRIOR_ABSENT,
    PRIOR_STOPPED,
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
from .runtime_process import CommandKind, ProcessCommandSpec, ProcessFailure, ProcessResult
from .runtime_process_helper import PROCESS_HELPER_SOURCE, PROCESS_HELPER_DIGEST

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
                                          "-DCMAKE_BUILD_TYPE=Release",
                                          "-DBUILD_SHARED_LIBS=OFF"),
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

    def _exec_argv(self, remote_argv: tuple[str, ...], *, stdin: bool = False) -> tuple[str, ...]:
        return (
            self._podman, "exec", *(("--interactive",) if stdin else ()), "--user", "root",
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
        supervise = cancel_requested is not None and kind in (CommandKind.FETCH, CommandKind.CONFIGURE, CommandKind.COMPILE)
        if supervise:
            from .runtime_process import DEFAULT_TIMEOUTS
            helper = self._stage_process_helper()
            remote_argv = ("python3", helper, str(timeout_seconds or DEFAULT_TIMEOUTS[kind.value]), "--", *remote_argv)
        spec = ProcessCommandSpec(
            kind=kind,
            argv=self._exec_argv(remote_argv, stdin=bool(stdin_payload) or supervise),
            timeout_seconds=timeout_seconds,
            expected_exit_codes=expected_exit_codes,
            stdin_payload=stdin_payload,
            stdin_heartbeat=supervise,
        )
        return self._proc.run(spec, cancel_requested=cancel_requested)

    def _stage_process_helper(self) -> str:
        directory = f"{self._loc.managed_root}/helper-process-{PROCESS_HELPER_DIGEST[:16]}"
        self._ensure_owned_directory(directory)
        destination = directory + "/runtime-process-helper.py"
        result = self._run_remote(CommandKind.SMOKE, (
            "python3", "-c", "import os,sys,pathlib,hashlib,tempfile;"
            "p=pathlib.Path(sys.argv[1]);data=sys.stdin.buffer.read();"
            "fd,tmp=tempfile.mkstemp(dir=p.parent);f=os.fdopen(fd,'wb');"
            "f.write(data);f.flush();os.fsync(fd);f.close();os.chmod(tmp,0o500);"
            "os.replace(tmp,p);print(hashlib.sha256(p.read_bytes()).hexdigest())",
            destination,
        ), stdin_payload=PROCESS_HELPER_SOURCE)
        if result.stdout_tail.strip() != PROCESS_HELPER_DIGEST:
            raise StepFailure("BUILD_ENVIRONMENT_UNPROVEN", "guest supervisor digest differs", mutation_possible=False)
        return destination

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
        return out if re.fullmatch(r"[0-9a-f]{64}", out) else None

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
            resolution="COMMIT" if _COMMIT_RE.fullmatch(requested_ref) else "PIN",
            prepared_build_id=self._prepared_build_for_source(commit),
        )

    def _resolve_ref_to_commit(self, requested_ref: str) -> str | None:
        # An exact object ID already names immutable content. Availability and
        # its commit type are proved in the heartbeat-enabled fetch step.
        if _COMMIT_RE.fullmatch(requested_ref):
            return requested_ref
        if requested_ref.startswith("refs/"):
            if not requested_ref.startswith(("refs/tags/", "refs/heads/")):
                return None
            refs = [requested_ref]
        else:
            refs = [f"refs/tags/{requested_ref}", f"refs/heads/{requested_ref}"]
        try:
            for ref in refs:
                self._run_remote(CommandKind.OBSERVE, ("git", "check-ref-format", ref))
            result = self._run_remote(CommandKind.OBSERVE, (
                "git", "ls-remote", "--exit-code", UPSTREAM_REPOSITORY,
                *refs, *(ref + "^{}" for ref in refs),
            ))
        except ProcessFailure:
            return None
        if result.truncated_stdout:
            return None
        advertised = {}
        for line in result.stdout_tail.splitlines():
            fields = line.split()
            if len(fields) != 2 or not _COMMIT_RE.fullmatch(fields[0]):
                return None
            advertised[fields[1]] = fields[0]
        matches = [ref for ref in refs if ref in advertised]
        # A tag and branch with the same short name require an explicit ref.
        if len(matches) != 1:
            return None
        ref = matches[0]
        commit = advertised.get(ref + "^{}", advertised[ref])
        return commit if _COMMIT_RE.fullmatch(commit) else None

    def observe_source_resolution(
        self, request: Any, evidence: ResolvedRuntimeSourceV1
    ) -> ProbeResult:
        current = self._resolve_ref_to_commit(evidence.requested_ref)
        if evidence.prepared_build_id and self._prepared_build_for_source(evidence.source_commit) != evidence.prepared_build_id:
            return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "PREPARED_RUNTIME_CHANGED")
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

    def _observed_tree(self, path: str, build_id: str | None = None) -> dict[str, Any] | None:
        """Match the envelope AND live binary to the immutable durable record."""
        payload = self._read_remote_json(f"{path}/manifest.json")
        if not payload or (build_id is not None and payload.get("build_id") != build_id):
            return None
        try:
            with self._units.read() as conn:
                record = RuntimeBuildRepository(conn).require(str(payload.get("build_id")))
            manifest = payload.get("manifest")
            if not isinstance(manifest, dict) or payload.get("manifest_digest") != record["manifest_digest"]:
                return None
            if record["provenance_class"] == "LEGACY_UNVERIFIED":
                if manifest != record["manifest"]:
                    return None
                expected = manifest.get("observed_server_sha256")
            else:
                actual_id, actual_digest = derive_build_id(manifest)
                if actual_id != record["build_id"] or actual_digest != record["manifest_digest"]:
                    return None
                expected = _server_digest_of(manifest.get("binaries") or [])
            if not expected or self._remote_sha256(f"{path}/{self._loc.server_binary_relpath}") != expected:
                return None
        except (RuntimeBuildError, ProcessFailure, ValueError, TypeError):
            return None
        return {"build_id": record["build_id"], "manifest_digest": record["manifest_digest"],
                "server_binary_digest": expected}

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
        if not self._recipe_matches(manifest):
            return False
        return self._observed_tree(self._loc.active_root, promoted) is not None

    def _recipe_matches(self, manifest: dict[str, Any]) -> bool:
        return (manifest.get("recipe_version") == RECIPE_VERSION
                and manifest.get("recipe_digest") == _digest(RECIPE_DIGEST_SEED)
                and manifest.get("cmake_generator") == self._cmake_generator
                and manifest.get("cmake_options") == list(self._cmake_options)
                and manifest.get("cmake_targets") == list(self._cmake_targets))

    def _prepared_build_for_source(self, source_commit: str | None = None) -> str | None:
        with self._units.read() as conn:
            component = RuntimeComponentRepository(conn).current() or {}
            if component.get("promoted_build_id") or component.get("rollback_build_id"):
                return None
            tree = RuntimeTreeRepository(conn).prepared_by_locator(
                self._loc.active_root.lstrip("/"), self._loc.container_name)
            if tree is None:
                return None
            record = RuntimeBuildRepository(conn).require(tree["build_id"])
        manifest = record["manifest"]
        if (record["provenance_class"] != "IMMUTABLE_SOURCE"
                or (source_commit is not None and record["source_commit"] != source_commit)
                or not self._recipe_matches(manifest)
                or not self._observed_tree(self._loc.active_root, record["build_id"])
                or not self._all_binaries_match(manifest)):
            return None
        return record["build_id"]

    def _all_binaries_match(self, manifest: dict[str, Any]) -> bool:
        """Bounded hashes/stat checks for the three regular owned executables."""
        binaries = manifest.get("binaries") or []
        expected_paths = {f"build/bin/{name}" for name in self._cmake_targets}
        if len(binaries) != len(expected_paths) or {b.get("path") for b in binaries} != expected_paths:
            return False
        script = (
            "import hashlib,json,os,pathlib,stat,sys;"
            "base=pathlib.Path(sys.argv[1]);root=pathlib.Path(sys.argv[2]);"
            "assert root in base.parents and base.resolve()==base;"
            "entries=json.loads(sys.argv[3]);paths=[base/e['path'] for e in entries];"
            "assert all(base in p.parents and not any(x.is_symlink() for x in (p,*p.parents) "
            "if x==base or base in x.parents) for p in paths);"
            "infos=[p.stat() for p in paths];"
            "assert all(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and s.st_size==e['size'] "
            "and stat.S_IMODE(s.st_mode)==int(e['mode'],8) and s.st_mode&0o111 "
            "for s,e in zip(infos,entries));"
            # At most three handles. Process exit also closes them on refusal.
            "streams=[p.open('rb') for p in paths];"
            "assert all(hashlib.file_digest(f,'sha256').hexdigest()==e['sha256'] "
            "for f,e in zip(streams,entries));"
            "[f.close() for f in streams];print('verified')"
        )
        try:
            return self._run_remote(CommandKind.OBSERVE, (
                "python3", "-c", script, self._loc.active_root, self._loc.approved_root,
                _json.dumps(binaries, sort_keys=True),
            )).stdout_tail.strip() == "verified"
        except ProcessFailure:
            return False

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
            ("python3", "-c", "import os,pathlib,sys;"
             "p=pathlib.Path(sys.argv[1]);root=pathlib.Path(sys.argv[2]);"
             "assert root in p.parents and '..' not in p.parts;"
             "assert not any(x.is_symlink() for x in [p,*p.parents] if root in x.parents);"
             "s=os.statvfs(p if p.exists() else root);print(s.f_bavail*s.f_frsize)",
             self._loc.managed_root, self._loc.approved_root),
        )
        lines = [l.strip() for l in result.stdout_tail.splitlines() if l.strip()]
        try:
            return int(lines[-1])
        except (IndexError, ValueError):
            return 0

    def _ensure_owned_directory(self, path: str) -> None:
        self._run_remote(CommandKind.PREFLIGHT, (
            "python3", "-c", "import os,pathlib,sys;"
            "p=pathlib.Path(sys.argv[1]);root=pathlib.Path(sys.argv[2]);"
            "assert p.is_absolute() and '..' not in p.parts;"
            "assert root in p.parents;"
            "assert not any(x.is_symlink() for x in [p,*p.parents] if root in x.parents);"
            "p.mkdir(mode=0o700,parents=True,exist_ok=True);"
            "assert p.is_dir() and p.stat().st_uid==os.geteuid();"
            "assert not p.stat().st_mode & 0o022",
            path, self._loc.approved_root,
        ))

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
        probe_dir = None
        try:
            self._ensure_owned_directory(self._loc.managed_root)
            probe_dir = self._run_remote(CommandKind.PREFLIGHT, (
                "mktemp", "-d", f"{self._loc.managed_root}/probe-XXXXXXXXXXXX",
            )).stdout_tail.strip()
            if not re.fullmatch(re.escape(self._loc.managed_root) + r"/probe-[A-Za-z0-9]+", probe_dir):
                probe_dir = None
                return False
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
                if probe_dir is not None:
                    self._run_remote(
                        CommandKind.CLEANUP, ("rm", "-rf", "--", probe_dir)
                    )
            except ProcessFailure:
                pass

    def _stage_helper(self, *, operation_id: str) -> str:
        """Copy the fixed helper into operation-owned space and VERIFY its
        digest remotely before it may ever execute."""
        if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", operation_id):
            raise StepFailure("ATOMIC_EXCHANGE_UNSUPPORTED", "invalid helper owner", mutation_possible=False)
        dest_dir = f"{self._loc.managed_root}/helper-{operation_id}"
        self._ensure_owned_directory(dest_dir)
        destination = f"{dest_dir}/bc250-exchange-helper.py"
        write = ProcessCommandSpec(
            kind=CommandKind.CLEANUP,
            argv=self._exec_argv((
                "python3", "-c",
                "import os,sys,pathlib,hashlib,tempfile;"
                "data=sys.stdin.buffer.read();"
                "p=pathlib.Path(sys.argv[1]);"
                "fd,tmp=tempfile.mkstemp(dir=p.parent);f=os.fdopen(fd,'wb');"
                "f.write(data);f.flush();os.fsync(f.fileno());f.close();"
                "os.chmod(tmp,0o500);os.replace(tmp,p);"
                "print(hashlib.sha256(p.read_bytes()).hexdigest())",
                destination,
            ), stdin=True),
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
        payload = self._observed_tree(self._loc.active_root)
        locator = self._loc.active_root.lstrip("/")
        if payload:
            build_id = str(payload["build_id"])
            with self._units.begin() as conn:
                trees = RuntimeTreeRepository(conn)
                row = trees.by_locator(locator)
                if row is None:
                    row = trees.record_candidate(
                        tree_id=f"tree-active-{_digest(build_id.encode())[:24]}",
                        build_id=build_id,
                        container_profile=self._loc.container_name,
                        locator=locator,
                        manifest_digest=str(payload["manifest_digest"]),
                        server_binary_digest=payload["server_binary_digest"],
                    )
                if row["build_id"] != build_id or row["server_binary_digest"] != payload["server_binary_digest"]:
                    raise StepFailure(CODE_ACTIVE_RUNTIME_UNPROVEN, "active tree registry differs", mutation_possible=False)
                trees.move_role(row["tree_id"], "ACTIVE_OBSERVED")
            return build_id
        if self._remote_exists_file(f"{self._loc.active_root}/manifest.json"):
            raise StepFailure(CODE_ACTIVE_RUNTIME_UNPROVEN, "active manifest or binary is unproven", mutation_possible=False)
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
        legacy_id = "legacy:llamacpp:" + server_digest
        locator = self._loc.active_root.lstrip("/")
        with self._units.begin() as conn:
            builds = RuntimeBuildRepository(conn)
            record = builds.create_legacy_backfill(
                legacy_id=legacy_id,
                metadata=metadata,
                source_commit=facts.get("commit_sha"),
                requested_ref=facts.get("describe"),
            )
            trees = RuntimeTreeRepository(conn)
            row = trees.by_locator(locator)
            if row is None:
                row = trees.record_candidate(
                    tree_id="tree-legacy-" + server_digest[:24],
                    build_id=legacy_id,
                    container_profile=self._loc.container_name,
                    locator=locator,
                    manifest_digest=record["manifest_digest"],
                    server_binary_digest=server_digest,
                    ownership_class="LEGACY_ADOPTED",
                )
            if row["build_id"] != legacy_id or row["server_binary_digest"] != server_digest:
                raise StepFailure(CODE_ACTIVE_RUNTIME_UNPROVEN, "legacy tree registry differs", mutation_possible=False)
            trees.move_role(row["tree_id"], "ACTIVE_OBSERVED")
        self._write_manifest_to_tree(self._loc.active_root, legacy_id, record["manifest_digest"], metadata)
        return legacy_id

    # ==========================================================================
    # Port: fetch / configure / compile / smoke
    # ==========================================================================

    def fetch_exact_commit(self, request: Any, source_commit: str, pulse: Any
                           ) -> FetchEvidenceV1:
        if not _COMMIT_RE.fullmatch(source_commit):
            raise StepFailure(CODE_SOURCE_COMMIT_UNAVAILABLE, "invalid commit", mutation_possible=False)
        checkout = f"{self._loc.sources_root}/worktrees/{source_commit}"
        observed = self.probe_checkout(source_commit)
        if observed.classification is RecoveryClass.COMPLETE:
            return FetchEvidenceV1(source_commit, checkout, "EXISTING", True)
        if observed.classification is RecoveryClass.UNCERTAIN_MANUAL:
            raise StepFailure(CODE_SOURCE_COMMIT_UNAVAILABLE, "checkout is not owned clean source", mutation_possible=False)
        self._ensure_owned_directory(self._loc.sources_root)
        self._ensure_owned_directory(f"{self._loc.sources_root}/worktrees")
        pulse(phase="fetch", current=1, total=3, summary="checkout")
        cancel = self._cancel_via_pulse(pulse)
        try:
            if not self._remote_is_dir(self._loc.bare_clone):
                self._run_remote(CommandKind.FETCH, ("git", "init", "--bare", self._loc.bare_clone))
            self._ensure_owned_directory(self._loc.bare_clone)
            bare = self._run_remote(CommandKind.OBSERVE, (
                "git", "-C", self._loc.bare_clone, "rev-parse", "--is-bare-repository",
            )).stdout_tail.strip()
            if bare != "true":
                raise StepFailure(CODE_SOURCE_COMMIT_UNAVAILABLE, "source cache is not bare", mutation_possible=False)
            origin = self._run_remote(CommandKind.OBSERVE, (
                "git", "-C", self._loc.bare_clone, "config", "--get", "remote.origin.url",
            ), expected_exit_codes=(0, 1)).stdout_tail.strip()
            if not origin:
                self._run_remote(CommandKind.FETCH, (
                    "git", "-C", self._loc.bare_clone, "remote", "add", "origin", UPSTREAM_REPOSITORY,
                ))
            elif origin != UPSTREAM_REPOSITORY:
                raise StepFailure(CODE_SOURCE_COMMIT_UNAVAILABLE, "source cache origin differs", mutation_possible=False)
            self._run_remote(CommandKind.FETCH, (
                "git", "-C", self._loc.bare_clone, "fetch", "--depth=1", "--no-tags", "origin", source_commit,
            ), cancel_requested=cancel)
            fetched = self._run_remote(CommandKind.OBSERVE, (
                "git", "-C", self._loc.bare_clone, "rev-parse", "FETCH_HEAD^{commit}",
            )).stdout_tail.strip()
            if fetched != source_commit:
                raise StepFailure(CODE_SOURCE_COMMIT_UNAVAILABLE, "fetched object differs", mutation_possible=False)
            self._run_remote(
                CommandKind.FETCH,
                ("git", "-C", self._loc.bare_clone, "worktree", "add",
                 "--detach", checkout, source_commit),
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
        if head != source_commit or self.probe_checkout(source_commit).classification is not RecoveryClass.COMPLETE:
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
        if not _COMMIT_RE.fullmatch(source_commit):
            return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "CHECKOUT_FOREIGN")
        checkout = f"{self._loc.sources_root}/worktrees/{source_commit}"
        if not (self._remote_exists_file(f"{checkout}/.git") or self._remote_is_dir(f"{checkout}/.git")):
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
            top = self._run_remote(CommandKind.OBSERVE, (
                "git", "-C", checkout, "rev-parse", "--show-toplevel",
            )).stdout_tail.strip()
            status = self._run_remote(CommandKind.OBSERVE, (
                "git", "-C", checkout, "status", "--porcelain", "--untracked-files=all",
            ))
        except ProcessFailure:
            return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "CHECKOUT_UNKNOWN")
        if out == source_commit and top == checkout and not status.stdout_tail and not status.truncated_stdout:
            return ProbeResult(RecoveryClass.COMPLETE, "CHECKOUT_PRESENT")
        return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "CHECKOUT_FOREIGN")

    def configure_build(self, request: Any, source_commit: str, pulse: Any
                        ) -> BuildEnvironmentEvidenceV1:
        if self.probe_checkout(source_commit).classification is not RecoveryClass.COMPLETE:
            raise StepFailure(CODE_SOURCE_COMMIT_UNAVAILABLE, "source checkout is unproven", mutation_possible=False)
        pulse(phase="configure", current=1, total=2)
        image_identity = self._observe_image_identity()
        toolchain = self._observe_toolchain()
        self._ensure_owned_directory(self._loc.managed_root)
        build_dir = self._run_remote(CommandKind.CONFIGURE, (
            "mktemp", "-d", f"{self._loc.managed_root}/candidate-XXXXXXXXXXXX",
        )).stdout_tail.strip()
        if not re.fullmatch(re.escape(self._loc.managed_root) + r"/candidate-[A-Za-z0-9]+", build_dir):
            raise StepFailure("BUILD_ENVIRONMENT_UNPROVEN", "candidate directory is unproven", mutation_possible=False)
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
            source_commit=source_commit,
        )

    def _observe_image_identity(self) -> dict[str, str]:
        assert self._proc is not None
        image_id = self._proc.run(ProcessCommandSpec(
            kind=CommandKind.OBSERVE, argv=(self._podman, "container", "inspect",
                "--format", "{{.Image}}", self._loc.container_name),
        )).stdout_tail.strip()
        if not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", image_id):
            raise StepFailure("BUILD_ENVIRONMENT_UNPROVEN", "container image ID is unproven", mutation_possible=False)
        result = self._proc.run(ProcessCommandSpec(
            kind=CommandKind.OBSERVE, argv=(self._podman, "image", "inspect",
                "--format", "{{.Id}} {{.Digest}}", image_id),
        ))
        parts = result.stdout_tail.split()
        if (len(parts) not in (1, 2) or result.truncated_stdout
                or parts[0].removeprefix("sha256:") != image_id.removeprefix("sha256:")
                or (len(parts) == 2 and not re.fullmatch(r"sha256:[0-9a-f]{64}", parts[1]))):
            raise StepFailure(
                "BUILD_ENVIRONMENT_UNPROVEN",
                "the build image identity could not be observed",
                mutation_possible=False,
            )
        return {"image_id": "sha256:" + image_id.removeprefix("sha256:"),
                "image_digest": parts[1] if len(parts) == 2 else ""}

    def _observe_toolchain(self) -> dict[str, str]:
        probes = (
            ("cmake", ("cmake", "--version")),
            ("ninja", ("ninja", "--version")) if self._cmake_generator == "Ninja" else ("make", ("make", "--version")),
            ("cc", ("cc", "--version")),
            ("cxx", ("c++", "--version")),
            ("linker", ("cc", "-Wl,--version")),
            ("libc", ("ldd", "--version")),
        )
        if "-DGGML_VULKAN=ON" in self._cmake_options:
            probes += (("glslc", ("glslc", "--version")),)
        toolchain: dict[str, str] = {}
        for name, argv in probes:
            out = self._run_remote(CommandKind.OBSERVE, argv).stdout_tail
            toolchain[name] = _digest(out.encode())[:16]
        return toolchain

    def _target_arch(self) -> str:
        return self._run_remote(
            CommandKind.OBSERVE, ("uname", "-m")
        ).stdout_tail.strip() or "unknown"

    def probe_build_environment(
        self, evidence: BuildEnvironmentEvidenceV1
    ) -> ProbeResult:
        if (evidence.recipe_version != RECIPE_VERSION or evidence.recipe_digest != _digest(RECIPE_DIGEST_SEED)
                or evidence.cmake_generator != self._cmake_generator
                or evidence.cmake_options != list(self._cmake_options)
                or evidence.cmake_targets != list(self._cmake_targets)
                or evidence.parallelism_policy != f"bounded-{self._jobs_cap}"):
            return ProbeResult(RecoveryClass.DISCARDABLE, "BUILD_RECIPE_CHANGED")
        try:
            fresh_image = self._observe_image_identity()
        except (StepFailure, ProcessFailure):
            fresh_image = {}
        if (fresh_image.get("image_id") != evidence.container_image_id
                or fresh_image.get("image_digest") != evidence.container_image_digest):
            return ProbeResult(
                RecoveryClass.DISCARDABLE, "BUILD_ENVIRONMENT_UNPROVEN"
            )
        if not evidence.toolchain or not evidence.build_dir_locator:
            return ProbeResult(
                RecoveryClass.DISCARDABLE, "BUILD_ENVIRONMENT_UNPROVEN"
            )
        if not re.fullmatch(re.escape(self._loc.managed_root) + r"/candidate-[A-Za-z0-9]+", evidence.build_dir_locator):
            return ProbeResult(RecoveryClass.DISCARDABLE, "BUILD_ENVIRONMENT_UNPROVEN")
        if not self._remote_is_dir(evidence.build_dir_locator):
            return ProbeResult(RecoveryClass.ABSENT, "BUILD_DIR_MISSING")
        if (not _COMMIT_RE.fullmatch(evidence.source_commit)
                or self.probe_checkout(evidence.source_commit).classification is not RecoveryClass.COMPLETE
                or self._observe_toolchain() != evidence.toolchain
                or self._target_arch() != evidence.target_arch):
            return ProbeResult(RecoveryClass.DISCARDABLE, "BUILD_ENVIRONMENT_UNPROVEN")
        return ProbeResult(
            RecoveryClass.COMPLETE, "BUILD_ENVIRONMENT_FROZEN",
            output=_asdict(evidence),
        )

    def compile_candidate(
        self, environment: BuildEnvironmentEvidenceV1, pulse: Any
    ) -> CandidateBuildEvidenceV1:
        if self.probe_build_environment(environment).classification is not RecoveryClass.COMPLETE:
            raise StepFailure("BUILD_ENVIRONMENT_UNPROVEN", "build environment changed", mutation_possible=False)
        source_dir = f"{self._loc.sources_root}/worktrees/{environment.source_commit}"
        binary_dir = f"{environment.build_dir_locator}/build"
        cancel = self._cancel_via_pulse(pulse)
        pulse(phase="configure", current=1, total=2, cancellation_safe=True)
        self._run_remote(
            CommandKind.CONFIGURE,
            ("cmake", "-S", source_dir, "-B", binary_dir,
             "-G", environment.cmake_generator,
             *tuple(environment.cmake_options)),
            cancel_requested=cancel,
        )
        pulse(phase="build", current=1, total=3, summary="compiling")
        self._run_remote(
            CommandKind.COMPILE,
            ("cmake", "--build", binary_dir,
             "--target", *environment.cmake_targets,
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
            version_out = self._binary_smoke_text(absolute, target)
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

    def _binary_smoke_text(self, absolute: str, target: str) -> str:
        # Upstream quantize has no --version switch and returns 1 for help.
        # Its usage response is the smoke contract. Strip the invocation
        # path so the identity cannot depend on a temporary directory.
        quantizer = target == "llama-quantize"
        result = self._run_remote(CommandKind.SMOKE,
                                  (absolute, "--help" if quantizer else "--version"),
                                  expected_exit_codes=(0, 1) if quantizer else (0,))
        output = result.stdout_tail + result.stderr_tail
        if quantizer and ("usage:" not in output.lower() or "llama-quantize" not in output):
            raise StepFailure("CANDIDATE_SMOKE_FAILED", "quantizer usage is unproven", mutation_possible=False)
        return output.replace(absolute, target)

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
            version_out = self._binary_smoke_text(absolute, target)
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
        if (source_commit != environment.source_commit or candidate.build_dir_locator != environment.build_dir_locator
                or self.probe_build_environment(environment).classification is not RecoveryClass.COMPLETE):
            raise StepFailure("BUILD_ENVIRONMENT_UNPROVEN", "candidate source or environment changed", mutation_possible=False)
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
            registered = trees.record_candidate(
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
            tree_id=registered["tree_id"],
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
             "import os,sys,pathlib,tempfile;"
             "p=pathlib.Path(sys.argv[1]);data=sys.stdin.buffer.read();"
             "fd,tmp=tempfile.mkstemp(prefix='.manifest-',dir=p.parent);"
             "f=os.fdopen(fd,'wb');f.write(data);f.flush();os.fsync(f.fileno());f.close();"
             "os.replace(tmp,p);fd=os.open(p.parent,os.O_RDONLY);os.fsync(fd);os.close(fd)",
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
        if not recorded or not live or recorded != live:
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
        active_build_id = self.ensure_runtime_registered() if self._remote_is_dir(self._loc.active_root) else None
        target_build_id = target_build_id or getattr(request, "target_build_id", None) or active_build_id
        with self._units.read() as conn:
            components = RuntimeComponentRepository(conn)
            builds = RuntimeBuildRepository(conn)
            component = components.current()
            promoted = (component or {}).get("promoted_build_id")
            trees = RuntimeTreeRepository(conn)
            active_tree = trees.by_locator(self._loc.active_root.lstrip("/"))
            targets = trees.for_build(target_build_id) if target_build_id else []
            targets = [row for row in targets if row["locator"] != self._loc.active_root.lstrip("/")]
        if target_build_id == active_build_id and active_build_id and not promoted:
            if self._prepared_build_for_source() != active_build_id:
                raise StepFailure("PREPARED_RUNTIME_CHANGED", "prepared build changed before activation",
                                  mutation_possible=False)
        if target_build_id != active_build_id and len(targets) != 1:
            raise StepFailure(CODE_ACTIVE_RUNTIME_UNPROVEN, "target tree is ambiguous", mutation_possible=False)
        target = active_tree if target_build_id == active_build_id else targets[0]
        known_good = self._known_good()
        payload = (
            self.renderer.observe(require_v2=False) if self.renderer else None
        )
        active, marker, health = self._capture_service_facts()
        inference_ok = False
        if active:
            inference_ok = self.server_port.inference(self._view(), timeout=20.0).get("ok") is True
            if not active_build_id or not self._health_matches(health) or not inference_ok:
                raise StepFailure(CODE_ACTIVE_RUNTIME_UNPROVEN, "running prior runtime cannot be restored with verified settings", mutation_possible=False)
        view = self._view()
        installation_only = not view.get("current_model")
        if installation_only:
            prior_prepared = active_build_id is None or self._prepared_build_for_source() == active_build_id
            if (active or self.server_port.capture(view).get("active") is not False
                    or known_good is not None or promoted or (component or {}).get("rollback_build_id")
                    or not prior_prepared):
                raise StepFailure("INITIAL_INSTALLATION_UNPROVEN",
                                  "no-model installation requires absent or prepared runtime and a stopped service",
                                  mutation_possible=False)
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
            observed_model_alias=health.get("model_id") if active else None,
            observed_context_total=health.get("context_total", health.get("n_ctx")) if active else None,
            observed_slots=health.get("parallel_slots") if active else None,
            inference_verified=inference_ok,
            active_tree_id=(active_tree or {}).get("tree_id"),
            target_tree_id=(target or {}).get("tree_id"),
            target_locator=(target or {}).get("locator"),
            rollback_tree_id=(component or {}).get("rollback_tree_id"),
            known_good_payload=known_good,
            installation_only=installation_only,
            installation_fingerprint=self._installation_fingerprint(view) if installation_only else None,
        )

    @staticmethod
    def _installation_fingerprint(view: dict[str, Any]) -> str:
        from .runtime_handoff import runtime_fingerprint

        return runtime_fingerprint(view)

    def observe_initial_installation(self, snapshot, target_build_id) -> ProbeResult:
        """Files installed without a model is never live or known-good proof."""
        view = self._view()
        with self._units.read() as conn:
            component = RuntimeComponentRepository(conn).current() or {}
            record = RuntimeBuildRepository(conn).require(target_build_id)
        same = (
            snapshot.installation_only
            and bool(snapshot.installation_fingerprint)
            and self._installation_fingerprint(view) == snapshot.installation_fingerprint
            and not view.get("current_model")
            and self.server_port.capture(view).get("active") is False
            and component.get("promoted_build_id") is None
            and component.get("rollback_build_id") is None
            and self._known_good() is None
            and self.renderer.observe(require_v2=False) == snapshot.handoff_payload
            and record["provenance_class"] == "IMMUTABLE_SOURCE"
            and self._observed_tree(self._loc.active_root, target_build_id) is not None
            and self._all_binaries_match(record["manifest"])
        )
        if not same:
            return ProbeResult(RecoveryClass.REVERTIBLE, "INITIAL_INSTALLATION_CHANGED")
        return ProbeResult(RecoveryClass.COMPLETE, "INITIAL_RUNTIME_INSTALLED",
                           output={"installation_verified": True, "inference_deferred": True})

    def _health_match_fields(self, health: dict[str, Any]) -> tuple[bool, bool, bool]:
        from .server import observed_model_matches_selected
        from .runtime_handoff import build_payload
        view = self._view()
        expected = build_payload(view, config_revision=int(view.get("revision") or 1))
        alias_ok = bool(health.get("model_id")) and observed_model_matches_selected(view, health.get("model_id"))
        slots = health.get("parallel_slots")
        per_slot = health.get("context_per_slot")
        context_ok = (per_slot == expected["ctx_total"] // expected["parallel_slots"]
                      if per_slot is not None else health.get("n_ctx") == expected["ctx_total"])
        return bool(alias_ok), bool(context_ok), slots == expected["parallel_slots"]

    def _health_matches(self, health: dict[str, Any]) -> bool:
        return bool(health.get("healthy")) and all(self._health_match_fields(health))

    def verify_activation_boundary(self, request, snapshot, target_build_id) -> None:
        if not self._thermal_ok():
            raise StepFailure(CODE_THERMAL_LATCH_STOPPED, "thermal stop before runtime activation",
                              mutation_possible=False)
        expected = getattr(request, "expected_active_build_id", None)
        if expected is not None \
                and (snapshot.promoted_build_id or snapshot.active_build_id) != expected:
            raise StepFailure(
                CODE_ACTIVE_TREE_CHANGED,
                "expected active build changed before the activation boundary",
                mutation_possible=False,
            )
        if snapshot.installation_only:
            view = self._view()
            if (view.get("current_model")
                    or self._installation_fingerprint(view) != snapshot.installation_fingerprint
                    or self.server_port.capture(view).get("active") is not False):
                raise StepFailure("INITIAL_INSTALLATION_UNPROVEN", "initial setup changed before publication",
                                  mutation_possible=False)
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

    def _classify_arrangement(
        self, snapshot: PriorRuntimeSnapshotV1, target_build_id: str,
    ) -> ProbeResult:
        """Observe the exact pair frozen before the effect, including its hashes.

        The displaced tree stays at the candidate's original locator. There
        are no post-exchange moves or deletes that recovery has to guess.
        """
        if not snapshot.target_locator or not snapshot.target_tree_id:
            return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "EXCHANGE_BINDING_MISSING")
        active = self._observed_tree(self._loc.active_root)
        if (active and active["build_id"] == target_build_id == snapshot.active_build_id
                and snapshot.target_tree_id == snapshot.active_tree_id
                and snapshot.target_locator == self._loc.active_root.lstrip("/")):
            return ProbeResult(RecoveryClass.COMPLETE, "UNCHANGED_ACTIVE_TREE")
        other_path = "/" + snapshot.target_locator
        other = self._observed_tree(other_path)
        if active and active["build_id"] == target_build_id:
            if snapshot.active_build_id is None:
                if not self._remote_test("-e", other_path):
                    return ProbeResult(RecoveryClass.COMPLETE, "PUBLISHED_INITIAL_VERIFIED")
            elif other and other["build_id"] == snapshot.active_build_id:
                return ProbeResult(RecoveryClass.COMPLETE, "TREE_EXCHANGE_COMPLETED",
                                   output={"active": target_build_id, "prior_locator": snapshot.target_locator})
            return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "PRIOR_RETAINED_UNPROVEN")
        prior_active = ((active and active["build_id"] == snapshot.active_build_id)
                        if snapshot.active_build_id else not self._remote_test("-e", self._loc.active_root))
        if prior_active and other and other["build_id"] == target_build_id:
            return ProbeResult(RecoveryClass.ABSENT, "EXCHANGE_NOT_LANDED")
        return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "ARRANGEMENT_UNKNOWN")

    def _record_pair_locations(self, snapshot, *, forward: bool) -> None:
        """One transaction moves durable locators after independent observation."""
        if not snapshot.target_tree_id or not snapshot.target_locator:
            raise StepFailure("TREE_EXCHANGE_UNCERTAIN", "missing tree binding", mutation_possible=True)
        if snapshot.target_tree_id == snapshot.active_tree_id:
            return
        active_locator = self._loc.active_root.lstrip("/")
        with self._units.begin() as conn:
            trees = RuntimeTreeRepository(conn)
            trees.relocate(snapshot.target_tree_id,
                           active_locator if forward else snapshot.target_locator,
                           "ACTIVE_OBSERVED" if forward else "RETAINED")
            if snapshot.active_tree_id:
                trees.relocate(snapshot.active_tree_id,
                               snapshot.target_locator if forward else active_locator,
                               "RETAINED" if forward else "ACTIVE_OBSERVED")

    def exchange_active_tree(
        self, snapshot, smoke, external_effect_id, *, mode,
    ) -> TreeExchangeEvidenceV1:
        self._require_seams()
        assert smoke is not None
        if smoke.tree_id != snapshot.target_tree_id or smoke.locator != snapshot.target_locator:
            raise StepFailure("TREE_EXCHANGE_UNCERTAIN", "target binding changed", mutation_possible=False)
        observed = self._classify_arrangement(snapshot, smoke.build_id)
        if observed.classification is RecoveryClass.COMPLETE:
            return TreeExchangeEvidenceV1(classification="EXCHANGED", exchanged_now=False,
                                          active_build_id_after=smoke.build_id)
        if observed.classification is not RecoveryClass.ABSENT:
            raise StepFailure("TREE_EXCHANGE_UNCERTAIN", observed.reason_code, mutation_possible=False)
        self._crash_point("exchange_active_tree", "after_step_start")
        helper = self._stage_helper(operation_id=external_effect_id[:24])
        initial = snapshot.active_build_id is None
        self._crash_point("exchange_active_tree", "before_publication" if initial else "before_swap")
        argv = build_helper_invocation(helper, self._loc.active_root,
                                       "/" + smoke.locator, self._loc.approved_root,
                                       initial=initial)
        try:
            self._run_remote(CommandKind.ATOMIC, tuple(argv))
        except ProcessFailure as exc:
            # A helper failure after its syscall/fsync may already have changed
            # the pair. Let recovery observe it; never label that a safe failure.
            raise StepFailure("TREE_EXCHANGE_UNCERTAIN", "atomic helper did not complete", mutation_possible=True) from exc
        self._crash_point("exchange_active_tree", "after_swap")
        self.verify_exchange(snapshot, smoke.build_id, mode=mode)
        return TreeExchangeEvidenceV1(
            classification="PUBLISHED_INITIAL" if initial else "EXCHANGED",
            exchanged_now=True, active_build_id_after=smoke.build_id,
            prior_tree={"tree_id": snapshot.active_tree_id, "locator": snapshot.target_locator} if snapshot.active_tree_id else None,
            target_tree={"tree_id": smoke.tree_id, "locator": self._loc.active_root.lstrip("/")},
        )

    def _active_build_id_after_swap(self) -> str | None:
        payload = self._observed_tree(self._loc.active_root)
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

    def _view_for_build(self, target_build_id: str, operation_id: str) -> dict[str, Any]:
        with self._units.read() as conn:
            record = RuntimeBuildRepository(conn).require(target_build_id)
        view = self._view()
        view.update(runtime_component_id=target_build_id,
                    runtime_manifest_digest=record["manifest_digest"],
                    runtime_source_commit=record["source_commit"] or "",
                    runtime_server_sha256=_server_digest_of(record["manifest"].get("binaries") or []),
                    runtime_operation_id=operation_id)
        return view

    def publish_handoff_v2(
        self, snapshot, target_build_id, operation_id, *, mode,
    ) -> HandoffComponentEvidenceV1:
        self._require_seams()
        self._crash_point("publish_component_handoff", "mid_effect")
        with self._units.read() as conn:
            builds = RuntimeBuildRepository(conn)
            record = builds.require(target_build_id)
        manifest = record["manifest"]
        view = self._view_for_build(target_build_id, operation_id)
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
        from .runtime_handoff import build_payload
        expected = build_payload(self._view_for_build(target_build_id, str(payload.get("runtime_operation_id") or "")),
                                 config_revision=int(self._view().get("revision") or 1))
        if payload == expected:
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
        view = self._view_for_build(target_build_id, operation_id)
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
                mutation_possible=True,
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
            with self._receipt_path().open(encoding="utf-8") as handle:
                text = handle.read(65537)
            if len(text) > 65536:
                return {}
            payload = _json.loads(text)
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
        handoff = self.renderer.observe(require_v2=True)
        tree = self._observed_tree(self._loc.active_root, target_build_id)
        receipt_ok = (
            bool(tree) and bool(handoff) and bool(receipt.get("nonce"))
            and receipt.get("build_id") == target_build_id
            and receipt.get("server_sha256") == tree["server_binary_digest"]
            and receipt.get("manifest_digest") == tree["manifest_digest"]
            and receipt.get("operation_id") == handoff.get("runtime_operation_id")
            and handoff.get("runtime_component_id") == target_build_id
            and bool(marker)
            and (
                marker != snapshot.invocation_marker
                or snapshot.active_build_id == target_build_id
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
        self, snapshot, target_build_id, pulse=None, *, timeout=120,
    ) -> RuntimeIdentityEvidenceV1:
        view = self._view()
        health = self.server_port.health(view, timeout=timeout, pulse=pulse)
        active_payload = self._observed_tree(self._loc.active_root, target_build_id)
        active_ok = bool(
            active_payload and active_payload.get("build_id") == target_build_id
        )
        binary_live = self._remote_sha256(
            self._loc.active_root + "/" + self._loc.server_binary_relpath
        )
        manifest_entry = (active_payload or {}).get("server_binary_digest")
        binary_ok = bool(binary_live) and binary_live == manifest_entry
        receipt_ok = self.observe_invocation(snapshot, target_build_id, mode="verify").classification is RecoveryClass.COMPLETE
        handoff_ok = self.observe_handoff_v2(snapshot, target_build_id, mode="verify").classification is RecoveryClass.COMPLETE
        alias_ok, context_ok, slots_ok = self._health_match_fields(health)
        return RuntimeIdentityEvidenceV1(
            component_ok=active_ok and bool(receipt_ok) and handoff_ok,
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
        self.verify_exchange(snapshot, target_build_id, mode=mode)
        identity = self.verify_runtime_identity(snapshot, target_build_id, timeout=15)
        if not all((identity.component_ok, identity.binary_digest_ok, identity.model_alias_ok,
                    identity.context_ok, identity.slots_ok, identity.health_ok)):
            raise StepFailure("RUNTIME_COMPONENT_MISMATCH", "promotion identity changed", mutation_possible=True)
        self._record_pair_locations(snapshot, forward=True)
        view = self._view_for_build(target_build_id, operation_id)
        handoff = self.renderer.observe(require_v2=True)
        from .repositories import KnownGoodRuntimeRepository, RuntimeConfigRepository
        with self._units.begin() as conn:
            components = RuntimeComponentRepository(conn)
            current = components.current() or components.initialize()
            former_promoted = snapshot.promoted_build_id
            if (current["promoted_build_id"] == target_build_id
                    and current["rollback_build_id"] == former_promoted
                    and current["last_operation_id"] == operation_id):
                return RuntimePromotionEvidenceV1(
                    generation_after=int(current["generation"]), promoted_build_id=target_build_id,
                    rollback_build_id=current.get("rollback_build_id"),
                    promoted_tree_id=current.get("promoted_tree_id"), rollback_tree_id=current.get("rollback_tree_id"), noop=True)
            promoted = components.promote_verified(
                expected_generation=int(snapshot.generation or 1),
                expected_promoted_build_id=snapshot.promoted_build_id,
                expected_rollback_build_id=snapshot.rollback_build_id,
                promoted_build_id=target_build_id, rollback_build_id=former_promoted,
                promoted_tree_id=snapshot.target_tree_id,
                rollback_tree_id=snapshot.active_tree_id if former_promoted else None,
                operation_id=operation_id,
            )
            # The workflow has verified inference for this unchanged model and
            # configuration. Bind that exact configuration, not an older row.
            runtime = RuntimeConfigRepository(conn).get()
            KnownGoodRuntimeRepository(conn).set(
                model_alias=view["current_model"], context=int(view["current_ctx"]),
                slots=int(handoff["parallel_slots"]), runtime=dict(view.get("optimizations") or {}),
                profile_id=runtime.get("profile_id"), profile_revision=runtime.get("profile_revision"),
                profile_fingerprint=runtime.get("profile_fingerprint"),
                runtime_fingerprint=handoff["runtime_fingerprint"], runtime_component_identity=target_build_id,
                verified_at=self._clock(),
            )
        return RuntimePromotionEvidenceV1(
            generation_after=int(promoted["generation"]), promoted_build_id=target_build_id,
            rollback_build_id=promoted["rollback_build_id"], promoted_tree_id=promoted["promoted_tree_id"],
            rollback_tree_id=promoted["rollback_tree_id"],
        )

    def observe_promotion(
        self, snapshot, target_build_id, *, mode,
    ) -> ProbeResult:
        with self._units.read() as conn:
            current = RuntimeComponentRepository(conn).current()
        if current is None:
            return ProbeResult(RecoveryClass.ABSENT, "NO_COMPONENT_ROW")
        if (target_build_id == snapshot.promoted_build_id == current.get("promoted_build_id")
                and current.get("generation") == snapshot.generation
                and current.get("rollback_build_id") == snapshot.rollback_build_id):
            return ProbeResult(RecoveryClass.COMPLETE, "RUNTIME_ALREADY_PROMOTED", output={
                "generation_after": current["generation"], "promoted_build_id": target_build_id,
                "rollback_build_id": current.get("rollback_build_id"), "noop": True})
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
        if not snapshot.target_tree_id or not snapshot.target_locator:
            raise StepFailure("RUNTIME_RESTORATION_UNCERTAIN", "missing original pair", mutation_possible=True)
        with self._units.read() as conn:
            target = RuntimeTreeRepository(conn).require(snapshot.target_tree_id)
        arrangement = self._classify_arrangement(snapshot, target["build_id"])
        stages = []
        if arrangement.classification is RecoveryClass.COMPLETE and snapshot.target_tree_id != snapshot.active_tree_id:
            helper = self._stage_helper(operation_id=f"restore-{restoration_id}"[:40])
            if snapshot.active_build_id is None:
                argv = build_helper_invocation(helper, "/" + snapshot.target_locator,
                                               self._loc.active_root, self._loc.approved_root, initial=True)
            else:
                argv = build_helper_invocation(helper, self._loc.active_root,
                                               "/" + snapshot.target_locator, self._loc.approved_root)
            self._run_remote(CommandKind.ATOMIC, tuple(argv))
            stages.append("REVERSE_EXCHANGED")
        elif arrangement.classification not in (RecoveryClass.ABSENT, RecoveryClass.COMPLETE):
            raise StepFailure("RUNTIME_RESTORATION_UNCERTAIN", arrangement.reason_code, mutation_possible=True)
        else:
            stages.append("TREE_ALREADY_PRIOR")
        restored_arrangement = self._classify_arrangement(snapshot, target["build_id"])
        unchanged = snapshot.target_tree_id == snapshot.active_tree_id and restored_arrangement.reason_code == "UNCHANGED_ACTIVE_TREE"
        if restored_arrangement.classification is not RecoveryClass.ABSENT and not unchanged:
            raise StepFailure("RUNTIME_RESTORATION_UNCERTAIN", "reverse exchange unproven", mutation_possible=True)
        self._record_pair_locations(snapshot, forward=False)
        observed_handoff = self.renderer.observe(require_v2=False)
        if observed_handoff != snapshot.handoff_payload and (
                not observed_handoff or observed_handoff.get("runtime_component_id") != target["build_id"]):
            raise StepFailure("RUNTIME_RESTORATION_UNCERTAIN", "foreign handoff", mutation_possible=True)
        self.renderer.restore_snapshot(snapshot.handoff_payload)
        stages.append("HANDOFF_RESTORED")
        # Restore the durable component before restart regenerates the handoff.
        from .repositories import KnownGoodRuntimeRepository
        with self._units.begin() as conn:
            components = RuntimeComponentRepository(conn)
            current = components.current()
            if current and current.get("promoted_build_id") != snapshot.promoted_build_id:
                if current.get("promoted_build_id") != target["build_id"]:
                    raise StepFailure("RUNTIME_RESTORATION_UNCERTAIN", "foreign promotion", mutation_possible=True)
                components.record_restoration(
                    expected_generation=int(current["generation"]),
                    expected_promoted_build_id=current["promoted_build_id"],
                    expected_rollback_build_id=current["rollback_build_id"],
                    restored_promoted_build_id=snapshot.promoted_build_id,
                    new_rollback_build_id=snapshot.rollback_build_id,
                    promoted_tree_id=snapshot.active_tree_id if snapshot.promoted_build_id else None,
                    rollback_tree_id=snapshot.rollback_tree_id,
                )
            kg = KnownGoodRuntimeRepository(conn)
            if snapshot.known_good_payload is None:
                kg.clear()
            else:
                kg.set(**snapshot.known_good_payload)
        view = self._view()
        if snapshot.handoff_payload and snapshot.handoff_payload.get("schema_version") == 2:
            view.update(runtime_component_id=snapshot.handoff_payload["runtime_component_id"],
                        runtime_manifest_digest=snapshot.handoff_payload["runtime_manifest_digest"],
                        runtime_source_commit=snapshot.handoff_payload["runtime_source_commit"],
                        runtime_server_sha256=snapshot.handoff_payload["runtime_server_sha256"],
                        runtime_operation_id=snapshot.handoff_payload["runtime_operation_id"])
        if snapshot.service_state == "ACTIVE_VERIFIED":
            self.server_port.restart(view)
            stages.append("SERVICE_STATE_RESTORED")
        else:
            self.server_port.stop(view)
            stages.append("SERVICE_STOPPED")
        if self.observe_restoration(snapshot, mode=mode).classification is not RecoveryClass.COMPLETE:
            raise StepFailure("RUNTIME_RESTORATION_UNCERTAIN", "prior runtime verification failed", mutation_possible=True)
        return RuntimeRestorationEvidenceV1(restored=True, stage_codes=stages, service_state=snapshot.service_state)

    def observe_restoration(self, snapshot, *, mode) -> ProbeResult:
        active = self._observed_tree(self._loc.active_root)
        active_ok = (active is not None and active["build_id"] == snapshot.active_build_id
                     if snapshot.active_build_id else not self._remote_test("-e", self._loc.active_root))
        handoff_ok = self.renderer.observe(require_v2=False) == snapshot.handoff_payload
        view = self._view()
        facts = self.server_port.capture(view)
        if snapshot.service_state == "ACTIVE_VERIFIED":
            service_ok = (bool(facts.get("active")) and self._health_matches(self.server_port.health(view, timeout=15))
                          and self.server_port.inference(view, timeout=20.0).get("ok") is True)
        else:
            service_ok = not facts.get("active")
        with self._units.read() as conn:
            current = RuntimeComponentRepository(conn).current()
        lineage_ok = ((current or {}).get("promoted_build_id") == snapshot.promoted_build_id
                      and (current or {}).get("rollback_build_id") == snapshot.rollback_build_id)
        known_good_ok = self._known_good() == snapshot.known_good_payload
        if active_ok and handoff_ok and service_ok and lineage_ok and known_good_ok:
            return ProbeResult(RecoveryClass.COMPLETE, "PRIOR_RUNTIME_RESTORED",
                               output={"restored": True, "service_state": snapshot.service_state})
        return ProbeResult(RecoveryClass.REVERTIBLE, "RESTORATION_INCOMPLETE")

    # -- finalization ----------------------------------------------------------

    def finalize_trees(self, snapshot, target_build_id, promotion, exchange,
                       *, mode) -> RuntimeCleanupEvidenceV1:
        self.verify_exchange(snapshot, target_build_id, mode=mode)
        self._record_pair_locations(snapshot, forward=True)
        if snapshot.target_tree_id == snapshot.active_tree_id:
            return RuntimeCleanupEvidenceV1(retained_locators=[], removed_locators=[], deferred_locators=[], noop=True)
        # Retain all displaced/other trees. Cleanup is a separate reviewed
        # action; an uncertain tree must never be removed during finalization.
        retained = [snapshot.target_locator] if snapshot.active_tree_id else []
        return RuntimeCleanupEvidenceV1(retained_locators=retained, removed_locators=[], deferred_locators=[])

    def observe_finalization(self, snapshot, target_build_id, *, mode) -> ProbeResult:
        arrangement = self._classify_arrangement(snapshot, target_build_id)
        if arrangement.classification is not RecoveryClass.COMPLETE:
            return arrangement
        with self._units.read() as conn:
            trees = RuntimeTreeRepository(conn)
            active = trees.get(snapshot.target_tree_id) if snapshot.target_tree_id else None
            prior = trees.get(snapshot.active_tree_id) if snapshot.active_tree_id else None
        if (arrangement.reason_code == "UNCHANGED_ACTIVE_TREE" and active
                and active["locator"] == self._loc.active_root.lstrip("/") and active["role"] == "ACTIVE_OBSERVED"):
            return ProbeResult(RecoveryClass.COMPLETE, "TREES_UNCHANGED", output={
                "retained_locators": [], "removed_locators": [], "deferred_locators": [], "noop": True})
        if (not active or active["build_id"] != target_build_id or active["locator"] != self._loc.active_root.lstrip("/")
                or active["role"] != "ACTIVE_OBSERVED"
                or (snapshot.active_tree_id and (not prior or prior["locator"] != snapshot.target_locator or prior["role"] != "RETAINED"))):
            return ProbeResult(RecoveryClass.PARTIALLY_RESUMABLE, "TREE_REGISTRY_PENDING")
        return ProbeResult(RecoveryClass.COMPLETE, "TREES_FINALIZED",
                           output={"retained_locators": [snapshot.target_locator] if prior else [],
                                   "removed_locators": [], "deferred_locators": [], "noop": False})

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
            tree_id = (current or {}).get("rollback_tree_id")
            row = trees.get(tree_id) if tree_id else None
            if row is None or row["build_id"] != target:
                raise StepFailure(
                    "RUNTIME_ROLLBACK_TARGET_MISSING",
                    "the retained target tree cannot be identified",
                    mutation_possible=False,
                )
        payload = self._observed_tree("/" + row["locator"], target)
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
        payload = self._observed_tree("/" + target.target_locator, target.target_build_id)
        if payload is None:
            return ProbeResult(RecoveryClass.ABSENT, "RETAINED_MANIFEST_MISSING")
        if payload.get("build_id") == target.target_build_id:
            return ProbeResult(RecoveryClass.COMPLETE, "RETAINED_SMOKE_OK")
        return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "RETAINED_IDENTITY_MOVED")
