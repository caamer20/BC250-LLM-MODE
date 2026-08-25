"""Durable fake runtime world for RUNTIME_UPDATE / RUNTIME_ROLLBACK tests.

Everything is REAL durable state under the injected temporary profile:
trees with manifests on disk, a real SQLite database through the
migration-005 repositories, handoff/receipt/service files, and an
effects ledger keyed by external-effect id. Fresh executors observe the
same files, so recovery probes prove postconditions instead of trusting
Python objects. No secrets, no sleeps, no host services.

Build identities are REAL content-derived IDs (runtime_builds
.derive_build_id) over minimal valid manifests; ``seed_promoted_runtime``
and ``stage_candidate`` map friendly labels to those IDs.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from bc250_llm_mode.operations.recovery import RecoveryClass
from bc250_llm_mode.operations.runtime_lifecycle import (
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
    CODE_ACTIVE_TREE_CHANGED,
    CODE_SOURCE_COMMIT_UNAVAILABLE,
    DEFAULT_REQUESTED_REF,
    PRIOR_ABSENT,
    PRIOR_STOPPED,
)
from bc250_llm_mode.operations.workflow import ProbeResult, StepFailure
from bc250_llm_mode.runtime_builds import (
    COMPONENT,
    MANIFEST_VERSION,
    RECIPE_VERSION,
    RuntimeBuildError,
    RuntimeBuildRepository,
    RuntimeComponentRepository,
    RuntimeTreeRepository,
    RuntimeVerificationRepository,
    canonical_manifest_bytes,
    derive_build_id,
)

SERVER_BYTES = {
    "llama-server": b"\x7fELF-fake-llama-server-",
    "llama-cli": b"\x7fELF-fake-llama-cli-",
    "llama-quantize": b"\x7fELF-fake-llama-quantize-",
}
SOURCE_COMMIT_A = "a" * 40
SOURCE_COMMIT_B = "b" * 40


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fake_manifest(source_commit: str, binaries: list[dict[str, Any]]) -> dict:
    return {
        "schema_version": MANIFEST_VERSION,
        "component": COMPONENT,
        "upstream_repository": "https://github.com/ggml-org/llama.cpp",
        "requested_ref": None,
        "source_commit": source_commit,
        "source_checkout_verified": True,
        "recipe_version": RECIPE_VERSION,
        "recipe_digest": _digest(b"recipe-v1"),
        "cmake_generator": "Ninja",
        "cmake_options": ["-DGGML_VULKAN=ON"],
        "cmake_targets": sorted(SERVER_BYTES),
        "build_parallelism": {"policy": "bounded", "jobs_cap": 2},
        "container_image_id": _digest(b"image"),
        "container_image_digest": _digest(b"image-digest"),
        "toolchain": {
            "cmake": "fake-cmake",
            "ninja": "fake-ninja",
            "cc": "fake-gcc",
            "linker": "fake-ld",
            "libc": "fake-libc",
        },
        "target_arch": "x86_64",
        "binaries": binaries,
        "smoke_contract_version": 1,
    }


class FakeRuntimeHost:
    """Production-port implementation over on-disk + SQLite fake reality."""

    def __init__(self, *, root: Path, units: Any, clock: Any = None) -> None:
        import threading

        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.units = units
        self.clock = clock or (lambda: "2026-08-23T12:00:00Z")
        self.lock = threading.RLock()

        # Configurable fault surface (tests mutate these directly).
        self.refs: dict[str, str] = {DEFAULT_REQUESTED_REF: SOURCE_COMMIT_B}
        self.disk_free_bytes = 50 * 1024 * 1024 * 1024
        self.atomic_exchange_supported = True
        self.thermal_ok = True
        self.inference_fails = False
        self.handoff_publish_fails = False
        self.restart_fails = False
        self.exchange_unsupported = False
        self._effect_crash: tuple[str, str] | None = None
        self.label_to_build: dict[str, str] = {}
        self.counter = 0

        self._write_json(self.effects_path, {"count": 0, "by_effect": {}})
        self._write_json(self.service_path, {"running": False, "build_id": None,
                                             "alias": None, "invocations": 0})
        self.sources_root.mkdir(parents=True, exist_ok=True)
        self.managed_root.mkdir(parents=True, exist_ok=True)
        self.retained_root.mkdir(parents=True, exist_ok=True)

    # -- layout -----------------------------------------------------------------
    @property
    def runtime_root(self) -> Path:
        return self.root / "runtime"

    @property
    def active_root(self) -> Path:
        return self.runtime_root / "active"

    @property
    def managed_root(self) -> Path:
        return self.root / "managed-trees"

    @property
    def retained_root(self) -> Path:
        return self.managed_root / "retained"

    @property
    def sources_root(self) -> Path:
        return self.root / "sources"

    @property
    def effects_path(self) -> Path:
        return self.root / "effects.json"

    @property
    def handoff_path(self) -> Path:
        return self.root / "handoff.json"

    @property
    def receipt_path(self) -> Path:
        return self.root / "start-receipt.json"

    @property
    def service_path(self) -> Path:
        return self.root / "service.json"

    # -- low-level helpers ---------------------------------------------------------
    def _read_json(self, path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True),
                        encoding="utf-8")

    def _ledger(self) -> dict[str, Any]:
        data = self._read_json(self.effects_path) or {"count": 0, "by_effect": {}}
        return data

    def _record_effect(self, external_effect_id: str, kind: str) -> bool:
        """Record one external effect; False when the id already ran."""
        with self.lock:
            ledger = self._ledger()
            if external_effect_id in ledger["by_effect"]:
                return False
            ledger["by_effect"][external_effect_id] = kind
            ledger["count"] = int(ledger.get("count") or 0) + 1
            self._write_json(self.effects_path, ledger)
            return True

    def exchange_count(self) -> int:
        return sum(
            1
            for kind in (self._ledger().get("by_effect") or {}).values()
            if kind == "exchange"
        )

    def exchange_event_codes(self) -> list[str]:
        return [
            "TREE_EXCHANGE_COMPLETED"
            for kind in (self._ledger().get("by_effect") or {}).values()
            if kind == "exchange"
        ]

    def _crash_point(self, step_key: str, subpoint: str) -> None:
        if self._effect_crash == (step_key, subpoint):
            from fakes import SimulatedProcessDeath

            self._effect_crash = None
            raise SimulatedProcessDeath(f"{step_key}:{subpoint}")

    def arm_effect_crash(self, step_key: str, subpoint: str) -> None:
        self._effect_crash = (step_key, subpoint)

    def clear_effect_crash(self) -> None:
        self._effect_crash = None

    # -- tree construction -------------------------------------------------------
    def _materialize_tree(self, destination: Path, build_label_seed: bytes,
                          source_commit: str) -> str:
        """Write a complete runtime tree; returns the derived build id."""
        binaries = []
        for name, blob in SERVER_BYTES.items():
            content = blob + build_label_seed
            relpath = f"build/bin/{name}"
            target = destination / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            binaries.append({
                "path": relpath,
                "size": len(content),
                "mode": "755",
                "sha256": _digest(content),
                "version_output_digest": _digest(b"version:" + content),
            })
        build_id, digest = derive_build_id(
            _fake_manifest(source_commit, binaries)
        )
        self._write_json(destination / "manifest.json", {
            "build_id": build_id,
            "manifest_digest": digest,
            "manifest": _fake_manifest(source_commit, binaries),
        })
        return build_id

    def _read_tree_manifest(self, tree: Path) -> dict[str, Any] | None:
        return self._read_json(tree / "manifest.json")

    def read_manifest(self, tree: Path) -> dict[str, Any] | None:
        return self._read_tree_manifest(tree)

    def destroy_manifests(self) -> None:
        for tree in self._iter_known_trees():
            (tree / "manifest.json").unlink(missing_ok=True)

    def _iter_known_trees(self) -> list[Path]:
        trees = [self.active_root]
        if self.managed_root.exists():
            trees.extend(sorted(self.managed_root.rglob("tree-*")))
        return [t for t in trees if t.exists()]

    def snapshot_tree_bytes(self) -> dict[str, int]:
        snapshot: dict[str, int] = {}
        for tree in [self.active_root, self.retained_root]:
            for path in sorted(tree.rglob("*")) if tree.exists() else []:
                if path.is_file():
                    snapshot[str(path.relative_to(self.root))] = path.stat().st_size
        snapshot["__exists_active"] = int(self.active_root.exists())
        snapshot["__exists_retained"] = int(self.retained_root.exists())
        return snapshot

    def seed_promoted_runtime(self, label: str) -> str:
        """Create the initial active tree + retained copy + promoted row."""
        seed_tree = self.root / f"seed-{label}"
        build_id = self._materialize_tree(seed_tree, label.encode(), SOURCE_COMMIT_A)
        shutil.copytree(seed_tree, self.active_root)
        retained = self.retained_root / "tree-prior"
        shutil.copytree(seed_tree, retained)
        shutil.rmtree(seed_tree)
        with self.units.begin() as conn:
            builds = RuntimeBuildRepository(conn)
            builds.create_immutable(manifest=self._manifest_from_tree(self.active_root))
            trees = RuntimeTreeRepository(conn)
            trees.record_candidate(
                tree_id="tree-active-initial",
                build_id=build_id,
                container_profile="fake",
                locator="runtime/active",
                manifest_digest=self._tree_digest(self.active_root),
                server_binary_digest=self._server_digest(self.active_root),
            )
            trees.move_role("tree-active-initial", "ACTIVE_OBSERVED")
            trees.record_candidate(
                tree_id="tree-retained-prior",
                build_id=build_id,
                container_profile="fake",
                locator="managed-trees/retained/tree-prior",
                manifest_digest=self._tree_digest(retained),
                server_binary_digest=self._server_digest(retained),
            )
            trees.move_role("tree-retained-prior", "RETAINED")
            components = RuntimeComponentRepository(conn)
            components.initialize()
            current = components.current()
            components.promote_verified(
                expected_generation=current["generation"],
                expected_promoted_build_id=None,
                expected_rollback_build_id=None,
                promoted_build_id=build_id,
                rollback_build_id=None,
                promoted_tree_id="tree-active-initial",
                rollback_tree_id="tree-retained-prior",
            )
        self.label_to_build[label] = build_id
        self._write_json(self.service_path, {
            "running": True, "build_id": build_id,
            "alias": "demo-model", "invocations": 1,
        })
        return build_id

    def stage_candidate(self, label: str) -> str:
        """Pre-stage a validated candidate the way smoke would publish it."""
        self.counter += 1
        staging = self.managed_root / f"staged-{label}"
        build_id = self._materialize_tree(staging, label.encode(), SOURCE_COMMIT_B)
        self.label_to_build.setdefault(label, build_id)
        return build_id

    # -- identity helpers --------------------------------------------------------
    def build_id(self, label: str) -> str:
        return self.label_to_build[label]

    def _manifest_from_tree(self, tree: Path) -> dict[str, Any]:
        record = self._read_tree_manifest(tree)
        if record is None:
            raise RuntimeBuildError("TREE_MANIFEST_MISSING")
        return record["manifest"]

    def _tree_digest(self, tree: Path) -> str:
        record = self._read_tree_manifest(tree)
        return record["manifest_digest"] if record else "0" * 64

    def _server_digest(self, tree: Path) -> str:
        server = tree / "build/bin/llama-server"
        return _digest(server.read_bytes()) if server.exists() else "0" * 64

    def active_build_id(self) -> str | None:
        record = self._read_tree_manifest(self.active_root)
        return record.get("build_id") if record else None

    @property
    def retained_prior_root(self) -> Path:
        return self.retained_root / "tree-prior"

    def retained_prior_build_id(self) -> str | None:
        record = self._read_tree_manifest(self.retained_prior_root)
        return record.get("build_id") if record else None

    def _component_row(self) -> dict[str, Any]:
        with self.units.read() as conn:
            return RuntimeComponentRepository(conn).current()

    def service(self) -> dict[str, Any]:
        return self._read_json(self.service_path) or {}

    def _pulse_stub(self, *args: Any, **kwargs: Any) -> None:
        return None

    # =========================================================================
    # RuntimeLifecycleHost implementation
    # =========================================================================

    def resolve_source(self, request: Any) -> ResolvedRuntimeSourceV1:
        ref = getattr(request, "requested_ref", None) or DEFAULT_REQUESTED_REF
        commit = self.refs.get(ref)
        if commit is None:
            raise StepFailure(
                CODE_SOURCE_COMMIT_UNAVAILABLE,
                f"ref {ref!r} cannot be resolved to a commit",
            )
        return ResolvedRuntimeSourceV1(
            requested_ref=ref,
            source_commit=commit,
            resolution="COMMIT" if len(ref) == 40 else "PIN",
        )

    def observe_source_resolution(
        self, request: Any, evidence: ResolvedRuntimeSourceV1
    ) -> ProbeResult:
        current = self.refs.get(evidence.requested_ref)
        if current == evidence.source_commit:
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "SOURCE_REF_RESOLVED",
                output=evidence_dict_local(evidence),
            )
        if current is None:
            # A moved mutable ref is a permanent refusal for THIS attempt:
            # no visible state exists to restore (D2).
            return ProbeResult(RecoveryClass.DISCARDABLE, "SOURCE_REF_MOVED")
        return ProbeResult(RecoveryClass.ABSENT, "RESOLUTION_STALE")

    def observe_noop(self, request: Any) -> bool:
        resolved = self.resolve_source(request)
        expected = self.expected_build_for_commit(resolved.source_commit)
        component = self._component_row()
        if (
            expected is not None
            and component is not None
            and component.get("promoted_build_id") == expected
            and self.active_build_id() == expected
        ):
            return True
        return False

    def expected_build_for_commit(self, commit: str) -> str | None:
        for tree in self._iter_known_trees():
            record = self._read_tree_manifest(tree)
            if record and record.get("manifest", {}).get("source_commit") == commit:
                return record["build_id"]
        return None

    def preflight_build(self, request: Any) -> BuildPreflightEvidenceV1:
        required = 4 * 1024 * 1024 * 1024
        component = self._component_row()
        return BuildPreflightEvidenceV1(
            thermal_ok=self.thermal_ok,
            disk_ok=self.disk_free_bytes >= required,
            disk_required_bytes=required,
            disk_available_bytes=self.disk_free_bytes,
            filesystem_same_volume=True,
            atomic_exchange_supported=(
                self.atomic_exchange_supported and not self.exchange_unsupported
            ),
            active_runtime_proven=self.active_build_id() is not None
            or component is not None,
            legacy_adoption_used=False,
        )

    def observe_preflight(
        self, request: Any, evidence: BuildPreflightEvidenceV1
    ) -> ProbeResult:
        fresh = self.preflight_build(request)
        same = all(
            getattr(fresh, name) == getattr(evidence, name)
            for name in (
                "thermal_ok", "disk_ok", "filesystem_same_volume",
                "atomic_exchange_supported", "active_runtime_proven",
            )
        )
        if same and evidence.thermal_ok and evidence.disk_ok \
                and evidence.filesystem_same_volume \
                and evidence.atomic_exchange_supported:
            return ProbeResult(RecoveryClass.COMPLETE, "PREFLIGHT_OK",
                               output=evidence_dict_local(evidence))
        if not evidence.thermal_ok:
            return ProbeResult(
                RecoveryClass.DISCARDABLE, "THERMAL_LATCH_STOPPED"
            )
        if not evidence.atomic_exchange_supported:
            return ProbeResult(
                RecoveryClass.DISCARDABLE, "ATOMIC_EXCHANGE_UNSUPPORTED"
            )
        if not evidence.disk_ok:
            return ProbeResult(
                RecoveryClass.DISCARDABLE, "BUILD_DISK_INSUFFICIENT"
            )
        if not evidence.active_runtime_proven:
            return ProbeResult(
                RecoveryClass.DISCARDABLE, "ACTIVE_RUNTIME_UNPROVEN"
            )
        return ProbeResult(RecoveryClass.DISCARDABLE, "PREFLIGHT_CHANGED")

    # -- candidate build ----------------------------------------------------------
    def fetch_exact_commit(self, request, source_commit, pulse) -> FetchEvidenceV1:
        pulse(phase="fetch", current=1, total=2, cancellation_safe=True)
        self._crash_point("fetch_source", "mid_effect")
        checkout = self.sources_root / source_commit
        state = "EXISTING" if checkout.exists() else "CREATED"
        checkout.mkdir(parents=True, exist_ok=True)
        (checkout / ".checked-out").write_text(source_commit, encoding="utf-8")
        pulse(phase="fetch", current=2, total=2, cancellation_safe=True)
        return FetchEvidenceV1(
            source_commit=source_commit,
            checkout_locator=str(checkout.relative_to(self.root)),
            fetch_state=state,
            verified=(checkout / ".checked-out").read_text() == source_commit,
        )

    def probe_checkout(self, source_commit: str) -> ProbeResult:
        checkout = self.sources_root / source_commit
        marker = checkout / ".checked-out"
        if marker.exists():
            if marker.read_text(encoding="utf-8") == source_commit:
                return ProbeResult(RecoveryClass.COMPLETE, "CHECKOUT_PRESENT")
            return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "CHECKOUT_FOREIGN")
        if checkout.exists():
            return ProbeResult(RecoveryClass.PARTIALLY_RESUMABLE, "CHECKOUT_PARTIAL")
        return ProbeResult(RecoveryClass.ABSENT, "NO_CHECKOUT")

    def configure_build(self, request, source_commit, pulse) -> BuildEnvironmentEvidenceV1:
        pulse(phase="configure", current=1, cancellation_safe=True)
        self.counter += 1
        locator = f"managed-trees/candidate-{self.counter}"
        return BuildEnvironmentEvidenceV1(
            recipe_version=RECIPE_VERSION,
            recipe_digest=_digest(b"recipe-v1"),
            cmake_generator="Ninja",
            cmake_options=["-DGGML_VULKAN=ON"],
            cmake_targets=["llama-server", "llama-cli", "llama-quantize"],
            parallelism_policy="bounded-2",
            container_image_id=_digest(b"image"),
            container_image_digest=_digest(b"image-digest"),
            toolchain={"cmake": "fake-cmake", "cc": "fake-gcc"},
            target_arch="x86_64",
            build_dir_locator=locator,
        )

    def probe_build_environment(self, evidence: BuildEnvironmentEvidenceV1) -> ProbeResult:
        if not evidence.container_image_id or not evidence.toolchain:
            return ProbeResult(
                RecoveryClass.DISCARDABLE, "BUILD_ENVIRONMENT_UNPROVEN"
            )
        return ProbeResult(RecoveryClass.COMPLETE, "BUILD_ENVIRONMENT_FROZEN",
                           output=evidence_dict_local(evidence))

    def compile_candidate(
        self, environment: BuildEnvironmentEvidenceV1, pulse
    ) -> CandidateBuildEvidenceV1:
        build_dir = self.root / environment.build_dir_locator
        build_dir.mkdir(parents=True, exist_ok=True)
        pulse(phase="build", current=1, total=2, cancellation_safe=True)
        self._crash_point("compile_candidate", "mid_effect")
        binaries = []
        for name, blob in SERVER_BYTES.items():
            content = blob + environment.build_dir_locator.encode()
            relpath = f"build/bin/{name}"
            (build_dir / relpath).parent.mkdir(parents=True, exist_ok=True)
            (build_dir / relpath).write_bytes(content)
            binaries.append({
                "path": relpath,
                "size": len(content),
                "mode": "755",
                "sha256": _digest(content),
                "version_output_digest": _digest(b"version:" + content),
            })
        pulse(phase="build", current=2, total=2, cancellation_safe=True)
        return CandidateBuildEvidenceV1(
            build_dir_locator=environment.build_dir_locator,
            binaries=binaries,
        )

    def probe_compilation(
        self, environment: BuildEnvironmentEvidenceV1
    ) -> ProbeResult:
        build_dir = self.root / environment.build_dir_locator
        found = []
        for name in SERVER_BYTES:
            binary = build_dir / f"build/bin/{name}"
            if not binary.exists():
                return ProbeResult(
                    RecoveryClass.PARTIALLY_RESUMABLE, "COMPILATION_PARTIAL"
                )
            content = binary.read_bytes()
            found.append({
                "path": f"build/bin/{name}",
                "size": len(content),
                "mode": "755",
                "sha256": _digest(content),
                "version_output_digest": _digest(b"version:" + content),
            })
        return ProbeResult(RecoveryClass.COMPLETE, "COMPILATION_COMPLETE",
                           output={"binaries": found})

    def smoke_and_register_candidate(self, request, source_commit, environment,
                                     candidate, operation_id) -> SmokeEvidenceV1:
        self._crash_point("smoke_candidate", "mid_effect")
        build_id, manifest_digest = derive_build_id(
            _fake_manifest(source_commit, candidate.binaries)
        )
        tree = self.root / candidate.build_dir_locator
        self._write_json(tree / "manifest.json", {
            "build_id": build_id,
            "manifest_digest": manifest_digest,
            "manifest": _fake_manifest(source_commit, candidate.binaries),
        })
        tree_id = f"tree-{operation_id[:24]}"
        with self.units.begin() as conn:
            builds = RuntimeBuildRepository(conn)
            builds.create_immutable(
                manifest=_fake_manifest(source_commit, candidate.binaries),
                created_by_operation_id=operation_id,
            )
            verifications = RuntimeVerificationRepository(conn)
            verifications.append(
                build_id=build_id,
                kind="SMOKE",
                evidence={"ok": True, "latency_bucket": "sub_second"},
                operation_id=operation_id,
            )
            trees = RuntimeTreeRepository(conn)
            trees.record_candidate(
                tree_id=tree_id,
                build_id=build_id,
                container_profile="fake",
                locator=candidate.build_dir_locator,
                manifest_digest=manifest_digest,
                server_binary_digest=_server_digest_entry(candidate.binaries),
                created_by_operation_id=operation_id,
            )
        return SmokeEvidenceV1(
            build_id=build_id,
            manifest_digest=manifest_digest,
            smoke_contract_version=1,
            binaries_ok=True,
            tree_id=tree_id,
            locator=candidate.build_dir_locator,
        )

    def observe_candidate_manifest(self, smoke: SmokeEvidenceV1) -> ProbeResult:
        tree = self.root / smoke.locator
        record = self._read_tree_manifest(tree)
        if record is None:
            return ProbeResult(RecoveryClass.ABSENT, "CANDIDATE_MANIFEST_MISSING")
        if record.get("build_id") != smoke.build_id:
            return ProbeResult(
                RecoveryClass.UNCERTAIN_MANUAL, "CANDIDATE_IDENTITY_MISMATCH"
            )
        return ProbeResult(RecoveryClass.COMPLETE, "CANDIDATE_SMOKE_OK",
                           output=evidence_dict_local(smoke))

    # -- activation boundary ------------------------------------------------------
    def capture_activation_boundary(
        self, request: Any, target_build_id: str | None
    ) -> PriorRuntimeSnapshotV1:
        self._crash_point("capture_activation_boundary", "mid_effect")
        component = self._component_row()
        handoff = self._read_json(self.handoff_path)
        service = self.service()
        active_id = self.active_build_id()
        running = bool(service.get("running"))
        return PriorRuntimeSnapshotV1(
            service_state=(
                "ACTIVE_VERIFIED" if running and not self.inference_fails
                else PRIOR_STOPPED if running or active_id is not None
                else PRIOR_ABSENT
            ),
            active_build_id=active_id,
            active_tree_id=None,
            promoted_build_id=(component or {}).get("promoted_build_id"),
            rollback_build_id=(component or {}).get("rollback_build_id"),
            generation=(component or {}).get("generation"),
            known_good_component_identity=(component or {}).get("promoted_build_id"),
            handoff_fingerprint=(handoff or {}).get("runtime_fingerprint"),
            handoff_payload=handoff,
            invocation_count=int(service.get("invocations") or 0),
            observed_model_alias=service.get("alias") if running else None,
            observed_context_total=8192 if running else None,
            observed_slots=4 if running else None,
            inference_verified=running and not self.inference_fails,
        )

    def verify_activation_boundary(self, request, snapshot, target_build_id) -> None:
        expected = getattr(request, "expected_active_build_id", None)
        if expected is not None and snapshot.promoted_build_id != expected:
            raise StepFailure(
                CODE_ACTIVE_TREE_CHANGED,
                "active build changed before the boundary",
                mutation_possible=False,
            )

    # -- atomic exchange ------------------------------------------------------------
    def exchange_active_tree(self, snapshot, smoke, external_effect_id,
                             *, mode) -> TreeExchangeEvidenceV1:
        self._crash_point("exchange_active_tree", "after_step_start")
        if not (self.atomic_exchange_supported and not self.exchange_unsupported):
            raise StepFailure(
                "ATOMIC_EXCHANGE_UNSUPPORTED",
                "filesystem cannot exchange atomically",
                mutation_possible=False,
            )
        # Crash surface BEFORE the intent marker: dying here leaves a
        # RUNNING step with reality untouched, so takeover classifies ABSENT
        # and performs the ORIGINAL exchange exactly once.
        self._crash_point("exchange_active_tree", "before_swap")
        # Idempotent completion: the same effect id never swaps twice.
        if not self._record_effect(external_effect_id, "exchange"):
            return self._classify_exchange(snapshot, mode)

        active = self.active_root
        if snapshot.active_build_id is None and not active.exists():
            # Initial installation: no-replace publication.
            self._crash_point("exchange_active_tree", "before_publication")
            source = self.root / smoke.locator
            published = self._publish_initial(source, external_effect_id)
            self._crash_point("exchange_active_tree", "after_swap")
            return TreeExchangeEvidenceV1(
                classification="PUBLISHED_INITIAL",
                exchanged_now=published,
                active_build_id_after=self.active_build_id() or "",
            )

        # Managed exchange: rotate active -> retained-next, staged -> active.
        staged = self.root / smoke.locator
        self._crash_point("exchange_active_tree", "before_syscall")
        prior_slot = self.retained_root / f"tree-{external_effect_id[:16]}"
        if prior_slot.exists():
            shutil.rmtree(prior_slot)
        shutil.move(str(active), str(prior_slot))
        self._crash_point("exchange_active_tree", "mid_swap")
        shutil.move(str(staged), str(active))
        self._crash_point("exchange_active_tree", "after_swap")
        # Promote the displaced tree to the standard retained slot.
        displaced = self.retained_root / "tree-prior"
        if displaced.exists():
            shutil.rmtree(displaced)
        shutil.move(str(prior_slot), str(displaced))
        return TreeExchangeEvidenceV1(
            classification="EXCHANGED",
            exchanged_now=True,
            active_build_id_after=self.active_build_id() or "",
            prior_tree={
                "tree_id": "tree-retained-prior",
                "locator": "managed-trees/retained/tree-prior",
                "role": "RETAINED",
                "manifest_digest": self._tree_digest(displaced),
                "server_binary_digest": self._server_digest(displaced),
            },
        )

    def _publish_initial(self, source: Path, external_effect_id: str) -> bool:
        self._crash_point("exchange_active_tree", "publication_syscall")
        if self.active_root.exists():
            return False
        shutil.move(str(source), str(self.active_root))
        self._record_effect(f"{external_effect_id}:published", "exchange")
        return True

    def _classify_exchange(self, snapshot, mode) -> TreeExchangeEvidenceV1:
        return TreeExchangeEvidenceV1(
            classification="EXCHANGED", exchanged_now=False,
            active_build_id_after=self.active_build_id() or "",
        )

    def probe_exchange(self, snapshot, target_build_id, *, mode) -> ProbeResult:
        """Exact two-tree classifier (ADR 004 D8)."""
        active_record = self._read_tree_manifest(self.active_root)
        active_id = active_record.get("build_id") if active_record else None
        prior_record = self._read_tree_manifest(self.retained_prior_root)
        prior_id = prior_record.get("build_id") if prior_record else None
        if active_id is None:
            return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "NO_ACTIVE_IDENTITY")
        if active_id == target_build_id:
            if snapshot.active_build_id is None:
                return ProbeResult(
                    RecoveryClass.COMPLETE,
                    "PUBLISHED_INITIAL_VERIFIED",
                    output=evidence_dict_local(TreeExchangeEvidenceV1(
                        classification="PUBLISHED_INITIAL",
                        exchanged_now=False,
                        active_build_id_after=active_id or "",
                    )),
                )
            if prior_id == snapshot.active_build_id:
                displaced = self.retained_prior_root
                return ProbeResult(
                    RecoveryClass.COMPLETE,
                    "TREE_EXCHANGE_COMPLETED",
                    output=evidence_dict_local(TreeExchangeEvidenceV1(
                        classification="EXCHANGED",
                        exchanged_now=False,
                        active_build_id_after=active_id or "",
                        prior_tree={
                            "tree_id": "tree-retained-prior",
                            "locator": "managed-trees/retained/tree-prior",
                            "role": "RETAINED",
                            "manifest_digest": prior_record["manifest_digest"],
                            "server_binary_digest":
                                self._server_digest(displaced),
                        },
                    )),
                )
            return ProbeResult(
                RecoveryClass.UNCERTAIN_MANUAL, "PRIOR_RETAINED_UNPROVEN"
            )
        if active_id == snapshot.active_build_id:
            # Prior still active: does any operation-owned tree carry the
            # exact target identity?
            candidates = [
                p for p in sorted(self.managed_root.glob("*")) if p.is_dir()
            ]
            still_staged = any(
                (self._read_tree_manifest(p) or {}).get("build_id")
                == target_build_id
                for p in candidates
            )
            if still_staged:
                return ProbeResult(RecoveryClass.ABSENT, "EXCHANGE_NOT_LANDED")
            return ProbeResult(
                RecoveryClass.UNCERTAIN_MANUAL, "TARGET_TREE_UNPROVABLE"
            )
        return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "ARRANGEMENT_UNKNOWN")

    def verify_exchange(self, snapshot, target_build_id, *, mode) -> None:
        result = self.probe_exchange(snapshot, target_build_id, mode=mode)
        if result.classification is not RecoveryClass.COMPLETE:
            raise StepFailure(
                "TREE_EXCHANGE_UNCERTAIN",
                f"exchange postcondition not proven ({result.reason_code})",
                mutation_possible=True,
            )

    # -- handoff v2 / restart / verification -------------------------------------------
    def publish_handoff_v2(self, snapshot, target_build_id, operation_id,
                           *, mode) -> HandoffComponentEvidenceV1:
        self._crash_point("publish_component_handoff", "mid_effect")
        if self.handoff_publish_fails:
            raise StepFailure(
                "HANDOFF_COMPONENT_PUBLISHED",
                "handoff publication failed",
                mutation_possible=False,
            )
        tree = self._tree_for_build(target_build_id)
        record = self._read_tree_manifest(tree) if tree else None
        payload = {
            "schema_version": 2,
            "config_revision": 1,
            "runtime_fingerprint": _digest(target_build_id.encode())[:16],
            "model_id": "demo-model",
            "alias": "demo-model",
            "port": 8080,
            "ctx_total": 32768,
            "parallel_slots": 4,
            "runtime_component_id": target_build_id,
            "runtime_source_commit": (record or {}).get("manifest", {}).get(
                "source_commit"
            ),
            "runtime_server_sha256": self._server_digest(tree) if tree else "",
            "runtime_manifest_digest": (record or {}).get("manifest_digest"),
            "runtime_operation_id": operation_id,
        }
        self._write_json(self.handoff_path, payload)
        return HandoffComponentEvidenceV1(
            fingerprint=payload["runtime_fingerprint"],
            schema_version=2,
            component_id=target_build_id,
            server_sha256=payload["runtime_server_sha256"],
            manifest_digest=payload["runtime_manifest_digest"],
            operation_id=operation_id,
        )

    def _tree_for_build(self, build_id: str) -> Path | None:
        if self.active_build_id() == build_id:
            return self.active_root
        for tree in sorted(self.managed_root.glob("*")):
            record = self._read_tree_manifest(tree)
            if record and record.get("build_id") == build_id:
                return tree
        for tree in sorted(self.retained_root.glob("*")):
            record = self._read_tree_manifest(tree)
            if record and record.get("build_id") == build_id:
                return tree
        return None

    def observe_handoff_v2(self, snapshot, target_build_id, *, mode) -> ProbeResult:
        payload = self._read_json(self.handoff_path)
        if payload is None:
            if snapshot.handoff_payload:
                return ProbeResult(RecoveryClass.ABSENT, "HANDOFF_REMOVED")
            return ProbeResult(RecoveryClass.ABSENT, "NO_HANDOFF")
        if payload.get("runtime_component_id") == target_build_id \
                and int(payload.get("schema_version") or 0) == 2:
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "HANDOFF_COMPONENT_PUBLISHED",
                output=evidence_dict_local(
                    HandoffComponentEvidenceV1(
                        fingerprint=payload["runtime_fingerprint"],
                        schema_version=2,
                        component_id=target_build_id,
                        server_sha256=payload["runtime_server_sha256"],
                        manifest_digest=payload["runtime_manifest_digest"],
                        operation_id=payload.get("runtime_operation_id") or "",
                    )
                ),
            )
        prior_component = (snapshot.handoff_payload or {}).get(
            "runtime_component_id"
        )
        if prior_component and payload.get("runtime_component_id") == prior_component:
            return ProbeResult(RecoveryClass.ABSENT, "EXACT_PRIOR_HANDOFF")
        return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "THIRD_PARTY_HANDOFF")

    def restart_for_runtime_change(self, snapshot, target_build_id,
                                   operation_id, *, mode) -> ServiceRestartEvidenceV1:
        self._crash_point("restart_runtime", "mid_effect")
        if self.restart_fails:
            raise StepFailure(
                "SERVICE_RESTART_FAILED", "restart command failed",
                mutation_possible=False,
            )
        service = self.service()
        already = (
            bool(service.get("running"))
            and service.get("build_id") == target_build_id
            and int(service.get("invocations") or 0)
            > int(snapshot.invocation_count or 0)
        )
        if already:
            receipt = self._read_json(self.receipt_path) or {}
            return ServiceRestartEvidenceV1(
                restarted_now=False,
                was_already_active=True,
                invocation_nonce=receipt.get("nonce", ""),
                receipt_present=bool(receipt),
            )
        nonce = f"{operation_id}:{int(service.get('invocations') or 0) + 1}"
        self._write_json(self.service_path, {
            **service,
            "running": True,
            "build_id": target_build_id,
            "alias": "demo-model",
            "invocations": int(service.get("invocations") or 0) + 1,
        })
        tree = self._tree_for_build(target_build_id)
        self._write_json(self.receipt_path, {
            "nonce": nonce,
            "build_id": target_build_id,
            "server_sha256": self._server_digest(tree) if tree else "",
            "operation_id": operation_id,
        })
        return ServiceRestartEvidenceV1(
            restarted_now=True, was_already_active=False,
            invocation_nonce=nonce, receipt_present=True,
        )

    def observe_invocation(self, snapshot, target_build_id, *, mode) -> ProbeResult:
        service = self.service()
        if not service.get("running"):
            return ProbeResult(RecoveryClass.REVERTIBLE, "SERVICE_INACTIVE")
        receipt = self._read_json(self.receipt_path) or {}
        receipt_ok = (
            receipt.get("build_id") == target_build_id
            and int(service.get("invocations") or 0)
            > int(snapshot.invocation_count or 0)
        )
        if service.get("build_id") != target_build_id:
            if service.get("build_id") == snapshot.active_build_id:
                return ProbeResult(RecoveryClass.REVERTIBLE, "PRIOR_STILL_RUNNING")
            return ProbeResult(
                RecoveryClass.UNCERTAIN_MANUAL, "INVOCATION_IDENTITY_AMBIGUOUS"
            )
        if not receipt_ok:
            return ProbeResult(
                RecoveryClass.UNCERTAIN_MANUAL, "STALE_START_RECEIPT"
            )
        return ProbeResult(
            RecoveryClass.COMPLETE,
            "NEW_INVOCATION_PROVEN",
            output=evidence_dict_local(ServiceRestartEvidenceV1(
                restarted_now=False,
                was_already_active=True,
                invocation_nonce=receipt.get("nonce", ""),
                receipt_present=True,
            )),
        )

    def verify_runtime_identity(self, snapshot, target_build_id) -> RuntimeIdentityEvidenceV1:
        service = self.service()
        tree = self._tree_for_build(target_build_id)
        running_target = service.get("build_id") == target_build_id
        return RuntimeIdentityEvidenceV1(
            component_ok=running_target,
            binary_digest_ok=bool(tree) and self._server_digest(tree)
            == ((self._read_json(self.receipt_path) or {}).get("server_sha256")),
            model_alias_ok=(service.get("alias") == "demo-model"),
            context_ok=True,
            slots_ok=True,
            health_ok=bool(service.get("running")) and running_target,
            observed_model_alias=str(service.get("alias") or ""),
        )

    def verify_runtime_inference(self, target_build_id) -> RuntimeInferenceEvidenceV1:
        service = self.service()
        ok = (
            not self.inference_fails
            and bool(service.get("running"))
            and service.get("build_id") == target_build_id
        )
        return RuntimeInferenceEvidenceV1(
            success=ok,
            generated_count=1 if ok else 0,
            latency_bucket="sub_second" if ok else "slow",
        )

    # -- promotion / restoration / finalization --------------------------------------------
    def promote_verified_runtime(self, snapshot, target_build_id, smoke,
                                 operation_id, *, mode) -> RuntimePromotionEvidenceV1:
        self._crash_point("promote_runtime", "mid_effect")
        with self.units.begin() as conn:
            components = RuntimeComponentRepository(conn)
            trees = RuntimeTreeRepository(conn)
            current = components.current() or components.initialize()
            # Idempotency: a death before checkpoint replays this effect;
            # if durable lineage ALREADY matches the goal, do nothing.
            if mode == "update":
                goal_rollback = snapshot.promoted_build_id
                if (
                    current["promoted_build_id"] == target_build_id
                    and current["rollback_build_id"] == goal_rollback
                    and current["generation"] >= int(snapshot.generation or 1)
                ):
                    return RuntimePromotionEvidenceV1(
                        generation_after=current["generation"],
                        promoted_build_id=target_build_id,
                        rollback_build_id=goal_rollback,
                        promoted_tree_id=current.get("promoted_tree_id"),
                        rollback_tree_id=current.get("rollback_tree_id"),
                        noop=True,
                    )
                if smoke is not None:
                    trees.observe_location(smoke.tree_id)
                    trees.move_role(smoke.tree_id, "ACTIVE_OBSERVED")
                retained_id = self._retained_tree_id(trees)
                promoted = components.promote_verified(
                    expected_generation=int(current["generation"]),
                    expected_promoted_build_id=current["promoted_build_id"],
                    expected_rollback_build_id=current["rollback_build_id"],
                    promoted_build_id=target_build_id,
                    rollback_build_id=snapshot.promoted_build_id,
                    promoted_tree_id=smoke.tree_id if smoke else None,
                    rollback_tree_id=retained_id,
                    operation_id=operation_id,
                    known_good_identity={
                        "runtime_fingerprint":
                            _digest(target_build_id.encode())[:16],
                        "runtime_component_identity": target_build_id,
                    },
                )
                return RuntimePromotionEvidenceV1(
                    generation_after=int(promoted["generation"]),
                    promoted_build_id=target_build_id,
                    rollback_build_id=snapshot.promoted_build_id,
                    promoted_tree_id=smoke.tree_id if smoke else None,
                    rollback_tree_id="tree-retained-prior",
                )
            # Rollback toggle: target becomes promoted; former active becomes
            # the next rollback target (D7).
            former_active = snapshot.promoted_build_id
            if (
                current["promoted_build_id"] == target_build_id
                and current["rollback_build_id"] == former_active
            ):
                return RuntimePromotionEvidenceV1(
                    generation_after=int(current["generation"]),
                    promoted_build_id=target_build_id,
                    rollback_build_id=former_active,
                    noop=True,
                )
            target_tree = None
            if smoke is not None:
                target_tree = smoke.tree_id
            restored = components.record_restoration(
                expected_generation=int(current["generation"]),
                expected_promoted_build_id=current["promoted_build_id"],
                expected_rollback_build_id=current.get("rollback_build_id"),
                restored_promoted_build_id=target_build_id,
                new_rollback_build_id=former_active,
                promoted_tree_id=target_tree,
                operation_id=operation_id,
                known_good_identity={
                    "runtime_fingerprint": _digest(target_build_id.encode())[:16],
                    "runtime_component_identity": target_build_id,
                },
            )
            return RuntimePromotionEvidenceV1(
                generation_after=int(restored["generation"]),
                promoted_build_id=target_build_id,
                rollback_build_id=former_active,
                promoted_tree_id=target_tree,
            )

    def _retained_tree_id(self, trees=None) -> str | None:
        """Existing retained-tree row id for the displaced active build."""
        record = self._read_tree_manifest(self.retained_prior_root)
        if record is None:
            return None
        return "tree-retained-prior"

    def observe_promotion(self, snapshot, target_build_id, *, mode) -> ProbeResult:
        component = self._component_row()
        if component is None:
            return ProbeResult(RecoveryClass.ABSENT, "NO_COMPONENT_ROW")
        expected_rollback = (
            snapshot.promoted_build_id if mode == "update" else snapshot.promoted_build_id
        )
        if (
            component.get("promoted_build_id") == target_build_id
            and int(component.get("generation") or 0)
            >= int(snapshot.generation or 1)
            and (mode != "rollback"
                 or component.get("rollback_build_id") == expected_rollback)
        ):
            return ProbeResult(
                RecoveryClass.COMPLETE, "RUNTIME_PROMOTED",
                output=evidence_dict_local(RuntimePromotionEvidenceV1(
                    generation_after=int(component["generation"]),
                    promoted_build_id=target_build_id,
                    rollback_build_id=component.get("rollback_build_id"),
                )),
            )
        if component.get("promoted_build_id") == snapshot.promoted_build_id:
            return ProbeResult(RecoveryClass.REVERTIBLE, "PROMOTION_NOT_APPLIED")
        return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "LINEAGE_AMBIGUOUS")

    def restore_prior_runtime(self, snapshot, restoration_id, *, mode
                              ) -> RuntimeRestorationEvidenceV1:
        stages: list[str] = []
        # 1. Reverse the exchange exactly once (deduped by restoration id).
        if snapshot.active_build_id is not None \
                and self.active_build_id() != snapshot.active_build_id:
            if self._record_effect(f"restore:{restoration_id}", "reverse_exchange"):
                current_active = self.active_root
                displaced = self.retained_root / "tree-prior"
                prior_record = self._read_tree_manifest(displaced)
                if prior_record is None or prior_record.get("build_id") \
                        != snapshot.active_build_id:
                    raise StepFailure(
                        "RUNTIME_RESTORATION_UNCERTAIN",
                        "the exact prior tree is not in the retained slot",
                        mutation_possible=True,
                    )
                park = self.retained_root / f"park-{restoration_id[:16]}"
                shutil.move(str(current_active), str(park))
                shutil.move(str(displaced), str(current_active))
                shutil.move(str(park), str(displaced))
                stages.append("REVERSE_EXCHANGED")
        elif snapshot.active_build_id is None and self.active_root.exists():
            # Initial-install compensation: remove only our published tree
            # when its identity proves ownership.
            record = self._read_tree_manifest(self.active_root)
            if record is None:
                raise StepFailure(
                    "RUNTIME_RESTORATION_UNCERTAIN",
                    "cannot prove ownership of the published tree",
                    mutation_possible=True,
                )
            shutil.rmtree(self.active_root)
            stages.append("INITIAL_PUBLICATION_REMOVED")
        else:
            stages.append("TREE_ALREADY_PRIOR")
        # 2. Restore prior handoff identity.
        prior_handoff = snapshot.handoff_payload
        if prior_handoff:
            self._write_json(self.handoff_path, prior_handoff)
            stages.append("HANDOFF_RESTORED")
        elif self.handoff_path.exists():
            self.handoff_path.unlink()
            stages.append("HANDOFF_REMOVED")
        # 3. Restore prior service state.
        service = self.service()
        if snapshot.service_state in ("ACTIVE_VERIFIED", PRIOR_STOPPED) \
                and snapshot.invocation_count is not None:
            self._write_json(self.service_path, {
                **service,
                "running": snapshot.service_state == "ACTIVE_VERIFIED",
                "build_id": snapshot.active_build_id,
                "invocations": max(
                    int(service.get("invocations") or 0),
                    int(snapshot.invocation_count or 0),
                )
                if snapshot.service_state == "ACTIVE_VERIFIED"
                else int(snapshot.invocation_count or 0),
            })
            stages.append("SERVICE_STATE_RESTORED")
        else:
            self._write_json(self.service_path, {
                **service, "running": False, "build_id": None,
            })
            stages.append("SERVICE_STOPPED")
        # 4. Restore prior database lineage.
        component = self._component_row()
        if component and snapshot.generation is not None \
                and component.get("promoted_build_id") != snapshot.promoted_build_id:
            with self.units.begin() as conn:
                components = RuntimeComponentRepository(conn)
                restored = components.record_restoration(
                    expected_generation=int(component["generation"]),
                    expected_promoted_build_id=component["promoted_build_id"],
                    expected_rollback_build_id=component.get("rollback_build_id"),
                    restored_promoted_build_id=snapshot.promoted_build_id or "",
                    new_rollback_build_id=component.get("promoted_build_id"),
                )
            stages.append("LINEAGE_TOGGLED_BACK")
        return RuntimeRestorationEvidenceV1(
            restored=True, stage_codes=stages,
            service_state=snapshot.service_state,
        )

    def observe_restoration(self, snapshot, *, mode) -> ProbeResult:
        active_ok = self.active_build_id() == snapshot.active_build_id
        if snapshot.active_build_id is None:
            active_ok = not self.active_root.exists()
        handoff = self._read_json(self.handoff_path)
        prior_component = (snapshot.handoff_payload or {}).get(
            "runtime_component_id"
        )
        if snapshot.handoff_payload:
            handoff_ok = (
                handoff is not None
                and handoff.get("runtime_component_id") == prior_component
            )
        else:
            handoff_ok = handoff is None
        service = self.service()
        if snapshot.service_state == "ACTIVE_VERIFIED":
            service_ok = (
                bool(service.get("running"))
                and service.get("build_id") == snapshot.active_build_id
                and int(service.get("invocations") or 0)
                >= int(snapshot.invocation_count or 0)
            )
        else:
            service_ok = not service.get("running")
        component = self._component_row()
        lineage_ok = (
            component is None
            or snapshot.generation is None
            or (
                component.get("promoted_build_id") == snapshot.promoted_build_id
                and int(component.get("generation") or 0)
                >= int(snapshot.generation or 0)
            )
        )
        if active_ok and handoff_ok and service_ok and lineage_ok:
            return ProbeResult(
                RecoveryClass.COMPLETE, "PRIOR_RUNTIME_RESTORED",
                output=evidence_dict_local(RuntimeRestorationEvidenceV1(
                    restored=True, service_state=snapshot.service_state,
                )),
            )
        known_prior = (
            snapshot.promoted_build_id is not None
            or snapshot.service_state in ("ACTIVE_VERIFIED", PRIOR_STOPPED)
        )
        if known_prior:
            return ProbeResult(RecoveryClass.REVERTIBLE, "RESTORATION_INCOMPLETE")
        return ProbeResult(
            RecoveryClass.UNCERTAIN_MANUAL, "NO_DURABLE_PRIOR_EVIDENCE"
        )

    # -- finalization ------------------------------------------------------------------
    def finalize_trees(self, snapshot, target_build_id, promotion, exchange,
                       *, mode) -> RuntimeCleanupEvidenceV1:
        deferred: list[str] = []
        removed: list[str] = []
        retained: list[str] = []
        with self.units.begin() as conn:
            trees = RuntimeTreeRepository(conn)
            protected = trees.protected_tree_ids(exclude_operation_id=None)
            # Mark roles for known trees.
            if self.active_root.exists():
                record = self._read_tree_manifest(self.active_root)
                if record:
                    row = trees.by_locator("runtime/active")
                    if row is None:
                        trees.record_candidate(
                            tree_id=f"tree-active-{record['build_id'][:16]}",
                            build_id=record["build_id"],
                            container_profile="fake",
                            locator="runtime/active",
                            manifest_digest=record["manifest_digest"],
                            server_binary_digest=self._server_digest(
                                self.active_root
                            ),
                        )
                        row = trees.by_locator("runtime/active")
                    if row["role"] != "ACTIVE_OBSERVED":
                        trees.move_role(row["tree_id"], "ACTIVE_OBSERVED")
            if self.retained_prior_root.exists():
                row = trees.by_locator("managed-trees/retained/tree-prior")
                if row is not None and row["role"] != "RETAINED":
                    trees.move_role(row["tree_id"], "RETAINED")
                retained.append("managed-trees/retained/tree-prior")
            # Cleanup: only OPERATION_OWNED, CANDIDATE-role, unprotected.
            candidates = [
                dict(r)
                for r in conn.execute(
                    "SELECT tree_id, locator FROM runtime_trees "
                    "WHERE role='CANDIDATE'"
                ).fetchall()
            ]
            for row in candidates:
                if row["tree_id"] in protected:
                    continue
                path = self.root / row["locator"]
                if path.exists() and str(path).startswith(str(self.managed_root)):
                    shutil.rmtree(path)
                    removed.append(row["locator"])
                    trees.move_role(row["tree_id"], "QUARANTINED")
                else:
                    deferred.append(row["locator"])
        return RuntimeCleanupEvidenceV1(
            retained_locators=retained,
            removed_locators=removed,
            deferred_locators=deferred,
        )

    def observe_finalization(self, snapshot, target_build_id, *,
                             mode) -> ProbeResult:
        return ProbeResult(
            RecoveryClass.COMPLETE, "TREES_FINALIZED",
            output=evidence_dict_local(RuntimeCleanupEvidenceV1()),
        )


# -- local helpers -------------------------------------------------------------------


def evidence_dict_local(evidence: Any) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(evidence)


def _server_digest_entry(binaries: list[dict[str, Any]]) -> str:
    for entry in binaries:
        if entry.get("path", "").endswith("llama-server"):
            return str(entry["sha256"])
    return binaries[0]["sha256"] if binaries else "0" * 64


# Snapshot helper used by observe_restoration for the absent-config case.


def _snapshot_has_config(snapshot: PriorRuntimeSnapshotV1) -> bool:
    return True


PriorRuntimeSnapshotV1.config_known = lambda self: (  # type: ignore[attr-defined]
    self.promoted_build_id is not None
    or self.service_state in ("ACTIVE_VERIFIED", PRIOR_STOPPED)
)


# -- rollback-specific port seams (appended to FakeRuntimeHost) -------------------


def _resolve_rollback_target(self, request) -> RollbackTargetEvidenceV1:
    component = self._component_row()
    target = getattr(request, "target_build_id", None)
    promoted = (component or {}).get("promoted_build_id")
    rollback_target = (component or {}).get("rollback_build_id")
    if not target or not promoted:
        raise StepFailure(
            "RUNTIME_ROLLBACK_TARGET_MISSING",
            "no durable rollback target is recorded",
        )
    if target != rollback_target:
        raise StepFailure(
            "RUNTIME_ROLLBACK_TARGET_MISSING",
            "requested target is not the durable current rollback target",
        )
    tree = self._tree_for_build(target)
    record = self._read_tree_manifest(tree) if tree else None
    if record is None:
        raise StepFailure(
            "RUNTIME_ROLLBACK_TARGET_MISSING",
            "the retained target tree cannot be identified",
        )
    return RollbackTargetEvidenceV1(
        target_build_id=target,
        current_promoted_build_id=promoted,
        generation=int((component or {}).get("generation") or 0),
        target_tree_id="tree-retained-prior"
        if self.retained_prior_build_id() == target
        else f"tree-{target[:24]}",
        target_locator=str((tree or self.root).relative_to(self.root)),
        manifest_digest=record["manifest_digest"],
        server_binary_digest=self._server_digest(tree),
    )


def _observe_rollback_target(self, request, evidence) -> ProbeResult:
    try:
        fresh = _resolve_rollback_target(self, request)
    except StepFailure as failure:
        if failure.code == "RUNTIME_ROLLBACK_TARGET_MISSING":
            return ProbeResult(RecoveryClass.DISCARDABLE, failure.code)
        raise
    if fresh.target_build_id == evidence.target_build_id \
            and fresh.manifest_digest == evidence.manifest_digest:
        return ProbeResult(RecoveryClass.COMPLETE, "ROLLBACK_TARGET_RESOLVED",
                           output=evidence_dict_local(evidence))
    return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "TARGET_IDENTITY_MOVED")


def _preflight_rollback(self, request, target) -> BuildPreflightEvidenceV1:
    component = self._component_row()
    service = self.service()
    active_matches_promoted = (
        service.get("build_id") == (component or {}).get("promoted_build_id")
        or not service.get("running")
    )
    return BuildPreflightEvidenceV1(
        thermal_ok=self.thermal_ok,
        disk_ok=True,
        disk_required_bytes=0,
        disk_available_bytes=self.disk_free_bytes,
        filesystem_same_volume=True,
        atomic_exchange_supported=(
            self.atomic_exchange_supported and not self.exchange_unsupported
        ),
        active_runtime_proven=bool(active_matches_promoted),
        legacy_adoption_used=False,
    )


def _observe_preflight_rollback(self, request, target, evidence) -> ProbeResult:
    fresh = _preflight_rollback(self, request, target)
    ok = (
        evidence.thermal_ok
        and evidence.atomic_exchange_supported
        and evidence.active_runtime_proven
    )
    same = all(
        getattr(fresh, name) == getattr(evidence, name)
        for name in ("thermal_ok", "atomic_exchange_supported")
    )
    if ok and same:
        return ProbeResult(RecoveryClass.COMPLETE, "ROLLBACK_PREFLIGHT_OK",
                           output=evidence_dict_local(evidence))
    if not evidence.thermal_ok:
        return ProbeResult(RecoveryClass.DISCARDABLE, "THERMAL_LATCH_STOPPED")
    if not evidence.atomic_exchange_supported:
        return ProbeResult(
            RecoveryClass.DISCARDABLE, "ATOMIC_EXCHANGE_UNSUPPORTED"
        )
    return ProbeResult(RecoveryClass.REVERTIBLE, "ACTIVE_STATE_CHANGED")


def _smoke_rollback_target(self, target) -> SmokeEvidenceV1:
    self._crash_point("smoke_rollback_target", "mid_effect")
    tree = self._tree_for_build(target.target_build_id)
    record = self._read_tree_manifest(tree) if tree else None
    if record is None or record.get("build_id") != target.target_build_id:
        raise StepFailure(
            "CANDIDATE_SMOKE_FAILED",
            "retained tree identity does not match the recorded build",
        )
    with self.units.begin() as conn:
        RuntimeVerificationRepository(conn).append(
            build_id=target.target_build_id,
            kind="SMOKE",
            evidence={"ok": True, "latency_bucket": "sub_second"},
        )
    return SmokeEvidenceV1(
        build_id=target.target_build_id,
        manifest_digest=record["manifest_digest"],
        smoke_contract_version=1,
        binaries_ok=True,
        tree_id=target.target_tree_id,
        locator=str((tree or self.root).relative_to(self.root)),
    )


def _observe_rollback_manifest(self, target) -> ProbeResult:
    tree = self._tree_for_build(target.target_build_id)
    record = self._read_tree_manifest(tree) if tree else None
    if record is None:
        return ProbeResult(RecoveryClass.ABSENT, "RETAINED_MANIFEST_MISSING")
    if record.get("build_id") == target.target_build_id:
        return ProbeResult(RecoveryClass.COMPLETE, "RETAINED_SMOKE_OK")
    return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "RETAINED_IDENTITY_MOVED")


FakeRuntimeHost.resolve_rollback_target = _resolve_rollback_target
FakeRuntimeHost.observe_rollback_target = _observe_rollback_target
FakeRuntimeHost.preflight_rollback = _preflight_rollback
FakeRuntimeHost.observe_preflight_rollback = _observe_preflight_rollback
FakeRuntimeHost.smoke_rollback_target = _smoke_rollback_target
FakeRuntimeHost.observe_rollback_manifest = _observe_rollback_manifest
