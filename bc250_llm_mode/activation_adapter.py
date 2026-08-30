"""Production implementation of the typed activation port (Session 5C §10).

``ActivationHostAdapter`` is the ONE production ``ActivationHost``. It
composes narrow primitives only:

- ``RuntimeConfigurationService`` commit/observe/restore/promote methods;
- ``RuntimeHandoffRenderer`` strict publication/observation;
- the injected ``server_port`` seam, whose production binding lives in
  composition and calls ONLY ``bc250_llm_mode.server`` functions;
- bounded GGUF/digest inspection from ``model_artifact``.

It never constructs operation SQL and never touches systemd, HTTP, or the
filesystem except through those owners.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any, Callable

from .model_artifact import (
    VERDICT_STANDARD,
    gguf_layout_verdict,
    streaming_identity,
)
from .operations.activation import (
    CandidateRuntimeV1,
    ConfigEvidenceV1,
    HandoffEvidenceV1,
    HealthEvidenceV1,
    InferenceEvidenceV1,
    KnownGoodEvidenceV1,
    ModelActivateRequestV1,
    PriorRuntimeSnapshotV1,
    RestartEvidenceV1,
    RestorationEvidenceV1,
)
from .operations.recovery import RecoveryClass
from .operations.workflow import ProbeResult
from .repositories import ThermalStateRepository


class ArtifactRejected(RuntimeError):
    """Closed candidate-rejection code for pre-mutation gates."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ActivationServerPort:
    """Typed server seams; command strings stay inside ``server.py``.

    The default binding calls :mod:`bc250_llm_mode.server` exclusively.
    Tests substitute fakes.
    """

    def observe_active(self, view: dict[str, Any], runner: Any) -> bool:
        from .server import service_observation

        return bool(service_observation(view, runner)["active"])

    def restart(self, view: dict[str, Any], runner: Any) -> None:
        from .server import restart_service

        restart_service(view, runner)

    def stop(self, view: dict[str, Any], runner: Any) -> dict[str, Any]:
        from .server import stop_service

        return stop_service(view, runner)

    def health(
        self,
        view: dict[str, Any],
        *,
        timeout: int = 120,
        monotonic: Any = None,
        sleep: Any = None,
        pulse: Any = None,
    ) -> dict[str, Any]:
        from .server import health_check

        return health_check(
            view, timeout=timeout, monotonic=monotonic, sleep=sleep,
            pulse=pulse,
        )

    def inference(self, view: dict[str, Any], *, timeout: float = 20.0) -> dict:
        from .server import minimal_inference_probe

        return minimal_inference_probe(view, timeout=timeout)


class ActivationHostAdapter:
    """The single production adapter behind MODEL_ACTIVATE v1."""

    def __init__(
        self,
        *,
        units: Any,
        runtime: Any,
        renderer: Any,
        state_supplier: Callable[[], dict[str, Any]],
        runner_factory: Callable[[], Any],
        server_port: Any | None = None,
        profile_query: Any | None = None,
        monotonic: Any = None,
        sleep: Any = None,
    ) -> None:
        self._units = units
        self._runtime = runtime
        self._renderer = renderer
        self._state_supplier = state_supplier
        self._runner_factory = runner_factory
        self._server = server_port or ActivationServerPort()
        self._profile_query = profile_query
        self._monotonic = monotonic or time.monotonic
        self._sleep = sleep or time.sleep

    # -- internal helpers -------------------------------------------------------
    def _view(self) -> dict[str, Any]:
        return dict(self._state_supplier() or {})

    def _candidate_view(self, candidate: CandidateRuntimeV1) -> dict[str, Any]:
        view = self._view()
        view["current_model"] = candidate.model_alias
        view["current_ctx"] = candidate.context_per_slot
        optimizations = dict(candidate.settings or view.get("optimizations") or {})
        optimizations["parallel_slots"] = candidate.parallel_slots
        view["optimizations"] = optimizations
        return view

    @staticmethod
    def _context_matches(
        health: dict[str, Any], candidate: CandidateRuntimeV1,
    ) -> bool:
        """Compare like units across current and legacy health payloads."""
        explicit = health.get("context_per_slot")
        if explicit is not None:
            return int(explicit) == candidate.context_per_slot
        # Compatibility with health seams produced before context units were
        # explicit: those tests/adapters reported aggregate ``n_ctx``.
        return int(health.get("n_ctx") or 0) == (
            candidate.context_per_slot * candidate.parallel_slots
        )

    def _latch(self) -> str:
        with self._units.read() as conn:
            return str(ThermalStateRepository(conn).get().get("latch_state") or "")

    def _identity(self, path) -> tuple[str, int, str]:
        return streaming_identity(path)

    def _matches_candidate(
        self, candidate: CandidateRuntimeV1, current: dict[str, Any]
    ) -> bool:
        if current.get("model_alias") != candidate.model_alias:
            return False
        if current.get("context") != candidate.context_per_slot:
            return False
        if current.get("slots") != candidate.parallel_slots:
            return False
        return (
            current.get("profile_id") == candidate.profile_id
            and current.get("profile_revision") == candidate.profile_revision
            and current.get("profile_fingerprint") == candidate.profile_fingerprint
        )

    # -- port: resolve / observe candidate ---------------------------------------
    def resolve_candidate(
        self, request: ModelActivateRequestV1
    ) -> CandidateRuntimeV1:
        if self._latch() == "stopped":
            raise ArtifactRejected("THERMAL_LATCH_STOPPED")
        with self._units.read() as conn:
            from .repositories import ModelInstallationsRepository

            models = ModelInstallationsRepository(conn).list()
        record = next(
            (m for m in models if m.get("id") == request.model_alias), None
        )
        if record is None:
            raise ArtifactRejected("MODEL_NOT_INSTALLED")
        if record.get("validation_status") == "quarantined":
            raise ArtifactRejected("MODEL_QUARANTINED")
        path = record.get("path")
        verdict = gguf_layout_verdict(path)
        if verdict != VERDICT_STANDARD:
            raise ArtifactRejected(f"MODEL_LAYOUT_{verdict.upper()}")
        desired: dict[str, Any] = {
            "model_alias": request.model_alias,
            "context": request.context_per_slot,
            "slots": request.parallel_slots,
            "profile_id": None,
            "profile_revision": None,
            "profile_fingerprint": None,
        }
        profile_id = None
        profile_revision = None
        profile_fingerprint = None
        profile_preview = None
        if request.profile_id is not None:
            from .workload_profiles import (
                WorkloadProfileCommandError,
                WorkloadProfileError,
                decode_profile_binding,
            )

            if self._profile_query is None:
                raise ArtifactRejected("PROFILE_RESOLVER_UNAVAILABLE")
            try:
                binding = decode_profile_binding(request.profile_id)
                profile_preview = self._profile_query.preview(
                    binding.profile_id, model_alias=request.model_alias
                )
            except (WorkloadProfileError, WorkloadProfileCommandError) as exc:
                raise ArtifactRejected(getattr(exc, "code", "PROFILE_BINDING_INVALID")) from exc
            if (
                profile_preview.get("profile_revision") != binding.revision
                or profile_preview.get("profile_fingerprint") != binding.fingerprint
            ):
                raise ArtifactRejected("PROFILE_PREVIEW_STALE")
            if profile_preview.get("refusal_code"):
                raise ArtifactRejected(str(profile_preview["refusal_code"]))
            if (
                profile_preview.get("tight_confirmation_required")
                and not binding.tight_confirmed
            ):
                raise ArtifactRejected("TIGHT_CONFIRMATION_REQUIRED")
            if (
                request.context_per_slot != profile_preview.get("context_per_slot")
                or request.parallel_slots != profile_preview.get("slots")
            ):
                raise ArtifactRejected("PROFILE_REQUEST_MISMATCH")
            profile_id = binding.profile_id
            profile_revision = binding.revision
            profile_fingerprint = binding.fingerprint
            desired.update({
                "context": int(profile_preview["context_per_slot"]),
                "slots": int(profile_preview["slots"]),
                "optimizations_patch": dict(
                    profile_preview.get("resolved_optimizations") or {}
                ),
                "profile_id": profile_id,
                "profile_revision": profile_revision,
                "profile_fingerprint": profile_fingerprint,
            })
        resolved = self._runtime.preview(desired)
        context = int(resolved["context_per_slot"])
        slots = int(resolved["slots"])
        digest, size, identity = self._identity(path)
        if (
            profile_preview is not None
            and digest != profile_preview.get("model_content_digest")
        ):
            raise ArtifactRejected("ARTIFACT_IDENTITY_CHANGED")
        partial = CandidateRuntimeV1(
            model_alias=request.model_alias,
            canonical_path=str(path),
            quant=str(record["quant"]),
            display_alias=str(record.get("display_name") or ""),
            validation_status=str(record.get("validation_status") or ""),
            provenance_class=str(record.get("provenance") or ""),
            byte_size=size,
            file_identity=identity,
            content_digest=digest,
            layout_verdict=verdict,
            context_per_slot=context,
            parallel_slots=slots,
            profile_id=profile_id,
            profile_revision=profile_revision,
            profile_fingerprint=profile_fingerprint,
        )
        view = self._candidate_view(partial)
        from .runtime_handoff import runtime_fingerprint

        with self._units.read() as conn:
            from .runtime_builds import RuntimeComponentRepository

            component_row = RuntimeComponentRepository(conn).current()
        identity = (
            (component_row or {}).get("promoted_build_id")
            or (view.get("llamacpp_build") or {}).get("describe", "")
            if isinstance(view.get("llamacpp_build"), dict)
            else (component_row or {}).get("promoted_build_id")
        ) or ""
        return dataclasses.replace(
            partial,
            settings=dict(resolved["resolved_optimizations"]),
            fit_verdict=str((resolved.get("fit") or {}).get("verdict") or ""),
            fit_detail=str((resolved.get("fit") or {}).get("detail") or ""),
            runtime_fingerprint=runtime_fingerprint(view),
            component_identity=str(identity),
        )

    def observe_candidate(
        self, request: ModelActivateRequestV1, candidate: CandidateRuntimeV1
    ) -> ProbeResult:
        try:
            digest, size, identity = self._identity(candidate.canonical_path)
        except OSError:
            return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "ARTIFACT_MISSING")
        verdict = gguf_layout_verdict(candidate.canonical_path)
        if (
            digest == candidate.content_digest
            and size == candidate.byte_size
            and identity == candidate.file_identity
            and verdict == candidate.layout_verdict
            and verdict == VERDICT_STANDARD
        ):
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "CANDIDATE_RESOLVED",
                output=dataclasses.asdict(candidate),
            )
        return ProbeResult(RecoveryClass.REVERTIBLE, "ARTIFACT_IDENTITY_CHANGED")

    # -- port: capture prior --------------------------------------------------------
    def capture_prior(
        self, request: ModelActivateRequestV1
    ) -> PriorRuntimeSnapshotV1:
        current = self._runtime.current()
        known_good = self._runtime.known_good()
        payload = self._renderer.observe()
        view = self._view()
        runner = self._runner_factory()
        active = False
        health: dict[str, Any] = {}
        inference_ok = False
        if self._server.observe_active(view, runner):
            try:
                health = self._server.health(view, timeout=15)
                probe = self._server.inference(view, timeout=10.0)
                active = bool(health.get("healthy"))
                inference_ok = probe.get("ok") is True
            except Exception:  # noqa: BLE001 - unverified prior is evidence
                active = True
                inference_ok = False
        running_model = (
            current.get("model_alias") if active else None
        )
        return PriorRuntimeSnapshotV1(
            service_state=(
                "ACTIVE_VERIFIED"
                if active and inference_ok
                else "UNVERIFIED" if active
                else "STOPPED"
            ),
            revision=int(current["revision"]),
            config={
                "model_alias": current.get("model_alias"),
                "context_per_slot": int(current.get("context") or 0),
                "parallel_slots": int(current.get("slots") or 1),
                "optimizations": dict(current.get("optimizations") or {}),
                "profile_id": current.get("profile_id"),
                "profile_revision": current.get("profile_revision"),
                "profile_fingerprint": current.get("profile_fingerprint"),
            },
            handoff_fingerprint=(
                payload.get("runtime_fingerprint") if payload else None
            ),
            handoff_payload=payload,
            known_good=dict(known_good) if known_good else None,
            component_identity=None,
            observed_model_alias=(
                health.get("model_id") or running_model
            )
            if active
            else None,
            observed_context_total=(
                health.get("context_total", health.get("n_ctx"))
                if active else None
            ),
            observed_slots=health.get("parallel_slots") if active else None,
            inference_verified=inference_ok,
        )

    # -- port: commit config ------------------------------------------------------
    def commit_candidate(
        self,
        request: ModelActivateRequestV1,
        candidate: CandidateRuntimeV1,
        external_effect_id: str,
    ) -> ConfigEvidenceV1:
        result = self._runtime.commit_candidate(
            {
                "model_alias": candidate.model_alias,
                "context": candidate.context_per_slot,
                "slots": candidate.parallel_slots,
                "optimizations_patch": dict(candidate.settings),
                "profile_id": candidate.profile_id,
                "profile_revision": candidate.profile_revision,
                "profile_fingerprint": candidate.profile_fingerprint,
            },
            expected_revision=request.expected_runtime_revision,
        )
        return ConfigEvidenceV1(
            revision=result.revision,
            model_alias=result.resolved["model_alias"],
            context_per_slot=int(result.resolved["context_per_slot"]),
            parallel_slots=int(result.resolved["slots"]),
            profile_id=result.resolved.get("profile_id"),
            profile_revision=result.resolved.get("profile_revision"),
            profile_fingerprint=result.resolved.get("profile_fingerprint"),
        )

    def observe_config(
        self,
        request: ModelActivateRequestV1,
        candidate: CandidateRuntimeV1,
        prior: PriorRuntimeSnapshotV1,
    ) -> ProbeResult:
        current = self._runtime.current()
        if self._matches_candidate(candidate, current):
            evidence = ConfigEvidenceV1(
                revision=int(current["revision"]),
                model_alias=current.get("model_alias"),
                context_per_slot=int(current.get("context") or 0),
                parallel_slots=int(current.get("slots") or 1),
                profile_id=current.get("profile_id"),
                profile_revision=current.get("profile_revision"),
                profile_fingerprint=current.get("profile_fingerprint"),
            )
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "CANDIDATE_CONFIG_COMMITTED",
                output=dataclasses.asdict(evidence),
            )
        config = prior.config or {}
        same_prior = (
            current.get("model_alias") == config.get("model_alias")
            and int(current.get("context") or 0)
            == int(config.get("context_per_slot") or -1)
            and int(current.get("slots") or 0)
            == int(config.get("parallel_slots") or -1)
            and current.get("profile_id") == config.get("profile_id")
            and current.get("profile_revision") == config.get("profile_revision")
            and current.get("profile_fingerprint")
            == config.get("profile_fingerprint")
        )
        if same_prior or not config:
            return ProbeResult(RecoveryClass.ABSENT, "EXACT_PRIOR_CONFIG")
        return ProbeResult(
            RecoveryClass.UNCERTAIN_MANUAL, "UNRELATED_CONFIG_REVISION"
        )

    # -- port: publish handoff ------------------------------------------------------
    def publish_candidate(
        self, candidate: CandidateRuntimeV1, external_effect_id: str
    ) -> HandoffEvidenceV1:
        revision = int(self._runtime.current()["revision"])
        view = self._candidate_view(candidate)
        self._renderer.publish(view, config_revision=revision)
        payload = self._renderer.observe()
        if payload is None:
            raise RuntimeError("published handoff failed strict observation")
        return HandoffEvidenceV1(
            fingerprint=payload["runtime_fingerprint"],
            config_revision=payload["config_revision"],
            model_id=payload["model_id"],
            schema_version=payload["schema_version"],
        )

    def observe_handoff(
        self,
        candidate: CandidateRuntimeV1,
        prior: PriorRuntimeSnapshotV1,
    ) -> ProbeResult:
        payload = self._renderer.observe()
        if payload is None:
            if self._renderer.path.exists():
                return ProbeResult(
                    RecoveryClass.UNCERTAIN_MANUAL, "MALFORMED_HANDOFF_PAYLOAD"
                )
            return ProbeResult(RecoveryClass.ABSENT, "NO_HANDOFF")
        if (
            payload.get("model_id") == candidate.model_alias
            and payload.get("config_revision") == prior.revision + 1
        ):
            evidence = HandoffEvidenceV1(
                fingerprint=payload["runtime_fingerprint"],
                config_revision=payload["config_revision"],
                model_id=payload["model_id"],
                schema_version=payload["schema_version"],
            )
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "CANDIDATE_HANDOFF_PUBLISHED",
                output=dataclasses.asdict(evidence),
            )
        if (
            prior.handoff_fingerprint
            and payload.get("runtime_fingerprint") == prior.handoff_fingerprint
        ):
            return ProbeResult(RecoveryClass.ABSENT, "EXACT_PRIOR_HANDOFF")
        return ProbeResult(
            RecoveryClass.UNCERTAIN_MANUAL, "THIRD_PARTY_HANDOFF_PAYLOAD"
        )

    # -- port: restart / health / inference ------------------------------------------
    def restart_candidate(
        self,
        candidate: CandidateRuntimeV1,
        prior: PriorRuntimeSnapshotV1,
        external_effect_id: str,
        pulse: Any = None,
    ) -> RestartEvidenceV1:
        already = self.observe_restart(candidate, prior, pulse=pulse)
        if (
            already.classification is RecoveryClass.COMPLETE
            and already.reason_code == "CANDIDATE_RUNTIME_VERIFIED"
        ):
            return RestartEvidenceV1(restarted_now=False, was_already_active=True)
        view = self._candidate_view(candidate)
        self._server.restart(view, self._runner_factory())
        return RestartEvidenceV1(restarted_now=True, was_already_active=False)

    def observe_restart(
        self, candidate: CandidateRuntimeV1, prior: PriorRuntimeSnapshotV1,
        pulse: Any = None,
    ) -> ProbeResult:
        view = self._candidate_view(candidate)
        runner = self._runner_factory()
        if not self._server.observe_active(view, runner):
            if prior.service_state == "STOPPED":
                return ProbeResult(
                    RecoveryClass.COMPLETE,
                    "PRIOR_STOPPED_UNCHANGED",
                    output=dataclasses.asdict(
                        RestartEvidenceV1(
                            restarted_now=False, was_already_active=False
                        )
                    ),
                )
            return ProbeResult(RecoveryClass.REVERTIBLE, "SERVICE_INACTIVE")
        try:
            health = self._server.health(view, pulse=pulse)
        except Exception:  # noqa: BLE001 - bounded timeout is revertible
            return ProbeResult(RecoveryClass.REVERTIBLE, "HEALTH_TIMEOUT")
        identity_ok = (
            health.get("healthy")
            and health.get("model_id") == candidate.model_alias
            and self._context_matches(health, candidate)
            and int(health.get("parallel_slots") or 0)
            == candidate.parallel_slots
        )
        if identity_ok:
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "CANDIDATE_RUNTIME_VERIFIED",
                output=dataclasses.asdict(
                    RestartEvidenceV1(
                        restarted_now=False, was_already_active=True
                    )
                ),
            )
        observed = health.get("model_id")
        if observed == (
            prior.observed_model_alias or prior.config.get("model_alias")
        ):
            return ProbeResult(RecoveryClass.REVERTIBLE, "PRIOR_STILL_ACTIVE")
        if observed == candidate.model_alias:
            return ProbeResult(
                RecoveryClass.REVERTIBLE, "CANDIDATE_IDENTITY_MISMATCH"
            )
        return ProbeResult(
            RecoveryClass.UNCERTAIN_MANUAL, "SERVICE_IDENTITY_AMBIGUOUS"
        )

    def check_health(
        self, candidate: CandidateRuntimeV1, pulse: Any = None,
    ) -> HealthEvidenceV1:
        view = self._candidate_view(candidate)
        health = self._server.health(view, pulse=pulse)
        healthy = bool(
            health.get("healthy")
            and health.get("model_id") == candidate.model_alias
            and self._context_matches(health, candidate)
            and int(health.get("parallel_slots") or 0)
            == candidate.parallel_slots
        )
        return HealthEvidenceV1(
            healthy=healthy,
            model_alias=candidate.model_alias,
            context_total=candidate.context_per_slot * candidate.parallel_slots,
            slots=candidate.parallel_slots,
        )

    def check_inference(self, candidate: CandidateRuntimeV1) -> InferenceEvidenceV1:
        view = self._candidate_view(candidate)
        started = self._monotonic()
        probe = self._server.inference(view)
        elapsed = self._monotonic() - started
        success = probe.get("ok") is True
        bucket = (
            "sub_second"
            if elapsed < 1.0
            else "seconds" if elapsed < 10.0 else "slow"
        )
        return InferenceEvidenceV1(
            success=bool(success),
            generated_count=1 if success else 0,
            elapsed_bucket=bucket,
        )

    # -- port: known-good ------------------------------------------------------------
    def promote_known_good(
        self, candidate: CandidateRuntimeV1, external_effect_id: str
    ) -> KnownGoodEvidenceV1:
        self._runtime.promote_known_good_exact(
            model_alias=candidate.model_alias,
            context=candidate.context_per_slot,
            slots=candidate.parallel_slots,
            runtime=dict(candidate.settings),
            fingerprint=candidate.runtime_fingerprint,
            component_identity=candidate.component_identity,
            profile_id=candidate.profile_id,
            profile_revision=candidate.profile_revision,
            profile_fingerprint=candidate.profile_fingerprint,
        )
        return KnownGoodEvidenceV1(
            model_alias=candidate.model_alias,
            context=int(candidate.context_per_slot),
            slots=int(candidate.parallel_slots),
            fingerprint=candidate.runtime_fingerprint,
            component_identity=candidate.component_identity,
            profile_id=candidate.profile_id,
            profile_revision=candidate.profile_revision,
            profile_fingerprint=candidate.profile_fingerprint,
        )

    def observe_known_good(
        self,
        candidate: CandidateRuntimeV1,
        prior: PriorRuntimeSnapshotV1,
    ) -> ProbeResult:
        row = self._runtime.known_good()
        if row is None:
            return ProbeResult(RecoveryClass.ABSENT, "NO_KNOWN_GOOD_ROW")
        matches_candidate = (
            row.get("model_alias") == candidate.model_alias
            and int(row.get("context") or 0) == int(candidate.context_per_slot)
            and int(row.get("slots") or 0) == int(candidate.parallel_slots)
            and row.get("runtime_fingerprint") == candidate.runtime_fingerprint
            and row.get("runtime_component_identity")
            == candidate.component_identity
            and row.get("profile_id") == candidate.profile_id
            and row.get("profile_revision") == candidate.profile_revision
            and row.get("profile_fingerprint") == candidate.profile_fingerprint
        )
        if matches_candidate:
            evidence = KnownGoodEvidenceV1(
                model_alias=row["model_alias"],
                context=int(row["context"]),
                slots=int(row["slots"]),
                fingerprint=row.get("runtime_fingerprint"),
                component_identity=row.get("runtime_component_identity"),
                profile_id=row.get("profile_id"),
                profile_revision=row.get("profile_revision"),
                profile_fingerprint=row.get("profile_fingerprint"),
            )
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "KNOWN_GOOD_PROMOTED",
                output=dataclasses.asdict(evidence),
            )
        prior_kg = prior.known_good
        if prior_kg is None:
            return ProbeResult(RecoveryClass.ABSENT, "PRIOR_KNOWN_GOOD_ABSENT")
        same_prior = (
            row.get("model_alias") == prior_kg.get("model_alias")
            and row.get("verified_at") == prior_kg.get("verified_at")
            and row.get("runtime_fingerprint")
            == prior_kg.get("runtime_fingerprint")
        )
        if same_prior:
            return ProbeResult(RecoveryClass.ABSENT, "EXACT_PRIOR_KNOWN_GOOD")
        return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "THIRD_PARTY_KNOWN_GOOD")

    # -- port: aggregate restoration (plan §9) -----------------------------------------
    def _config_matches_prior(
        self, current: dict[str, Any], prior: PriorRuntimeSnapshotV1
    ) -> bool:
        config = prior.config or {}
        return (
            current.get("model_alias") == config.get("model_alias")
            and int(current.get("context") or 0)
            == int(config.get("context_per_slot") or -1)
            and int(current.get("slots") or 0)
            == int(config.get("parallel_slots") or -1)
            and current.get("profile_id") == config.get("profile_id")
            and current.get("profile_revision") == config.get("profile_revision")
            and current.get("profile_fingerprint")
            == config.get("profile_fingerprint")
        )

    def _service_matches_prior(
        self, prior: PriorRuntimeSnapshotV1, view: dict[str, Any],
        pulse: Any = None,
    ) -> tuple[bool, bool]:
        """``(verified, service_active)`` from read-only observation."""
        runner = self._runner_factory()
        active = self._server.observe_active(view, runner)
        verified = False
        if active:
            try:
                health = self._server.health(view, pulse=pulse)
                probe = self._server.inference(view, timeout=10.0)
                verified = bool(health.get("healthy")) and (
                    probe.get("ok") is True
                )
            except Exception:  # noqa: BLE001
                verified = False
        return verified, active

    def _restoration_state(
        self, prior: PriorRuntimeSnapshotV1, pulse: Any = None,
    ) -> tuple[bool, bool]:
        """``(verified, service_active)`` against the durable prior target."""
        current = self._runtime.current()
        config_ok = self._config_matches_prior(current, prior)
        row = self._runtime.known_good()
        prior_kg = prior.known_good
        kg_ok = True
        if prior_kg is None:
            kg_ok = row is None
        elif row is not None:
            kg_ok = (
                row.get("model_alias") == prior_kg.get("model_alias")
                and int(row.get("context") or 0)
                == int(prior_kg.get("context") or 0)
                and int(row.get("slots") or 0) == int(prior_kg.get("slots") or 0)
                and row.get("runtime_fingerprint")
                == prior_kg.get("runtime_fingerprint")
                and row.get("profile_id") == prior_kg.get("profile_id")
                and row.get("profile_revision")
                == prior_kg.get("profile_revision")
                and row.get("profile_fingerprint")
                == prior_kg.get("profile_fingerprint")
            )
        else:
            kg_ok = False
        payload = self._renderer.observe()
        handoff_ok = True
        if prior.handoff_payload:
            handoff_ok = (
                payload is not None
                and payload.get("model_id")
                == prior.handoff_payload.get("model_id")
            )
        else:
            handoff_ok = payload is None
        service_verified, service_active = self._service_matches_prior(
            prior, self._view(), pulse=pulse
        )
        if prior.service_state == "ACTIVE_VERIFIED":
            service_ok = service_verified and self._config_matches_prior(
                current, prior
            )
        else:
            service_ok = not service_active
        verified = bool(config_ok and kg_ok and handoff_ok and service_ok)
        return verified, service_active

    def restore_prior(
        self,
        prior: PriorRuntimeSnapshotV1,
        candidate: CandidateRuntimeV1,
        restoration_id: str,
        pulse: Any = None,
    ) -> RestorationEvidenceV1:
        proven, _active = self._restoration_state(prior, pulse=pulse)
        if proven:
            return RestorationEvidenceV1(
                restored=True,
                stage_codes=["ALREADY_PROVEN"],
                service_state=prior.service_state,
            )
        stage_codes: list[str] = []
        # 3. Restore prior desired content as a NEW revision (never rewind).
        config = dict(prior.config or {})
        self._runtime.restore_content(
            {
                "model_alias": config.get("model_alias"),
                "context": int(config.get("context_per_slot") or 8192),
                "slots": int(config.get("parallel_slots") or 4),
                "optimizations_patch": dict(config.get("optimizations") or {}),
                "profile_id": config.get("profile_id"),
                "profile_revision": config.get("profile_revision"),
                "profile_fingerprint": config.get("profile_fingerprint"),
                "restored_content_of_revision": prior.revision,
            }
        )
        stage_codes.append("CONFIG_RESTORED")
        # 4. Restore the prior known-good row exactly, including absence.
        self._runtime.restore_known_good(prior.known_good)
        stage_codes.append("KNOWN_GOOD_RESTORED")
        # 5. Publish the prior handoff exactly, or remove own candidate one.
        view = self._view()
        if prior.handoff_payload:
            self._renderer.publish(
                view, config_revision=int(self._runtime.current()["revision"])
            )
            stage_codes.append("HANDOFF_RESTORED")
        elif self._renderer.path.exists():
            self._renderer.path.unlink()
            stage_codes.append("HANDOFF_REMOVED")
        # 6/7. Service state through server.py only.
        if prior.service_state == "ACTIVE_VERIFIED":
            alias = prior.observed_model_alias or config.get("model_alias")
            restart_view = dict(view)
            restart_view["current_model"] = alias
            if not self._server.observe_active(
                restart_view, self._runner_factory()
            ):
                self._server.restart(restart_view, self._runner_factory())
                stage_codes.append("SERVICE_RESTARTED")
            verified, _ = self._service_matches_prior(
                prior, restart_view, pulse=pulse
            )
            if not verified:
                raise RuntimeError("restored runtime failed verification")
            stage_codes.append("SERVICE_ACTIVE_VERIFIED")
        else:
            self._server.stop(view, self._runner_factory())
            stage_codes.append("SERVICE_STOPPED")
        return RestorationEvidenceV1(
            restored=True,
            stage_codes=stage_codes,
            service_state=prior.service_state,
        )

    def observe_restoration(
        self,
        prior: PriorRuntimeSnapshotV1,
        candidate: CandidateRuntimeV1,
        pulse: Any = None,
    ) -> ProbeResult:
        verified, _active = self._restoration_state(prior, pulse=pulse)
        if verified:
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "PRIOR_RUNTIME_RESTORED",
                output={"service_state": prior.service_state},
            )
        if prior.config:
            return ProbeResult(RecoveryClass.REVERTIBLE, "RESTORATION_INCOMPLETE")
        return ProbeResult(
            RecoveryClass.UNCERTAIN_MANUAL, "NO_DURABLE_PRIOR_EVIDENCE"
        )
