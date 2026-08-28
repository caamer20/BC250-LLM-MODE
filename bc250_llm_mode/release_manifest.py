"""G3 §G3.4 (RELEASE_GATE_AND_PIPELINE_REMEDIATION plan): the decision-derived
release manifest v3 (pure).

A release manifest is DERIVED FROM the evaluator's decision + the artifact
inventory — never hand-written and never generated from caller booleans. A
draft manifest for an ineligible candidate is prominently labeled
``release_status=BLOCKED`` and names its blockers; ``final=True`` refuses an
ineligible decision outright (a blocked candidate can never masquerade as a
final release manifest). The manifest carries a canonical ``manifest_digest``
bound to the exact candidate + artifact bytes, and the manifest file itself is
excluded from its own embedded inventory.

The C1/P9 facade manifest (``release_state.build_release_manifest``, schema v2)
remains as the informational read facade; this module is the release-pipeline
authority.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .release_artifacts import ArtifactInventory
from .release_gate import ReleaseDecision

RELEASE_MANIFEST_SCHEMA_VERSION = 3

RELEASE_MANIFEST_NAME = "release-manifest.json"


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def build_release_manifest(
    *,
    decision: ReleaseDecision,
    inventory: ArtifactInventory,
    sbom_digest: str | None = None,
    final: bool = False,
) -> dict[str, Any]:
    """Derive the schema-v3 release manifest from a decision + inventory.

    ``final=True`` raises ``ValueError`` unless the decision is eligible for
    1.0.0. The manifest file is excluded from its own embedded inventory.
    """
    if final and not decision.eligible_for_1_0_0:
        raise ValueError(
            "a final release manifest requires an eligible decision; "
            f"blocking codes: {list(decision.blocking_codes)}")

    # The manifest file can never be a member of its own inventory.
    artifacts = [a for a in inventory.artifacts
                 if a.name != RELEASE_MANIFEST_NAME]
    inventory_doc = {
        "inventory_schema_version": 2,
        "artifacts": [a.to_dict() for a in artifacts],
        "inventory_digest": inventory.inventory_digest(),
    }

    body: dict[str, Any] = {
        "manifest_schema_version": RELEASE_MANIFEST_SCHEMA_VERSION,
        "release_status": ("QUALIFIED" if decision.eligible_for_1_0_0
                           else "BLOCKED"),
        "qualification_level": "final" if final else "draft",
        "version": decision.candidate_version,
        "source_commit": decision.source_commit,
        "source_ref": decision.source_ref,
        "repository": decision.repository,
        "policy_digest": decision.policy_digest,
        "inventory": inventory_doc,
        "inventory_digest": decision.inventory_digest,
        "sbom_digest": sbom_digest,
        "eligible_for_rc": decision.eligible_for_rc,
        "eligible_for_1_0_0": decision.eligible_for_1_0_0,
        "blocking_codes": list(decision.blocking_codes),
        "evidence_used": list(decision.evidence_used),
    }
    body["manifest_digest"] = ("sha256:" + hashlib.sha256(
        _canonical(body).encode("utf-8")).hexdigest())
    return body
