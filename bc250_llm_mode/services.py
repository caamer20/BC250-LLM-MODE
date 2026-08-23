"""Domain services (Phase A of ROAD_TO_1_0_IMPLEMENTATION_PLAN.md).

Each service owns a narrow slice of durable state, performs its repository
writes inside one explicit commit, and enforces its own invariants so no
whole-state writer can violate them.
"""

from __future__ import annotations

from typing import Any

from .legacy_import import utcnow  # noqa: F401  (re-exported for repositories)
from .repositories import (
    KnownGoodRuntimeRepository,
    SettingsRepository,
    ThermalStateRepository,
)

NOMINAL = "nominal"
THROTTLED = "throttled"
STOPPED = "stopped"


class ThermalLatchProtected(RuntimeError):
    """A latched thermal stop may not be cleared or downgraded implicitly."""


class ThermalStateService:
    """Safety-authoritative persistence for the thermal latch and baseline.

    Every command runs in its own unit of work (one connection,
    ``BEGIN IMMEDIATE``, commit-or-rollback), so concurrent status probes
    and configuration writers serialize through SQLite instead of sharing
    the facade's connection. The thermal_state row is writable ONLY through
    this service; whole-state dictionaries can never clear or downgrade a
    latched stop.
    """

    def __init__(self, units: "UnitOfWorkFactory") -> None:
        from .unit_of_work import UnitOfWorkFactory

        if not isinstance(units, UnitOfWorkFactory):
            raise TypeError(
                "ThermalStateService requires a UnitOfWorkFactory; raw "
                "shared connections bypass serialization"
            )
        self._units = units

    @classmethod
    def for_database(cls, database_path) -> "ThermalStateService":
        from .unit_of_work import UnitOfWorkFactory

        return cls(UnitOfWorkFactory(database_path))

    def current(self) -> dict[str, Any]:
        with self._units.read() as conn:
            return ThermalStateRepository(conn).get()

    def _apply(
        self,
        *,
        latch_state: str | None = None,
        set_baseline: dict[str, Any] | None = None,
        clear_baseline: bool = False,
        annotate_baseline: dict[str, Any] | None = None,
        allow_from_stopped: bool = False,
    ) -> dict[str, Any]:
        with self._units.begin() as conn:
            repo = ThermalStateRepository(conn)
            current = repo.get()
            current_latch = str(current.get("latch_state") or NOMINAL)
            if (
                current_latch == STOPPED
                and not allow_from_stopped
                and latch_state is not None
                and latch_state != STOPPED
            ):
                raise ThermalLatchProtected(
                    "Thermal latch is STOPPED; an explicit safe reset is required "
                    "before the state can change."
                )
            new_state = latch_state or current_latch

            if clear_baseline:
                new_baseline = None
            elif set_baseline is not None:
                new_baseline = set_baseline
            elif annotate_baseline is not None:
                merged = dict(current.get("baseline") or {})
                merged.update(annotate_baseline)
                new_baseline = merged
            else:
                new_baseline = current.get("baseline")

            repo.set(new_state, new_baseline)
        # Commit happened atomically on context exit.
        return {"latch_state": new_state, "baseline": new_baseline}

    # -- commands ---------------------------------------------------------

    def ensure_throttle(self, baseline: dict[str, Any]) -> dict[str, Any]:
        """Enter THROTTLED and capture the user's GPU profile verbatim."""
        return self._apply(latch_state=THROTTLED, set_baseline=dict(baseline))

    def mark_hold(self) -> dict[str, Any]:
        """Stay THROTTLED without touching the preserved baseline."""
        return self._apply(latch_state=THROTTLED)

    def mark_nominal(self, *, clear_baseline: bool = False) -> dict[str, Any]:
        """Return to NOMINAL after verified host restoration."""
        return self._apply(latch_state=NOMINAL, clear_baseline=clear_baseline)

    def mark_stopped(self) -> dict[str, Any]:
        """Escalate to STOPPED. Always permitted; baseline kept as evidence."""
        return self._apply(latch_state=STOPPED)

    def annotate_restore_failure(self, error: str) -> dict[str, Any]:
        """Record failed profile restoration on the durable baseline."""
        return self._apply(annotate_baseline={"last_restore_error": error})

    def reset_to_nominal(self) -> dict[str, Any]:
        """The only path out of STOPPED: explicit human-safe reset."""
        return self._apply(
            latch_state=NOMINAL, clear_baseline=True, allow_from_stopped=True
        )


# --- Setup workflow (A3) --------------------------------------------------

SETUP_STAGES = (
    "WELCOME",
    "SAFETY_ACKNOWLEDGED",
    "HARDWARE_VALIDATED",
    "TKINTER_READY",
    "LLM_MODE_CONFIGURED",
    "RUNTIME_READY",
    "MODEL_SELECTED",
    "MODEL_PREPARED",
    "PROFILE_APPLIED",
    "SERVICE_INSTALLED",
    "OPTIONALS_CONFIGURED",
    "VERIFIED",
    "COMPLETE",
)
_STAGE_INDEX = {stage: index for index, stage in enumerate(SETUP_STAGES)}


class SetupConflict(RuntimeError):
    """A stale, skipped, or out-of-order workflow transition was rejected."""


class SetupService:
    """Owns the named setup workflow and its compatibility projection.

    Stages persist as canonical strings; the legacy numeric ``setup_phase``
    is maintained only as a monotone projection until the facade is removed.
    Every durable write happens in one unit of work and bumps the global
    configuration revision so stale GUI drafts surface as conflicts.

    Safety acknowledgement is NEVER rewound by repair; models, backups,
    credentials, and known-good runtime information are untouched by this
    service (they live in their own tables/keys).
    """

    def __init__(self, units) -> None:
        from .unit_of_work import UnitOfWorkFactory

        if not isinstance(units, UnitOfWorkFactory):
            raise TypeError("SetupService requires a UnitOfWorkFactory")
        self._units = units

    def current_workflow(self) -> dict[str, Any]:
        with self._units.read() as conn:
            settings = SettingsRepository(conn)
            return self._snapshot(settings)

    @staticmethod
    def _snapshot(settings: SettingsRepository) -> dict[str, Any]:
        stage = str(settings.get("setup_stage", "WELCOME"))
        return {
            "stage": stage,
            "phase": _STAGE_INDEX.get(stage, 0),
            "evidence": settings.get("setup_evidence", {}) or {},
            "complete": stage == "COMPLETE",
        }

    def _transition(
        self,
        conn,
        *,
        expected_stage: str | None,
        next_stage: str,
        evidence: Any = None,
        extra_settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        settings = SettingsRepository(conn)
        snapshot = self._snapshot(settings)
        current = snapshot["stage"]

        if current == next_stage:
            # Repeating an already verified stage is idempotent.
            return snapshot

        if expected_stage is not None:
            if current != expected_stage:
                raise SetupConflict(
                    f"Setup stage conflict: expected {expected_stage}, "
                    f"current is {current}"
                )
            if _STAGE_INDEX[next_stage] != _STAGE_INDEX[current] + 1:
                raise SetupConflict(
                    f"Setup stages cannot be skipped: {current} -> {next_stage}"
                )

        now = utcnow()
        entry: dict[str, Any] = {"at": now}
        if evidence is not None:
            entry["evidence"] = evidence
        updates: dict[str, Any] = {
            "setup_stage": next_stage,
            "setup_phase": _STAGE_INDEX[next_stage],
        }
        new_evidence = dict(snapshot["evidence"])
        new_evidence[next_stage] = entry
        updates["setup_evidence"] = new_evidence
        if extra_settings:
            updates.update(extra_settings)
        settings.set_many(updates)
        settings.set_revision(settings.revision() + 1)
        result = self._snapshot(settings)
        result["transition"] = {"from": current, "to": next_stage, "at": now}
        return result

    def acknowledge_safety(self) -> dict[str, Any]:
        """Record the mandatory safety acknowledgement (never auto-reset)."""
        with self._units.begin() as conn:
            settings = SettingsRepository(conn)
            updates: dict[str, Any] = {"disclaimer_ack": True}
            if not settings.get("ack_timestamp"):
                updates["ack_timestamp"] = utcnow()
            settings.set_many(updates)
            snapshot = self._snapshot(settings)
            if snapshot["stage"] == "WELCOME":
                settings.set_many({"setup_stage": "SAFETY_ACKNOWLEDGED"})
            settings.set_revision(settings.revision() + 1)
            result = self._snapshot(settings)
            result["acknowledged"] = True
            return result

    def record_hardware_validation(self, evidence: Any = None) -> dict[str, Any]:
        with self._units.begin() as conn:
            return self._transition(
                conn,
                expected_stage="SAFETY_ACKNOWLEDGED",
                next_stage="HARDWARE_VALIDATED",
                evidence=evidence,
            )

    def mark_tkinter_staged(self, evidence: Any = None) -> dict[str, Any]:
        with self._units.begin() as conn:
            return self._transition(
                conn,
                expected_stage="HARDWARE_VALIDATED",
                next_stage="TKINTER_READY",
                evidence=evidence,
                extra_settings={
                    "bootstrap_tkinter_staged": True,
                    "reboot_required": True,
                },
            )

    def advance(
        self,
        expected_stage: str,
        next_stage: str,
        evidence: Any = None,
        *,
        extra_settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._units.begin() as conn:
            return self._transition(
                conn,
                expected_stage=expected_stage,
                next_stage=next_stage,
                evidence=evidence,
                extra_settings=extra_settings,
            )

    def mark_setup_complete(self) -> dict[str, Any]:
        with self._units.begin() as conn:
            result = self._transition(
                conn,
                expected_stage="VERIFIED",
                next_stage="COMPLETE",
                extra_settings={"setup_complete": True},
            )
            SettingsRepository(conn).set_many({"setup_complete": True})
            return result

    def reset_for_repair(self, reason: str) -> dict[str, Any]:
        """Rewind to SAFETY_ACKNOWLEDGED for repair.

        The safety acknowledgement itself is preserved forever; installed
        models, backups, credentials, and known-good runtime records live
        outside this service and are not touched.
        """
        with self._units.begin() as conn:
            settings = SettingsRepository(conn)
            snapshot = self._snapshot(settings)
            evidence = dict(snapshot["evidence"])
            evidence["repair"] = {"reason": reason, "at": utcnow()}
            settings.set_many({
                "setup_stage": "SAFETY_ACKNOWLEDGED",
                "setup_phase": _STAGE_INDEX["SAFETY_ACKNOWLEDGED"],
                "setup_complete": False,
                "setup_evidence": evidence,
            })
            settings.set_revision(settings.revision() + 1)
            result = self._snapshot(settings)
            result["repair_reason"] = reason
            return result


class SharingService:
    """Desired sharing mode and observed Tailscale/Serve state."""

    def __init__(self, units) -> None:
        self._units = units

    def start(self, view, runner) -> Any:
        from .sharing import start_https_sharing

        before = dict(view)
        result = start_https_sharing(view, runner)
        persist_state_diff(self._units, before, view)
        return result

    def stop(self, view, runner) -> Any:
        from .sharing import stop_https_sharing

        before = dict(view)
        result = stop_https_sharing(view, runner)
        persist_state_diff(self._units, before, view)
        return result

    def emergency_disable(self, view, runner) -> Any:
        from .sharing import stop_https_sharing

        before = dict(view)
        view["https_sharing_enabled"] = False
        result = stop_https_sharing(view, runner)
        persist_state_diff(self._units, before, view)
        return result


class ModelInstallationService:
    """Synchronous download/prepare/register flow (Phase-B operation later)."""

    def __init__(self, units) -> None:
        self._units = units

    def download_and_prepare(self, view, model, quant, runner, *,
                             downloaded=None) -> Path:
        from .download import download_model
        from .prepare import prepare_model

        before = dict(view)
        artifact = downloaded or download_model(view, model, quant, runner)
        prepare_model(view, model, quant, Path(str(artifact)), runner)
        persist_state_diff(self._units, before, view)
        return Path(str(artifact))

    def register_context_change(self, view, ctx: int) -> int:
        before = dict(view)
        view["current_ctx"] = ctx
        return persist_state_diff(self._units, before, view)


class MaintenanceService:
    """Uninstall/desktop-safe teardown with exact destructive targets."""

    def __init__(self, units) -> None:
        self._units = units

    def uninstall(self, view, runner, *, remove_container=False,
                  remove_models=False):
        from .uninstall import uninstall

        before = dict(view)
        result = uninstall(
            view, runner,
            remove_container=remove_container, remove_models=remove_models,
        )
        persist_state_diff(self._units, before, view)
        return result


# --- Runtime configuration + model activation (A4/A5) ----------------------

from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402

from .catalog import calculate_fit  # noqa: E402
from .local_models import installed_fit_entry  # noqa: E402
from .optimize import (  # noqa: E402
    kv_scale_for_settings,
    normalized_settings,
    parallel_slots_for_settings,
    validate_settings,
)
from .repositories import (  # noqa: E402
    AutotuneHistoryRepository,
    ModelInstallationsRepository,
    RuntimeConfigRepository,
)


class RuntimeValidationError(RuntimeError):
    code = "validation_rejected"


class RevisionConflict(RuntimeError):
    code = "conflict"


RUNTIME_RESTART_KEYS = (
    "parallel_slots", "ubatch_size", "kv_cache_type", "flash_attention",
    "batch_size", "threads", "governor_profile", "gpu_max_mhz", "gpu_min_mhz",
)
HOST_TUNING_KEYS = ("governor_profile", "gpu_max_mhz", "gpu_min_mhz")


@dataclass
class ApplyResult:
    status: str          # committed | committed_handoff_regeneration_required
    revision: int
    resolved: dict[str, Any]
    restart_required: bool
    host_tuning_changes: bool
    handoff_published: bool
    handoff_error: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class ActivationRequest:
    model_id: str
    quant: str | None = None
    context: int | None = None
    slots: int | None = None
    profile_id: str | None = None
    expected_revision: int | None = None
    requester: str = "cli"
    allow_preview: bool = False

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class ActivationResult:
    status: str  # SUCCEEDED | FAILED_ROLLED_BACK | RECOVERY_REQUIRED | REJECTED_* | CONFLICT
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "SUCCEEDED"

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, **self.detail}


class RuntimeController:
    """Typed host-control port. Tests substitute fakes; never CommandRunner."""

    def restart(self, state_view: dict[str, Any]) -> None:
        raise NotImplementedError

    def health_check(self, state_view: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def minimal_inference_probe(self, state_view: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class RuntimeConfigurationService:
    """Typed desired-runtime persistence: pure preview, validated apply.

    ``apply`` commits the candidate desired configuration (settings keys +
    runtime_config row) in ONE unit of work, bumps the configuration
    revision, then renders the runtime handoff from the committed revision.
    Handoff publication failure is reported separately — the commit stands.
    Host tuning and service restart are deliberately separate steps; the
    operation engine will orchestrate them in Phase B.
    """

    def __init__(self, units, *, app_dir=None, state_supplier=None) -> None:
        from .unit_of_work import UnitOfWorkFactory

        if not isinstance(units, UnitOfWorkFactory):
            raise TypeError("RuntimeConfigurationService requires a UnitOfWorkFactory")
        self._units = units
        self._app_dir = Path(app_dir) if app_dir else None
        self._state_supplier = state_supplier

    def current(self) -> dict[str, Any]:
        with self._units.read() as conn:
            settings = SettingsRepository(conn)
            runtime = RuntimeConfigRepository(conn).get()
            return {
                "model_alias": runtime.get("model_alias") or settings.get("current_model"),
                "context": runtime.get("context") or int(settings.get("current_ctx", 8192)),
                "slots": runtime.get("slots")
                or parallel_slots_for_settings(settings.get("optimizations") or {}),
                "profile_id": None,
                "optimizations": settings.get("optimizations") or {},
                "revision": settings.revision(),
            }

    def capture(self) -> dict[str, Any]:
        """Full desired-configuration snapshot for later restore."""
        return self.current()

    def known_good(self):
        with self._units.read() as conn:
            return KnownGoodRuntimeRepository(conn).get()

    def recovery_required(self):
        with self._units.read() as conn:
            return SettingsRepository(conn).get("recovery_required")

    def mark_recovery_required(self, detail: dict[str, Any]) -> None:
        with self._units.begin() as conn:
            settings = SettingsRepository(conn)
            settings.set_many({"recovery_required": {"at": utcnow(), **detail}})
            settings.set_revision(settings.revision() + 1)

    def _resolve(self, conn, desired: dict[str, Any]) -> dict[str, Any]:
        settings = SettingsRepository(conn)
        models = ModelInstallationsRepository(conn).list()
        runtime_row = RuntimeConfigRepository(conn).get()

        base_opts = settings.get("optimizations") or {}
        merged_opts = {**base_opts, **(desired.get("optimizations_patch") or {})}
        try:
            resolved = validate_settings(normalized_settings(merged_opts))
        except (ValueError, TypeError, KeyError) as exc:
            raise RuntimeValidationError(f"invalid runtime settings: {exc}") from exc

        model_alias = (
            desired.get("model_alias")
            or runtime_row.get("model_alias")
            or settings.get("current_model")
        )
        context = int(
            desired.get("context")
            or runtime_row.get("context")
            or settings.get("current_ctx", 8192)
        )
        slots = int(
            desired.get("slots")
            or runtime_row.get("slots")
            or parallel_slots_for_settings(resolved)
        )
        profile_id = desired.get("profile_id")

        record = next((m for m in models if m.get("id") == model_alias), None)
        if record is None:
            raise RuntimeValidationError(f"Model is not installed: {model_alias}")
        if record.get("validation_status") == "quarantined":
            raise RuntimeValidationError(
                f"Model artifact is quarantined: {model_alias}"
            )

        try:
            fit = calculate_fit(
                installed_fit_entry(record),
                str(record["quant"]),
                context,
                kv_scale=kv_scale_for_settings(resolved),
                parallel_slots=slots,
            )
        except ValueError as exc:
            raise RuntimeValidationError(f"fit rejected: {exc}") from exc
        if fit.verdict == "NO-FIT":
            raise RuntimeValidationError(f"fit rejected: {fit.detail}")

        changed: dict[str, Any] = {
            key: value for key, value in resolved.items()
            if base_opts.get(key) != value
        }
        if (runtime_row.get("model_alias") or settings.get("current_model")) != model_alias:
            changed["model"] = model_alias
        if int(runtime_row.get("context") or settings.get("current_ctx", 8192)) != context:
            changed["context"] = context

        restart_required = bool(
            (set(changed) & set(RUNTIME_RESTART_KEYS))
            or "model" in changed
            or "context" in changed
        )
        host_tuning_changes = bool(set(changed) & set(HOST_TUNING_KEYS))

        history = AutotuneHistoryRepository(conn).list()
        tested = any(
            entry.get("model") == model_alias and entry.get("context") == context
            for entry in history
        )

        warnings = []
        if fit.verdict == "TIGHT":
            warnings.append(f"Memory fit is TIGHT: {fit.detail}")

        return {
            "model_alias": model_alias,
            "quant": record.get("quant"),
            "context_per_slot": context,
            "slots": slots,
            "total_context": context * slots,
            "kv_scale": kv_scale_for_settings(resolved),
            "fit": {"verdict": fit.verdict, "detail": fit.detail},
            "resolved_optimizations": resolved,
            "changed_settings": changed,
            "restart_required": restart_required,
            "host_tuning_changes": host_tuning_changes,
            "tested": tested,
            "warnings": warnings,
        }

    def preview(self, desired: dict[str, Any] | None = None) -> dict[str, Any]:
        """Pure projection of a candidate change. Performs zero writes."""
        with self._units.read() as conn:
            return self._resolve(conn, desired or {})

    def _publish_handoff(self, *, revision: int) -> tuple[bool, str | None]:
        if self._app_dir is None:
            return False, "no app_dir wired for handoff publication"
        from .runtime_handoff import RuntimeHandoffRenderer

        state = self._state_supplier() if self._state_supplier else None
        if state is None:
            from .state import DEFAULT_STATE

            state = deepcopy(DEFAULT_STATE)
            with self._units.read() as conn:
                state.update(SettingsRepository(conn).all())
        renderer = RuntimeHandoffRenderer(self._app_dir)
        try:
            renderer.publish(state, config_revision=revision)
        except Exception as exc:  # HandoffPublicationError / OSError
            return False, str(exc)
        return True, None

    def apply(
        self,
        desired: dict[str, Any] | None = None,
        *,
        expected_revision: int | None = None,
    ) -> ApplyResult:
        desired = desired or {}
        resolved = None
        with self._units.begin() as conn:
            settings = SettingsRepository(conn)
            revision = settings.revision()
            if expected_revision is not None and expected_revision != revision:
                raise RevisionConflict(
                    f"Runtime revision conflict: expected {expected_revision}, "
                    f"current {revision}"
                )
            try:
                resolved = self._resolve(conn, desired)
            except BaseException:
                raise  # rollback: nothing was written
            updates = {
                "optimizations": resolved["resolved_optimizations"],
                "current_ctx": resolved["context_per_slot"],
                "current_model": resolved["model_alias"],
            }
            settings.set_many(updates)
            RuntimeConfigRepository(conn).update(
                model_alias=resolved["model_alias"],
                context=resolved["context_per_slot"],
                slots=resolved["slots"],
            )
            settings.set_revision(revision + 1)
        new_revision = revision + 1

        published, error = self._publish_handoff(revision=new_revision)
        return ApplyResult(
            status=(
                "committed" if published
                else "committed_handoff_regeneration_required"
            ),
            revision=new_revision,
            resolved={
                "model_alias": resolved["model_alias"],
                "context_per_slot": resolved["context_per_slot"],
                "slots": resolved["slots"],
                "optimizations": resolved["resolved_optimizations"],
            },
            restart_required=resolved["restart_required"],
            host_tuning_changes=resolved["host_tuning_changes"],
            handoff_published=published,
            handoff_error=error,
            warnings=resolved["warnings"],
        )

    def restore(self, snapshot: dict[str, Any], *, expected_revision: int) -> ApplyResult:
        """Restore a previously captured desired configuration."""
        return self.apply(
            {
                "model_alias": snapshot.get("model_alias"),
                "context": snapshot.get("context"),
                "slots": snapshot.get("slots"),
                "optimizations_patch": snapshot.get("optimizations") or {},
            },
            expected_revision=expected_revision,
        )

    def promote_known_good(
        self, *, component_identity: str | None = None
    ) -> dict[str, Any]:
        """Snapshot the CURRENT committed configuration as last-known-good.

        Callers must only invoke this after health + inference verification.
        """
        with self._units.begin() as conn:
            current = self.current()
            KnownGoodRuntimeRepository(conn).set(
                model_alias=current["model_alias"],
                context=int(current["context"]),
                slots=int(current["slots"]),
                profile_id=current.get("profile_id"),
                runtime=current["optimizations"],
                runtime_fingerprint=None,
                runtime_component_identity=component_identity,
                verified_at=utcnow(),
            )
            current["verified_at"] = utcnow()
            return current


class ModelActivationService:
    """Atomic model activation on top of RuntimeConfigurationService.

    Flow: validate -> commit desired config -> publish handoff -> restart ->
    health check -> minimal inference probe -> promote known-good.
    On any verification failure: restore prior configuration, regenerate the
    handoff, restart and verify the previous model. If rollback itself
    fails, persist RECOVERY_REQUIRED instead of pretending all is well.
    """

    def __init__(
        self,
        units,
        runtime: RuntimeConfigurationService,
        controller: RuntimeController,
        *,
        state_supplier=None,
    ) -> None:
        from .unit_of_work import UnitOfWorkFactory

        if not isinstance(units, UnitOfWorkFactory):
            raise TypeError("ModelActivationService requires a UnitOfWorkFactory")
        self._units = units
        self.runtime = runtime
        self.controller = controller
        self._state_supplier = state_supplier

    def _view(self, resolved: dict[str, Any]) -> dict[str, Any]:
        base = self._state_supplier() if self._state_supplier else {}
        view = {**base, **resolved}
        view["current_model"] = resolved["model_alias"]
        return view

    def activate(self, request: ActivationRequest) -> ActivationResult:
        latch = ThermalStateService(self._units).current()["latch_state"]
        if latch == STOPPED:
            return ActivationResult(
                "REJECTED_THERMAL_LATCH",
                {"reason": "Thermal latch is stopped; reset it before activating."},
            )

        prior = self.runtime.capture()
        try:
            applied = self.runtime.apply(
                {
                    "model_alias": request.model_id,
                    "context": request.context,
                    "slots": request.slots,
                    "profile_id": request.profile_id,
                },
                expected_revision=request.expected_revision,
            )
        except RuntimeValidationError as exc:
            return ActivationResult(
                "REJECTED_INVALID", {"reason": str(exc), "phase": "pre-mutation"}
            )
        except RevisionConflict as exc:
            return ActivationResult(
                "CONFLICT", {"reason": str(exc), "phase": "pre-mutation"}
            )

        view = self._view(applied.resolved)
        try:
            self.controller.restart(view)
            self.controller.health_check(view)
            self.controller.minimal_inference_probe(view)
        except Exception as activation_error:  # noqa: BLE001 - rollback covers all
            return self._rollback(request, prior, applied, activation_error)

        build = view.get("llamacpp_build")
        self.runtime.promote_known_good(
            component_identity=build.get("describe") if isinstance(build, dict) else None,
        )
        return ActivationResult("SUCCEEDED", {
            "model": applied.resolved["model_alias"],
            "revision": applied.revision,
            "handoff_published": applied.handoff_published,
        })

    def _rollback(self, request, prior, applied, activation_error) -> ActivationResult:
        current_revision = self.runtime.current()["revision"]
        try:
            restored = self.runtime.restore(prior, expected_revision=current_revision)
            view = self._view(restored.resolved)
            self.controller.restart(view)
            self.controller.health_check(view)
        except Exception as rollback_error:  # noqa: BLE001 - both failed
            self.runtime.mark_recovery_required({
                "activation_error": str(activation_error),
                "rollback_error": str(rollback_error),
                "request": request.to_dict(),
            })
            return ActivationResult("RECOVERY_REQUIRED", {
                "activation_error": str(activation_error),
                "rollback_error": str(rollback_error),
            })
        return ActivationResult("FAILED_ROLLED_BACK", {
            "activation_error": str(activation_error),
            "restored_model": prior.get("model_alias"),
            "handoff_published": restored.handoff_published,
        })


# --- Session 3: narrow lifecycle services ----------------------------------

_NON_DURABLE_KEYS = {"revision", "schema_version", "app_dir", "logs_dir",
                     "state_path"}


def persist_state_diff(
    units, before: dict, after: dict, *, allowed_keys=None
) -> int:
    """Persist ONLY the keys an operation changed, in one unit of work.

    A whole-state dictionary is never written: the diff between the
    pre-command and post-command views is committed with a single revision
    bump so stale frontends surface conflicts. When ``allowed_keys`` is
    provided (frontend transition path), only those keys are eligible.
    """
    changes = {
        key: value
        for key, value in after.items()
        if key not in _NON_DURABLE_KEYS
        and (allowed_keys is None or key in allowed_keys)
        and before.get(key) != value
    }
    if not changes:
        return 0
    with units.begin() as conn:
        settings = SettingsRepository(conn)
        settings.set_many(changes)
        settings.set_revision(settings.revision() + 1)
    return len(changes)


class HostModeService:
    """Boot-policy intent and current-boot LLM mode transitions."""

    def __init__(self, units) -> None:
        self._units = units

    def enforce_desktop_next_boot(self, view, runner) -> dict[str, Any]:
        from .desktop_mode import stage_desktop_boot

        before = dict(view)
        stage_desktop_boot(view, runner)
        persist_state_diff(self._units, before, view)
        return {"boot_policy": view.get("boot_policy", "desktop")}

    def enter_llm_mode(self, view, runner, *, install_service_fn=None,
                       install: bool = False) -> dict[str, Any]:
        from .llm_mode import apply_llm_mode

        before = dict(view)
        apply_llm_mode(view, runner)
        if install and install_service_fn is not None:
            install_service_fn(view, runner)
        persist_state_diff(self._units, before, view)
        return {"system_mode": view.get("system_mode")}

    def return_to_desktop(self, view, runner, *, activate_now: bool = False) -> dict[str, Any]:
        from .desktop_mode import switch_to_desktop_mode

        before = dict(view)
        switch_to_desktop_mode(view, runner, activate_now=activate_now)
        persist_state_diff(self._units, before, view)
        return {"boot_policy": view.get("boot_policy", "desktop")}


class ComponentLifecycleService:
    """Environment setup and llama.cpp update/rollback/provenance."""

    def __init__(self, units) -> None:
        self._units = units

    def record_provenance(self, component: str, describe: str,
                          commit_sha=None) -> None:
        from .legacy_import import utcnow
        from .repositories import ComponentProvenanceRepository

        with self._units.begin() as conn:
            ComponentProvenanceRepository(conn).set_component(
                component, describe, commit_sha
            )

    def setup_environment(self, view, runner) -> Any:
        from .env import setup_environment

        before = dict(view)
        result = setup_environment(view, runner)
        persist_state_diff(self._units, before, view)
        return result

    def update_llamacpp(self, view, runner, *, tag: str | None = None) -> Any:
        from .env import update_llamacpp

        before = dict(view)
        result = update_llamacpp(view, runner, tag=tag)
        persist_state_diff(self._units, before, view)
        return result

    def rollback_llamacpp(self, view, runner) -> Any:
        from .env import rollback_llamacpp

        before = dict(view)
        result = rollback_llamacpp(view, runner)
        persist_state_diff(self._units, before, view)
        return result


class OpenWebUIService:
    """Desired Open WebUI install/run state; status queries never write."""

    def __init__(self, units) -> None:
        self._units = units

    def install(self, view, runner) -> Any:
        from .openwebui import install_open_webui

        before = dict(view)
        result = install_open_webui(view, runner)
        persist_state_diff(self._units, before, view)
        return result

    def start(self, view, runner) -> Any:
        from .openwebui import start_open_webui

        before = dict(view)
        result = start_open_webui(view, runner)
        persist_state_diff(self._units, before, view)
        return result

    def stop(self, view, runner) -> Any:
        from .openwebui import stop_open_webui

        before = dict(view)
        result = stop_open_webui(view, runner)
        persist_state_diff(self._units, before, view)
        return result

    def restart(self, view, runner) -> Any:
        from .openwebui import restart_open_webui

        before = dict(view)
        result = restart_open_webui(view, runner)
        persist_state_diff(self._units, before, view)
        return result

    def status(self, view, runner) -> Any:
        from .openwebui import open_webui_status

        return open_webui_status(view, runner)