"""P8 §14.4: Repair Center contract (pure, no I/O).

Read-only repair findings plus explicit, idempotent, auditable repair actions
that are UNAVAILABLE when their preconditions are not met. The Repair Center
never edits SQLite or the filesystem directly — every mutation routes through a
durable operation or an existing composed service. This module is pure: it maps
a set of observed conditions to findings and gates each action on its
preconditions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

REPAIR_CENTER_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RepairAction:
    """One explicit repair action (P8 §14.4)."""

    action_id: str
    title: str
    routes_to: str
    preconditions: tuple[str, ...]
    idempotent: bool = True
    auditable: bool = True
    destructive: bool = False

    def available(self, conditions: set[str]) -> bool:
        """True only when EVERY precondition is present in ``conditions``."""
        return all(p in conditions for p in self.preconditions)

    def to_dict(self, conditions: set[str]) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "title": self.title,
            "routes_to": self.routes_to,
            "preconditions": list(self.preconditions),
            "available": self.available(conditions),
            "idempotent": self.idempotent,
            "auditable": self.auditable,
            "destructive": self.destructive,
        }


# The closed §14.4 repair-action catalogue.
REPAIR_ACTIONS: tuple[RepairAction, ...] = (
    RepairAction(
        action_id="retry-legacy-import",
        title="Retry a failed legacy import",
        routes_to="legacy-import",
        preconditions=("legacy_import_failed",),
    ),
    RepairAction(
        action_id="upgrade-newer-schema",
        title="Resolve a newer-schema refusal through upgrade (never reset)",
        routes_to="schema-upgrade",
        preconditions=("newer_schema_refused",),
    ),
    RepairAction(
        action_id="reclaim-orphaned-content",
        title="Reclaim orphaned staging/quarantine content after evidence checks",
        routes_to="storage-cleanup",
        preconditions=("orphaned_content_evidenced",),
    ),
    RepairAction(
        action_id="release-expired-worker-locks",
        title="Release expired worker locks via lease fencing",
        routes_to="operation-recover",
        preconditions=("expired_worker_locks",),
    ),
    RepairAction(
        action_id="regenerate-runtime-handoff",
        title="Regenerate a missing/stale runtime handoff",
        routes_to="runtime-handoff",
        preconditions=("handoff_missing_or_stale", "runtime_active_verified"),
    ),
    RepairAction(
        action_id="restore-known-good-lineage",
        title="Restore a known-good runtime/model/config lineage",
        routes_to="runtime-rollback",
        preconditions=("known_good_lineage_present",),
    ),
    RepairAction(
        action_id="rotate-gateway-credentials",
        title="Rotate or revoke gateway credentials",
        routes_to="gateway-credentials",
        preconditions=("gateway_provisioned",),
    ),
    RepairAction(
        action_id="rebuild-support-bundle",
        title="Rebuild a support bundle",
        routes_to="support-bundle",
        preconditions=(),
    ),
)


def repair_actions_for_conditions(
    conditions: set[str],
) -> list[dict[str, Any]]:
    """Render every action with its availability for the observed conditions."""
    return [action.to_dict(conditions) for action in REPAIR_ACTIONS]


def available_action_ids(conditions: set[str]) -> list[str]:
    return [a.action_id for a in REPAIR_ACTIONS if a.available(conditions)]


@dataclass(frozen=True)
class RepairFinding:
    """A read-only repair finding (stable id + recommended action)."""

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
    """Map observed conditions to read-only findings (one per condition)."""
    mapping = {
        "legacy_import_failed": RepairFinding(
            "repair-legacy-import", "WARN", "Legacy import failed",
            "retry-legacy-import"),
        "newer_schema_refused": RepairFinding(
            "repair-newer-schema", "FAIL", "Database schema is newer than "
            "supported; upgrade rather than reset", "upgrade-newer-schema"),
        "orphaned_content_evidenced": RepairFinding(
            "repair-orphaned-content", "WARN", "Orphaned staging/quarantine "
            "content detected", "reclaim-orphaned-content"),
        "expired_worker_locks": RepairFinding(
            "repair-worker-locks", "WARN", "Expired worker locks present",
            "release-expired-worker-locks"),
        "handoff_missing_or_stale": RepairFinding(
            "repair-handoff", "WARN", "Runtime handoff missing or stale",
            "regenerate-runtime-handoff"),
    }
    findings = []
    for condition in sorted(conditions):
        if condition in mapping:
            findings.append(mapping[condition])
    return findings
