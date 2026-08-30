"""Production host adapter for durable workload-profile calibration."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

from .fsops import atomic_write_text, ensure_private_dir
from .model_artifact import VERDICT_STANDARD, gguf_layout_verdict, streaming_identity
from .operations.calibration import (
    CalibrationBaselineV1,
    CalibrationPlanV1,
    ProfileCalibrateRequestV1,
)
from .operations.recovery import RecoveryClass
from .operations.workflow import EffectContext, ProbeResult, StepFailure
from .workload_profiles import (
    ProfileResolutionIdentity,
    WorkloadProfileRepository,
    calibration_evidence_fingerprint,
)


FIXED_PROMPT_ID = "local-summary-v1"
FIXED_PROMPT = (
    "In three short bullet points, summarize why a local language model can "
    "be useful when an internet connection is unavailable."
)
MAX_MEASURED_UNITS = 96
RECEIPT_SCHEMA_VERSION = 1
MAX_SSE_LINE_BYTES = 64 * 1024
_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "candidate_index",
        "status",
        "candidate_fingerprint",
        "evidence_fingerprint",
        "profile_resolution_fingerprint",
        "started_at",
        "completed_at",
        "time_to_first_unit_ms",
        "prompt_per_second",
        "generation_per_second",
        "peak_temperature_c",
        "throttling_class",
        "measured_units",
    }
)


class CalibrationServerPort:
    """The only production server/measurement seam used by calibration."""

    def active(self, state: dict[str, Any], runner: Any) -> bool:
        from .server import service_observation

        return bool(service_observation(state, runner).get("active"))

    def restart(self, state: dict[str, Any], runner: Any) -> None:
        from .server import restart_and_wait

        restart_and_wait(state, runner)

    def stop(self, state: dict[str, Any], runner: Any) -> None:
        from .server import stop_service

        stop_service(state, runner)

    def health(self, state: dict[str, Any]) -> dict[str, Any]:
        from .server import health_check

        return health_check(state, timeout=120)

    def measure(
        self,
        state: dict[str, Any],
        *,
        temperature_supplier: Callable[[], float | None],
        monotonic: Callable[[], float],
    ) -> dict[str, Any]:
        """Stream one fixed non-sensitive prompt and retain metrics only."""
        import httpx

        port = int(state.get("server_port", 8080))
        payload = {
            "model": state.get("current_model") or "local",
            "messages": [{"role": "user", "content": FIXED_PROMPT}],
            "stream": True,
            "max_tokens": MAX_MEASURED_UNITS,
            "cache_prompt": True,
        }
        start = monotonic()
        first_at: float | None = None
        generated_units = 0
        peak: float | None = None
        timings: dict[str, Any] = {}
        timeout = httpx.Timeout(180.0, connect=10.0, read=180.0, write=30.0)
        with httpx.stream(
            "POST",
            f"http://127.0.0.1:{port}/v1/chat/completions",
            json=payload,
            timeout=timeout,
        ) as response:
            response.raise_for_status()
            pending = bytearray()
            for chunk in response.iter_bytes():
                pending.extend(chunk)
                if len(pending) > MAX_SSE_LINE_BYTES and b"\n" not in pending:
                    raise StepFailure(
                        "CALIBRATION_RESPONSE_OVERSIZE",
                        "calibration stream event exceeded the bounded line size",
                    )
                while b"\n" in pending:
                    raw_line, _separator, remainder = pending.partition(b"\n")
                    pending = bytearray(remainder)
                    if len(raw_line) > MAX_SSE_LINE_BYTES:
                        raise StepFailure(
                            "CALIBRATION_RESPONSE_OVERSIZE",
                            "calibration stream event exceeded the bounded line size",
                        )
                    try:
                        line = raw_line.rstrip(b"\r").decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise StepFailure(
                            "CALIBRATION_RESPONSE_INVALID",
                            "calibration stream was not valid UTF-8",
                        ) from exc
                    sample = temperature_supplier()
                    if sample is not None:
                        peak = sample if peak is None else max(peak, sample)
                    if not line.startswith("data:"):
                        continue
                    body = line[5:].strip()
                    if not body or body == "[DONE]":
                        continue
                    try:
                        event = json.loads(body)
                    except ValueError as exc:
                        raise StepFailure(
                            "CALIBRATION_RESPONSE_INVALID",
                            "calibration stream event was not valid JSON",
                        ) from exc
                    if not isinstance(event, dict):
                        continue
                    if isinstance(event.get("timings"), dict):
                        timings = dict(event["timings"])
                    choices = event.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
                    if isinstance(delta, dict) and delta.get("content"):
                        generated_units += 1
                        if first_at is None:
                            first_at = monotonic()
            if pending.strip():
                raise StepFailure(
                    "CALIBRATION_RESPONSE_INVALID",
                    "calibration stream ended with an incomplete event",
                )
        elapsed = max(0.001, monotonic() - start)
        generation = timings.get("predicted_per_second")
        if not isinstance(generation, (int, float)) or generation <= 0:
            generation = generated_units / elapsed
        prompt_rate = timings.get("prompt_per_second")
        if not isinstance(prompt_rate, (int, float)) or prompt_rate < 0:
            prompt_rate = 0.0
        if generated_units < 1 or first_at is None:
            raise StepFailure(
                "CALIBRATION_NO_GENERATION",
                "fixed calibration prompt produced no measurable output",
            )
        optimizations = state.get("optimizations") or {}
        stop_c = float(optimizations.get("thermal_stop_c") or 90.0)
        throttle_c = float(optimizations.get("thermal_throttle_c") or 82.0)
        throttling = (
            "UNKNOWN" if peak is None
            else "STOP_THRESHOLD" if peak >= stop_c
            else "THROTTLED" if peak >= throttle_c
            else "NOT_OBSERVED"
        )
        return {
            "time_to_first_unit_ms": max(0, int((first_at - start) * 1000)),
            "prompt_per_second": round(float(prompt_rate), 4),
            "generation_per_second": round(float(generation), 4),
            "peak_temperature_c": round(peak, 2) if peak is not None else None,
            "throttling_class": throttling,
            "measured_units": generated_units,
        }


class CalibrationHostAdapter:
    """Fenced trial execution with crash-visible, privacy-safe receipts."""

    def __init__(
        self,
        *,
        units,
        profiles,
        runtime,
        app_dir: Path,
        state_supplier: Callable[[], dict[str, Any]],
        runner_factory: Callable[[], Any],
        server_port: Any | None = None,
        clock: Callable[[], str],
        monotonic: Callable[[], float] | None = None,
        temperature_supplier: Callable[[], float | None] | None = None,
    ) -> None:
        self._units = units
        self._profiles = profiles
        self._runtime = runtime
        self._state_supplier = state_supplier
        self._runner_factory = runner_factory
        self._server = server_port or CalibrationServerPort()
        self._clock = clock
        self._monotonic = monotonic or time.monotonic
        if temperature_supplier is None:
            from .thermals import read_gpu_temperature

            temperature_supplier = read_gpu_temperature
        self._temperature_supplier = temperature_supplier
        self._receipts = Path(app_dir) / "calibration-receipts"

    @staticmethod
    def _candidate_fingerprint(
        resolution_fingerprint: str, policy: str, label: str
    ) -> str:
        encoded = json.dumps(
            {
                "candidate_policy": policy,
                "fixed_prompt_id": FIXED_PROMPT_ID,
                "label": label,
                "resolution_fingerprint": resolution_fingerprint,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _receipt(self, operation_id: str, index: int) -> Path:
        safe = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:32]
        return self._receipts / safe / f"candidate-{index + 1}.json"

    def _read_receipt(self, operation_id: str, index: int) -> dict[str, Any] | None:
        path = self._receipt(operation_id, index)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        if len(raw) > 16384:
            return None
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        if (
            not isinstance(value, dict)
            or set(value) != _RECEIPT_FIELDS
            or value.get("schema_version") != RECEIPT_SCHEMA_VERSION
            or value.get("status") != "COMPLETE"
            or not isinstance(value.get("candidate_index"), int)
            or not 0 <= int(value["candidate_index"]) < 3
            or any(
                not isinstance(value.get(key), str)
                or len(str(value[key])) != 64
                for key in (
                    "candidate_fingerprint",
                    "evidence_fingerprint",
                    "profile_resolution_fingerprint",
                )
            )
        ):
            return None
        return value

    @staticmethod
    def _plan(ctx: EffectContext) -> dict[str, Any]:
        value = ctx.prior_outputs.get("resolve_plan")
        if not isinstance(value, dict):
            raise StepFailure("CALIBRATION_PLAN_MISSING", "plan checkpoint missing")
        return value

    @staticmethod
    def _baseline(ctx: EffectContext) -> dict[str, Any]:
        value = ctx.prior_outputs.get("capture_baseline")
        if not isinstance(value, dict):
            raise StepFailure("CALIBRATION_BASELINE_MISSING", "baseline checkpoint missing")
        return value

    def _candidate(self, ctx: EffectContext, index: int) -> dict[str, Any] | None:
        candidates = self._plan(ctx).get("candidates") or ()
        return dict(candidates[index]) if index < len(candidates) else None

    def _installed_record(self, alias: str) -> dict[str, Any]:
        from .repositories import ModelInstallationsRepository

        with self._units.read() as conn:
            record = ModelInstallationsRepository(conn).get_by_alias(alias)
        if record is None:
            raise StepFailure("MODEL_NOT_INSTALLED", "calibration model is not installed")
        return record

    def preflight(self, request: ProfileCalibrateRequestV1) -> None:
        preview = self._profiles.preview(request.profile_id, model_alias=request.model_alias)
        if preview["profile_revision"] != request.expected_profile_revision:
            raise StepFailure("PROFILE_REVISION_CONFLICT", "profile revision changed")
        if preview.get("refusal_code"):
            raise StepFailure(str(preview["refusal_code"]), "profile is not calibration-ready")
        if preview.get("tight_confirmation_required") and not request.accept_tight:
            raise StepFailure("TIGHT_CONFIRMATION_REQUIRED", "TIGHT calibration needs confirmation")
        if not preview.get("runtime_component_identity"):
            raise StepFailure("RUNTIME_IDENTITY_UNVERIFIED", "promoted runtime identity is unavailable")
        state = self._state_supplier()
        if self._server.active(state, self._runner_factory()) and self._runtime.known_good() is None:
            raise StepFailure(
                "KNOWN_GOOD_REQUIRED",
                "active runtime cannot be displaced without a known-good restoration target",
            )

    def resolve_plan(self, request: ProfileCalibrateRequestV1) -> CalibrationPlanV1:
        preview = self._profiles.preview(request.profile_id, model_alias=request.model_alias)
        if preview["profile_revision"] != request.expected_profile_revision:
            raise StepFailure("PROFILE_REVISION_CONFLICT", "profile changed before planning")
        record = self._installed_record(request.model_alias)
        digest, _size, _identity = streaming_identity(Path(record["path"]))
        if digest != preview.get("model_content_digest"):
            raise StepFailure("ARTIFACT_IDENTITY_CHANGED", "model artifact changed")
        if gguf_layout_verdict(Path(record["path"])) != VERDICT_STANDARD:
            raise StepFailure("MODEL_LAYOUT_REJECTED", "model layout is not standard GGUF")
        component = str(preview["runtime_component_identity"])
        profile = preview["profile"]
        base = dict(preview["resolved_optimizations"])
        variants = [
            ("profile", base),
            (
                "conservative",
                {
                    **base,
                    "kv_cache_type": "q4_0",
                    "batch_size": min(int(base["batch_size"]), 512),
                    "ubatch_size": min(int(base["ubatch_size"]), 128),
                    "flash_attention": "off",
                },
            ),
        ]
        if request.candidate_policy == "balanced-v1":
            variants.append((
                "throughput-candidate",
                {
                    **base,
                    "ubatch_size": min(512, int(base["batch_size"])),
                    "flash_attention": "on",
                },
            ))
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for label, settings in variants:
            resolved = self._runtime.preview({
                "model_alias": request.model_alias,
                "context": preview["context_per_slot"],
                "slots": preview["slots"],
                "optimizations_patch": settings,
                "profile_id": None,
                "profile_revision": None,
                "profile_fingerprint": None,
            })
            if resolved["fit"]["verdict"] == "NO-FIT":
                continue
            exact = dict(resolved["resolved_optimizations"])
            identity = ProfileResolutionIdentity(
                profile_id=request.profile_id,
                profile_revision=request.expected_profile_revision,
                model_artifact_digest=str(preview["model_content_digest"]),
                quant=str(preview["model_quant"]),
                context_per_slot=int(preview["context_per_slot"]),
                slots=int(preview["slots"]),
                kv_cache_type=str(exact["kv_cache_type"]),
                batch_size=int(exact["batch_size"]),
                ubatch_size=int(exact["ubatch_size"]),
                flash_attention=str(exact["flash_attention"]),
                optimization_preset_id=str(profile["optimization_preset_id"]),
                thermal_policy=str(profile["thermal_policy"]),
                idle_policy=str(profile["idle_policy"]),
                stop_after_minutes=profile.get("stop_after_minutes"),
            )
            resolution_fingerprint = identity.fingerprint()
            if resolution_fingerprint in seen:
                continue
            seen.add(resolution_fingerprint)
            candidates.append({
                "candidate_index": len(candidates),
                "label": label,
                "context_per_slot": int(preview["context_per_slot"]),
                "slots": int(preview["slots"]),
                "settings": exact,
                "fit_verdict": str(resolved["fit"]["verdict"]),
                "profile_resolution_fingerprint": resolution_fingerprint,
                "candidate_fingerprint": self._candidate_fingerprint(
                    resolution_fingerprint, request.candidate_policy, label
                ),
                "evidence_fingerprint": calibration_evidence_fingerprint(
                    resolution_fingerprint, component
                ),
            })
        if not candidates:
            raise StepFailure("CALIBRATION_NO_SAFE_CANDIDATE", "no candidate fits safely")
        return CalibrationPlanV1(
            profile_id=request.profile_id,
            profile_revision=request.expected_profile_revision,
            profile_fingerprint=str(preview["profile_fingerprint"]),
            expected_evidence_fingerprint=str(preview["expected_evidence_fingerprint"]),
            model_alias=request.model_alias,
            runtime_component_identity=component,
            candidates=tuple(candidates[:3]),
        )

    def capture_baseline(
        self, request: ProfileCalibrateRequestV1, plan: CalibrationPlanV1
    ) -> CalibrationBaselineV1:
        del request, plan
        state = self._state_supplier()
        runner = self._runner_factory()
        active = self._server.active(state, runner)
        verified = False
        if active:
            health = self._server.health(state)
            current = self._runtime.current()
            verified = bool(
                health.get("healthy")
                and health.get("model_id") == current.get("model_alias")
            )
            if not verified:
                raise StepFailure("BASELINE_UNVERIFIED", "active baseline is not verified")
        return CalibrationBaselineV1(
            config=self._runtime.capture(),
            known_good=self._runtime.known_good(),
            service_active=active,
            service_verified=verified,
        )

    @staticmethod
    def _config_matches(current: dict[str, Any], expected: dict[str, Any]) -> bool:
        from .optimize import normalized_settings

        scalar_match = all(
            current.get(key) == expected.get(key)
            for key in (
                "model_alias", "context", "slots", "profile_id",
                "profile_revision", "profile_fingerprint",
            )
        )
        return bool(
            scalar_match
            and normalized_settings(current.get("optimizations") or {})
            == normalized_settings(expected.get("optimizations") or {})
        )

    def probe_candidate(self, ctx: EffectContext, index: int) -> ProbeResult:
        candidate = self._candidate(ctx, index)
        if candidate is None:
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "CANDIDATE_SLOT_UNUSED",
                output={"candidate_index": index, "status": "SKIPPED"},
            )
        receipt = self._read_receipt(ctx.operation_id, index)
        if (
            receipt is not None
            and receipt.get("candidate_fingerprint")
            == candidate["candidate_fingerprint"]
        ):
            output = dict(receipt)
            output.pop("schema_version", None)
            return ProbeResult(RecoveryClass.COMPLETE, "CALIBRATION_RECEIPT", output=output)
        baseline = self._baseline(ctx)
        if self._config_matches(self._runtime.current(), baseline["config"]):
            return ProbeResult(RecoveryClass.ABSENT, "BASELINE_STILL_ACTIVE")
        return ProbeResult(RecoveryClass.REVERTIBLE, "TRIAL_RUNTIME_ACTIVE")

    def _restore(self, baseline: dict[str, Any]) -> None:
        current = self._runtime.current()
        # Always republish the baseline handoff, even when SQLite already
        # matches. A crash can occur after the DB restore but before handoff
        # publication; equality of desired config alone is not proof.
        self._runtime.restore(
            dict(baseline["config"]), expected_revision=int(current["revision"])
        )
        self._runtime.restore_known_good(baseline.get("known_good"))
        state = self._state_supplier()
        runner = self._runner_factory()
        if baseline.get("service_active"):
            self._server.restart(state, runner)
            health = self._server.health(self._state_supplier())
            expected = baseline["config"]
            if not (
                health.get("healthy")
                and health.get("model_id") == expected.get("model_alias")
            ):
                raise StepFailure(
                    "BASELINE_RESTORE_UNVERIFIED",
                    "restored baseline health identity did not verify",
                    mutation_possible=True,
                )
        else:
            self._server.stop(state, runner)

    def run_candidate(self, ctx: EffectContext, index: int) -> dict[str, Any]:
        candidate = self._candidate(ctx, index)
        if candidate is None:
            return {"candidate_index": index, "status": "SKIPPED"}
        baseline = self._baseline(ctx)
        started = self._clock()
        current = self._runtime.current()
        desired = {
            "model_alias": self._plan(ctx)["model_alias"],
            "context": candidate["context_per_slot"],
            "slots": candidate["slots"],
            "optimizations_patch": candidate["settings"],
            "profile_id": None,
            "profile_revision": None,
            "profile_fingerprint": None,
        }
        try:
            self._runtime.apply(desired, expected_revision=int(current["revision"]))
            self._server.restart(self._state_supplier(), self._runner_factory())
            health = self._server.health(self._state_supplier())
            if not (
                health.get("healthy")
                and health.get("model_id") == self._plan(ctx)["model_alias"]
                and int(health.get("parallel_slots") or candidate["slots"])
                == int(candidate["slots"])
            ):
                raise StepFailure(
                    "CALIBRATION_HEALTH_MISMATCH",
                    "candidate server identity did not verify",
                    mutation_possible=True,
                )
            metrics = self._server.measure(
                self._state_supplier(),
                temperature_supplier=self._temperature_supplier,
                monotonic=self._monotonic,
            )
        finally:
            self._restore(baseline)
        output = {
            "candidate_index": index,
            "status": "COMPLETE",
            "candidate_fingerprint": candidate["candidate_fingerprint"],
            "evidence_fingerprint": candidate["evidence_fingerprint"],
            "profile_resolution_fingerprint": candidate[
                "profile_resolution_fingerprint"
            ],
            "started_at": started,
            "completed_at": self._clock(),
            **metrics,
        }
        receipt = {"schema_version": RECEIPT_SCHEMA_VERSION, **output}
        target = self._receipt(ctx.operation_id, index)
        ensure_private_dir(target.parent)
        atomic_write_text(target, json.dumps(receipt, sort_keys=True), mode=0o600)
        return output

    def verify_candidate(self, ctx: EffectContext, index: int) -> dict[str, Any]:
        output = ctx.prior_outputs.get(ctx.step_key) or {}
        if output.get("status") not in {"COMPLETE", "SKIPPED"}:
            raise StepFailure("CALIBRATION_RESULT_INVALID", "candidate result is incomplete")
        if not self._config_matches(self._runtime.current(), self._baseline(ctx)["config"]):
            raise StepFailure(
                "BASELINE_RESTORE_UNVERIFIED",
                "candidate did not restore baseline",
                mutation_possible=True,
            )
        try:
            self._receipt(ctx.operation_id, index).unlink()
        except FileNotFoundError:
            pass
        try:
            self._receipt(ctx.operation_id, index).parent.rmdir()
        except OSError:
            pass
        return {}

    def restore_baseline(self, ctx: EffectContext) -> dict[str, Any]:
        self._restore(self._baseline(ctx))
        return {"restored": True}

    def probe_restoration(self, ctx: EffectContext) -> ProbeResult:
        if self._config_matches(self._runtime.current(), self._baseline(ctx)["config"]):
            return ProbeResult(
                RecoveryClass.COMPLETE, "BASELINE_RESTORED", output={"restored": True}
            )
        return ProbeResult(RecoveryClass.REVERTIBLE, "BASELINE_NOT_RESTORED")

    def verify_restoration(self, ctx: EffectContext) -> dict[str, Any]:
        result = self.probe_restoration(ctx)
        if result.classification is not RecoveryClass.COMPLETE:
            raise StepFailure("BASELINE_RESTORE_UNVERIFIED", "restoration did not verify")
        return {}

    @staticmethod
    def _winner(ctx: EffectContext) -> dict[str, Any] | None:
        completed = []
        for index in range(3):
            item = ctx.prior_outputs.get(f"measure_candidate_{index + 1}")
            if (
                isinstance(item, dict)
                and item.get("status") == "COMPLETE"
                and isinstance(item.get("generation_per_second"), (int, float))
            ):
                completed.append(item)
        return max(
            completed,
            key=lambda item: (
                float(item["generation_per_second"]),
                -int(item["candidate_index"]),
            ),
            default=None,
        )

    def probe_recorded_winner(self, ctx: EffectContext) -> ProbeResult:
        winner = self._winner(ctx)
        if winner is None:
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "NO_WINNER_TO_RECORD",
                output={"recorded": False, "winner_fingerprint": None},
            )
        plan = self._plan(ctx)
        should_record = (
            winner.get("evidence_fingerprint")
            == plan.get("expected_evidence_fingerprint")
        )
        if not should_record:
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "WINNER_IS_PROPOSAL",
                output={
                    "recorded": False,
                    "winner_fingerprint": winner.get("candidate_fingerprint"),
                },
            )
        with self._units.read() as conn:
            row = WorkloadProfileRepository(conn).get(
                str(plan["profile_id"]), include_deleted=False
            )
        if row and row.evidence_fingerprint == winner.get("evidence_fingerprint"):
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "WINNER_EVIDENCE_RECORDED",
                output={
                    "recorded": True,
                    "winner_fingerprint": winner.get("candidate_fingerprint"),
                },
            )
        return ProbeResult(RecoveryClass.ABSENT, "WINNER_EVIDENCE_ABSENT")

    def record_winner(self, ctx: EffectContext) -> dict[str, Any]:
        probe = self.probe_recorded_winner(ctx)
        if probe.classification is RecoveryClass.COMPLETE:
            return dict(probe.output or {})
        winner = self._winner(ctx)
        plan = self._plan(ctx)
        assert winner is not None
        with self._units.begin() as conn:
            WorkloadProfileRepository(conn, clock=self._clock).record_evidence(
                str(plan["profile_id"]),
                expected_revision=int(plan["profile_revision"]),
                evidence_class="MEASURED_LOCAL",
                evidence_fingerprint=str(winner["evidence_fingerprint"]),
                evidence_recorded_at=self._clock(),
            )
        return {
            "recorded": True,
            "winner_fingerprint": winner.get("candidate_fingerprint"),
        }


__all__ = [
    "CalibrationHostAdapter",
    "CalibrationServerPort",
    "FIXED_PROMPT_ID",
    "MAX_MEASURED_UNITS",
]
