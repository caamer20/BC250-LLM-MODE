"""Typed workflow contracts and the exact-version registry (Session 5B §5).

A workflow is a stateless, immutable declaration: a typed request decoder,
an ordered tuple of `StepDefinition`s, resource requirements, and terminal
mapping. Per-operation mutable data lives in the durable rows plus a
short-lived execution context. Registration is keyed by exactly
`(OperationType, request_version, recovery_policy_version)`; lookup never
falls back to "closest".

This module must not import tkinter, gui.*, chat, __main__, sqlite3, host
helpers, or global state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .model import (
    OperationError,
    OperationState,
    OperationType,
    OperationValidationError,
)
from .recovery import RecoveryClass, RecoveryDecision


class WorkflowRegistryError(OperationError):
    """Registration-time workflow contract violation."""


class WorkflowVersionUnavailable(OperationError):
    """No exact `(type, request_version, recovery_policy_version)` match."""


class StepFailure(OperationError):
    """Bounded, stable step failure.

    ``code`` is a stable identifier; ``detail`` must already be sanitized;
    ``mutation_possible`` tells recovery whether an external effect may have
    happened. Raw exception text is never carried here.
    """

    def __init__(
        self,
        code: str,
        summary: str,
        *,
        mutation_possible: bool = False,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.summary = summary
        self.mutation_possible = mutation_possible
        self.detail = detail or {}


@dataclass(frozen=True)
class ProbeResult:
    """Read-only postcondition inspection for an interrupted/effected step."""

    classification: RecoveryClass
    reason_code: str
    output: dict[str, Any] | None = None


@dataclass(frozen=True)
class EffectContext:
    """What step callables receive. Never carries a database connection."""

    operation_id: str
    step_key: str
    external_effect_id: str
    inputs: dict[str, Any]
    prior_outputs: dict[str, Any]
    request: Any
    pulse: Callable[..., None] = lambda *a, **k: None


@dataclass(frozen=True)
class TerminalDecision:
    """Typed successful-completion decision (U1.1 §8.1).

    Closed to SUCCEEDED / FAILED_SAFE: rollback, recovery-required, and
    cancellation remain engine/recovery decisions that workflow callbacks
    cannot invent.
    """

    state: OperationState
    result_code: str
    detail: dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    def __post_init__(self) -> None:
        allowed = {OperationState.SUCCEEDED, OperationState.FAILED_SAFE}
        from .model import OperationState as _OS

        if not isinstance(self.state, _OS) or self.state not in allowed:
            raise OperationValidationError(
                "terminal decisions are closed to SUCCEEDED / FAILED_SAFE"
            )


def _default_terminal_decision(request: Any, verified_outputs: dict) -> TerminalDecision:
    return TerminalDecision(
        OperationState.SUCCEEDED,
        "ALL_STEPS_VERIFIED",
        {},
        "all steps verified; operation succeeded",
    )


@dataclass(frozen=True)
class StepDefinition:
    step_key: str
    phase: str
    sequence: int
    derive_input: Callable[..., dict[str, Any]]
    probe: Callable[[EffectContext], ProbeResult]
    execute: Callable[[EffectContext], dict[str, Any]]
    verify: Callable[[EffectContext], dict[str, Any]]
    unit: str | None = None
    implementation_version: int = 1
    resources: tuple[str, ...] = ()
    externally_visible: bool = False
    cancel_safe_before: bool = True
    critical: bool = False
    # U1.1 §3.2 closed effect disposition. FORWARD_ONLY effects are never
    # compensated: recovery proceeds forward through probes instead.
    effect_disposition: str = "REVERSIBLE"
    compensate: Callable[[EffectContext], dict[str, Any]] | None = None
    verify_restoration: Callable[[EffectContext], dict[str, Any]] | None = None
    probe_restoration: Callable[[EffectContext], ProbeResult] | None = None
    safe_if_uncertain: bool = False

    def __post_init__(self) -> None:
        if int(self.implementation_version) < 1:
            raise WorkflowRegistryError(
                f"step {self.step_key!r} implementation_version must be >= 1"
            )
        allowed = {"NONE", "HIDDEN_DURABLE", "FORWARD_ONLY", "REVERSIBLE"}
        if self.effect_disposition not in allowed:
            raise WorkflowRegistryError(
                f"step {self.step_key!r} effect_disposition "
                f"{self.effect_disposition!r} not in {sorted(allowed)}"
            )


@dataclass(frozen=True)
class WorkflowDefinition:
    operation_type: OperationType
    request_version: int
    recovery_policy_version: int
    decode_request: Callable[[dict[str, Any]], Any]
    steps: tuple[StepDefinition, ...]
    summary: Callable[[Any], str]
    terminal_decision: Callable[[Any, dict], TerminalDecision] = (
        _default_terminal_decision
    )
    preflight: Callable[[Any], None] = lambda request: None
    # U1.1 §3.4: cancellation finalizer for forward-only workflows. Called
    # inside a fenced unit of work when cancellation is honored; releases
    # reservations and records retained-partial evidence without touching
    # published/quarantined artifacts. When absent, the engine keeps the
    # activation rollback behavior unchanged.
    cancel_finalizer: Callable[[Any], dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        seen_keys: set[str] = set()
        for position, step in enumerate(self.steps, start=1):
            if step.sequence != position:
                raise WorkflowRegistryError(
                    f"step {step.step_key!r} declares sequence "
                    f"{step.sequence}; expected {position} (declaration order)"
                )
            if step.step_key in seen_keys:
                raise WorkflowRegistryError(
                    f"duplicate step key {step.step_key!r}"
                )
            seen_keys.add(step.step_key)

    def resources_for(self, step_key: str) -> tuple[str, ...]:
        for step in self.steps:
            if step.step_key == step_key:
                return step.resources
        raise WorkflowRegistryError(f"no such step: {step_key!r}")

    def all_resources(self) -> tuple[str, ...]:
        ordered: list[str] = []
        for step in self.steps:
            for key in sorted(step.resources):
                if key not in ordered:
                    ordered.append(key)
        return tuple(ordered)


class WorkflowRegistry:
    """Immutable after freeze; duplicate keys fail during registration."""

    def __init__(self) -> None:
        self._workflows: dict[
            tuple[OperationType, int, int], WorkflowDefinition
        ] = {}
        self._frozen = False

    def register(self, definition: WorkflowDefinition) -> None:
        if self._frozen:
            raise WorkflowRegistryError("registry is frozen")
        key = (
            definition.operation_type,
            definition.request_version,
            definition.recovery_policy_version,
        )
        if key in self._workflows:
            raise WorkflowRegistryError(
                f"workflow already registered for {key}"
            )
        self._workflows[key] = definition

    def freeze(self) -> "WorkflowRegistry":
        self._frozen = True
        return self

    def lookup(
        self,
        operation_type: OperationType | str,
        request_version: int,
        recovery_policy_version: int,
    ) -> WorkflowDefinition:
        key = (
            OperationType(operation_type),
            int(request_version),
            int(recovery_policy_version),
        )
        try:
            return self._workflows[key]
        except KeyError:
            raise WorkflowVersionUnavailable(
                f"no workflow registered for {key}"
            ) from None

    def definition_for_type(
        self, operation_type: OperationType | str
    ) -> WorkflowDefinition:
        """The single registered version of a type (0.9 enqueue path).

        Ambiguity is refused rather than guessed; multiple versions of one
        type require an explicit version-aware enqueue API in a later slice.
        """
        resolved = OperationType(operation_type)
        matches = [
            definition
            for (op_type, _rv, _rpv), definition in self._workflows.items()
            if op_type is resolved
        ]
        if len(matches) != 1:
            raise WorkflowVersionUnavailable(
                f"expected exactly one registered workflow for "
                f"{resolved.value}; found {len(matches)}"
            )
        return matches[0]


class EnqueueService:
    """Atomic enqueue: registry decode + operation + ordered steps + event."""

    def __init__(
        self,
        units: Any,
        registry: WorkflowRegistry,
        *,
        clock: Callable[[], str],
        uuid_factory: Callable[[], str],
    ) -> None:
        self._units = units
        self._registry = registry
        self._clock = clock
        self._uuid_factory = uuid_factory

    def enqueue(
        self,
        *,
        operation_type: OperationType | str,
        payload: dict[str, Any],
        surface: str = "unknown",
        operation_id: str | None = None,
        parent_operation_id: str | None = None,
    ):
        from .model import as_request_dict
        from .repositories import OperationRepository, StepRepository

        resolved_type = OperationType(operation_type)
        definition = self._registry.definition_for_type(resolved_type)
        decoded = definition.decode_request(dict(payload))
        request_dict = as_request_dict(decoded)
        with self._units.begin() as conn:
            ops = OperationRepository(
                conn, clock=self._clock, uuid_factory=self._uuid_factory
            )
            steps = StepRepository(conn, clock=self._clock)
            record = ops.create(
                operation_type=resolved_type,
                request=request_dict,
                request_version=definition.request_version,
                recovery_policy_version=definition.recovery_policy_version,
                surface=surface,
                operation_id=operation_id,
                parent_operation_id=parent_operation_id,
            )
            steps.add_steps(
                record.id,
                [(step.step_key, step.sequence) for step in definition.steps],
                implementation_version=[
                    step.implementation_version for step in definition.steps
                ],
                inputs={
                    step.step_key: step.derive_input(request=decoded, prior={})
                    for step in definition.steps
                },
            )
        return record