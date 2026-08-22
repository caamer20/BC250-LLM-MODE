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