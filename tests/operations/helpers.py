"""Fake reversible workflow (Session 5B §6.2) and the engine test harness.

Registered under MODEL_ACTIVATE request version 1 inside the test harness
only; no production operation type or schema change is involved.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.operations.model import OperationValidationError, OperationType
from bc250_llm_mode.operations.recovery import RecoveryClass
from bc250_llm_mode.operations.workflow import (
    EffectContext,
    EnqueueService,
    ProbeResult,
    StepDefinition,
    WorkflowDefinition,
    WorkflowRegistry,
)
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory

try:  # normal package context (tests/operations/__init__.py present)
    from .fakes import (
        CrashInjector,
        EffectRecorder,
        FakeClock,
        FakeWorld,
        SequenceIds,
    )
except ImportError:  # pytest top-level module context
    from fakes import (
        CrashInjector,
        EffectRecorder,
        FakeClock,
        FakeWorld,
        SequenceIds,
    )


@dataclass(frozen=True)
class FakeActivateRequest:
    desired_value: str


def decode_fake_request(payload: dict[str, Any]) -> FakeActivateRequest:
    value = payload.get("desired_value")
    if not isinstance(value, str) or not value.strip():
        raise OperationValidationError("desired_value must be a non-empty string")
    extra = set(payload) - {"desired_value"}
    if extra:
        raise OperationValidationError(f"unknown request fields: {sorted(extra)}")
    return FakeActivateRequest(desired_value=value)


def _verify_effect(world: FakeWorld, ctx: EffectContext) -> dict[str, Any]:
    active = world.read_active()
    desired = world.read_desired()["value"]
    if ctx.external_effect_id not in active["effects"]:
        raise AssertionError("effect was never applied")
    if active["value"] != desired:
        raise AssertionError("active value does not match desired")
    return {"verified_value": active["value"]}


def build_fake_workflow(
    world: FakeWorld,
    recorder: EffectRecorder,
    injector: CrashInjector,
    *,
    resources_in_unsorted_order: bool = True,
) -> WorkflowDefinition:
    """Five-step reversible fake workflow exercising every engine class."""

    def _capture_probe(_ctx: EffectContext) -> ProbeResult:
        if world.read_prior() is not None:
            return ProbeResult(RecoveryClass.COMPLETE, "PRIOR_PRESENT")
        return ProbeResult(RecoveryClass.ABSENT, "NO_PRIOR")

    def _apply_probe(ctx: EffectContext) -> ProbeResult:
        active = world.read_active()
        if ctx.external_effect_id in active["effects"]:
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "EFFECT_RECORDED",
                output={"applied_value": active["value"]},
            )
        return ProbeResult(RecoveryClass.ABSENT, "NO_EFFECT")

    apply_resources = (
        ("beta-res", "alpha-res")
        if resources_in_unsorted_order
        else ("alpha-res", "beta-res")
    )

    def _publish_probe(_ctx: EffectContext) -> ProbeResult:
        if world.publication_exists():
            return ProbeResult(RecoveryClass.COMPLETE, "PUBLISHED")
        return ProbeResult(RecoveryClass.ABSENT, "NOT_PUBLISHED")

    def _noop_execute(_ctx: EffectContext) -> dict[str, Any]:
        return {}

    def _require(condition: bool, message: str) -> dict[str, Any]:
        if not condition:
            raise AssertionError(message)
        return {}

    steps = (
        StepDefinition(
            step_key="capture_prior",
            phase="prepare",
            sequence=1,
            unit="snapshot",
            derive_input=lambda *, request, prior: {"value": request.desired_value},
            probe=_capture_probe,
            execute=lambda ctx: (
                injector.check("capture_prior", "after_external_effect"),
                world.capture_prior(ctx.inputs["value"]),
                recorder.record("effect", step="capture_prior"),
                {},
            )[2],
            verify=lambda ctx: _require(
                (world.read_prior() or {}).get("value") == ctx.inputs["value"],
                "prior not captured",
            ),
        ),
        StepDefinition(
            step_key="apply_effect",
            phase="effect",
            sequence=2,
            unit="application",
            resources=apply_resources,
            externally_visible=True,
            safe_if_uncertain=False,
            derive_input=lambda *, request, prior: {"value": request.desired_value},
            probe=_apply_probe,
            execute=lambda ctx: (
                world.apply_effect(ctx.inputs["value"], ctx.external_effect_id),
                injector.check("apply_effect", "after_external_effect"),
                recorder.record("effect", step="apply_effect"),
                {"applied": ctx.external_effect_id},
            )[2],
            verify=lambda ctx: _verify_effect(world, ctx),
            compensate=lambda ctx: (
                injector.check("apply_effect", "during_compensation"),
                world.restore_prior(),
                recorder.record("compensate", step="apply_effect"),
                {"restored": True},
            )[2],
            verify_restoration=lambda ctx: _require(
                world.read_active().get("compensated") is True, "not restored"
            ),
        ),
        StepDefinition(
            step_key="verify_effect",
            phase="verify",
            sequence=3,
            derive_input=lambda *, request, prior: {},
            probe=_apply_probe,
            execute=_noop_execute,
            verify=lambda ctx: _verify_effect(world, ctx),
        ),
        StepDefinition(
            step_key="publish",
            phase="commit",
            sequence=4,
            externally_visible=True,
            critical=True,
            cancel_safe_before=False,
            safe_if_uncertain=False,
            derive_input=lambda *, request, prior: {
                "marker": f"pub:{request.desired_value}"
            },
            probe=_publish_probe,
            execute=lambda ctx: (
                injector.check("publish", "after_external_effect"),
                world.publish(ctx.inputs["marker"]),
                recorder.record("effect", step="publish"),
                {"published": True},
            )[2],
            verify=lambda ctx: _require(
                world.publication_exists(), "not published"
            ),
            # No compensation: publication is irreversible by design.
        ),
        StepDefinition(
            step_key="verify_publication",
            phase="finalize",
            sequence=5,
            derive_input=lambda *, request, prior: {},
            probe=_publish_probe,
            execute=_noop_execute,
            verify=lambda ctx: _require(
                world.publication_exists(), "not published"
            ),
        ),
    )
    return WorkflowDefinition(
        operation_type=OperationType.MODEL_ACTIVATE,
        request_version=1,
        recovery_policy_version=1,
        decode_request=decode_fake_request,
        steps=steps,
        summary=lambda request: f"fake activate {request.desired_value!r}",
    )


class Harness:
    """Database + fake-world + registry harness shared by 5B tests."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "profile"
        self.database = self.root / "state.db"
        initialize_and_close(self.database)
        self.units = UnitOfWorkFactory(self.database)
        self.clock = FakeClock()
        world_root = tmp_path / "fake-world"
        world_root.mkdir(parents=True, exist_ok=True)
        self.world = FakeWorld(root=world_root)
        self.recorder = EffectRecorder()
        self.injector = CrashInjector()
        self.workflow = build_fake_workflow(
            self.world, self.recorder, self.injector
        )
        registry = WorkflowRegistry()
        registry.register(self.workflow)
        self.registry = registry.freeze()
        self.operation_ids = SequenceIds("op")
        self.worker_ids = SequenceIds("worker")
        self.effect_ids = SequenceIds("eff")

    def enqueue(
        self,
        desired_value: str = "v1",
        *,
        operation_id: str | None = None,
    ):
        enqueue = EnqueueService(
            self.units,
            self.registry,
            clock=self.clock.now,
            uuid_factory=self.operation_ids,
        )
        return enqueue.enqueue(
            operation_type="MODEL_ACTIVATE",
            payload={"desired_value": desired_value},
            surface="test",
            operation_id=operation_id,
        )

    def set_desired(self, value: str = "v1") -> None:
        self.world.set_desired(value)