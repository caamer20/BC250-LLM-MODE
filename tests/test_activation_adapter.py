"""Session 5C §10/§14.7: production activation adapter integration.

Real SQLite services + strict handoff renderer + the ONE production
adapter; only the ``ActivationServerPort`` seam is faked, proving all
host effects route through injected seams (never systemd/HTTP directly).
"""

from __future__ import annotations

import hashlib
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys_path = Path(__file__).parent
ops_support = sys_path / "operations"
for _path in (sys_path, ops_support):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from bc250_llm_mode.activation_adapter import (
    ActivationHostAdapter,
    ArtifactRejected,
)
from bc250_llm_mode.catalog import CATALOG
from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.model_artifact import gguf_layout_verdict
from bc250_llm_mode.operations.activation import build_activation_workflow
from bc250_llm_mode.operations.engine import ExecutionEngine
from bc250_llm_mode.operations.model import OperationState
from bc250_llm_mode.operations.repositories import OperationRepository
from bc250_llm_mode.operations.workflow import EnqueueService, WorkflowRegistry
from bc250_llm_mode.repositories import (
    ModelArtifactRepository,
    ModelInstallationsRepository,
    SettingsRepository,
    ThermalStateRepository,
)
from bc250_llm_mode.runtime_handoff import RuntimeHandoffRenderer
from bc250_llm_mode.services import RuntimeConfigurationService
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory
from bc250_llm_mode.workload_profiles import WorkloadProfileQueryService


def gguf_bytes(arch: bytes = b"llama", tensors: int = 1) -> bytes:
    def gguf_string(value: bytes) -> bytes:
        return struct.pack("<Q", len(value)) + value

    metadata = (
        gguf_string(b"general.architecture")
        + struct.pack("<I", 8)
        + gguf_string(arch)
    )
    return (
        b"GGUF"
        + struct.pack("<I", 3)
        + struct.pack("<Q", tensors)
        + struct.pack("<Q", 1)
        + metadata
    )


class FakeServerPort:
    """Seam fake: records effects, serves configurable identity."""

    def __init__(self) -> None:
        self.restarts = 0
        self.stops = 0
        self.active = False
        self.running_model: str | None = None
        self.health_override: dict | None = None

    def observe_active(self, view, runner):
        return self.active

    def restart(self, view, runner):
        self.restarts += 1
        self.active = True
        self.running_model = view.get("current_model")

    def stop(self, view, runner):
        self.stops += 1
        self.active = False
        self.running_model = None
        return {"stopped": True}

    def health(
        self, view, *, timeout=120, monotonic=None, sleep=None, pulse=None,
    ):
        if pulse is not None:
            pulse()
        if self.health_override is not None:
            return self.health_override
        ctx = int(view.get("current_ctx") or 8192)
        slots = int((view.get("optimizations") or {}).get("parallel_slots", 4))
        return {
            "healthy": self.active,
            "model_id": self.running_model or view.get("current_model"),
            "n_ctx": ctx * slots,
            "context_per_slot": ctx,
            "context_total": ctx * slots,
            "parallel_slots": slots,
        }

    def inference(self, view, *, timeout=20.0):
        return {"ok": self.active}


class QuietRunner:
    def run(self, command, check=True):
        class _Result:
            returncode = 0
            stdout = "active"

        return _Result()

    def emit(self, message):
        pass


@pytest.fixture()
def world(tmp_path):
    root = tmp_path / "profile"
    app_dir = root / "app"
    app_dir.mkdir(parents=True)
    database = root / "state.db"
    initialize_and_close(database)
    units = UnitOfWorkFactory(database)

    model_a, model_b = CATALOG[0], CATALOG[1]
    model_c = next(model for model in CATALOG if model.id == "qwen35-9b")
    quant = "Q8_0"
    artifacts = {}
    records = []
    for index, model in enumerate((model_a, model_b, model_c), start=1):
        path = root / f"{model.id}.gguf"
        content = gguf_bytes(tensors=index)
        path.write_bytes(content)
        artifacts[model.id] = path
        records.append(
            (model, path, content)
        )
    with units.begin() as conn:
        SettingsRepository(conn).set_many(
            {
                "current_model": model_a.id,
                "current_ctx": 4096,
                "optimizations": {
                    "parallel_slots": 2,
                    "runtime_enabled": True,
                },
                "server_port": 8099,
                "llama_cpp_path": str(root),
                "service_name": "bc250-llm.service",
            }
        )
        artifact_repository = ModelArtifactRepository(conn)
        installations = ModelInstallationsRepository(conn)
        for model, path, content in records:
            artifact_id = f"artifact-{model.id}"
            artifact_repository.record_verified(
                artifact_id=artifact_id,
                content_digest=hashlib.sha256(content).hexdigest(),
                byte_size=len(content),
                canonical_path=str(path),
                architecture="llama",
                quantization=quant,
                tensor_count=1,
                catalog_id=model.id,
            )
            installations.install_alias(
                alias=model.id,
                artifact_id=artifact_id,
                quant=quant,
                display_name=model.display_name,
            )
        # A configured system carries at least one committed revision.
        SettingsRepository(conn).set_revision(1)

    runtime = RuntimeConfigurationService(units, app_dir=app_dir)
    profile_query = WorkloadProfileQueryService(units)
    renderer = RuntimeHandoffRenderer(app_dir)
    server = FakeServerPort()

    def state_supplier():
        with units.read() as conn:
            settings = SettingsRepository(conn)
            return {
                "current_model": settings.get("current_model"),
                "current_ctx": settings.get("current_ctx"),
                "optimizations": settings.get("optimizations") or {},
                "server_port": settings.get("server_port"),
                "llama_cpp_path": settings.get("llama_cpp_path"),
                "service_name": settings.get("service_name"),
                "llamacpp_build": {"describe": "fake-build-1"},
                "installed_models": ModelInstallationsRepository(conn).list(),
            }

    adapter = ActivationHostAdapter(
        units=units,
        runtime=runtime,
        renderer=renderer,
        state_supplier=state_supplier,
        runner_factory=QuietRunner,
        server_port=server,
        profile_query=profile_query,
        monotonic=lambda: 0.0,
        sleep=lambda seconds: None,
    )
    registry = WorkflowRegistry()
    registry.register(build_activation_workflow(adapter))

    return SimpleNamespace(
        units=units,
        database=database,
        runtime=runtime,
        profile_query=profile_query,
        renderer=renderer,
        server=server,
        adapter=adapter,
        registry=registry.freeze(),
        artifacts=artifacts,
        model_a=model_a,
        model_b=model_b,
        model_c=model_c,
        quant=quant,
        state_supplier=state_supplier,
    )


def _engine(world, worker_id="worker-a"):
    return ExecutionEngine(
        world.units,
        world.registry,
        clock=lambda: "2026-08-23T12:00:00Z",
        uuid_factory=lambda: "eff-fixed",
        worker_id=worker_id,
        lease_ttl_seconds=60,
    )


def _enqueue(world, alias, *, operation_id):
    revision = int(world.runtime.current()["revision"])
    enqueue = EnqueueService(
        world.units,
        world.registry,
        clock=lambda: "2026-08-23T12:00:00Z",
        uuid_factory=lambda: operation_id,
    )
    return enqueue.enqueue(
        operation_type="MODEL_ACTIVATE",
        payload={
            "model_alias": alias,
            "expected_runtime_revision": revision,
            "requested_by": "cli",
        },
        surface="test",
        operation_id=operation_id,
    )


def test_gguf_layout_verdict_accepts_minimal_llama(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(gguf_bytes())
    assert gguf_layout_verdict(path) == "standard"


def test_resolve_rejects_thermal_latch_before_anything(world):
    with world.units.begin() as conn:
        ThermalStateRepository(conn).set("stopped", None)
    request = __import__(
        "bc250_llm_mode.operations.activation", fromlist=["ModelActivateRequestV1"]
    ).ModelActivateRequestV1(model_alias=world.model_b.id)
    with pytest.raises(ArtifactRejected) as err:
        world.adapter.resolve_candidate(request)
    assert err.value.code == "THERMAL_LATCH_STOPPED"


def test_resolve_rejects_missing_and_non_gguf_artifacts(world, tmp_path):
    from bc250_llm_mode.operations.activation import ModelActivateRequestV1

    # Not installed.
    with pytest.raises(ArtifactRejected) as err:
        world.adapter.resolve_candidate(
            ModelActivateRequestV1(model_alias="no-such-model")
        )
    assert err.value.code == "MODEL_NOT_INSTALLED"

    # Installed but not a parseable GGUF (fused/MAX-style rejection).
    bad = world.artifacts[world.model_b.id]
    bad.write_bytes(b"not-a-gguf-file")
    with pytest.raises(ArtifactRejected) as err:
        world.adapter.resolve_candidate(
            ModelActivateRequestV1(model_alias=world.model_b.id)
        )
    assert "LAYOUT" in err.value.code


def test_full_happy_path_through_production_adapter(world):
    record = _enqueue(
        world,
        world.model_b.id,
        operation_id="op-prod-1",
    )
    outcome = _engine(world).execute_one(record.id)

    assert outcome.kind == "COMPLETED"
    assert outcome.reason_code == "SUCCEEDED"

    current = world.runtime.current()
    assert current["model_alias"] == world.model_b.id
    assert current["revision"] >= 2
    # Handoff is valid under strict observation and names the candidate.
    payload = world.renderer.observe()
    assert payload is not None
    assert payload["model_id"] == world.model_b.id
    assert payload["config_revision"] == current["revision"]
    # Known-good row carries the EXACT verified identity.
    kg = world.runtime.known_good()
    assert kg["model_alias"] == world.model_b.id
    assert kg["context"] == current["context"]
    assert kg["slots"] == current["slots"]
    assert kg["runtime_component_identity"] == "fake-build-1"
    assert kg["runtime_fingerprint"]
    # Exactly one restart effect; service was never enabled/disabled.
    assert world.server.restarts == 1
    with world.units.begin() as conn:
        ops = OperationRepository(conn)
        assert (
            ops.require("op-prod-1").state is OperationState.SUCCEEDED
        )


def test_public_display_alias_proves_local_candidate_identity(world):
    from bc250_llm_mode.operations.activation import ModelActivateRequestV1

    request = ModelActivateRequestV1(model_alias=world.model_b.id)
    candidate = world.adapter.resolve_candidate(request)
    prior = world.adapter.capture_prior(request)
    world.server.active = True
    world.server.health_override = {
        "healthy": True,
        "model_id": candidate.display_alias,
        "context_per_slot": candidate.context_per_slot,
        "parallel_slots": candidate.parallel_slots,
    }

    observed = world.adapter.observe_restart(candidate, prior)

    assert observed.reason_code == "CANDIDATE_RUNTIME_VERIFIED"
    assert world.adapter.check_health(candidate).healthy is True


def test_wrong_health_identity_rolls_back_to_prior(world):
    record = _enqueue(
        world,
        world.model_b.id,
        operation_id="op-prod-rb",
    )
    prior_model = world.model_a.id
    # After the candidate restart, health answers with the PRIOR model:
    # identity cannot be proven -> revertible -> aggregate restoration.
    world.server.health_override = {
        "healthy": True,
        "model_id": prior_model,
        "n_ctx": 8192,
        "parallel_slots": 2,
    }
    outcome = _engine(world).execute_one(record.id)
    assert outcome.reason_code == "FAILED_ROLLED_BACK"
    current = world.runtime.current()
    assert current["model_alias"] == prior_model
    assert current["revision"] >= 3  # candidate commit + restore; never rewound
    kg = world.runtime.known_good()
    if kg is not None and not world.server.active:
        pytest.fail("prior known-good should survive when service restored")
