"""Domain services (Phase A of ROAD_TO_1_0_IMPLEMENTATION_PLAN.md).

Each service owns a narrow slice of durable state, performs its repository
writes inside one explicit commit, and enforces its own invariants so no
whole-state writer can violate them.
"""

from __future__ import annotations

from copy import deepcopy
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

    def mark_tkinter_staged(
        self, evidence: Any = None, *, reboot_required: bool = True
    ) -> dict[str, Any]:
        with self._units.begin() as conn:
            return self._transition(
                conn,
                expected_stage="HARDWARE_VALIDATED",
                next_stage="TKINTER_READY",
                evidence=evidence,
                extra_settings={
                    "bootstrap_tkinter_staged": True,
                    "reboot_required": bool(reboot_required),
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

    def __init__(self, units, *, gateway=None, connection_credentials=None) -> None:
        self._units = units
        self._gateway = gateway
        self._connection_credentials = connection_credentials

    def _refresh_gateway(self, view: dict[str, Any]) -> None:
        if self._gateway is not None:
            self._gateway.write_state_fields(view)
        if self._connection_credentials is not None:
            self._connection_credentials.write_state_fields(view)

    def start(self, view, runner) -> Any:
        from .sharing import start_https_sharing

        self._refresh_gateway(view)
        if (
            self._connection_credentials is not None
            and view.get("gateway_backend_identity") == "disabled"
        ):
            access = self._connection_credentials.access_state()
            self._connection_credentials.enable_for_sharing(
                expected_revision=int(access["revision"]))
            self._refresh_gateway(view)
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

    def status(self, view, runner) -> Any:
        from .sharing import https_sharing_status

        self._refresh_gateway(view)
        return https_sharing_status(view, runner)


class UserPreferencesService:
    """Small typed GUI preference boundary over ordinary settings rows."""

    DEFAULTS = {
        "appearance": "system",
        "reduced_motion": False,
        "notifications_enabled": True,
    }

    def __init__(self, units) -> None:
        self._units = units

    @classmethod
    def validate(cls, values) -> dict[str, Any]:
        if not isinstance(values, dict):
            raise ValueError("preferences must be an object")
        unknown = set(values) - set(cls.DEFAULTS)
        if unknown:
            raise ValueError(f"unknown preference keys: {sorted(unknown)}")
        merged = {**cls.DEFAULTS, **values}
        if merged["appearance"] not in {"system", "light", "dark"}:
            raise ValueError("appearance must be system, light, or dark")
        for key in ("reduced_motion", "notifications_enabled"):
            if not isinstance(merged[key], bool):
                raise ValueError(f"{key} must be boolean")
        return merged

    def current(self) -> dict[str, Any]:
        from .repositories import SettingsRepository

        with self._units.read() as conn:
            settings = SettingsRepository(conn)
            return self.validate({
                key: settings.get(key, default)
                for key, default in self.DEFAULTS.items()
            })

    def apply(self, values) -> dict[str, Any]:
        from .repositories import SettingsRepository

        checked = self.validate(values)
        with self._units.begin() as conn:
            settings = SettingsRepository(conn)
            settings.set_many(checked)
            settings.set_revision(settings.revision() + 1)
        return checked


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
                "profile_id": runtime.get("profile_id"),
                "profile_revision": runtime.get("profile_revision"),
                "profile_fingerprint": runtime.get("profile_fingerprint"),
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
        profile_fields_supplied = any(
            key in desired
            for key in ("profile_id", "profile_revision", "profile_fingerprint")
        )
        profile_id = (
            desired.get("profile_id")
            if profile_fields_supplied else runtime_row.get("profile_id")
        )
        profile_revision = (
            desired.get("profile_revision")
            if profile_fields_supplied else runtime_row.get("profile_revision")
        )
        profile_fingerprint = (
            desired.get("profile_fingerprint")
            if profile_fields_supplied else runtime_row.get("profile_fingerprint")
        )
        if any(value is not None for value in (
            profile_id, profile_revision, profile_fingerprint
        )) and not all(value is not None for value in (
            profile_id, profile_revision, profile_fingerprint
        )):
            raise RuntimeValidationError(
                "profile id, revision, and fingerprint must be set together"
            )
        if profile_id is not None:
            if not isinstance(profile_id, str) or not 1 <= len(profile_id) <= 64:
                raise RuntimeValidationError("profile id is invalid")
            if not isinstance(profile_revision, int) or isinstance(profile_revision, bool) or profile_revision < 1:
                raise RuntimeValidationError("profile revision is invalid")
            if (
                not isinstance(profile_fingerprint, str)
                or len(profile_fingerprint) != 64
                or any(char not in "0123456789abcdef" for char in profile_fingerprint)
            ):
                raise RuntimeValidationError("profile fingerprint is invalid")

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
            "profile_id": profile_id,
            "profile_revision": profile_revision,
            "profile_fingerprint": profile_fingerprint,
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

    def _commit_transaction(
        self,
        conn,
        desired: dict[str, Any],
        *,
        expected_revision: int | None,
    ) -> tuple[dict[str, Any], int]:
        """One-UoW candidate commit: CAS revision, resolve, write, bump."""
        settings = SettingsRepository(conn)
        revision = settings.revision()
        if expected_revision is not None and expected_revision != revision:
            raise RevisionConflict(
                f"Runtime revision conflict: expected {expected_revision}, "
                f"current {revision}"
            )
        resolved = self._resolve(conn, desired)
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
            profile_id=resolved["profile_id"],
            profile_revision=resolved["profile_revision"],
            profile_fingerprint=resolved["profile_fingerprint"],
        )
        settings.set_revision(revision + 1)
        return resolved, revision + 1

    def commit_candidate(
        self,
        desired: dict[str, Any] | None = None,
        *,
        expected_revision: int | None = None,
    ) -> ApplyResult:
        """Exact-revision candidate commit WITHOUT handoff publication.

        Session 5C §10.1: the handoff render is its own durable operation
        step, so this primitive deliberately performs no filesystem effect.
        """
        desired = desired or {}
        with self._units.begin() as conn:
            resolved, new_revision = self._commit_transaction(
                conn, desired, expected_revision=expected_revision
            )
        return ApplyResult(
            status="committed_candidate",
            revision=new_revision,
            resolved={
                "model_alias": resolved["model_alias"],
                "context_per_slot": resolved["context_per_slot"],
                "slots": resolved["slots"],
                "optimizations": resolved["resolved_optimizations"],
                "profile_id": resolved["profile_id"],
                "profile_revision": resolved["profile_revision"],
                "profile_fingerprint": resolved["profile_fingerprint"],
            },
            restart_required=resolved["restart_required"],
            host_tuning_changes=resolved["host_tuning_changes"],
            handoff_published=False,
            warnings=resolved["warnings"],
        )

    def restore_content(
        self,
        prior_config: dict[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> ApplyResult:
        """Restore PRIOR CONTENT as a NEW committed revision (never rewind).

        Records ``restored_content_of_revision`` lineage alongside the
        configuration so probes compare content + lineage instead of
        assuming numeric rollback (Session 5C plan §9).
        """
        content_of = prior_config.pop("restored_content_of_revision", None)
        result = self.apply(prior_config, expected_revision=expected_revision)
        if content_of is not None:
            with self._units.begin() as conn:
                SettingsRepository(conn).set_many(
                    {"restored_content_of_revision": int(content_of)}
                )
        return result

    def apply(
        self,
        desired: dict[str, Any] | None = None,
        *,
        expected_revision: int | None = None,
    ) -> ApplyResult:
        """Candidate commit PLUS handoff regeneration (non-activation paths).

        The durable activation path uses :meth:`commit_candidate` instead;
        this retained entry serves autotune-style callers that commit and
        immediately restart within one host action.
        """
        result = self.commit_candidate(desired, expected_revision=expected_revision)
        published, error = self._publish_handoff(revision=result.revision)
        return ApplyResult(
            status=(
                "committed" if published
                else "committed_handoff_regeneration_required"
            ),
            revision=result.revision,
            resolved=result.resolved,
            restart_required=result.restart_required,
            host_tuning_changes=result.host_tuning_changes,
            handoff_published=published,
            handoff_error=error,
            warnings=result.warnings,
        )

    def restore(self, snapshot: dict[str, Any], *, expected_revision: int) -> ApplyResult:
        """Restore a previously captured desired configuration."""
        return self.apply(
            {
                "model_alias": snapshot.get("model_alias"),
                "context": snapshot.get("context"),
                "slots": snapshot.get("slots"),
                "optimizations_patch": snapshot.get("optimizations") or {},
                "profile_id": snapshot.get("profile_id"),
                "profile_revision": snapshot.get("profile_revision"),
                "profile_fingerprint": snapshot.get("profile_fingerprint"),
            },
            expected_revision=expected_revision,
        )

    def promote_known_good(
        self, *, component_identity: str | None = None
    ) -> dict[str, Any]:
        """Snapshot the CURRENT committed configuration as last-known-good.

        Callers must only invoke this after health + inference verification.
        Session 5C §10.1: all source data is read and written on the SAME
        unit-of-work connection (no nested ``current()`` re-entry).
        """
        with self._units.begin() as conn:
            settings = SettingsRepository(conn)
            runtime_row = RuntimeConfigRepository(conn).get()
            model_alias = (
                runtime_row.get("model_alias") or settings.get("current_model")
            )
            context = int(
                runtime_row.get("context")
                or settings.get("current_ctx", 8192)
            )
            slots = int(
                runtime_row.get("slots")
                or parallel_slots_for_settings(settings.get("optimizations") or {})
            )
            optimizations = settings.get("optimizations") or {}
            verified_at = utcnow()
            KnownGoodRuntimeRepository(conn).set(
                model_alias=model_alias,
                context=context,
                slots=slots,
                profile_id=runtime_row.get("profile_id"),
                profile_revision=runtime_row.get("profile_revision"),
                profile_fingerprint=runtime_row.get("profile_fingerprint"),
                runtime=optimizations,
                runtime_fingerprint=None,
                runtime_component_identity=component_identity,
                verified_at=verified_at,
            )
            return {
                "model_alias": model_alias,
                "context": context,
                "slots": slots,
                "runtime": dict(optimizations),
                "component_identity": component_identity,
                "verified_at": verified_at,
            }

    def restore_known_good(self, row: dict[str, Any] | None) -> None:
        """Restore the prior known-good row EXACTLY, including absence."""
        with self._units.begin() as conn:
            repo = KnownGoodRuntimeRepository(conn)
            if row is None:
                repo.clear()
                return
            repo.set(
                model_alias=row["model_alias"],
                context=int(row["context"]),
                slots=int(row["slots"]),
                profile_id=row.get("profile_id"),
                profile_revision=row.get("profile_revision"),
                profile_fingerprint=row.get("profile_fingerprint"),
                runtime=row.get("runtime") or {},
                runtime_fingerprint=row.get("runtime_fingerprint"),
                runtime_component_identity=row.get("runtime_component_identity"),
                verified_at=row.get("verified_at") or utcnow(),
            )

    def promote_known_good_exact(
        self,
        *,
        model_alias: str,
        context: int,
        slots: int,
        runtime: dict[str, Any] | None = None,
        fingerprint: str | None = None,
        component_identity: str | None = None,
        profile_id: str | None = None,
        profile_revision: int | None = None,
        profile_fingerprint: str | None = None,
    ) -> None:
        """Write the EXACT verified candidate row (Session 5C §7 step 8).

        One unit of work; no second-connection reads inside the write.
        """
        with self._units.begin() as conn:
            KnownGoodRuntimeRepository(conn).set(
                model_alias=model_alias,
                context=int(context),
                slots=int(slots),
                profile_id=profile_id,
                profile_revision=profile_revision,
                profile_fingerprint=profile_fingerprint,
                runtime=runtime or {},
                runtime_fingerprint=fingerprint,
                runtime_component_identity=component_identity,
                verified_at=utcnow(),
            )


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
    """Platform-gated boot-policy and current-boot mode transitions."""

    def __init__(self, units, platform=None) -> None:
        self._units = units
        self._platform = platform

    def enforce_desktop_next_boot(self, view, runner) -> dict[str, Any]:
        from .llmmode import stage_desktop_boot

        before = dict(view)
        stage_desktop_boot(view, runner, platform=self._platform)
        persist_state_diff(self._units, before, view)
        return {"boot_policy": view.get("boot_policy", "desktop")}

    def enter_llm_mode(self, view, runner, *, install_service_fn=None,
                       install: bool = False,
                       mask_desktop_services: bool = False) -> dict[str, Any]:
        from .llmmode import apply_llm_mode

        before = dict(view)
        apply_llm_mode(
            view, runner, platform=self._platform,
            mask_desktop_services=mask_desktop_services,
        )
        if install and install_service_fn is not None:
            install_service_fn(view, runner)
        persist_state_diff(self._units, before, view)
        return {"system_mode": view.get("system_mode")}

    def return_to_desktop(self, view, runner, *, activate_now: bool = False) -> dict[str, Any]:
        from .desktop import switch_to_desktop_mode

        before = dict(view)
        switch_to_desktop_mode(
            view, runner, activate_now=activate_now, platform=self._platform
        )
        persist_state_diff(self._units, before, view)
        return {"boot_policy": view.get("boot_policy", "desktop")}


class ComponentLifecycleService:
    """Environment provisioning; runtime lifecycle lives in the durable
    ``RuntimeLifecycleCommandService`` (ADR 004 D11)."""

    def __init__(self, units, platform=None) -> None:
        self._units = units
        self._platform = platform

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
        result = setup_environment(view, runner, platform=self._platform)
        persist_state_diff(self._units, before, view)
        return result

    def provision_environment(self, view, runner) -> Any:
        """Provision container/venv/toolchain prerequisites (no runtime)."""
        from .env import setup_environment

        before = dict(view)
        result = setup_environment(view, runner, platform=self._platform)
        persist_state_diff(self._units, before, view)
        return result


class OpenWebUIService:
    """Desired Open WebUI install/run state; status queries never write.

    ``gateway`` (a ``GatewayCredentialService``) is optional but, when present,
    refreshes the durable gateway_* view fields into the running snapshot first
    so the container resolves the scoped credential file through ADR 005 D3.
    """

    def __init__(self, units, gateway=None, secret_file_dir=None,
                 connection_credentials=None) -> None:
        self._units = units
        self._gateway = gateway
        self._secret_file_dir = secret_file_dir
        self._connection_credentials = connection_credentials

    def _refresh_gateway(self, view: dict[str, Any]) -> None:
        if self._gateway is not None:
            self._gateway.write_state_fields(view)
        if self._connection_credentials is not None:
            self._connection_credentials.write_state_fields(view)

    def install(self, view, runner) -> Any:
        from .openwebui import install_open_webui

        # Refresh durable gateway fields into the snapshot so the container
        # resolves the scoped credential file via state (ADR 005 D3); the module
        # call stays (state, runner) for the adapter contract.
        self._refresh_gateway(view)
        before = dict(view)
        result = install_open_webui(view, runner)
        persist_state_diff(self._units, before, view)
        return result

    def start(self, view, runner) -> Any:
        from .openwebui import start_open_webui

        self._refresh_gateway(view)
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

        self._refresh_gateway(view)
        before = dict(view)
        result = restart_open_webui(view, runner)
        persist_state_diff(self._units, before, view)
        return result

    def status(self, view, runner) -> Any:
        from .openwebui import open_webui_status

        self._refresh_gateway(view)
        return open_webui_status(view, runner)
