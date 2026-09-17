"""Fresh setup through real Git/CMake/filesystem and production workflows.

Only container transport/image observation and the service/health/inference
port are fixtures. The service fixture uses production model validation, so
it cannot make an empty-model runtime appear healthy. Not hardware acceptance.
"""

import hashlib
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import uuid

import pytest

from test_runtime_adapter_real import real_adapter
from test_activation_adapter import gguf_bytes, FakeServerPort, QuietRunner
from fakes import FakeClock, SimulatedProcessDeath
from bc250_llm_mode.activation_adapter import ActivationHostAdapter
from bc250_llm_mode.catalog import CATALOG
from bc250_llm_mode.operations.activation import build_activation_workflow
from bc250_llm_mode.operations.engine import ExecutionEngine
from bc250_llm_mode.operations.model import OperationState
from bc250_llm_mode.operations.repositories import OperationRepository
from bc250_llm_mode.operations.runtime_lifecycle import build_runtime_update_workflow
from bc250_llm_mode.operations.workflow import WorkflowRegistry, EnqueueService
from bc250_llm_mode.repositories import (SettingsRepository, ModelArtifactRepository,
                                       ModelInstallationsRepository, KnownGoodRuntimeRepository)
from bc250_llm_mode.runtime_builds import RuntimeComponentRepository, RuntimeTreeRepository
from bc250_llm_mode.runtime_handoff import RuntimeHandoffRenderer
from bc250_llm_mode.runtime_lifecycle_command import RuntimeLifecycleCommandService
from bc250_llm_mode.server import current_model_record
from bc250_llm_mode.services import RuntimeConfigurationService


pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="real Linux initial runtime publication")


class InitialService(FakeServerPort):
    def __init__(self, renderer):
        super().__init__()
        self.renderer = renderer
        self.fail_next_v2_inference = False

    def capture(self, view):
        return {"active": self.active, "invocation_marker": str(self.restarts) if self.active else None}

    def restart(self, view, runner=None):
        current_model_record(view)
        super().restart(view, runner)
        payload = self.renderer.observe()
        if payload.get("schema_version") == 2:
            receipt = {"build_id": payload["runtime_component_id"],
                       "server_sha256": payload["runtime_server_sha256"],
                       "manifest_digest": payload["runtime_manifest_digest"],
                       "operation_id": payload["runtime_operation_id"], "nonce": str(self.restarts)}
            (self.renderer.path.parent / "start-receipt.json").write_text(json.dumps(receipt))

    def stop(self, view, runner=None):
        return super().stop(view, runner)

    def inference(self, view, **kwargs):
        payload = self.renderer.observe() or {}
        if self.fail_next_v2_inference and payload.get("schema_version") == 2:
            self.fail_next_v2_inference = False
            return {"ok": False}
        current_model_record(view)
        return super().inference(view, **kwargs)


@pytest.fixture
def initial_world(real_adapter, tmp_path):
    if not all(shutil.which(tool) for tool in ("cmake", "make", "cc")):
        pytest.skip("real CMake toolchain required")
    return make_initial_world(real_adapter, tmp_path)


def make_initial_world(real_adapter, tmp_path):
    adapter, upstream, commit = real_adapter
    units = adapter._units
    profile = tmp_path / "profile"
    profile.mkdir()
    with units.begin() as conn:
        settings = SettingsRepository(conn)
        settings.set_many({"current_ctx": 4096, "optimizations": {"parallel_slots": 1},
                           "llama_cpp_path": adapter._loc.active_root, "container_name": "fixture",
                           "server_port": 8080, "service_name": "fixture.service"})
        settings.set_revision(1)

    def view():
        with units.read() as conn:
            settings = SettingsRepository(conn)
            result = {key: settings.get(key) for key in ("current_model", "current_ctx", "optimizations",
                       "llama_cpp_path", "container_name", "server_port", "service_name")}
            result["installed_models"] = ModelInstallationsRepository(conn).list()
            result["revision"] = settings.revision()
            return result

    renderer = RuntimeHandoffRenderer(profile)
    service = InitialService(renderer)
    adapter.renderer = renderer
    adapter.server_port = service
    adapter.state_supplier = view
    clock = FakeClock()
    runtime = RuntimeConfigurationService(units, app_dir=profile)
    activation = ActivationHostAdapter(units=units, runtime=runtime, renderer=renderer,
                                       state_supplier=view, runner_factory=QuietRunner, server_port=service)
    registry = WorkflowRegistry()
    registry.register(build_runtime_update_workflow(adapter))
    registry.register(build_activation_workflow(activation))
    registry = registry.freeze()
    identifiers = lambda: str(uuid.uuid4())
    enqueue = EnqueueService(units, registry, clock=clock.now, uuid_factory=identifiers)

    def engine():
        return ExecutionEngine(units, registry, clock=clock.now, uuid_factory=identifiers,
                               worker_id=identifiers(), lease_ttl_seconds=60)

    command = RuntimeLifecycleCommandService(units=units, enqueue=enqueue, engine_factory=engine)
    builds = []
    original = adapter.compile_candidate

    def compile_candidate(environment, pulse):
        builds.append(environment.build_dir_locator)
        return original(environment, pulse)

    adapter.compile_candidate = compile_candidate
    return SimpleNamespace(adapter=adapter, units=units, registry=registry, enqueue=enqueue,
                           engine=engine, command=command, service=service, renderer=renderer,
                           view=view, clock=clock, commit=commit, builds=builds, profile=profile)


def activate_model(world):
    model = CATALOG[0]
    path = world.profile / "model.gguf"
    content = gguf_bytes()
    path.write_bytes(content)
    with world.units.begin() as conn:
        ModelArtifactRepository(conn).record_verified(
            artifact_id="fixture-model", content_digest=hashlib.sha256(content).hexdigest(),
            byte_size=len(content), canonical_path=str(path), architecture="llama",
            quantization="Q8_0", tensor_count=1, catalog_id=model.id)
        ModelInstallationsRepository(conn).install_alias(alias=model.id, artifact_id="fixture-model",
                                                         quant="Q8_0", display_name=model.display_name)
    operation = world.enqueue.enqueue(operation_type="MODEL_ACTIVATE", surface="test", payload={
        "model_alias": model.id, "context_per_slot": 4096, "parallel_slots": 1, "requested_by": "setup"})
    world.engine().execute_one(operation.id)
    with world.units.read() as conn:
        row = OperationRepository(conn).require(operation.id)
    assert row.state is OperationState.SUCCEEDED, (row.error_code, row.error_detail)
    return model.id


def install(world):
    outcome = world.command.update(requested_ref=world.commit, requested_by="setup")
    assert outcome.ok, outcome.to_dict()
    assert outcome.detail["result_code"] == "RUNTIME_INSTALLED"
    assert not world.service.active and world.service.restarts == 0
    assert not world.renderer.path.exists()
    with world.units.read() as conn:
        assert not (RuntimeComponentRepository(conn).current() or {}).get("promoted_build_id")
        assert KnownGoodRuntimeRepository(conn).get() is None
    return outcome


def test_initial_install_repeat_model_activation_and_exact_promotion(initial_world):
    world = initial_world
    install(world)
    prepared = world.command.status()["prepared"]
    assert prepared and prepared["source_commit"] == world.commit
    install(world)  # Re-entering Setup after a lost window must not rebuild.
    assert len(world.builds) == 1
    alias = activate_model(world)
    verified = world.command.verify_prepared()
    assert verified.ok, verified.to_dict()
    assert verified.detail["result_code"] == "RUNTIME_PROMOTED"
    assert len(world.builds) == 1
    with world.units.read() as conn:
        component = RuntimeComponentRepository(conn).current()
        known_good = KnownGoodRuntimeRepository(conn).get()
        tree = RuntimeTreeRepository(conn).require(component["promoted_tree_id"])
    assert component["promoted_build_id"] == prepared["build_id"]
    assert component["rollback_build_id"] is None
    assert tree["locator"] == world.adapter._loc.active_root.lstrip("/")
    assert known_good["model_alias"] == alias and known_good["runtime_component_identity"] == prepared["build_id"]
    assert world.command.verify_prepared() is None


def test_first_verification_failure_restores_unpromoted_runtime_without_rebuild(initial_world):
    world = initial_world
    install(world)
    alias = activate_model(world)
    prior_handoff = world.renderer.observe()
    with world.units.read() as conn:
        prior_known_good = KnownGoodRuntimeRepository(conn).get()
    world.service.fail_next_v2_inference = True
    outcome = world.command.verify_prepared()
    assert outcome.status == "FAILED_ROLLED_BACK", outcome.to_dict()
    assert world.service.active and world.service.running_model == alias
    assert world.renderer.observe() == prior_handoff
    with world.units.read() as conn:
        assert not (RuntimeComponentRepository(conn).current() or {}).get("promoted_build_id")
        assert KnownGoodRuntimeRepository(conn).get() == prior_known_good
    assert len(world.builds) == 1
    assert world.command.verify_prepared().ok
    assert len(world.builds) == 1


def test_death_after_initial_publication_recovers_without_starting_a_service(initial_world):
    world = initial_world
    def crash(step, point):
        if step == "exchange_active_tree" and point == "after_swap":
            raise SimulatedProcessDeath("initial publication")
    world.adapter._effect_crash_hook = crash
    operation = world.enqueue.enqueue(operation_type="RUNTIME_UPDATE", surface="test",
                                      payload={"requested_ref": world.commit, "requested_by": "setup"})
    with pytest.raises(SimulatedProcessDeath):
        world.engine().execute_one(operation.id)
    world.adapter._effect_crash_hook = None
    world.clock.advance(120)
    world.engine().execute_one(operation.id)
    with world.units.read() as conn:
        row = OperationRepository(conn).require(operation.id)
    assert row.state is OperationState.SUCCEEDED, (row.error_code, row.error_detail)
    assert row.result_code == "RUNTIME_INSTALLED"
    assert len(world.builds) == 1 and world.service.restarts == 0


def test_prepared_binary_change_is_refused_before_reuse(initial_world):
    from bc250_llm_mode.operations.recovery import RecoveryClass
    from bc250_llm_mode.operations.runtime_lifecycle import RuntimeUpdateRequestV1
    from bc250_llm_mode.operations.workflow import StepFailure

    world = initial_world
    install(world)
    request = RuntimeUpdateRequestV1(requested_ref=world.commit)
    resolution = world.adapter.resolve_source(request)
    assert resolution.prepared_build_id
    binary = Path(world.adapter._loc.active_root, "build/bin/llama-quantize")
    binary.write_bytes(binary.read_bytes() + b"changed")
    assert world.adapter.observe_source_resolution(request, resolution).classification is RecoveryClass.UNCERTAIN_MANUAL
    with pytest.raises(StepFailure, match="PREPARED_RUNTIME_CHANGED"):
        world.adapter.capture_activation_boundary(request, resolution.prepared_build_id)
    assert world.service.restarts == 0 and len(world.builds) == 1


def test_verification_without_a_model_remains_pending(initial_world):
    world = initial_world
    install(world)
    outcome = world.command.verify_prepared()
    assert outcome.status == "BUSY" and outcome.detail["model_verification_pending"]
    assert world.command.status()["promoted"] is None
    assert world.command.status()["prepared"]
    assert world.service.restarts == 0 and len(world.builds) == 1
