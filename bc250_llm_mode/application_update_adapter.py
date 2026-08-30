"""Production host adapter for ``APPLICATION_UPDATE v1``.

The default release resolver and post-update process port are deliberately
unavailable. They make a accidentally enqueued update fail before mutation
until EXP-6's verified bundle import/channel and bounded replacement-process
path are composed. Tests inject durable fakes; no trust is invented here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .application_installation import (
    ApplicationInstallationError,
    ApplicationInstallationRepository,
    ApplicationInstallationStateRepository,
    InstallationState,
    SmokeState,
)
from .application_pointer_helper import PointerRefusal
from .application_publisher import (
    ApplicationPointerPublisher,
    PointerPublishRequest,
)
from .application_staging import (
    ApplicationInstallationStager,
    ApplicationStageRequest,
    ApplicationStagingError,
)
from .application_update import VerifiedApplicationRelease
from .operations.recovery import RecoveryClass
from .operations.workflow import EffectContext, ProbeResult, StepFailure
from .paths import AppPaths


@dataclass(frozen=True)
class HeldApplicationRelease:
    release: VerifiedApplicationRelease
    bundle_root: Path
    wheel_name: str
    wheel_size: int

    def stage_request(self, operation_id: str) -> ApplicationStageRequest:
        return ApplicationStageRequest(
            operation_id=operation_id,
            release=self.release,
            bundle_root=self.bundle_root,
            wheel_name=self.wheel_name,
            wheel_size=self.wheel_size,
        )


class ApplicationReleaseResolver(Protocol):
    def resolve(self, release_set_digest: str) -> HeldApplicationRelease: ...

    def available(self, release_set_digest: str) -> bool: ...


class UnavailableApplicationReleaseResolver:
    def resolve(self, release_set_digest: str) -> HeldApplicationRelease:
        raise StepFailure(
            "SIGNED_UPDATE_CHANNEL_UNAVAILABLE",
            "no reviewed signed application release is available",
        )

    def available(self, release_set_digest: str) -> bool:
        return False


class PostUpdateProcessPort(Protocol):
    def launch(
        self,
        *,
        operation_id: str,
        release: VerifiedApplicationRelease,
        backup: dict[str, Any],
        publication: dict[str, Any],
    ) -> dict[str, Any]: ...

    def probe(
        self, *, operation_id: str, release: VerifiedApplicationRelease
    ) -> ProbeResult: ...

    def health(
        self, *, operation_id: str, release: VerifiedApplicationRelease
    ) -> dict[str, Any]: ...

    def probe_health(
        self, *, operation_id: str, release: VerifiedApplicationRelease
    ) -> ProbeResult: ...

    def restore_profile(
        self,
        *,
        operation_id: str,
        release: VerifiedApplicationRelease,
        backup: dict[str, Any],
    ) -> dict[str, Any]: ...

    def probe_profile_restored(
        self, *, operation_id: str, release: VerifiedApplicationRelease
    ) -> ProbeResult: ...


class UnavailablePostUpdateProcessPort:
    def _fail(self) -> None:
        raise StepFailure(
            "POST_UPDATE_PROCESS_UNAVAILABLE",
            "bounded post-update process is unavailable",
            mutation_possible=True,
        )

    def launch(self, **_: Any) -> dict[str, Any]:
        self._fail()

    def health(self, **_: Any) -> dict[str, Any]:
        self._fail()

    def restore_profile(self, **_: Any) -> dict[str, Any]:
        self._fail()

    def probe(self, **_: Any) -> ProbeResult:
        return ProbeResult(RecoveryClass.ABSENT, "POST_UPDATE_ACK_ABSENT")

    def probe_health(self, **_: Any) -> ProbeResult:
        return ProbeResult(RecoveryClass.ABSENT, "POST_UPDATE_HEALTH_ABSENT")

    def probe_profile_restored(self, **_: Any) -> ProbeResult:
        return ProbeResult(RecoveryClass.ABSENT, "PROFILE_NOT_RESTORED")


class ApplicationUpdateHostAdapter:
    def __init__(
        self,
        units: Any,
        paths: AppPaths,
        *,
        resolver: ApplicationReleaseResolver | None = None,
        stager: ApplicationInstallationStager | None = None,
        publisher: ApplicationPointerPublisher | None = None,
        backup_supplier: Callable[[], Any] | None = None,
        post_update: PostUpdateProcessPort | None = None,
    ) -> None:
        self.units = units
        self.paths = paths
        self.resolver = resolver or UnavailableApplicationReleaseResolver()
        self.stager = stager or ApplicationInstallationStager(paths)
        self.publisher = publisher or ApplicationPointerPublisher(paths)
        self.backup_supplier = backup_supplier
        self.post_update = post_update or UnavailablePostUpdateProcessPort()

    def _held(self, ctx: EffectContext) -> HeldApplicationRelease:
        held = self.resolver.resolve(ctx.request.release_set_digest)
        if held.release.release_set_digest != ctx.request.release_set_digest:
            raise StepFailure(
                "RELEASE_IDENTITY_MISMATCH", "resolved release identity changed"
            )
        return held

    def _pointer_request(self, ctx: EffectContext) -> PointerPublishRequest:
        stage = (ctx.prior_outputs.get("stage_candidate") or {}).get("stage") or {}
        return PointerPublishRequest(
            operation_id=ctx.operation_id,
            candidate_installation_id=ctx.request.release_set_digest,
            expected_current_installation_id=(
                ctx.request.expected_current_installation_id
            ),
            expected_previous_installation_id=(
                ctx.request.expected_previous_installation_id
            ),
            expected_pointer_generation=ctx.request.expected_pointer_generation,
            stage_identity=stage.get("stage_identity"),
        )

    # -- release / reservation -------------------------------------------

    def verify_release(self, ctx: EffectContext) -> dict[str, Any]:
        held = self._held(ctx)
        try:
            with self.units.begin() as conn:
                state_repo = ApplicationInstallationStateRepository(conn)
                state = state_repo.get()
                if (
                    state.current_installation_id
                    != ctx.request.expected_current_installation_id
                    or state.previous_installation_id
                    != ctx.request.expected_previous_installation_id
                    or state.pointer_generation
                    != ctx.request.expected_pointer_generation
                ):
                    raise StepFailure(
                        "PREVIEW_STALE", "installation pointer state changed"
                    )
                state_repo.reserve(
                    ctx.operation_id, expected_revision=state.revision
                )
        except ApplicationInstallationError as exc:
            raise StepFailure("UPDATE_BUSY", "application update is reserved") from exc
        return held.release.to_dict()

    def probe_release(self, ctx: EffectContext) -> ProbeResult:
        if not self.resolver.available(ctx.request.release_set_digest):
            return ProbeResult(RecoveryClass.ABSENT, "VERIFIED_RELEASE_ABSENT")
        with self.units.read() as conn:
            state = ApplicationInstallationStateRepository(conn).get()
        if state.pending_update_operation_id == ctx.operation_id:
            try:
                release = self._held(ctx).release.to_dict()
            except Exception:
                return ProbeResult(
                    RecoveryClass.UNCERTAIN_MANUAL, "HELD_RELEASE_UNREADABLE"
                )
            return ProbeResult(
                RecoveryClass.COMPLETE, "VERIFIED_RELEASE_RESERVED",
                {"release": release},
            )
        if state.pending_update_operation_id is None:
            return ProbeResult(RecoveryClass.ABSENT, "UPDATE_NOT_RESERVED")
        return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "OTHER_UPDATE_RESERVED")

    # -- staging ----------------------------------------------------------

    def stage_candidate(self, ctx: EffectContext) -> dict[str, Any]:
        held = self._held(ctx)
        if ctx.request.mode == "ROLLBACK":
            with self.units.read() as conn:
                row = ApplicationInstallationRepository(conn).get(
                    held.release.release_set_digest
                )
            if row is None or row.smoke_state is not SmokeState.PASSED:
                raise StepFailure(
                    "ROLLBACK_UNAVAILABLE", "prior release is not verified"
                )
            return {
                "stage_identity": None,
                "release_set_digest": row.release_set_digest,
                "receipt_digest": row.manifest_digest,
                "smoke_codes": ["PRIOR_RELEASE_VERIFIED"],
                "already_complete": True,
            }
        try:
            result = self.stager.stage(held.stage_request(ctx.operation_id))
            with self.units.begin() as conn:
                repo = ApplicationInstallationRepository(conn)
                row = repo.get(held.release.release_set_digest)
                if row is None:
                    row = repo.insert_staged(
                        held.release, created_by_operation_id=ctx.operation_id
                    )
                if row.smoke_state is SmokeState.PENDING:
                    repo.mark_smoke(
                        row.installation_id,
                        state=SmokeState.PASSED,
                        expected_revision=row.revision,
                    )
            return result.to_dict()
        except ApplicationStagingError as exc:
            raise StepFailure(exc.code, "candidate staging failed") from exc

    def _release_receipt_present(self, identity: str) -> bool:
        path = self.paths.application_releases_dir / identity
        return self.publisher._load_release_receipt(path, identity)

    def probe_staged(self, ctx: EffectContext) -> ProbeResult:
        try:
            held = self._held(ctx)
        except Exception:
            return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "HELD_RELEASE_UNREADABLE")
        with self.units.read() as conn:
            row = ApplicationInstallationRepository(conn).get(
                held.release.release_set_digest
            )
        if row is None or row.smoke_state is not SmokeState.PASSED:
            return ProbeResult(RecoveryClass.ABSENT, "STAGED_RECORD_ABSENT")
        if ctx.request.mode == "ROLLBACK" or self._release_receipt_present(
            held.release.release_set_digest
        ):
            return ProbeResult(
                RecoveryClass.COMPLETE, "CANDIDATE_RELEASE_PRESENT",
                {"stage": {"stage_identity": None,
                 "release_set_digest": held.release.release_set_digest}},
            )
        staged = self.stager.probe(held.stage_request(ctx.operation_id))
        if staged is None:
            return ProbeResult(
                RecoveryClass.UNCERTAIN_MANUAL, "STAGING_RECORD_WITHOUT_TREE"
            )
        return ProbeResult(
            RecoveryClass.COMPLETE, "CANDIDATE_STAGED",
            {"stage": staged.to_dict()},
        )

    def discard_stage(self, ctx: EffectContext) -> dict[str, Any]:
        # Verified release evidence is retained for typed cleanup. Deleting a
        # tree from generic compensation would violate retention/forensics.
        return {"retained": True, "reason_code": "STAGE_RETAINED_FOR_CLEANUP"}

    def probe_stage_discarded(self, ctx: EffectContext) -> ProbeResult:
        return ProbeResult(
            RecoveryClass.COMPLETE, "STAGE_SAFELY_RETAINED",
            {"retained": True},
        )

    # -- backup -----------------------------------------------------------

    def _backup_row(self, operation_id: str):
        with self.units.read() as conn:
            return conn.execute(
                "SELECT backup_id, manifest_digest, verification_state "
                "FROM backup_sets WHERE created_by_operation_id = ? "
                "ORDER BY created_at DESC, backup_id DESC LIMIT 1",
                (operation_id,),
            ).fetchone()

    def ensure_backup(self, ctx: EffectContext) -> dict[str, Any]:
        service = self.backup_supplier() if self.backup_supplier else None
        if service is None:
            raise StepFailure(
                "VERIFIED_BACKUP_REQUIRED", "backup service is unavailable"
            )
        row = self._backup_row(ctx.operation_id)
        if row is None:
            outcome = service.create_backup(
                f"application-update-{ctx.operation_id}.tar",
                requested_by="application-update",
                parent_operation_id=ctx.operation_id,
            )
            if not outcome.ok:
                raise StepFailure(
                    "VERIFIED_BACKUP_REQUIRED", "profile backup did not verify"
                )
            row = self._backup_row(ctx.operation_id)
        if row is None or row["verification_state"] != "verified":
            raise StepFailure(
                "VERIFIED_BACKUP_REQUIRED", "verified backup row is absent"
            )
        verification = service.verify_backup(row["backup_id"])
        if not verification.get("valid"):
            raise StepFailure(
                "VERIFIED_BACKUP_REQUIRED", "backup digest verification failed"
            )
        return {
            "backup_id": row["backup_id"],
            "manifest_digest": row["manifest_digest"],
            "source_schema": self._held(ctx).release.source_schema,
        }

    def probe_backup(self, ctx: EffectContext) -> ProbeResult:
        row = self._backup_row(ctx.operation_id)
        if row is None:
            return ProbeResult(RecoveryClass.ABSENT, "UPDATE_BACKUP_ABSENT")
        service = self.backup_supplier() if self.backup_supplier else None
        if service is None or row["verification_state"] != "verified":
            return ProbeResult(
                RecoveryClass.UNCERTAIN_MANUAL, "UPDATE_BACKUP_UNVERIFIED"
            )
        verified = service.verify_backup(row["backup_id"])
        if not verified.get("valid"):
            return ProbeResult(
                RecoveryClass.UNCERTAIN_MANUAL, "UPDATE_BACKUP_DIGEST_MISMATCH"
            )
        return ProbeResult(
            RecoveryClass.COMPLETE, "UPDATE_BACKUP_VERIFIED",
            {"backup": {"backup_id": row["backup_id"],
             "manifest_digest": row["manifest_digest"]}},
        )

    # -- pointer ----------------------------------------------------------

    def publish_pointer(self, ctx: EffectContext) -> dict[str, Any]:
        try:
            return self.publisher.publish(self._pointer_request(ctx)).to_dict()
        except PointerRefusal as exc:
            raise StepFailure(
                exc.code, "application pointer publication failed",
                mutation_possible=True,
            ) from exc

    def probe_pointer(self, ctx: EffectContext) -> ProbeResult:
        try:
            state, observation = self.publisher.probe(self._pointer_request(ctx))
        except PointerRefusal:
            return ProbeResult(
                RecoveryClass.UNCERTAIN_MANUAL, "POINTER_OBSERVATION_REFUSED"
            )
        output = {
            "candidate_installation_id": observation.current,
            "previous_installation_id": observation.previous,
            "pointer_generation": ctx.request.expected_pointer_generation + 1,
        }
        if state == "COMPLETE":
            return ProbeResult(
                RecoveryClass.COMPLETE, "POINTERS_PUBLISHED",
                {"publication": output},
            )
        if state == "ABSENT":
            return ProbeResult(RecoveryClass.ABSENT, "POINTERS_UNCHANGED")
        if state == "PREPARED":
            return ProbeResult(
                RecoveryClass.PARTIALLY_RESUMABLE, "POINTERS_PREPARED",
                {"publication": output},
            )
        return ProbeResult(
            RecoveryClass.UNCERTAIN_MANUAL, "POINTER_IDENTITY_MISMATCH"
        )

    def restore_pointer(self, ctx: EffectContext) -> dict[str, Any]:
        try:
            return self.publisher.restore(self._pointer_request(ctx)).to_dict()
        except PointerRefusal as exc:
            raise StepFailure(
                exc.code, "application pointer rollback failed",
                mutation_possible=True,
            ) from exc

    def probe_pointer_restored(self, ctx: EffectContext) -> ProbeResult:
        try:
            restored = self.publisher.probe_restored(self._pointer_request(ctx))
        except PointerRefusal:
            restored = False
        return ProbeResult(
            RecoveryClass.COMPLETE if restored else RecoveryClass.UNCERTAIN_MANUAL,
            "POINTERS_RESTORED" if restored else "POINTER_ROLLBACK_UNCERTAIN",
        )

    # -- replacement process / profile ----------------------------------

    def launch_post_update(self, ctx: EffectContext) -> dict[str, Any]:
        return self.post_update.launch(
            operation_id=ctx.operation_id,
            release=self._held(ctx).release,
            backup=(ctx.inputs.get("backup") or {}),
            publication=(ctx.inputs.get("publication") or {}),
        )

    def probe_acknowledgment(self, ctx: EffectContext) -> ProbeResult:
        result = self.post_update.probe(
            operation_id=ctx.operation_id, release=self._held(ctx).release
        )
        if result.output is None:
            return result
        return ProbeResult(
            result.classification, result.reason_code, {"ack": result.output}
        )

    def restore_profile(self, ctx: EffectContext) -> dict[str, Any]:
        return self.post_update.restore_profile(
            operation_id=ctx.operation_id,
            release=self._held(ctx).release,
            backup=(ctx.inputs.get("backup") or {}),
        )

    def probe_profile_restored(self, ctx: EffectContext) -> ProbeResult:
        return self.post_update.probe_profile_restored(
            operation_id=ctx.operation_id, release=self._held(ctx).release
        )

    def verify_health(self, ctx: EffectContext) -> dict[str, Any]:
        return self.post_update.health(
            operation_id=ctx.operation_id, release=self._held(ctx).release
        )

    def probe_health(self, ctx: EffectContext) -> ProbeResult:
        result = self.post_update.probe_health(
            operation_id=ctx.operation_id, release=self._held(ctx).release
        )
        if result.output is None:
            return result
        return ProbeResult(
            result.classification, result.reason_code, {"health": result.output}
        )

    # -- final record -----------------------------------------------------

    def record_installation(self, ctx: EffectContext) -> dict[str, Any]:
        ack = (ctx.inputs.get("ack") or {})
        acknowledgment_digest = ack.get("acknowledgment_digest")
        if not isinstance(acknowledgment_digest, str):
            raise StepFailure("POST_UPDATE_ACK_INVALID", "ack digest is absent")
        try:
            with self.units.begin() as conn:
                installations = ApplicationInstallationRepository(conn)
                state_repo = ApplicationInstallationStateRepository(conn)
                state = state_repo.get()
                if (
                    state.current_installation_id
                    == ctx.request.release_set_digest
                    and state.previous_installation_id
                    == ctx.request.expected_current_installation_id
                    and state.pointer_generation
                    == ctx.request.expected_pointer_generation + 1
                    and state.pending_update_operation_id is None
                ):
                    return {"pointer_generation": state.pointer_generation}
                candidate = installations.get(ctx.request.release_set_digest)
                current = installations.get(
                    ctx.request.expected_current_installation_id
                )
                if candidate is None or current is None:
                    raise ApplicationInstallationError("installation row missing")
                candidate_expected = (
                    InstallationState.PREVIOUS
                    if ctx.request.mode == "ROLLBACK"
                    else InstallationState.STAGED
                )
                installations.transition(
                    candidate.installation_id,
                    target=InstallationState.CURRENT,
                    expected_state=candidate_expected,
                    expected_revision=candidate.revision,
                )
                installations.transition(
                    current.installation_id,
                    target=InstallationState.PREVIOUS,
                    expected_state=InstallationState.CURRENT,
                    expected_revision=current.revision,
                )
                old_previous_id = ctx.request.expected_previous_installation_id
                if old_previous_id and old_previous_id != candidate.installation_id:
                    old_previous = installations.get(old_previous_id)
                    if old_previous and old_previous.state is InstallationState.PREVIOUS:
                        installations.transition(
                            old_previous.installation_id,
                            target=InstallationState.QUARANTINED,
                            expected_state=InstallationState.PREVIOUS,
                            expected_revision=old_previous.revision,
                        )
                committed = state_repo.commit_publication(
                    operation_id=ctx.operation_id,
                    candidate_installation_id=candidate.installation_id,
                    prior_current_installation_id=current.installation_id,
                    expected_previous_installation_id=old_previous_id,
                    expected_pointer_generation=ctx.request.expected_pointer_generation,
                    acknowledgment_digest=acknowledgment_digest,
                    expected_revision=state.revision,
                )
            return {"pointer_generation": committed.pointer_generation}
        except ApplicationInstallationError as exc:
            raise StepFailure(
                "INSTALLATION_RECORD_CONFLICT", "installation record CAS failed",
                mutation_possible=True,
            ) from exc

    def probe_recorded(self, ctx: EffectContext) -> ProbeResult:
        with self.units.read() as conn:
            state = ApplicationInstallationStateRepository(conn).get()
            candidate = ApplicationInstallationRepository(conn).get(
                ctx.request.release_set_digest
            )
        complete = (
            state.current_installation_id == ctx.request.release_set_digest
            and state.previous_installation_id
            == ctx.request.expected_current_installation_id
            and state.pointer_generation
            == ctx.request.expected_pointer_generation + 1
            and state.pending_update_operation_id is None
            and candidate is not None
            and candidate.state is InstallationState.CURRENT
        )
        return ProbeResult(
            RecoveryClass.COMPLETE if complete else RecoveryClass.ABSENT,
            "INSTALLATION_RECORDED" if complete else "INSTALLATION_RECORD_ABSENT",
            {"record": {"pointer_generation": state.pointer_generation}}
            if complete else None,
        )


__all__ = [
    "ApplicationReleaseResolver", "ApplicationUpdateHostAdapter",
    "HeldApplicationRelease", "PostUpdateProcessPort",
    "UnavailableApplicationReleaseResolver", "UnavailablePostUpdateProcessPort",
]
