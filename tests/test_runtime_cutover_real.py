"""Production cutover/recovery with real directories and the shipped helper."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import shutil
import uuid
import subprocess

import pytest

from test_runtime_adapter_real import real_adapter
from test_runtime_migration import _manifest
from bc250_llm_mode.operations.recovery import RecoveryClass
from bc250_llm_mode.operations.runtime_lifecycle import SmokeEvidenceV1, RuntimeUpdateRequestV1
from bc250_llm_mode.operations.workflow import StepFailure
from bc250_llm_mode.runtime_builds import RuntimeBuildRepository, RuntimeTreeRepository, RuntimeComponentRepository
from bc250_llm_mode.runtime_handoff import RuntimeHandoffRenderer

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="real Linux runtime cutover")


class ServiceFixture:
    def __init__(self, renderer):
        self.renderer = renderer
        self.running = True
        self.starts = 1
        self.replies = {"healthy": True, "model_id": "Fixture model", "model_matches_desired": True,
                        "context_per_slot": 4096, "context_total": 8192, "n_ctx": 4096, "parallel_slots": 2}

    def capture(self, view):
        return {"active": self.running, "invocation_marker": str(self.starts)}

    def health(self, view, **kwargs):
        if kwargs.get("pulse"):
            kwargs["pulse"]()
        return dict(self.replies)

    def inference(self, view, **kwargs):
        return {"ok": True}

    def restart(self, view):
        self.starts += 1
        self.running = True
        payload = self.renderer.observe()
        if payload.get("schema_version") == 2:
            receipt = {"build_id": payload["runtime_component_id"],
                       "server_sha256": payload["runtime_server_sha256"],
                       "manifest_digest": payload["runtime_manifest_digest"],
                       "operation_id": payload["runtime_operation_id"], "nonce": str(self.starts)}
            (self.renderer.path.parent / "start-receipt.json").write_text(json.dumps(receipt))

    def stop(self, view):
        self.running = False


def seed_tree(adapter, root, name, *, legacy=False):
    root = Path(root)
    binary = root / "build/bin/llama-server"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nprintf '" + name + "\\n'\n")
    binary.chmod(0o755)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    if legacy:
        return None
    manifest = _manifest(binaries=[{"path": "build/bin/llama-server", "size": binary.stat().st_size,
                                   "mode": "755", "sha256": digest, "version_output_digest": "d" * 64}])
    with adapter._units.begin() as conn:
        record = RuntimeBuildRepository(conn).create_immutable(manifest=manifest)
        row = RuntimeTreeRepository(conn).record_candidate(
            tree_id="tree-" + name, build_id=record["build_id"], container_profile="fixture",
            locator=str(root).lstrip("/"), manifest_digest=record["manifest_digest"], server_binary_digest=digest)
    adapter._write_manifest_to_tree(str(root), record["build_id"], record["manifest_digest"], manifest)
    return SmokeEvidenceV1(record["build_id"], record["manifest_digest"], 1, True, row["tree_id"], row["locator"])


@pytest.fixture
def cutover(real_adapter, tmp_path):
    adapter, _, _ = real_adapter
    renderer = RuntimeHandoffRenderer(tmp_path / "profile")
    view = {"current_model": "fixture", "current_ctx": 4096, "llama_cpp_path": adapter._loc.active_root,
            "server_port": 8080, "optimizations": {"parallel_slots": 2},
            "installed_models": [{"id": "fixture", "display_name": "Fixture model", "path": "/fixture.gguf"}],
            "revision": 1}
    adapter.renderer = renderer
    adapter.state_supplier = lambda: dict(view)
    adapter.server_port = ServiceFixture(renderer)
    renderer.publish(view, config_revision=1)
    return adapter


def test_legacy_adoption_records_observed_manifest_without_promotion(cutover):
    seed_tree(cutover, cutover._loc.active_root, "legacy", legacy=True)
    build = cutover.ensure_runtime_registered()
    payload = cutover.read_active_manifest()
    assert payload and payload["build_id"] == build
    with cutover._units.read() as conn:
        assert RuntimeBuildRepository(conn).require(build)["provenance_class"] == "LEGACY_UNVERIFIED"
        assert not (RuntimeComponentRepository(conn).current() or {}).get("promoted_build_id")
    assert cutover.ensure_runtime_registered() == build
    Path(cutover._loc.active_root, "build/bin/llama-server").write_text("replaced")
    with pytest.raises(StepFailure):
        cutover.ensure_runtime_registered()


def test_death_after_atomic_swap_preserves_and_recovers_exact_pair(cutover):
    prior = seed_tree(cutover, cutover._loc.active_root, "prior")
    target = seed_tree(cutover, Path(cutover._loc.managed_root) / "candidate-fixture", "target")
    unrelated = Path(cutover._loc.managed_root) / "prior"
    unrelated.mkdir()
    (unrelated / "owner.txt").write_text("preserve")
    snapshot = cutover.capture_activation_boundary(RuntimeUpdateRequestV1(), target.build_id)
    class Death(BaseException):
        pass
    def crash(step, point):
        if point == "after_swap":
            raise Death()
    cutover._effect_crash_hook = crash
    with pytest.raises(Death):
        cutover.exchange_active_tree(snapshot, target, "fixture-effect", mode="update")
    cutover._effect_crash_hook = None
    assert cutover.probe_exchange(snapshot, target.build_id, mode="update").classification is RecoveryClass.COMPLETE
    repeated = cutover.exchange_active_tree(snapshot, target, "fixture-effect", mode="update")
    assert not repeated.exchanged_now
    assert cutover.read_active_manifest()["build_id"] == target.build_id
    assert json.loads(Path("/" + target.locator, "manifest.json").read_text())["build_id"] == prior.build_id
    assert (unrelated / "owner.txt").read_text() == "preserve"
    cutover.restore_prior_runtime(snapshot, "fixture-restore", mode="update")
    assert cutover.observe_restoration(snapshot, mode="update").classification is RecoveryClass.COMPLETE
    assert cutover.read_active_manifest()["build_id"] == prior.build_id


def test_live_identity_requires_receipt_hash_and_exact_observed_settings(cutover):
    prior = seed_tree(cutover, cutover._loc.active_root, "prior")
    target = seed_tree(cutover, Path(cutover._loc.managed_root) / "candidate-fixture", "target")
    snapshot = cutover.capture_activation_boundary(RuntimeUpdateRequestV1(), target.build_id)
    cutover.exchange_active_tree(snapshot, target, "fixture-effect", mode="update")
    cutover.publish_handoff_v2(snapshot, target.build_id, "fixture-operation", mode="update")
    cutover.restart_for_runtime_change(snapshot, target.build_id, "fixture-operation", mode="update")
    pulses = []
    evidence = cutover.verify_runtime_identity(snapshot, target.build_id, pulse=lambda: pulses.append(True))
    assert pulses
    assert evidence.component_ok and evidence.model_alias_ok and evidence.context_ok and evidence.slots_ok
    assert evidence.binary_digest_ok and evidence.health_ok
    cutover._receipt_path().unlink()
    assert not cutover.verify_runtime_identity(snapshot, target.build_id).component_ok
    cutover.server_port.replies.update(model_id=None, model_matches_desired=False, n_ctx=None,
                                     context_per_slot=None, parallel_slots=None)
    evidence = cutover.verify_runtime_identity(snapshot, target.build_id)
    assert not evidence.model_alias_ok and not evidence.context_ok and not evidence.slots_ok


def test_foreign_or_changed_candidate_never_swaps_or_deletes(cutover):
    prior = seed_tree(cutover, cutover._loc.active_root, "prior")
    target = seed_tree(cutover, Path(cutover._loc.managed_root) / "candidate-fixture", "target")
    snapshot = cutover.capture_activation_boundary(RuntimeUpdateRequestV1(), target.build_id)
    binary = Path("/" + target.locator, "build/bin/llama-server")
    binary.write_text("foreign")
    with pytest.raises(StepFailure):
        cutover.exchange_active_tree(snapshot, target, "fixture-effect", mode="update")
    assert cutover.read_active_manifest()["build_id"] == prior.build_id
    assert binary.read_text() == "foreign"


def test_initial_publication_is_no_clobber_and_restoration_retains_candidate(cutover):
    cutover.server_port.running = False
    target = seed_tree(cutover, Path(cutover._loc.managed_root) / "candidate-fixture", "target")
    snapshot = cutover.capture_activation_boundary(RuntimeUpdateRequestV1(), target.build_id)
    active = Path(cutover._loc.active_root)
    active.mkdir()
    with pytest.raises(StepFailure):
        cutover.exchange_active_tree(snapshot, target, "fixture-effect", mode="update")
    assert active.is_dir() and not list(active.iterdir())
    active.rmdir()
    cutover.exchange_active_tree(snapshot, target, "fixture-effect", mode="update")
    assert cutover.probe_exchange(snapshot, target.build_id, mode="update").classification is RecoveryClass.COMPLETE
    cutover.restore_prior_runtime(snapshot, "fixture-restore", mode="update")
    assert not active.exists()
    assert Path("/" + target.locator, "build/bin/llama-server").exists()


def test_restoration_after_first_promotion_restores_legacy_known_good_exactly(cutover):
    from bc250_llm_mode.operations.repositories import OperationRepository
    from bc250_llm_mode.repositories import KnownGoodRuntimeRepository
    seed_tree(cutover, cutover._loc.active_root, "legacy", legacy=True)
    target = seed_tree(cutover, Path(cutover._loc.managed_root) / "candidate-fixture", "target")
    with cutover._units.begin() as conn:
        OperationRepository(conn).create(operation_type="RUNTIME_UPDATE", request={}, surface="test", operation_id="first-promotion")
        KnownGoodRuntimeRepository(conn).set(model_alias="fixture", context=4096, slots=2,
                                             runtime={"parallel_slots": 2}, runtime_fingerprint="prior",
                                             verified_at="2026-09-17T00:00:00Z")
    snapshot = cutover.capture_activation_boundary(RuntimeUpdateRequestV1(), target.build_id)
    exchange = cutover.exchange_active_tree(snapshot, target, "fixture-effect", mode="update")
    cutover.publish_handoff_v2(snapshot, target.build_id, "first-promotion", mode="update")
    cutover.restart_for_runtime_change(snapshot, target.build_id, "first-promotion", mode="update")
    assert cutover.verify_runtime_inference(target.build_id).success
    promoted = cutover.promote_verified_runtime(snapshot, target.build_id, target, "first-promotion", mode="update")
    cutover.finalize_trees(snapshot, target.build_id, promoted, exchange, mode="update")
    assert cutover.observe_finalization(snapshot, target.build_id, mode="update").classification is RecoveryClass.COMPLETE
    cutover.restore_prior_runtime(snapshot, "first-promotion-restore", mode="update")
    assert cutover.observe_restoration(snapshot, mode="update").classification is RecoveryClass.COMPLETE
    with cutover._units.read() as conn:
        assert KnownGoodRuntimeRepository(conn).get() == snapshot.known_good_payload
        assert RuntimeComponentRepository(conn).current()["promoted_build_id"] is None
        assert RuntimeTreeRepository(conn).by_locator(cutover._loc.active_root.lstrip("/"))["build_id"] == snapshot.active_build_id


def test_production_workflow_build_update_noop_and_rollback(cutover):
    if not all(shutil.which(tool) for tool in ("cmake", "make", "cc")):
        pytest.skip("real CMake toolchain required")
    from bc250_llm_mode.operations.engine import ExecutionEngine
    from bc250_llm_mode.operations.runtime_lifecycle import build_runtime_update_workflow, build_runtime_rollback_workflow
    from bc250_llm_mode.operations.repositories import OperationRepository
    from bc250_llm_mode.operations.workflow import WorkflowRegistry, EnqueueService
    from bc250_llm_mode.repositories import KnownGoodRuntimeRepository
    from bc250_llm_mode.legacy_import import utcnow
    prior = seed_tree(cutover, cutover._loc.active_root, "prior")
    with cutover._units.begin() as conn:
        components = RuntimeComponentRepository(conn)
        components.initialize()
        components.promote_verified(expected_generation=1, expected_promoted_build_id=None,
                                    expected_rollback_build_id=None, promoted_build_id=prior.build_id,
                                    rollback_build_id=None, promoted_tree_id=prior.tree_id)
    registry = WorkflowRegistry()
    registry.register(build_runtime_update_workflow(cutover))
    registry.register(build_runtime_rollback_workflow(cutover))
    registry = registry.freeze()
    ids = lambda: str(uuid.uuid4())
    def run(kind, identifier, payload):
        EnqueueService(cutover._units, registry, clock=utcnow, uuid_factory=ids).enqueue(operation_type=kind, payload=payload,
                                                     surface="test", operation_id=identifier)
        outcome = ExecutionEngine(cutover._units, registry, clock=utcnow, uuid_factory=ids,
                                  worker_id="fixture-worker").execute_one(identifier)
        with cutover._units.read() as conn:
            record = OperationRepository(conn).require(identifier)
            steps = [dict(row) for row in conn.execute("SELECT step_key,state,failure_code,failure_detail FROM operation_steps WHERE operation_id=?", (identifier,))]
        if record.state.value != "SUCCEEDED":
            pytest.fail(str(outcome) + str((record.error_code, record.error_detail))
                        + json.dumps([row for row in steps if row["state"] != "PENDING"]))
    run("RUNTIME_UPDATE", "update-fixture", {"requested_by": "cli", "requested_ref": "main"})
    # The executable must still run after relocation, independent of the
    # former build directory now occupied by the displaced runtime.
    assert subprocess.check_output([str(Path(cutover._loc.active_root, "build/bin/llama-server")), "--version"], text=True).strip() == "fixture 1"
    with cutover._units.read() as conn:
        component = RuntimeComponentRepository(conn).current()
        known_good = KnownGoodRuntimeRepository(conn).get()
        trees = RuntimeTreeRepository(conn)
        assert trees.require(component["promoted_tree_id"])["locator"] == cutover._loc.active_root.lstrip("/")
        assert trees.require(component["rollback_tree_id"])["build_id"] == prior.build_id
        assert known_good["runtime_component_identity"] == component["promoted_build_id"]
    generation = component["generation"]
    starts = cutover.server_port.starts
    run("RUNTIME_UPDATE", "noop-fixture", {"requested_by": "cli", "requested_ref": "main"})
    with cutover._units.read() as conn:
        assert RuntimeComponentRepository(conn).current()["generation"] == generation
    assert cutover.server_port.starts == starts
    run("RUNTIME_ROLLBACK", "rollback-fixture", {"requested_by": "cli", "target_build_id": prior.build_id,
                                                  "expected_active_build_id": component["promoted_build_id"]})
    assert cutover.read_active_manifest()["build_id"] == prior.build_id
    with cutover._units.read() as conn:
        restored = RuntimeComponentRepository(conn).current()
        assert restored["promoted_build_id"] == prior.build_id
        assert restored["rollback_build_id"] == component["promoted_build_id"]
        assert RuntimeTreeRepository(conn).require(restored["promoted_tree_id"])["locator"] == cutover._loc.active_root.lstrip("/")
