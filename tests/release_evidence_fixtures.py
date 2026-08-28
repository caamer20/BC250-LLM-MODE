"""G2 §G2.3/§17 (RELEASE_GATE_AND_PIPELINE_REMEDIATION plan): TEST-ONLY
verified-evidence factory.

``make_verified_record`` builds a complete schema-v2 evidence record bound to
a real ``CandidateIdentity`` + ``ArtifactInventory`` and promotes it to a
``VerifiedEvidenceRecord`` through the module-private verifier sentinel — a
clearly test-only trust root. ``wrap_verified`` promotes an already-shaped
record dict the same way for tests that mutate records.

These records are FIXTURES. They must never be committed to ``release/
evidence/`` or presented as real release evidence (plan §2.2: no fabricated
evidence; production verification goes through
``release_evidence.verify_evidence_attestation``).
"""

from __future__ import annotations

from typing import Any

from bc250_llm_mode.release_evidence import (
    EVIDENCE_SCHEMA_VERSION,
    KIND_MEASUREMENT_CONTRACTS,
    VerifiedEvidenceRecord,
    _VERIFIER_KEY,
)

# Per-kind measurement payloads that satisfy the §G2.2 kind contracts.
KIND_MEASUREMENT_FIXTURES: dict[str, dict[str, Any]] = {
    "SOURCE_CHECKOUT": {"checkout_clean": True},
    "DEFAULT_TEST_SUITE": {"collected": 1140, "passed": 1138, "skipped": 2},
    "SLOW_SECURITY_STRESS": {"collected": 51, "passed": 51},
    "CLEAN_WHEEL_SMOKE": {"smoke_result": "PASS"},
    "UPGRADE_MATRIX": {"collected": 2, "passed": 2},
    "BACKUP_RESTORE_HARDWARE": {"round_trip_result": "PASS"},
    "SBOM": {"component_count": 7},
    "BUILD_PROVENANCE": {"builder": "test-fixture-builder"},
    "ARTIFACT_ATTESTATION": {"attestation_format": "sigstore-bundle"},
    "PACKAGE_PUBLISH_ATTESTATION": {"attestation_format": "sigstore-bundle"},
    "CONTAINER_IDENTITY": {"image_ref": "ghcr.io/example/image@sha256:" + "a" * 64},
    "HARDWARE_QUALIFICATION": {"hardware_model": "BC-250-fixture"},
    "SOAK_TEST": {"duration_hours": 24},
    "SECURITY_REVIEW": {"review_scope": "fixture-scope"},
    "HUMAN_ACCEPTANCE": {"acceptance_scope": "fixture-scope"},
    "KNOWN_LIMITATION_ACCEPTANCE": {"capability": "model-conversion"},
    "DOCUMENTATION_RECONCILIATION": {"documents_checked": 5},
    "RELEASE_APPROVAL": {"approver_role": "fixture-approver"},
}

_FALLBACK_SUBJECT_SHA = "a" * 64


def make_verified_record(
    kind: str,
    *,
    candidate: Any,
    inventory: Any,
    evidence_id: str | None = None,
    mechanism: str = "sigstore-bundle",
    **overrides: Any,
) -> VerifiedEvidenceRecord:
    """Build one schema-v2 verified record bound to ``candidate`` and
    ``inventory`` (TEST-ONLY trust root; see module docstring)."""
    subjects = [
        {"name": a.name, "sha256": a.sha256,
         "media_type": a.media_type or "application/octet-stream"}
        for a in inventory.artifacts
    ]
    subject_sha = (inventory.artifacts[0].sha256
                   if inventory.artifacts else _FALLBACK_SUBJECT_SHA)
    record: dict[str, Any] = {
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "evidence_id": evidence_id or f"ev-fixture-{kind}",
        "kind": kind,
        "release_candidate_version": candidate.version,
        "source_repository": candidate.repository,
        "source_commit": candidate.source_commit,
        "policy_digest": candidate.policy_digest,
        "artifact_inventory_digest": inventory.inventory_digest(),
        "artifact_subjects": subjects,
        "issuer": {"type": "ci", "identity": "test-fixture"},
        "issued_at": "2026-01-01T00:00:00+00:00",
        "expires_at": None,
        "environment": {"os": "linux", "architecture": "x86_64",
                        "python": "3.14", "runner": "pytest"},
        "result": "PASS",
        "measurements": dict(KIND_MEASUREMENT_FIXTURES.get(kind, {"note": "fixture"})),
        "attachments": [],
        "verification": {
            "mechanism": mechanism,
            "subject": subject_sha,
            "verifier": "tests/release_evidence_fixtures.py",
            "verified_at": "2026-01-01T00:00:01+00:00",
            "bundle_digest": "sha256:" + "e" * 64,
        },
        "supersedes_evidence_id": None,
    }
    record.update(overrides)
    return wrap_verified(record)


def wrap_verified(record: dict[str, Any]) -> VerifiedEvidenceRecord:
    """TEST-ONLY: promote an already-shaped record dict to a
    ``VerifiedEvidenceRecord`` without running the attestation adapter."""
    verification = record.get("verification") or {}
    return VerifiedEvidenceRecord(
        record=record,
        mechanism=str(verification.get("mechanism") or ""),
        verifier=str(verification.get("verifier") or ""),
        verified_at=str(verification.get("verified_at") or ""),
        bundle_digest=str(verification.get("bundle_digest") or ""),
        _verifier_key=_VERIFIER_KEY,
    )


__all__ = [
    "KIND_MEASUREMENT_FIXTURES",
    "make_verified_record",
    "wrap_verified",
]

# Sanity: every fixture measurement payload satisfies its kind contract.
for _kind, _contract in KIND_MEASUREMENT_CONTRACTS.items():
    assert _contract(KIND_MEASUREMENT_FIXTURES[_kind]), _kind
