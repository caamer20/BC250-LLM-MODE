from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from bc250_llm_mode.application_update import (
    ApplicationReleaseVerifier,
    ApplicationUpdateQueryService,
    InstalledApplication,
    ReleaseMember,
    ReleaseSetMaterial,
    UnavailableApplicationReleaseChannel,
    UpdateCode,
    UpdateOutcome,
    VerifiedApplicationRelease,
)
from bc250_llm_mode.release_artifacts import Artifact, ArtifactInventory


REPOSITORY = "caamer20/BC250-LLM-MODE"
COMMIT = "a" * 40
POLICY = "sha256:" + "b" * 64
PLATFORM = "bazzite"


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class _TestTrust:
    available = True
    trust_root_id = "test-only-root"

    def verify_signature(self, canonical_envelope, signature, mechanism):
        return signature == b"signed" and mechanism == "test-only"

    def verify_provenance(self, provenance, **bindings):
        return provenance == {"test_fixture": "verified"}

    def verify_evidence(self, evidence, **bindings):
        return (
            evidence == {"test_fixture": "verified"}
            and bindings["platform_profile"] == PLATFORM
        )


class _Channel:
    available = True
    channel_id = "test-only"

    def __init__(self, *materials):
        self._materials = materials

    def candidates(self):
        return self._materials


def _member(name: str, role: str, payload: bytes, media="application/json"):
    return ReleaseMember(name, role, _digest(payload), len(payload), media)


def _release_material(notes: str = "Security and reliability fixes."):
    version = "1.0.0"
    wheel = ReleaseMember(
        "bc250_llm_mode-1.0.0-py3-none-any.whl", "python-wheel",
        "1" * 64, 1000, "application/vnd.pypi.wheel.v1",
    )
    sdist = ReleaseMember(
        "bc250_llm_mode-1.0.0.tar.gz", "python-sdist",
        "2" * 64, 2000, "application/gzip",
    )
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "metadata": {"component": {
            "name": "bc250-llm-mode", "version": version,
            "hashes": [{"alg": "SHA-256", "content": wheel.sha256}],
        }},
        "components": [],
    }
    sbom_member = _member("sbom.cdx.json", "cyclonedx-sbom", _canonical(sbom))
    checksums = (
        f"{wheel.sha256}  {wheel.name}\n"
        f"{sdist.sha256}  {sdist.name}\n"
        f"{sbom_member.sha256}  {sbom_member.name}\n"
    )
    checksums_member = _member(
        "checksums.sha256", "checksums", checksums.encode(), "text/plain"
    )
    inventory_obj = ArtifactInventory((
        Artifact(**wheel.to_dict()),
        Artifact(**sdist.to_dict()),
        Artifact(**checksums_member.to_dict()),
        Artifact(**sbom_member.to_dict()),
    ))
    inventory = inventory_obj.to_dict()
    inventory_digest = inventory_obj.inventory_digest()
    decision = {
        "decision_schema_version": 3,
        "eligible_for_rc": True,
        "eligible_for_1_0_0": True,
        "blocking_codes": [],
        "blocking_explanations": [],
        "accepted_limitations": ["model-conversion"],
        "candidate_version": version,
        "source_commit": COMMIT,
        "source_ref": f"refs/tags/v{version}",
        "repository": REPOSITORY,
        "inventory_digest": inventory_digest,
        "evidence_used": ["test-only-evidence"],
        "evidence_rejected": [],
        "policy_digest": POLICY,
    }
    manifest = {
        "manifest_schema_version": 3,
        "release_status": "QUALIFIED",
        "qualification_level": "final",
        "version": version,
        "source_commit": COMMIT,
        "source_ref": f"refs/tags/v{version}",
        "repository": REPOSITORY,
        "policy_digest": POLICY,
        "inventory": inventory,
        "inventory_digest": inventory_digest,
        "sbom_digest": "sha256:" + sbom_member.sha256,
        "eligible_for_rc": True,
        "eligible_for_1_0_0": True,
        "blocking_codes": [],
        "evidence_used": ["test-only-evidence"],
    }
    manifest["manifest_digest"] = "sha256:" + _digest(_canonical(manifest))
    evidence = {"test_fixture": "verified"}
    provenance = {"test_fixture": "verified"}
    compatibility = {
        "schema_version": 1,
        "package_name": "bc250-llm-mode",
        "source_schema": 13,
        "minimum_readable_schema": 13,
        "maximum_readable_schema": 14,
        "target_schema": 14,
        "supported_platforms": ["bazzite", "arch"],
    }
    signature = b"signed"
    members = (
        wheel,
        sdist,
        checksums_member,
        sbom_member,
        _member("inventory.json", "artifact-inventory", _canonical(inventory)),
        _member("release-manifest.json", "release-manifest", _canonical(manifest)),
        _member("release-decision.json", "release-decision", _canonical(decision)),
        _member("verified-evidence.json", "verified-evidence", _canonical(evidence)),
        _member("provenance.json", "build-provenance", _canonical(provenance)),
        _member("compatibility.json", "database-compatibility", _canonical(compatibility)),
        _member("release-notes.txt", "release-notes", notes.encode(), "text/plain"),
        _member("release-signature.sig", "release-signature", signature,
                "application/octet-stream"),
    )
    envelope = {
        "format_version": 1,
        "verifier_policy_version": 1,
        "repository": REPOSITORY,
        "version": version,
        "source_commit": COMMIT,
        "source_ref": f"refs/tags/v{version}",
        "published_at": "2026-08-30T12:00:00Z",
        "trust_root_id": "test-only-root",
        "signature_mechanism": "test-only",
        "members": [
            item.to_dict() for item in sorted(
                (item for item in members if item.role != "release-signature"),
                key=lambda item: (item.role, item.name),
            )
        ],
    }
    return ReleaseSetMaterial(
        envelope=envelope,
        decision=decision,
        manifest=manifest,
        inventory=inventory,
        checksums=checksums,
        sbom=sbom,
        evidence=evidence,
        provenance=provenance,
        compatibility=compatibility,
        release_notes=notes,
        signature=signature,
        members=members,
    )


def _verifier():
    return ApplicationReleaseVerifier(
        expected_repository=REPOSITORY, trust=_TestTrust()
    )


def _installed():
    return InstalledApplication(
        "0.9.0.dev0", "c" * 40, "d" * 64, "current", 13,
        current_installation_id="old-release", previous_installation_id="older",
        pointer_generation=7, revision=4,
    )


def test_production_default_is_honestly_unavailable():
    verifier = ApplicationReleaseVerifier(expected_repository=REPOSITORY)
    result = verifier.verify(
        _release_material(), installed_schema=13, platform_profile=PLATFORM
    )
    assert not result.verified
    assert result.reason_code is UpdateCode.SIGNED_UPDATE_CHANNEL_UNAVAILABLE

    service = ApplicationUpdateQueryService(
        installed=_installed(), verifier=verifier, platform_profile=PLATFORM,
        channel=UnavailableApplicationReleaseChannel(),
    )
    assert service.status().reason_code is UpdateCode.SIGNED_UPDATE_CHANNEL_UNAVAILABLE
    assert service.check().release is None
    assert service.preview("1.0.0").outcome is UpdateOutcome.UNAVAILABLE


def test_complete_evaluator_bound_release_verifies():
    result = _verifier().verify(
        _release_material(), installed_schema=13, platform_profile=PLATFORM
    )
    assert result.verified
    assert result.release.version == "1.0.0"
    assert result.release.source_ref == "refs/tags/v1.0.0"
    assert result.release.source_schema == 13
    assert result.release.target_schema == 14


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("repository", "attacker/project", UpdateCode.UNTRUSTED_REPOSITORY),
        ("source_ref", "refs/heads/main", UpdateCode.IMMUTABLE_REF_REQUIRED),
        ("trust_root_id", "unknown", UpdateCode.SIGNATURE_INVALID),
    ],
)
def test_envelope_trust_bindings_fail_closed(field, value, code):
    material = _release_material()
    material = replace(material, envelope={**material.envelope, field: value})
    result = _verifier().verify(
        material, installed_schema=13, platform_profile=PLATFORM
    )
    assert result.reason_code is code
    assert result.release is None


def test_decision_manifest_inventory_and_member_tamper_are_rejected():
    material = _release_material()
    bad_decision = replace(
        material,
        decision={**material.decision, "eligible_for_1_0_0": False},
    )
    assert _verifier().verify(
        bad_decision, installed_schema=13, platform_profile=PLATFORM
    ).reason_code is UpdateCode.BUNDLE_MEMBER_REFUSED

    bad_member = list(material.members)
    bad_member[0] = replace(bad_member[0], sha256="f" * 64)
    assert _verifier().verify(
        replace(material, members=tuple(bad_member)),
        installed_schema=13, platform_profile=PLATFORM,
    ).reason_code is UpdateCode.BUNDLE_MEMBER_REFUSED


def test_compatibility_platform_and_plain_text_note_gates():
    material = _release_material()
    assert _verifier().verify(
        material, installed_schema=12, platform_profile=PLATFORM
    ).reason_code is UpdateCode.DATABASE_INCOMPATIBLE
    assert _verifier().verify(
        material, installed_schema=13, platform_profile="debian"
    ).reason_code is UpdateCode.PLATFORM_EVIDENCE_MISSING

    unsafe = _release_material("looks fine\x1b[31mnot fine")
    assert _verifier().verify(
        unsafe, installed_schema=13, platform_profile=PLATFORM
    ).reason_code is UpdateCode.NOTES_INVALID


def test_verified_release_cannot_be_forged_by_a_caller():
    with pytest.raises(ValueError):
        VerifiedApplicationRelease(
            "1.0.0", COMMIT, "refs/tags/v1.0.0", REPOSITORY,
            "1" * 64, "sha256:" + "2" * 64, "sha256:" + "3" * 64,
            "4" * 64, "2026-08-30T12:00:00Z", "notes",
            13, 13, 14, 14, PLATFORM, 10, 12, "root",
        )


def test_preview_is_revision_bound_literal_and_space_checked():
    notes = "# Literal heading\n<a href='https://bad'>not rendered</a>"
    material = _release_material(notes)
    fixed_now = lambda: datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    service = ApplicationUpdateQueryService(
        installed=_installed(), verifier=_verifier(), platform_profile=PLATFORM,
        channel=_Channel(material), free_bytes=2 * 1024 * 1024 * 1024,
        now=fixed_now,
    )
    checked = service.check()
    assert checked.release.version == "1.0.0"
    preview = service.preview("1.0.0")
    assert preview.outcome is UpdateOutcome.READY
    assert preview.release_notes_plain_text == notes
    assert preview.expected_installation_revision == 4
    assert preview.rollback_installation_id == "old-release"
    assert preview.profile_restore_on_rollback is True
    assert len(preview.preview_digest) == len(preview.confirmation_token) == 64

    too_small = ApplicationUpdateQueryService(
        installed=_installed(), verifier=_verifier(), platform_profile=PLATFORM,
        channel=_Channel(material), free_bytes=1,
    ).preview("1.0.0")
    assert too_small.reason_code is UpdateCode.INSUFFICIENT_SPACE
    assert too_small.confirmation_token is None
