"""EXP-3 evidence-bound coach, calibration, and idle-policy qualification."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path

import pytest

from bc250_llm_mode import __main__ as entry
from bc250_llm_mode.calibration_adapter import (
    FIXED_PROMPT,
    MAX_SSE_LINE_BYTES,
    CalibrationHostAdapter,
    CalibrationServerPort,
)
from bc250_llm_mode.catalog import CATALOG
from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.idle_policy import IdlePolicyService
from bc250_llm_mode.operations.calibration import (
    CalibrationBaselineV1,
    CalibrationPlanV1,
    ProfileCalibrateRequestV1,
    build_calibration_workflow,
    decode_calibration_request,
)
from bc250_llm_mode.operations.engine import ExecutionEngine
from bc250_llm_mode.operations.model import (
    OperationState,
    OperationValidationError,
)
from bc250_llm_mode.operations.recovery import RecoveryClass
from bc250_llm_mode.operations.repositories import OperationRepository
from bc250_llm_mode.operations.workflow import (
    EffectContext,
    EnqueueService,
    ProbeResult,
    WorkflowRegistry,
    StepFailure,
)
from bc250_llm_mode.performance_coach import (
    MAX_SUGGESTIONS,
    PerformanceCoachService,
    SUGGESTION_CODES,
)
from bc250_llm_mode.repositories import (
    BenchHistoryRepository,
    KnownGoodRuntimeRepository,
    ModelArtifactRepository,
    ModelInstallationsRepository,
    RuntimeConfigRepository,
    SettingsRepository,
)
from bc250_llm_mode.runtime_builds import RuntimeComponentRepository
from bc250_llm_mode.services import RuntimeConfigurationService
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory
from bc250_llm_mode.workload_profiles import (
    WorkloadProfileQueryService,
    WorkloadProfileRepository,
)


NOW = "2026-08-29T20:00:00Z"


def _gguf_bytes() -> bytes:
    def text(value: bytes) -> bytes:
        return struct.pack("<Q", len(value)) + value

    metadata = text(b"general.architecture") + struct.pack("<I", 8) + text(b"llama")
    return b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 1) + struct.pack("<Q", 1) + metadata


@dataclass
class ProfileWorld:
    database: Path
    units: UnitOfWorkFactory
    query: WorkloadProfileQueryService
    model_alias: str


def _profile_world(tmp_path: Path) -> ProfileWorld:
    root = tmp_path / "profile"
    root.mkdir()
    database = root / "state.db"
    initialize_and_close(database)
    units = UnitOfWorkFactory(database)
    model = next(item for item in CATALOG if item.id == "lfm25-26b")
    content = _gguf_bytes()
    path = root / "small.gguf"
    path.write_bytes(content)
    with units.begin() as conn:
        SettingsRepository(conn).set_many(
            {
                "current_model": model.id,
                "current_ctx": 4096,
                "optimizations": {"parallel_slots": 1, "runtime_enabled": True},
            }
        )
        SettingsRepository(conn).set_revision(1)
        ModelArtifactRepository(conn).record_verified(
            artifact_id="artifact-small",
            content_digest=hashlib.sha256(content).hexdigest(),
            byte_size=len(content),
            canonical_path=str(path),
            architecture="llama",
            quantization="Q5_K_M",
            tensor_count=1,
            catalog_id=model.id,
        )
        ModelInstallationsRepository(conn).install_alias(
            alias=model.id,
            artifact_id="artifact-small",
            quant="Q5_K_M",
            display_name=model.display_name,
        )
        RuntimeComponentRepository(conn).initialize()
        KnownGoodRuntimeRepository(conn).set(
            model_alias=model.id,
            context=4096,
            slots=1,
            runtime_fingerprint="runtime-fingerprint-a",
            runtime_component_identity="local-build-a",
            verified_at=NOW,
        )
    return ProfileWorld(database, units, WorkloadProfileQueryService(units), model.id)


def _dump(database: Path) -> str:
    with sqlite3.connect(database) as conn:
        return "\n".join(conn.iterdump())


def test_calibration_request_and_workflow_are_closed_and_bounded():
    request = decode_calibration_request(
        {
            "profile_id": " builtin-interactive ",
            "expected_profile_revision": 1,
            "model_alias": " local-model ",
            "candidate_policy": "balanced-v1",
            "accept_tight": False,
            "requested_by": "cli",
        }
    )
    assert request.profile_id == "builtin-interactive"
    assert request.model_alias == "local-model"
    with pytest.raises(OperationValidationError):
        decode_calibration_request(
            {
                "profile_id": "builtin-interactive",
                "expected_profile_revision": 1,
                "model_alias": "local",
                "prompt": "private text",
            }
        )
    definition = build_calibration_workflow(_FakeCalibrationHost())
    assert [step.step_key for step in definition.steps] == [
        "resolve_plan",
        "capture_baseline",
        "measure_candidate_1",
        "measure_candidate_2",
        "measure_candidate_3",
        "record_winner",
    ]
    assert sum(step.critical for step in definition.steps) == 4
    assert all(
        step.cancel_safe_before
        for step in definition.steps
        if step.step_key.startswith("measure_candidate_")
    )


def test_coach_and_calibration_cli_surface_is_typed_and_bounded():
    parse = entry._parser().parse_args
    coach = parse(("coach", "--profile", "builtin-shared", "--ctx", "16384", "--users", "3"))
    assert coach.profile == "builtin-shared"
    assert coach.requested_context == 16384 and coach.requested_users == 3
    calibration = parse((
        "calibrate", "--profile", "builtin-cool", "--model", "lfm25-26b",
        "--candidate-policy", "conservative-v1", "--accept-tight",
    ))
    assert calibration.profile == "builtin-cool"
    assert calibration.candidate_policy == "conservative-v1"
    assert calibration.accept_tight is True


def test_calibration_stream_refuses_an_oversize_sse_event(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"x" * (MAX_SSE_LINE_BYTES + 1)

    import httpx

    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: Response())
    with pytest.raises(StepFailure) as raised:
        CalibrationServerPort().measure(
            {"server_port": 8080, "optimizations": {}},
            temperature_supplier=lambda: 60.0,
            monotonic=lambda: 1.0,
        )
    assert raised.value.code == "CALIBRATION_RESPONSE_OVERSIZE"


def test_evidence_requires_exact_profile_and_runtime_fingerprint(tmp_path):
    world = _profile_world(tmp_path)
    preview = world.query.preview("builtin-interactive")
    assert preview["evidence_class"] == "ESTIMATED"
    assert preview["expected_evidence_fingerprint"]
    with world.units.begin() as conn:
        stored = WorkloadProfileRepository(conn, clock=lambda: NOW).record_evidence(
            "builtin-interactive",
            expected_revision=1,
            evidence_class="MEASURED_LOCAL",
            evidence_fingerprint=preview["expected_evidence_fingerprint"],
            evidence_recorded_at=NOW,
        )
        assert stored.revision == 1
    measured = world.query.preview("builtin-interactive")
    assert measured["evidence_class"] == "MEASURED_LOCAL"
    assert measured["tested"] is True

    with world.units.begin() as conn:
        KnownGoodRuntimeRepository(conn).set(
            model_alias=world.model_alias,
            context=4096,
            slots=1,
            runtime_fingerprint="runtime-fingerprint-b",
            runtime_component_identity="local-build-b",
            verified_at=NOW,
        )
    stale = world.query.preview("builtin-interactive")
    assert stale["expected_evidence_fingerprint"] != preview["expected_evidence_fingerprint"]
    assert stale["evidence_class"] == "ESTIMATED"
    assert stale["tested"] is False


def test_coach_is_bounded_query_only_and_never_auto_applies(tmp_path):
    world = _profile_world(tmp_path)
    preview = world.query.preview("builtin-interactive")
    with world.units.begin() as conn:
        BenchHistoryRepository(conn).append(
            {"timestamp": NOW, "legacy_rate": 12.5}, commit=False
        )
        for number in range(2):
            conn.execute(
                "INSERT INTO operations(id, operation_type, request_version, "
                "recovery_policy_version, request_json, state, state_revision, "
                "surface, error_code, created_at, updated_at, finished_at) "
                "VALUES (?, 'MODEL_ACTIVATE', 1, 1, ?, 'FAILED_SAFE', 2, "
                "'test', 'LOAD_FAILED', ?, ?, ?)",
                (
                    f"failure-{number}",
                    json.dumps({
                        "model_alias": world.model_alias,
                        "profile_id": f"builtin-interactive@1:{preview['profile_fingerprint']}:0",
                    }),
                    NOW,
                    NOW,
                    NOW,
                ),
            )
            conn.execute(
                "INSERT INTO operation_steps(operation_id, step_key, sequence, "
                "implementation_version, state, attempts, input_json, output_json, "
                "started_at, checkpointed_at, finished_at) "
                "VALUES (?, 'resolve_candidate', 1, 1, 'VERIFIED', 1, '{}', ?, ?, ?, ?)",
                (
                    f"failure-{number}",
                    json.dumps({
                        "profile_fingerprint": preview["profile_fingerprint"],
                        "component_identity": preview["runtime_component_identity"],
                    }),
                    NOW,
                    NOW,
                    NOW,
                ),
            )
    before = _dump(world.database)
    coach = PerformanceCoachService(world.units, profiles=world.query)
    first = coach.suggestions(profile_id="builtin-interactive")
    second = coach.suggestions(profile_id="builtin-interactive")
    assert first == second
    assert len(first) <= MAX_SUGGESTIONS
    assert {item["code"] for item in first} <= set(SUGGESTION_CODES)
    assert "REPEATED_LOAD_FAILURE" in {item["code"] for item in first}
    assert "BASELINE_UNATTRIBUTED" in {item["code"] for item in first}
    assert all(item["apply_action"]["automatic"] is False for item in first)
    assert all(item["confidence"] != "MEASURED_LOCAL" for item in first if item["code"] == "BASELINE_UNATTRIBUTED")
    assert _dump(world.database) == before


class _Clock:
    def __init__(self) -> None:
        from datetime import datetime

        self._datetime = datetime
        self._value = datetime.strptime(NOW, "%Y-%m-%dT%H:%M:%SZ")

    def now(self) -> str:
        return self._value.strftime("%Y-%m-%dT%H:%M:%SZ")

    def advance(self, seconds: int) -> None:
        from datetime import timedelta

        self._value += timedelta(seconds=seconds)


class _ProcessDeath(BaseException):
    pass


class _FakeCalibrationHost:
    def __init__(self) -> None:
        self.active = "baseline"
        self.run_counts = [0, 0, 0]
        self.results: dict[int, dict] = {}
        self.restore_count = 0
        self.record_count = 0

    def preflight(self, request: ProfileCalibrateRequestV1) -> None:
        assert request.expected_profile_revision == 1

    def resolve_plan(self, request: ProfileCalibrateRequestV1) -> CalibrationPlanV1:
        candidates = tuple(
            {
                "candidate_index": index,
                "candidate_fingerprint": character * 64,
                "evidence_fingerprint": character * 64,
            }
            for index, character in enumerate(("a", "b"))
        )
        return CalibrationPlanV1(
            request.profile_id,
            request.expected_profile_revision,
            "c" * 64,
            "a" * 64,
            request.model_alias,
            "runtime-a",
            candidates,
        )

    def capture_baseline(self, request, plan) -> CalibrationBaselineV1:
        del request, plan
        return CalibrationBaselineV1(
            {"model_alias": "baseline"}, None, True, True
        )

    def probe_candidate(self, ctx: EffectContext, index: int) -> ProbeResult:
        del ctx
        if index >= 2:
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "UNUSED",
                output={"candidate_index": index, "status": "SKIPPED"},
            )
        if index in self.results:
            return ProbeResult(
                RecoveryClass.COMPLETE, "RECEIPT", output=self.results[index]
            )
        classification = (
            RecoveryClass.ABSENT
            if self.active == "baseline"
            else RecoveryClass.REVERTIBLE
        )
        return ProbeResult(classification, "OBSERVED")

    def run_candidate(self, ctx: EffectContext, index: int) -> dict:
        del ctx
        if index >= 2:
            return {"candidate_index": index, "status": "SKIPPED"}
        self.run_counts[index] += 1
        self.active = f"candidate-{index}"
        output = {
            "candidate_index": index,
            "status": "COMPLETE",
            "candidate_fingerprint": ("a" if index == 0 else "b") * 64,
            "evidence_fingerprint": ("a" if index == 0 else "b") * 64,
            "profile_resolution_fingerprint": ("c" if index == 0 else "d") * 64,
            "started_at": NOW,
            "completed_at": NOW,
            "time_to_first_unit_ms": 100 + index,
            "prompt_per_second": 20.0,
            "generation_per_second": 15.0 - index,
            "peak_temperature_c": 70.0,
            "throttling_class": "NOT_OBSERVED",
            "measured_units": 32,
        }
        self.active = "baseline"
        self.results[index] = output
        return output

    def verify_candidate(self, ctx: EffectContext, index: int) -> dict:
        del ctx, index
        assert self.active == "baseline"
        return {}

    def restore_baseline(self, ctx: EffectContext) -> dict:
        del ctx
        self.restore_count += 1
        self.active = "baseline"
        return {"restored": True}

    def probe_restoration(self, ctx: EffectContext) -> ProbeResult:
        del ctx
        if self.active == "baseline":
            return ProbeResult(
                RecoveryClass.COMPLETE, "RESTORED", output={"restored": True}
            )
        return ProbeResult(RecoveryClass.REVERTIBLE, "NOT_RESTORED")

    def verify_restoration(self, ctx: EffectContext) -> dict:
        assert self.probe_restoration(ctx).classification is RecoveryClass.COMPLETE
        return {}

    def probe_recorded_winner(self, ctx: EffectContext) -> ProbeResult:
        del ctx
        if self.record_count:
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "RECORDED",
                output={"recorded": True, "winner_fingerprint": "a" * 64},
            )
        return ProbeResult(RecoveryClass.ABSENT, "NOT_RECORDED")

    def record_winner(self, ctx: EffectContext) -> dict:
        del ctx
        self.record_count += 1
        return {"recorded": True, "winner_fingerprint": "a" * 64}


class _AdapterServer:
    def __init__(self) -> None:
        self.is_active = False
        self.current_model = None
        self.measurements = 0
        self.stops = 0

    def active(self, state, runner):
        del state, runner
        return self.is_active

    def restart(self, state, runner):
        del runner
        self.is_active = True
        self.current_model = state.get("current_model")

    def stop(self, state, runner):
        del state, runner
        self.stops += 1
        self.is_active = False
        self.current_model = None

    def health(self, state):
        slots = int((state.get("optimizations") or {}).get("parallel_slots") or 1)
        return {
            "healthy": self.is_active,
            "model_id": self.current_model,
            "parallel_slots": slots,
        }

    def measure(self, state, *, temperature_supplier, monotonic):
        del state, temperature_supplier, monotonic
        self.measurements += 1
        return {
            "time_to_first_unit_ms": 100,
            "prompt_per_second": 25.0,
            "generation_per_second": 30.0 - self.measurements,
            "peak_temperature_c": 71.0,
            "throttling_class": "NOT_OBSERVED",
            "measured_units": 32,
        }


def test_production_calibration_adapter_restores_and_records_exact_evidence(tmp_path):
    world = _profile_world(tmp_path)
    runtime = RuntimeConfigurationService(world.units)
    server = _AdapterServer()

    def state_supplier():
        current = runtime.current()
        with world.units.read() as conn:
            models = ModelInstallationsRepository(conn).list()
        return {
            "current_model": current["model_alias"],
            "current_ctx": current["context"],
            "optimizations": current["optimizations"],
            "installed_models": models,
            "server_port": 8080,
        }

    baseline = runtime.capture()
    adapter = CalibrationHostAdapter(
        units=world.units,
        profiles=world.query,
        runtime=runtime,
        app_dir=tmp_path / "app",
        state_supplier=state_supplier,
        runner_factory=lambda: object(),
        server_port=server,
        clock=lambda: NOW,
        monotonic=lambda: 1.0,
        temperature_supplier=lambda: 65.0,
    )
    registry = WorkflowRegistry()
    registry.register(build_calibration_workflow(adapter))
    frozen = registry.freeze()
    preview = world.query.preview("builtin-interactive")
    operation = EnqueueService(
        world.units, frozen, clock=lambda: NOW, uuid_factory=lambda: "adapter-op"
    ).enqueue(
        operation_type="PROFILE_CALIBRATE",
        payload={
            "profile_id": "builtin-interactive",
            "expected_profile_revision": 1,
            "model_alias": world.model_alias,
            "candidate_policy": "balanced-v1",
            "accept_tight": bool(preview["tight_confirmation_required"]),
            "requested_by": "test",
        },
        surface="test",
        operation_id="adapter-op",
    )
    outcome = ExecutionEngine(
        world.units,
        frozen,
        clock=lambda: NOW,
        uuid_factory=lambda: "adapter-effect",
        worker_id="adapter-worker",
    ).execute_one(operation.id)
    assert outcome.reason_code == "CALIBRATION_WINNER_PROPOSED", outcome.detail
    after = runtime.current()
    assert all(after[key] == baseline[key] for key in (
        "model_alias", "context", "slots", "profile_id",
        "profile_revision", "profile_fingerprint",
    ))
    from bc250_llm_mode.optimize import normalized_settings

    assert normalized_settings(after["optimizations"]) == normalized_settings(
        baseline["optimizations"]
    )
    assert server.measurements >= 1
    assert server.is_active is False
    measured = world.query.preview("builtin-interactive")
    assert measured["evidence_class"] == "MEASURED_LOCAL"
    assert measured["evidence_fingerprint"] == preview["expected_evidence_fingerprint"]
    assert not list((tmp_path / "app" / "calibration-receipts").rglob("*.json"))


@dataclass
class CalibrationHarness:
    database: Path
    units: UnitOfWorkFactory
    host: _FakeCalibrationHost
    registry: object
    clock: _Clock

    def engine(self, worker: str, crash_hook=None) -> ExecutionEngine:
        return ExecutionEngine(
            self.units,
            self.registry,
            clock=self.clock.now,
            uuid_factory=lambda: f"effect-{worker}",
            worker_id=worker,
            lease_ttl_seconds=60,
            crash_hook=crash_hook,
        )


def _calibration_harness(tmp_path: Path) -> CalibrationHarness:
    database = tmp_path / "calibration.db"
    initialize_and_close(database)
    units = UnitOfWorkFactory(database)
    host = _FakeCalibrationHost()
    registry = WorkflowRegistry()
    registry.register(build_calibration_workflow(host))
    clock = _Clock()
    frozen = registry.freeze()
    EnqueueService(
        units, frozen, clock=clock.now, uuid_factory=lambda: "calibration-op"
    ).enqueue(
        operation_type="PROFILE_CALIBRATE",
        payload={
            "profile_id": "builtin-interactive",
            "expected_profile_revision": 1,
            "model_alias": "local-model",
            "candidate_policy": "balanced-v1",
            "accept_tight": False,
            "requested_by": "test",
        },
        surface="test",
        operation_id="calibration-op",
    )
    return CalibrationHarness(database, units, host, frozen, clock)


def test_calibration_death_before_checkpoint_recovers_without_duplicate_trial(tmp_path):
    harness = _calibration_harness(tmp_path)
    fired = False

    def crash(step: str, point: str) -> None:
        nonlocal fired
        if not fired and step == "measure_candidate_1" and point == "before_step_checkpoint":
            fired = True
            raise _ProcessDeath()

    with pytest.raises(_ProcessDeath):
        harness.engine("worker-a", crash).execute_one("calibration-op")
    assert harness.host.run_counts == [1, 0, 0]
    assert harness.host.active == "baseline"
    harness.clock.advance(120)
    outcome = harness.engine("worker-b").execute_one("calibration-op")
    assert outcome.reason_code == "CALIBRATION_WINNER_PROPOSED"
    assert harness.host.run_counts == [1, 1, 0]
    with harness.units.read() as conn:
        final = OperationRepository(conn).require("calibration-op")
    assert final.state is OperationState.SUCCEEDED
    detail = json.loads(final.result_detail)
    assert detail["applied"] is False
    assert detail["winner"]["candidate_index"] == 0
    database_bytes = harness.database.read_bytes()
    assert FIXED_PROMPT.encode("utf-8") not in database_bytes


def test_calibration_cancellation_is_honored_between_candidates_and_restores(tmp_path):
    harness = _calibration_harness(tmp_path)

    def crash(step: str, point: str) -> None:
        if step == "measure_candidate_2" and point == "before_step_start":
            raise _ProcessDeath()

    with pytest.raises(_ProcessDeath):
        harness.engine("worker-a", crash).execute_one("calibration-op")
    assert harness.host.run_counts == [1, 0, 0]
    with harness.units.begin() as conn:
        OperationRepository(conn, clock=harness.clock.now).request_cancel(
            "calibration-op"
        )
    harness.clock.advance(120)
    outcome = harness.engine("worker-b").execute_one("calibration-op")
    assert outcome.reason_code == "CANCELLED"
    assert harness.host.run_counts == [1, 0, 0]
    assert harness.host.active == "baseline"
    assert harness.host.restore_count >= 1
    with harness.units.read() as conn:
        final = OperationRepository(conn).require("calibration-op")
    assert final.state is OperationState.CANCELLED


def test_idle_policy_only_stops_and_suppresses_during_operations(tmp_path):
    world = _profile_world(tmp_path)
    cool = world.query.preview("builtin-cool")
    with world.units.begin() as conn:
        RuntimeConfigRepository(conn).update(
            model_alias=world.model_alias,
            context=cool["context_per_slot"],
            slots=cool["slots"],
            profile_id="builtin-cool",
            profile_revision=1,
            profile_fingerprint=cool["profile_fingerprint"],
        )
    stops: list[str] = []
    service = IdlePolicyService(
        world.units,
        server_active=lambda: True,
        stop_server=lambda: (stops.append("stop") or {"active": False}),
        now=lambda: NOW,
    )
    recent = service.enforce_once(last_request_at="2026-08-29T19:45:01Z", active_requests=0)
    assert recent["reason_code"] == "IDLE_INTERVAL_NOT_REACHED"
    assert recent["started"] is False and not stops
    elapsed = service.enforce_once(last_request_at="2026-08-29T19:29:59Z", active_requests=0)
    assert elapsed["reason_code"] == "STOP_AFTER_ELAPSED"
    assert elapsed["stopped"] is True and stops == ["stop"]

    with world.units.begin() as conn:
        OperationRepository(conn, clock=lambda: NOW, uuid_factory=lambda: "active-op").create(
            operation_type="PROFILE_CALIBRATE",
            request={
                "profile_id": "builtin-cool",
                "expected_profile_revision": 1,
                "model_alias": world.model_alias,
            },
            surface="test",
        )
    suppressed = service.enforce_once(last_request_at="2026-08-29T18:00:00Z")
    assert suppressed["reason_code"] == "ACTIVE_OPERATION"
    assert stops == ["stop"]


def test_desktop_presence_does_not_stop_interactive_chat(tmp_path):
    world = _profile_world(tmp_path)
    interactive = world.query.preview("builtin-interactive")
    with world.units.begin() as conn:
        RuntimeConfigRepository(conn).update(
            model_alias=world.model_alias,
            context=interactive["context_per_slot"],
            slots=interactive["slots"],
            profile_id="builtin-interactive",
            profile_revision=1,
            profile_fingerprint=interactive["profile_fingerprint"],
        )
    stops: list[str] = []
    active = IdlePolicyService(
        world.units,
        server_active=lambda: True,
        stop_server=lambda: (stops.append("stop") or {"active": False}),
        now=lambda: NOW,
    )
    keep = active.enforce_once(last_request_at="2026-08-20T00:00:00Z")
    assert keep["reason_code"] == "KEEP_LOADED_CURRENT_BOOT" and not stops
    desktop = active.enforce_once(
        last_request_at="2026-08-29T19:59:59Z", desktop_mode=True
    )
    assert desktop["reason_code"] == "KEEP_LOADED_CURRENT_BOOT" and stops == []
    inactive = IdlePolicyService(
        world.units,
        server_active=lambda: False,
        stop_server=lambda: stops.append("unexpected"),
        now=lambda: NOW,
    ).enforce_once(last_request_at=None)
    assert inactive["reason_code"] == "SERVER_ALREADY_STOPPED"
    assert inactive["started"] is False and stops == []
