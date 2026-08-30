"""Pure EXP-5 repair catalogue and availability projection.

The catalogue is shared by GUI, CLI, and the typed command service.
``owner_id`` is descriptive only: composition constructs executable bindings
explicitly; no string in this module is imported or executed dynamically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

REPAIR_CENTER_SCHEMA_VERSION = 2
REPAIR_CONTRACT_VERSION = 1

PRIVILEGES = frozenset({"USER", "ELEVATED", "MIXED"})
CANCELLATION_POLICIES = frozenset({
    "NOT_APPLICABLE", "BEFORE_EFFECT", "OWNER_SAFE_POINTS",
})
REVERSIBILITY_CLASSES = frozenset({
    "EXACT_UNTIL", "COMPENSATED_BY_OWNER", "IRREVERSIBLE",
})
DURATION_CLASSES = frozenset({"INSTANT", "SHORT", "LONG"})
TARGET_POLICIES = frozenset({"NONE", "REQUIRED", "AUTO_BUNDLE"})


@dataclass(frozen=True)
class RepairAction:
    """One complete, closed repair descriptor (ADR 012 D1/D2)."""

    action_id: str
    title: str
    owner_kind: str
    owner_id: str
    preconditions: tuple[str, ...]
    mutation_steps: tuple[str, ...]
    privilege: str
    cancellation_policy: str
    duration_class: str
    reversibility: str
    success_probe_id: str
    failure_codes: tuple[str, ...]
    support_relevance: str
    target_policy: str = "NONE"
    prior_state_survives: bool = True
    estimated_bytes: int | None = None
    idempotent: bool = True
    auditable: bool = True
    destructive: bool = False

    def __post_init__(self) -> None:
        if self.privilege not in PRIVILEGES:
            raise ValueError("unknown repair privilege")
        if self.cancellation_policy not in CANCELLATION_POLICIES:
            raise ValueError("unknown repair cancellation policy")
        if self.duration_class not in DURATION_CLASSES:
            raise ValueError("unknown repair duration class")
        if self.reversibility not in REVERSIBILITY_CLASSES:
            raise ValueError("unknown repair reversibility class")
        if self.target_policy not in TARGET_POLICIES:
            raise ValueError("unknown repair target policy")
        if not 1 <= len(self.mutation_steps) <= 16:
            raise ValueError("repair mutation preview must have 1-16 steps")

    @property
    def routes_to(self) -> str:
        """Read-only compatibility label; never an executable route."""
        return self.owner_id

    def available(self, conditions: set[str]) -> bool:
        return all(item in conditions for item in self.preconditions)

    def to_dict(self, conditions: set[str]) -> dict[str, Any]:
        missing = tuple(item for item in self.preconditions if item not in conditions)
        return {
            "schema_version": REPAIR_CENTER_SCHEMA_VERSION,
            "contract_version": REPAIR_CONTRACT_VERSION,
            "action_id": self.action_id,
            "title": self.title,
            "owner_kind": self.owner_kind,
            "owner_id": self.owner_id,
            "routes_to": self.owner_id,  # compatibility data, never execution
            "preconditions": list(self.preconditions),
            "missing_preconditions": list(missing),
            "available": not missing,
            "mutation_steps": list(self.mutation_steps),
            "privilege": self.privilege,
            "cancellation_policy": self.cancellation_policy,
            "duration_class": self.duration_class,
            "estimated_bytes": self.estimated_bytes,
            "reversibility": self.reversibility,
            "success_probe_id": self.success_probe_id,
            "failure_codes": list(self.failure_codes),
            "support_relevance": self.support_relevance,
            "target_policy": self.target_policy,
            "prior_state_survives": self.prior_state_survives,
            "idempotent": self.idempotent,
            "auditable": self.auditable,
            "destructive": self.destructive,
        }


def _action(
    action_id: str,
    title: str,
    owner_kind: str,
    owner_id: str,
    preconditions: tuple[str, ...],
    mutation_steps: tuple[str, ...],
    success_probe_id: str,
    *,
    privilege: str = "USER",
    cancellation_policy: str = "BEFORE_EFFECT",
    duration_class: str = "SHORT",
    reversibility: str = "COMPENSATED_BY_OWNER",
    failure_codes: tuple[str, ...] = (
        "PRECONDITION_UNMET", "PREVIEW_STALE", "OWNER_FAILED",
    ),
    support_relevance: str = "REPAIR",
    target_policy: str = "NONE",
    prior_state_survives: bool = True,
    destructive: bool = False,
) -> RepairAction:
    return RepairAction(
        action_id, title, owner_kind, owner_id, preconditions, mutation_steps,
        privilege, cancellation_policy, duration_class, reversibility,
        success_probe_id, failure_codes, support_relevance, target_policy,
        prior_state_survives, None, True, True, destructive,
    )


# ADR 012 D1: tuple order is stable presentation order.
REPAIR_ACTIONS: tuple[RepairAction, ...] = (
    _action(
        "retry-legacy-import", "Retry a failed legacy import", "SERVICE",
        "legacy-import", ("legacy_import_failed",),
        ("validate unchanged legacy source", "publish only after full validation"),
        "LEGACY_IMPORT_PUBLISHED",
    ),
    _action(
        "upgrade-newer-schema",
        "Resolve a newer-schema refusal through a verified upgrade", "QUERY",
        "application-update", ("newer_schema_refused",),
        ("inspect compatible signed update", "preserve the existing database"),
        "COMPATIBLE_UPDATE_IDENTIFIED", cancellation_policy="NOT_APPLICABLE",
        reversibility="IRREVERSIBLE", support_relevance="DATABASE",
    ),
    _action(
        "inspect-verified-backup", "Inspect a verified recovery backup",
        "SERVICE", "backup", ("verified_backup_available",),
        ("verify backup identity", "report restore eligibility without mutation"),
        "BACKUP_VERIFIED", cancellation_policy="NOT_APPLICABLE",
        reversibility="IRREVERSIBLE", target_policy="REQUIRED",
        support_relevance="DATABASE",
    ),
    _action(
        "reclaim-orphaned-content", "Quarantine abandoned application staging",
        "OPERATION", "STORAGE_CLEANUP", ("orphaned_content_evidenced",),
        ("revalidate exact staging identities", "move to retained quarantine",
         "verify receipts and destination identities"),
        "CLEANUP_QUARANTINE_VERIFIED", target_policy="REQUIRED",
        reversibility="EXACT_UNTIL", support_relevance="STORAGE",
    ),
    _action(
        "release-expired-worker-locks", "Reclaim an expired worker lock",
        "SERVICE", "worker-lock", ("expired_worker_locks",),
        ("compare expired owner generation", "fenced takeover and release"),
        "WORKER_LOCK_RELEASED", cancellation_policy="NOT_APPLICABLE",
        reversibility="IRREVERSIBLE", support_relevance="OPERATIONS",
    ),
    _action(
        "recover-durable-operation", "Recover interrupted durable work", "SERVICE",
        "operation-recover", ("operation_interrupted",
        "operation_policy_recoverable", "operation_leases_expired"),
        ("revalidate state and lease revisions", "drive owner recovery policy",
         "verify durable terminal or safe resumable state"),
        "OPERATION_RECOVERY_VERIFIED", cancellation_policy="OWNER_SAFE_POINTS",
        target_policy="REQUIRED", support_relevance="OPERATIONS",
    ),
    _action(
        "regenerate-runtime-handoff", "Regenerate a stale runtime handoff",
        "SERVICE", "runtime-handoff", ("handoff_missing_or_stale",
        "runtime_active_verified"),
        ("render from committed runtime revision", "atomically publish handoff",
         "verify fingerprint and revision"),
        "HANDOFF_MATCHES_RUNTIME", support_relevance="RUNTIME",
    ),
    _action(
        "restore-known-good-lineage", "Restore known-good runtime lineage",
        "OPERATION", "RUNTIME_ROLLBACK", ("known_good_lineage_present",),
        ("bind verified rollback identity", "atomically publish runtime",
         "verify health and bounded inference"),
        "KNOWN_GOOD_LINEAGE_ACTIVE", cancellation_policy="OWNER_SAFE_POINTS",
        duration_class="LONG", support_relevance="RUNTIME",
    ),
    _action(
        "rotate-gateway-credentials", "Rotate one client credential", "SERVICE",
        "connection-credentials", ("gateway_provisioned",),
        ("compare client revision", "publish new mode-0600 secret",
         "retire prior generation"),
        "CLIENT_GENERATION_ROTATED", target_policy="REQUIRED",
        reversibility="EXACT_UNTIL", support_relevance="CONNECTIONS",
    ),
    _action(
        "revoke-gateway-credentials", "Revoke one client credential", "SERVICE",
        "connection-credentials", ("gateway_provisioned",),
        ("compare client revision", "revoke metadata and remove secret generations"),
        "CLIENT_REVOKED", target_policy="REQUIRED", reversibility="IRREVERSIBLE",
        destructive=True, support_relevance="CONNECTIONS",
    ),
    _action(
        "disable-unsafe-sharing", "Disable unsafe remote sharing", "SERVICE",
        "sharing", ("sharing_enabled_unsafe",),
        ("disable tailnet Serve and Funnel mappings", "verify no public exposure"),
        "SHARING_DISABLED", privilege="ELEVATED", reversibility="IRREVERSIBLE",
        support_relevance="CONNECTIONS",
    ),
    _action(
        "quarantine-invalid-model", "Quarantine an invalid managed model",
        "OPERATION", "MODEL_REMOVE", ("managed_model_invalid",),
        ("bind managed artifact and alias", "detach alias",
         "retain unreferenced bytes in quarantine"),
        "MODEL_QUARANTINE_VERIFIED", target_policy="REQUIRED",
        cancellation_policy="OWNER_SAFE_POINTS", support_relevance="MODELS",
    ),
    _action(
        "rebuild-service-launcher", "Rebuild app-owned service files", "SERVICE",
        "component-lifecycle", ("service_files_stale",),
        ("render launcher from verified state", "install app-owned unit",
         "verify unit identity without enabling boot start"),
        "SERVICE_FILES_VERIFIED", privilege="ELEVATED",
        support_relevance="RUNTIME",
    ),
    _action(
        "return-to-desktop", "Return to normal desktop mode", "SERVICE",
        "host-mode", ("desktop_return_available",),
        ("restore graphical default target", "unmask app-owned desktop services",
         "preserve models and disable model boot start"),
        "DESKTOP_NEXT_BOOT_VERIFIED", privilege="ELEVATED",
        support_relevance="HOST",
    ),
    _action(
        "rebuild-support-bundle", "Create and verify a redacted support bundle",
        "SERVICE", "support-bundle", (),
        ("collect bounded redacted diagnostics", "write local manifest",
         "self-check every emitted digest"),
        "SUPPORT_BUNDLE_VERIFIED", target_policy="AUTO_BUNDLE",
        cancellation_policy="OWNER_SAFE_POINTS", reversibility="IRREVERSIBLE",
        support_relevance="SUPPORT",
    ),
)

REPAIR_ACTION_IDS = tuple(action.action_id for action in REPAIR_ACTIONS)
if len(REPAIR_ACTION_IDS) != len(set(REPAIR_ACTION_IDS)):
    raise RuntimeError("duplicate repair action id")


def action_by_id(action_id: str) -> RepairAction:
    for action in REPAIR_ACTIONS:
        if action.action_id == action_id:
            return action
    raise KeyError(action_id)


def repair_actions_for_conditions(conditions: set[str]) -> list[dict[str, Any]]:
    return [action.to_dict(conditions) for action in REPAIR_ACTIONS]


def available_action_ids(conditions: set[str]) -> list[str]:
    return [action.action_id for action in REPAIR_ACTIONS if action.available(conditions)]


@dataclass(frozen=True)
class RepairFinding:
    finding_id: str
    severity: str
    title: str
    recommended_action_id: str | None
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": REPAIR_CENTER_SCHEMA_VERSION,
            "finding_id": self.finding_id,
            "severity": self.severity,
            "title": self.title,
            "recommended_action_id": self.recommended_action_id,
            "evidence": self.evidence,
        }


def findings_from_conditions(conditions: set[str]) -> list[RepairFinding]:
    mapping = {
        "legacy_import_failed": RepairFinding(
            "repair-legacy-import", "WARN", "Legacy import failed",
            "retry-legacy-import"),
        "newer_schema_refused": RepairFinding(
            "repair-newer-schema", "FAIL",
            "Database schema is newer than supported; upgrade rather than reset",
            "upgrade-newer-schema"),
        "database_corruption_observed": RepairFinding(
            "repair-database", "FAIL", "Database integrity needs recovery",
            "inspect-verified-backup"),
        "orphaned_content_evidenced": RepairFinding(
            "repair-orphaned-content", "WARN",
            "Abandoned application staging was detected",
            "reclaim-orphaned-content"),
        "expired_worker_locks": RepairFinding(
            "repair-worker-locks", "WARN", "Expired worker lock present",
            "release-expired-worker-locks"),
        "handoff_missing_or_stale": RepairFinding(
            "repair-handoff", "WARN", "Runtime handoff missing or stale",
            "regenerate-runtime-handoff"),
        "sharing_enabled_unsafe": RepairFinding(
            "repair-sharing", "FAIL", "Remote sharing is not safely configured",
            "disable-unsafe-sharing"),
        "managed_model_invalid": RepairFinding(
            "repair-model", "WARN", "A managed model failed verification",
            "quarantine-invalid-model"),
    }
    return [mapping[item] for item in sorted(conditions) if item in mapping]


__all__ = [
    "REPAIR_ACTIONS", "REPAIR_ACTION_IDS", "REPAIR_CENTER_SCHEMA_VERSION",
    "REPAIR_CONTRACT_VERSION", "RepairAction", "RepairFinding", "action_by_id",
    "available_action_ids", "findings_from_conditions",
    "repair_actions_for_conditions",
]
