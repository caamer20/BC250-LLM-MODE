"""Durable ``APPLICATION_UPDATE v1`` workflow (ADR 013 D7).

The workflow owns orchestration only. Filesystem, signature, backup, pointer,
process, migration, and repository effects remain behind the typed host port.
Every externally visible step has a probe and an explicit inverse where exact
rollback exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .model import OperationState, OperationType
from .recovery import RecoveryClass
from .validation import OperationValidationError
from .workflow import (
    EffectContext,
    ProbeResult,
    StepDefinition,
    TerminalDecision,
    WorkflowDefinition,
)


REQUEST_VERSION = 1
RECOVERY_POLICY_VERSION = 1
STEP_VERSION = 1
UPDATE_RESOURCES = ("application-installation", "profile-publication")
MODES = frozenset({"APPLY", "ROLLBACK"})
_DIGEST = re.compile(r"[0-9a-f]{64}")
_SURFACE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


@dataclass(frozen=True)
class ApplicationUpdateRequestV1:
    mode: str
    release_set_digest: str
    expected_current_installation_id: str
    expected_previous_installation_id: str | None
    expected_pointer_generation: int
    preview_digest: str
    confirmation_digest: str
    requested_by: str = "cli"


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise OperationValidationError(f"{field} must be lowercase sha256 hex")
    return value


def decode_application_update_request(
    payload: dict[str, Any],
) -> ApplicationUpdateRequestV1:
    allowed = frozenset({
        "mode", "release_set_digest", "expected_current_installation_id",
        "expected_previous_installation_id", "expected_pointer_generation",
        "preview_digest", "confirmation_digest", "requested_by",
    })
    unknown = set(payload) - allowed
    if unknown:
        raise OperationValidationError(
            f"unknown application update fields: {sorted(unknown)}"
        )
    mode = str(payload.get("mode") or "").upper()
    if mode not in MODES:
        raise OperationValidationError("application update mode is unknown")
    prior = payload.get("expected_previous_installation_id")
    if prior is not None:
        prior = _digest(prior, "expected_previous_installation_id")
    generation = payload.get("expected_pointer_generation")
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or not 0 <= generation <= (1 << 63) - 1
    ):
        raise OperationValidationError("pointer generation is invalid")
    surface = payload.get("requested_by", "cli")
    if not isinstance(surface, str) or not _SURFACE.fullmatch(surface):
        raise OperationValidationError("requested_by is invalid")
    release = _digest(payload.get("release_set_digest"), "release_set_digest")
    current = _digest(
        payload.get("expected_current_installation_id"),
        "expected_current_installation_id",
    )
    if mode == "ROLLBACK" and release != prior:
        raise OperationValidationError(
            "rollback release must equal the expected previous installation"
        )
    if release == current:
        raise OperationValidationError("application update target is already current")
    return ApplicationUpdateRequestV1(
        mode=mode,
        release_set_digest=release,
        expected_current_installation_id=current,
        expected_previous_installation_id=prior,
        expected_pointer_generation=generation,
        preview_digest=_digest(payload.get("preview_digest"), "preview_digest"),
        confirmation_digest=_digest(
            payload.get("confirmation_digest"), "confirmation_digest"
        ),
        requested_by=surface,
    )


class ApplicationUpdateHost:
    """Typed host port; production and crash-test worlds implement this."""

    def verify_release(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_release(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def stage_candidate(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_staged(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def discard_stage(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_stage_discarded(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def ensure_backup(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_backup(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def publish_pointer(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_pointer(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def restore_pointer(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_pointer_restored(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def launch_post_update(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_acknowledgment(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def restore_profile(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_profile_restored(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def verify_health(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_health(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def record_installation(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_recorded(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError


def _require_complete(probe: ProbeResult, code: str) -> dict[str, Any]:
    if probe.classification is not RecoveryClass.COMPLETE:
        raise OperationValidationError(f"{code}: {probe.reason_code}")
    return {}


def build_application_update_workflow(
    host: ApplicationUpdateHost,
) -> WorkflowDefinition:
    app_resource = (UPDATE_RESOURCES[0],)
    publish_resources = UPDATE_RESOURCES
    steps = (
        StepDefinition(
            step_key="verify_release_set",
            phase="verify-release",
            sequence=1,
            derive_input=lambda request, prior: {
                "mode": request.mode,
                "release_set_digest": request.release_set_digest,
                "preview_digest": request.preview_digest,
            },
            probe=host.probe_release,
            execute=lambda ctx: {"release": host.verify_release(ctx)},
            verify=lambda ctx: _require_complete(
                host.probe_release(ctx), "RELEASE_NOT_VERIFIED"
            ),
            implementation_version=STEP_VERSION,
            resources=app_resource,
            effect_disposition="HIDDEN_DURABLE",
        ),
        StepDefinition(
            step_key="stage_candidate",
            phase="stage",
            sequence=2,
            derive_input=lambda request, prior: {
                "release_set_digest": request.release_set_digest,
                "release": (prior.get("verify_release_set") or {}).get("release"),
            },
            probe=host.probe_staged,
            execute=lambda ctx: {"stage": host.stage_candidate(ctx)},
            verify=lambda ctx: _require_complete(
                host.probe_staged(ctx), "STAGING_NOT_VERIFIED"
            ),
            implementation_version=STEP_VERSION,
            resources=app_resource,
            effect_disposition="REVERSIBLE",
            compensate=lambda ctx: {"discard": host.discard_stage(ctx)},
            probe_restoration=host.probe_stage_discarded,
            verify_restoration=lambda ctx: _require_complete(
                host.probe_stage_discarded(ctx), "STAGING_NOT_DISCARDED"
            ),
        ),
        StepDefinition(
            step_key="verify_profile_backup",
            phase="backup",
            sequence=3,
            derive_input=lambda request, prior: {
                "release_set_digest": request.release_set_digest,
                "source_schema": (
                    ((prior.get("verify_release_set") or {}).get("release") or {})
                    .get("source_schema")
                ),
            },
            probe=host.probe_backup,
            execute=lambda ctx: {"backup": host.ensure_backup(ctx)},
            verify=lambda ctx: _require_complete(
                host.probe_backup(ctx), "VERIFIED_BACKUP_REQUIRED"
            ),
            implementation_version=STEP_VERSION,
            resources=app_resource,
            effect_disposition="FORWARD_ONLY",
        ),
        StepDefinition(
            step_key="publish_pointers",
            phase="publish",
            sequence=4,
            derive_input=lambda request, prior: {
                "mode": request.mode,
                "release_set_digest": request.release_set_digest,
                "expected_current_installation_id": (
                    request.expected_current_installation_id
                ),
                "expected_previous_installation_id": (
                    request.expected_previous_installation_id
                ),
                "expected_pointer_generation": request.expected_pointer_generation,
                "stage": (prior.get("stage_candidate") or {}).get("stage"),
                "backup": (
                    (prior.get("verify_profile_backup") or {}).get("backup")
                ),
            },
            probe=host.probe_pointer,
            execute=lambda ctx: {"publication": host.publish_pointer(ctx)},
            verify=lambda ctx: _require_complete(
                host.probe_pointer(ctx), "POINTER_PUBLICATION_NOT_VERIFIED"
            ),
            implementation_version=STEP_VERSION,
            resources=publish_resources,
            externally_visible=True,
            critical=True,
            effect_disposition="REVERSIBLE",
            compensate=lambda ctx: {"restoration": host.restore_pointer(ctx)},
            probe_restoration=host.probe_pointer_restored,
            verify_restoration=lambda ctx: _require_complete(
                host.probe_pointer_restored(ctx), "POINTER_ROLLBACK_NOT_VERIFIED"
            ),
        ),
        StepDefinition(
            step_key="post_update_ack",
            phase="migrate-and-ack",
            sequence=5,
            derive_input=lambda request, prior: {
                "release_set_digest": request.release_set_digest,
                "publication": (
                    (prior.get("publish_pointers") or {}).get("publication")
                ),
                "backup": (
                    (prior.get("verify_profile_backup") or {}).get("backup")
                ),
            },
            probe=host.probe_acknowledgment,
            execute=lambda ctx: {"ack": host.launch_post_update(ctx)},
            verify=lambda ctx: _require_complete(
                host.probe_acknowledgment(ctx), "POST_UPDATE_ACK_INVALID"
            ),
            implementation_version=STEP_VERSION,
            resources=publish_resources,
            externally_visible=True,
            critical=True,
            effect_disposition="REVERSIBLE",
            compensate=lambda ctx: {"profile": host.restore_profile(ctx)},
            probe_restoration=host.probe_profile_restored,
            verify_restoration=lambda ctx: _require_complete(
                host.probe_profile_restored(ctx), "PROFILE_RESTORE_FAILED"
            ),
        ),
        StepDefinition(
            step_key="verify_application_health",
            phase="health",
            sequence=6,
            derive_input=lambda request, prior: {
                "release_set_digest": request.release_set_digest,
                "ack": (prior.get("post_update_ack") or {}).get("ack"),
            },
            probe=host.probe_health,
            execute=lambda ctx: {"health": host.verify_health(ctx)},
            verify=lambda ctx: _require_complete(
                host.probe_health(ctx), "POST_UPDATE_HEALTH_FAILED"
            ),
            implementation_version=STEP_VERSION,
            resources=publish_resources,
            effect_disposition="HIDDEN_DURABLE",
        ),
        StepDefinition(
            step_key="record_installation",
            phase="record",
            sequence=7,
            derive_input=lambda request, prior: {
                "mode": request.mode,
                "release_set_digest": request.release_set_digest,
                "publication": (
                    (prior.get("publish_pointers") or {}).get("publication")
                ),
                "ack": (prior.get("post_update_ack") or {}).get("ack"),
            },
            probe=host.probe_recorded,
            execute=lambda ctx: {"record": host.record_installation(ctx)},
            verify=lambda ctx: _require_complete(
                host.probe_recorded(ctx), "INSTALLATION_RECORD_MISSING"
            ),
            implementation_version=STEP_VERSION,
            resources=publish_resources,
            critical=True,
            effect_disposition="HIDDEN_DURABLE",
        ),
    )

    def terminal(
        request: ApplicationUpdateRequestV1, outputs: dict[str, Any]
    ) -> TerminalDecision:
        code = (
            "APPLICATION_UPDATED" if request.mode == "APPLY"
            else "APPLICATION_ROLLED_BACK"
        )
        return TerminalDecision(
            OperationState.SUCCEEDED,
            code,
            {
                "mode": request.mode,
                "release_set_digest": request.release_set_digest,
                "pointer_generation": (
                    (((outputs.get("record_installation") or {}).get("record") or {})
                     .get("pointer_generation"))
                ),
            },
            "application update verified" if request.mode == "APPLY"
            else "application rollback verified",
        )

    return WorkflowDefinition(
        operation_type=OperationType.APPLICATION_UPDATE,
        request_version=REQUEST_VERSION,
        recovery_policy_version=RECOVERY_POLICY_VERSION,
        decode_request=decode_application_update_request,
        steps=steps,
        summary=lambda request: (
            f"Application {request.mode.lower()} to "
            f"{request.release_set_digest[:12]}"
        ),
        terminal_decision=terminal,
        phase_scoped_resources=True,
        recovery_barrier_resources=UPDATE_RESOURCES,
    )


__all__ = [
    "ApplicationUpdateHost", "ApplicationUpdateRequestV1", "MODES",
    "RECOVERY_POLICY_VERSION", "REQUEST_VERSION", "UPDATE_RESOURCES",
    "build_application_update_workflow", "decode_application_update_request",
]
