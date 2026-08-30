"""Fail-closed application release verification and update query contracts.

EXP-6 deliberately separates *observing* an update from installing it.  This
module is pure: it does not open URLs, archives, files, databases, or processes.
Adapters provide bounded, already-observed member identities and one reviewed
trust implementation.  Production currently composes the unavailable adapters,
so no branch, package index, local wheel, or caller boolean can become an update
authority by accident.

See :doc:`docs/adr/013-signed-two-slot-application-updates.md`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import InitVar, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, Sequence

from .release_artifacts import Artifact, ArtifactInventory, INVENTORY_SCHEMA_VERSION
from .release_gate import RELEASE_DECISION_SCHEMA_VERSION
from .release_manifest import RELEASE_MANIFEST_SCHEMA_VERSION


APPLICATION_RELEASE_SET_VERSION = 1
APPLICATION_UPDATE_QUERY_SCHEMA_VERSION = 1
APPLICATION_UPDATE_VERIFIER_POLICY_VERSION = 1
APPLICATION_COMPATIBILITY_SCHEMA_VERSION = 1

MAX_METADATA_BYTES = 1024 * 1024
MAX_COMBINED_METADATA_BYTES = 8 * 1024 * 1024
MAX_RELEASE_NOTES_BYTES = 32 * 1024
MAX_MEMBERS = 32
MAX_MEMBER_NAME = 240
MAX_MEMBER_BYTES = 4 * 1024 * 1024 * 1024
MAX_PREVIEW_AGE_SECONDS = 15 * 60

EXPECTED_PACKAGE_NAME = "bc250-llm-mode"
EXPECTED_RELEASE_REPOSITORY = "caamer20/BC250-LLM-MODE"
DEFAULT_CHANNEL_ID = "unavailable"

_HEX64_RE = re.compile(r"[0-9a-f]{64}")
_FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
_VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc|\.dev|\.post)[0-9]+)?")
_SAFE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,239}")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class UpdateOutcome(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    READY = "READY"
    ACCEPTED = "ACCEPTED"
    SUCCEEDED = "SUCCEEDED"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED_SAFE = "FAILED_SAFE"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class UpdateCode(str, Enum):
    OK = "OK"
    SIGNED_UPDATE_CHANNEL_UNAVAILABLE = "SIGNED_UPDATE_CHANNEL_UNAVAILABLE"
    CHANNEL_POLICY_INVALID = "CHANNEL_POLICY_INVALID"
    CHANNEL_FETCH_FAILED = "CHANNEL_FETCH_FAILED"
    REDIRECT_REFUSED = "REDIRECT_REFUSED"
    UNTRUSTED_REPOSITORY = "UNTRUSTED_REPOSITORY"
    IMMUTABLE_REF_REQUIRED = "IMMUTABLE_REF_REQUIRED"
    RELEASE_DECISION_INELIGIBLE = "RELEASE_DECISION_INELIGIBLE"
    MANIFEST_MISMATCH = "MANIFEST_MISMATCH"
    INVENTORY_MISMATCH = "INVENTORY_MISMATCH"
    CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
    SBOM_MISMATCH = "SBOM_MISMATCH"
    PROVENANCE_INVALID = "PROVENANCE_INVALID"
    SIGNATURE_INVALID = "SIGNATURE_INVALID"
    PLATFORM_EVIDENCE_MISSING = "PLATFORM_EVIDENCE_MISSING"
    DATABASE_INCOMPATIBLE = "DATABASE_INCOMPATIBLE"
    BUNDLE_MALFORMED = "BUNDLE_MALFORMED"
    BUNDLE_MEMBER_REFUSED = "BUNDLE_MEMBER_REFUSED"
    UNKNOWN_BUNDLE_MEMBER = "UNKNOWN_BUNDLE_MEMBER"
    NOTES_INVALID = "NOTES_INVALID"
    INSUFFICIENT_SPACE = "INSUFFICIENT_SPACE"
    VERIFIED_BACKUP_REQUIRED = "VERIFIED_BACKUP_REQUIRED"
    PREVIEW_STALE = "PREVIEW_STALE"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    UPDATE_BUSY = "UPDATE_BUSY"
    STAGING_FAILED = "STAGING_FAILED"
    SMOKE_FAILED = "SMOKE_FAILED"
    POINTER_IDENTITY_MISMATCH = "POINTER_IDENTITY_MISMATCH"
    POST_UPDATE_ACK_TIMEOUT = "POST_UPDATE_ACK_TIMEOUT"
    POST_UPDATE_ACK_INVALID = "POST_UPDATE_ACK_INVALID"
    PROFILE_MIGRATION_FAILED = "PROFILE_MIGRATION_FAILED"
    PROFILE_RESTORE_FAILED = "PROFILE_RESTORE_FAILED"
    ROLLBACK_UNAVAILABLE = "ROLLBACK_UNAVAILABLE"
    RECOVERY_BARRIER_MANUAL = "RECOVERY_BARRIER_MANUAL"
    VERSION_NOT_FOUND = "VERSION_NOT_FOUND"


SIGNED_MEMBER_ROLES: tuple[str, ...] = (
    "python-wheel",
    "python-sdist",
    "checksums",
    "cyclonedx-sbom",
    "artifact-inventory",
    "release-manifest",
    "release-decision",
    "verified-evidence",
    "build-provenance",
    "database-compatibility",
    "release-notes",
)
SIGNATURE_ROLE = "release-signature"
REQUIRED_BUNDLE_ROLES = frozenset((*SIGNED_MEMBER_ROLES, SIGNATURE_ROLE))
REQUIRED_INVENTORY_ROLES = frozenset(
    {"python-wheel", "python-sdist", "checksums", "cyclonedx-sbom"}
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_hex64(value: Any) -> bool:
    return isinstance(value, str) and bool(_HEX64_RE.fullmatch(value))


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and _is_hex64(value[7:])
    )


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _bounded_json(value: Any, *, limit: int = MAX_METADATA_BYTES) -> bool:
    try:
        return len(_canonical_bytes(value)) <= limit
    except (TypeError, ValueError, RecursionError):
        return False


def _closed(doc: Mapping[str, Any], fields: frozenset[str]) -> bool:
    return set(doc) == fields


@dataclass(frozen=True)
class ReleaseMember:
    """A member identity observed by a bounded streaming/archive adapter."""

    name: str
    role: str
    sha256: str
    size: int
    media_type: str

    def valid(self) -> bool:
        return (
            isinstance(self.name, str)
            and len(self.name) <= MAX_MEMBER_NAME
            and bool(_SAFE_NAME_RE.fullmatch(self.name))
            and self.role in REQUIRED_BUNDLE_ROLES
            and _is_hex64(self.sha256)
            and isinstance(self.size, int)
            and not isinstance(self.size, bool)
            and 0 <= self.size <= MAX_MEMBER_BYTES
            and isinstance(self.media_type, str)
            and 1 <= len(self.media_type) <= 128
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "sha256": self.sha256,
            "size": self.size,
            "media_type": self.media_type,
        }


@dataclass(frozen=True)
class ReleaseSetMaterial:
    """One completely observed candidate; contains no filesystem paths."""

    envelope: Mapping[str, Any]
    decision: Mapping[str, Any]
    manifest: Mapping[str, Any]
    inventory: Mapping[str, Any]
    checksums: str
    sbom: Mapping[str, Any]
    evidence: Mapping[str, Any]
    provenance: Mapping[str, Any]
    compatibility: Mapping[str, Any]
    release_notes: str
    signature: bytes
    members: tuple[ReleaseMember, ...]


class ReleaseTrustAdapter(Protocol):
    """Reviewed cryptographic/evidence boundary.

    Implementations must verify actual signatures and provenance against
    package-owned trust material.  Returning values from untrusted documents
    is not an implementation of this protocol.
    """

    @property
    def available(self) -> bool: ...

    @property
    def trust_root_id(self) -> str: ...

    def verify_signature(
        self, canonical_envelope: bytes, signature: bytes, mechanism: str
    ) -> bool: ...

    def verify_provenance(
        self,
        provenance: Mapping[str, Any],
        *,
        repository: str,
        source_ref: str,
        source_commit: str,
        subjects: tuple[ReleaseMember, ...],
    ) -> bool: ...

    def verify_evidence(
        self,
        evidence: Mapping[str, Any],
        *,
        version: str,
        source_commit: str,
        inventory_digest: str,
        platform_profile: str,
    ) -> bool: ...


class UnavailableReleaseTrustAdapter:
    """Production default until reviewed signing material is packaged."""

    available = False
    trust_root_id = "unavailable"

    def verify_signature(
        self, canonical_envelope: bytes, signature: bytes, mechanism: str
    ) -> bool:
        return False

    def verify_provenance(
        self, provenance: Mapping[str, Any], **_: Any
    ) -> bool:
        return False

    def verify_evidence(self, evidence: Mapping[str, Any], **_: Any) -> bool:
        return False


_VERIFIED_RELEASE_KEY = object()


@dataclass(frozen=True)
class VerifiedApplicationRelease:
    """Trust-bearing value constructible only by ApplicationReleaseVerifier."""

    version: str
    source_commit: str
    source_ref: str
    repository: str
    release_set_digest: str
    manifest_digest: str
    inventory_digest: str
    wheel_digest: str
    published_at: str
    notes: str
    source_schema: int
    minimum_readable_schema: int
    maximum_readable_schema: int
    target_schema: int
    platform_profile: str
    total_bytes: int
    member_count: int
    trust_root_id: str
    _verifier_key: InitVar[object] = None

    def __post_init__(self, _verifier_key: object) -> None:
        if _verifier_key is not _VERIFIED_RELEASE_KEY:
            raise ValueError(
                "VerifiedApplicationRelease can only be produced by "
                "ApplicationReleaseVerifier"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source_commit": self.source_commit,
            "source_ref": self.source_ref,
            "repository": self.repository,
            "release_set_digest": self.release_set_digest,
            "manifest_digest": self.manifest_digest,
            "inventory_digest": self.inventory_digest,
            "wheel_digest": self.wheel_digest,
            "published_at": self.published_at,
            "notes": self.notes,
            "source_schema": self.source_schema,
            "minimum_readable_schema": self.minimum_readable_schema,
            "maximum_readable_schema": self.maximum_readable_schema,
            "target_schema": self.target_schema,
            "platform_profile": self.platform_profile,
            "total_bytes": self.total_bytes,
            "member_count": self.member_count,
            "trust_root_id": self.trust_root_id,
        }


@dataclass(frozen=True)
class ReleaseVerification:
    outcome: UpdateOutcome
    reason_code: UpdateCode
    release: VerifiedApplicationRelease | None = None

    @property
    def verified(self) -> bool:
        return self.release is not None and self.outcome is UpdateOutcome.AVAILABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": APPLICATION_UPDATE_QUERY_SCHEMA_VERSION,
            "outcome": self.outcome.value,
            "reason_code": self.reason_code.value,
            "release": self.release.to_dict() if self.release else None,
        }


class ApplicationReleaseVerifier:
    """Verify all non-I/O trust bindings for one observed release set."""

    _ENVELOPE_FIELDS = frozenset({
        "format_version", "verifier_policy_version", "repository", "version",
        "source_commit", "source_ref", "published_at", "trust_root_id",
        "signature_mechanism", "members",
    })
    _COMPATIBILITY_FIELDS = frozenset({
        "schema_version", "package_name", "source_schema",
        "minimum_readable_schema", "maximum_readable_schema",
        "target_schema", "supported_platforms",
    })

    def __init__(
        self,
        *,
        expected_repository: str,
        trust: ReleaseTrustAdapter | None = None,
    ) -> None:
        if not isinstance(expected_repository, str) or not expected_repository.strip():
            raise ValueError("expected repository identity is required")
        self.expected_repository = expected_repository
        self.trust = trust or UnavailableReleaseTrustAdapter()

    @staticmethod
    def _refusal(code: UpdateCode) -> ReleaseVerification:
        return ReleaseVerification(UpdateOutcome.UNAVAILABLE, code)

    def verify(
        self,
        material: ReleaseSetMaterial,
        *,
        installed_schema: int,
        platform_profile: str,
    ) -> ReleaseVerification:
        if not self.trust.available:
            return self._refusal(UpdateCode.SIGNED_UPDATE_CHANNEL_UNAVAILABLE)
        if not isinstance(material, ReleaseSetMaterial):
            return self._refusal(UpdateCode.BUNDLE_MALFORMED)
        if not isinstance(installed_schema, int) or installed_schema < 1:
            return self._refusal(UpdateCode.DATABASE_INCOMPATIBLE)
        if not isinstance(platform_profile, str) or not platform_profile:
            return self._refusal(UpdateCode.PLATFORM_EVIDENCE_MISSING)

        envelope = material.envelope
        docs: tuple[Any, ...] = (
            envelope, material.decision, material.manifest, material.inventory,
            material.sbom, material.evidence, material.provenance,
            material.compatibility,
        )
        try:
            checksums_bytes = material.checksums.encode("utf-8", "strict")
            notes_bytes = material.release_notes.encode("utf-8", "strict")
            combined_size = (
                sum(len(_canonical_bytes(item)) for item in docs)
                + len(checksums_bytes) + len(notes_bytes)
            )
        except (AttributeError, TypeError, UnicodeError, ValueError, RecursionError):
            return self._refusal(UpdateCode.BUNDLE_MALFORMED)
        if (
            not all(isinstance(item, Mapping) and _bounded_json(item) for item in docs)
            or combined_size > MAX_COMBINED_METADATA_BYTES
            or not isinstance(material.signature, bytes)
            or len(material.signature) > MAX_METADATA_BYTES
        ):
            return self._refusal(UpdateCode.BUNDLE_MALFORMED)
        if not _closed(envelope, self._ENVELOPE_FIELDS):
            return self._refusal(UpdateCode.BUNDLE_MALFORMED)
        if envelope.get("format_version") != APPLICATION_RELEASE_SET_VERSION:
            return self._refusal(UpdateCode.BUNDLE_MALFORMED)
        if (
            envelope.get("verifier_policy_version")
            != APPLICATION_UPDATE_VERIFIER_POLICY_VERSION
        ):
            return self._refusal(UpdateCode.CHANNEL_POLICY_INVALID)

        repository = envelope.get("repository")
        version = envelope.get("version")
        source_commit = envelope.get("source_commit")
        source_ref = envelope.get("source_ref")
        published_at = envelope.get("published_at")
        if repository != self.expected_repository:
            return self._refusal(UpdateCode.UNTRUSTED_REPOSITORY)
        if (
            not isinstance(version, str)
            or len(version) > 64
            or not _VERSION_RE.fullmatch(version)
            or not isinstance(source_commit, str)
            or not _FULL_SHA_RE.fullmatch(source_commit)
            or set(source_commit) == {"0"}
            or source_ref != f"refs/tags/v{version}"
        ):
            return self._refusal(UpdateCode.IMMUTABLE_REF_REQUIRED)
        if _parse_timestamp(published_at) is None:
            return self._refusal(UpdateCode.BUNDLE_MALFORMED)
        if (
            envelope.get("trust_root_id") != self.trust.trust_root_id
            or not isinstance(envelope.get("signature_mechanism"), str)
            or not envelope.get("signature_mechanism")
            or len(envelope.get("signature_mechanism")) > 64
        ):
            return self._refusal(UpdateCode.SIGNATURE_INVALID)

        members = material.members
        if not (1 <= len(members) <= MAX_MEMBERS) or any(
            not member.valid() for member in members
        ):
            return self._refusal(UpdateCode.BUNDLE_MEMBER_REFUSED)
        names = [member.name for member in members]
        roles = [member.role for member in members]
        if (
            len(names) != len(set(names))
            or len(roles) != len(set(roles))
            or set(roles) != REQUIRED_BUNDLE_ROLES
        ):
            return self._refusal(UpdateCode.UNKNOWN_BUNDLE_MEMBER)
        by_role = {member.role: member for member in members}

        envelope_members = envelope.get("members")
        signed_members = tuple(
            sorted(
                (member for member in members if member.role != SIGNATURE_ROLE),
                key=lambda item: (item.role, item.name),
            )
        )
        if envelope_members != [member.to_dict() for member in signed_members]:
            return self._refusal(UpdateCode.BUNDLE_MEMBER_REFUSED)

        document_bytes: dict[str, bytes] = {
            "release-decision": _canonical_bytes(material.decision),
            "release-manifest": _canonical_bytes(material.manifest),
            "artifact-inventory": _canonical_bytes(material.inventory),
            "checksums": checksums_bytes,
            "cyclonedx-sbom": _canonical_bytes(material.sbom),
            "verified-evidence": _canonical_bytes(material.evidence),
            "build-provenance": _canonical_bytes(material.provenance),
            "database-compatibility": _canonical_bytes(material.compatibility),
            "release-notes": notes_bytes,
            "release-signature": material.signature,
        }
        for role, payload in document_bytes.items():
            observed = by_role[role]
            if observed.size != len(payload) or observed.sha256 != _sha256(payload):
                return self._refusal(UpdateCode.BUNDLE_MEMBER_REFUSED)

        if (
            len(notes_bytes) > MAX_RELEASE_NOTES_BYTES
            or _CONTROL_RE.search(material.release_notes)
            or "\x1b" in material.release_notes
        ):
            return self._refusal(UpdateCode.NOTES_INVALID)

        canonical_envelope = _canonical_bytes(envelope)
        try:
            signature_valid = self.trust.verify_signature(
                canonical_envelope,
                material.signature,
                str(envelope["signature_mechanism"]),
            )
        except Exception:  # trust adapters never leak implementation details
            signature_valid = False
        if not signature_valid:
            return self._refusal(UpdateCode.SIGNATURE_INVALID)

        inventory_result = self._verify_inventory(
            material.inventory, by_role=by_role
        )
        if isinstance(inventory_result, UpdateCode):
            return self._refusal(inventory_result)
        inventory, inventory_digest, wheel_digest = inventory_result

        if not self._verify_decision(
            material.decision,
            repository=repository,
            version=version,
            source_commit=source_commit,
            source_ref=source_ref,
            inventory_digest=inventory_digest,
        ):
            return self._refusal(UpdateCode.RELEASE_DECISION_INELIGIBLE)
        manifest_digest = self._verify_manifest(
            material.manifest,
            decision=material.decision,
            inventory=material.inventory,
            inventory_digest=inventory_digest,
            sbom_digest="sha256:" + by_role["cyclonedx-sbom"].sha256,
        )
        if manifest_digest is None:
            return self._refusal(UpdateCode.MANIFEST_MISMATCH)
        if not self._verify_checksums(
            material.checksums, inventory=inventory, by_role=by_role
        ):
            return self._refusal(UpdateCode.CHECKSUM_MISMATCH)
        if not self._verify_sbom(material.sbom, version, wheel_digest):
            return self._refusal(UpdateCode.SBOM_MISMATCH)
        try:
            provenance_valid = self.trust.verify_provenance(
                material.provenance,
                repository=repository,
                source_ref=source_ref,
                source_commit=source_commit,
                subjects=signed_members,
            )
        except Exception:
            provenance_valid = False
        if not provenance_valid:
            return self._refusal(UpdateCode.PROVENANCE_INVALID)

        compatibility = material.compatibility
        if not _closed(compatibility, self._COMPATIBILITY_FIELDS):
            return self._refusal(UpdateCode.DATABASE_INCOMPATIBLE)
        values = tuple(
            compatibility.get(key)
            for key in (
                "source_schema", "minimum_readable_schema",
                "maximum_readable_schema", "target_schema",
            )
        )
        if (
            compatibility.get("schema_version")
            != APPLICATION_COMPATIBILITY_SCHEMA_VERSION
            or compatibility.get("package_name") != EXPECTED_PACKAGE_NAME
            or any(not isinstance(value, int) or isinstance(value, bool) or value < 1
                   for value in values)
        ):
            return self._refusal(UpdateCode.DATABASE_INCOMPATIBLE)
        source_schema, min_schema, max_schema, target_schema = values
        platforms = compatibility.get("supported_platforms")
        if (
            not isinstance(platforms, list)
            or not (1 <= len(platforms) <= 8)
            or any(not isinstance(item, str) or not item or len(item) > 64
                   for item in platforms)
            or platform_profile not in platforms
        ):
            return self._refusal(UpdateCode.PLATFORM_EVIDENCE_MISSING)
        if (
            min_schema > max_schema
            or not min_schema <= installed_schema <= max_schema
            or source_schema != installed_schema
            or target_schema < source_schema
        ):
            return self._refusal(UpdateCode.DATABASE_INCOMPATIBLE)
        try:
            evidence_valid = self.trust.verify_evidence(
                material.evidence,
                version=version,
                source_commit=source_commit,
                inventory_digest=inventory_digest,
                platform_profile=platform_profile,
            )
        except Exception:
            evidence_valid = False
        if not evidence_valid:
            return self._refusal(UpdateCode.PLATFORM_EVIDENCE_MISSING)

        release_digest = _sha256(canonical_envelope)
        total_bytes = sum(member.size for member in members)
        release = VerifiedApplicationRelease(
            version=version,
            source_commit=source_commit,
            source_ref=source_ref,
            repository=repository,
            release_set_digest=release_digest,
            manifest_digest=manifest_digest,
            inventory_digest=inventory_digest,
            wheel_digest=wheel_digest,
            published_at=str(published_at),
            notes=material.release_notes,
            source_schema=source_schema,
            minimum_readable_schema=min_schema,
            maximum_readable_schema=max_schema,
            target_schema=target_schema,
            platform_profile=platform_profile,
            total_bytes=total_bytes,
            member_count=len(members),
            trust_root_id=self.trust.trust_root_id,
            _verifier_key=_VERIFIED_RELEASE_KEY,
        )
        return ReleaseVerification(UpdateOutcome.AVAILABLE, UpdateCode.OK, release)

    @staticmethod
    def _verify_inventory(
        doc: Mapping[str, Any], *, by_role: Mapping[str, ReleaseMember]
    ) -> tuple[ArtifactInventory, str, str] | UpdateCode:
        if set(doc) != {"inventory_schema_version", "artifacts", "inventory_digest"}:
            return UpdateCode.INVENTORY_MISMATCH
        if doc.get("inventory_schema_version") != INVENTORY_SCHEMA_VERSION:
            return UpdateCode.INVENTORY_MISMATCH
        raw = doc.get("artifacts")
        if not isinstance(raw, list) or not (1 <= len(raw) <= MAX_MEMBERS):
            return UpdateCode.INVENTORY_MISMATCH
        artifacts: list[Artifact] = []
        names: set[str] = set()
        roles: set[str] = set()
        for item in raw:
            if not isinstance(item, Mapping) or set(item) != {
                "name", "sha256", "size", "media_type", "role"
            }:
                return UpdateCode.INVENTORY_MISMATCH
            member = ReleaseMember(
                name=item.get("name"), role=item.get("role"),
                sha256=item.get("sha256"), size=item.get("size"),
                media_type=item.get("media_type"),
            )
            if (
                not member.valid()
                or member.role not in REQUIRED_INVENTORY_ROLES
                or member.name in names
                or member.role in roles
                or by_role.get(member.role) != member
            ):
                return UpdateCode.INVENTORY_MISMATCH
            names.add(member.name)
            roles.add(member.role)
            artifacts.append(Artifact(**member.to_dict()))
        if roles != REQUIRED_INVENTORY_ROLES:
            return UpdateCode.INVENTORY_MISMATCH
        inventory = ArtifactInventory(tuple(artifacts))
        digest = inventory.inventory_digest()
        if doc.get("inventory_digest") != digest:
            return UpdateCode.INVENTORY_MISMATCH
        wheel = next(item for item in artifacts if item.role == "python-wheel")
        return inventory, digest, wheel.sha256

    @staticmethod
    def _verify_decision(
        doc: Mapping[str, Any], *, repository: str, version: str,
        source_commit: str, source_ref: str, inventory_digest: str,
    ) -> bool:
        # The canonical signed decision must be the final output of the sole
        # evaluator. Unknown fields are refused so no shadow approval surface
        # can be smuggled into the updater.
        required = frozenset({
            "decision_schema_version", "eligible_for_rc",
            "eligible_for_1_0_0", "blocking_codes", "blocking_explanations",
            "accepted_limitations", "candidate_version", "source_commit",
            "source_ref", "repository", "inventory_digest", "evidence_used",
            "evidence_rejected", "policy_digest",
        })
        return (
            set(doc) == required
            and doc.get("decision_schema_version") == RELEASE_DECISION_SCHEMA_VERSION
            and doc.get("eligible_for_1_0_0") is True
            and doc.get("eligible_for_rc") is True
            and doc.get("blocking_codes") == []
            and doc.get("blocking_explanations") == []
            and doc.get("candidate_version") == version
            and doc.get("source_commit") == source_commit
            and doc.get("source_ref") == source_ref
            and doc.get("repository") == repository
            and doc.get("inventory_digest") == inventory_digest
            and _is_sha256(doc.get("policy_digest"))
            and isinstance(doc.get("evidence_used"), list)
            and len(doc.get("evidence_used")) >= 1
            and doc.get("evidence_rejected") == []
        )

    @staticmethod
    def _verify_manifest(
        doc: Mapping[str, Any], *, decision: Mapping[str, Any],
        inventory: Mapping[str, Any], inventory_digest: str, sbom_digest: str,
    ) -> str | None:
        required = frozenset({
            "manifest_schema_version", "release_status", "qualification_level",
            "version", "source_commit", "source_ref", "repository",
            "policy_digest", "inventory", "inventory_digest", "sbom_digest",
            "eligible_for_rc", "eligible_for_1_0_0", "blocking_codes",
            "evidence_used", "manifest_digest",
        })
        if set(doc) != required:
            return None
        recorded_digest = doc.get("manifest_digest")
        body = dict(doc)
        body.pop("manifest_digest", None)
        expected_digest = "sha256:" + _sha256(_canonical_bytes(body))
        bindings = (
            doc.get("manifest_schema_version") == RELEASE_MANIFEST_SCHEMA_VERSION,
            doc.get("release_status") == "QUALIFIED",
            doc.get("qualification_level") == "final",
            doc.get("eligible_for_rc") is True,
            doc.get("eligible_for_1_0_0") is True,
            doc.get("blocking_codes") == [],
            doc.get("version") == decision.get("candidate_version"),
            doc.get("source_commit") == decision.get("source_commit"),
            doc.get("source_ref") == decision.get("source_ref"),
            doc.get("repository") == decision.get("repository"),
            doc.get("policy_digest") == decision.get("policy_digest"),
            doc.get("inventory_digest") == inventory_digest,
            doc.get("inventory") == inventory,
            doc.get("sbom_digest") == sbom_digest,
            doc.get("evidence_used") == decision.get("evidence_used"),
            recorded_digest == expected_digest,
        )
        return expected_digest if all(bindings) else None

    @staticmethod
    def _verify_checksums(
        text: str, *, inventory: ArtifactInventory,
        by_role: Mapping[str, ReleaseMember],
    ) -> bool:
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_METADATA_BYTES:
            return False
        expected = {artifact.name: artifact.sha256 for artifact in inventory.artifacts
                    if artifact.role != "checksums"}
        parsed: dict[str, str] = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 2 or not _is_hex64(parts[0]) or parts[1] in parsed:
                return False
            if not _SAFE_NAME_RE.fullmatch(parts[1]):
                return False
            parsed[parts[1]] = parts[0]
        # The checksum file cannot list its own digest; all other inventory
        # subjects must be named exactly once.
        return parsed == expected and by_role["checksums"].name not in parsed

    @staticmethod
    def _verify_sbom(doc: Mapping[str, Any], version: str, wheel_digest: str) -> bool:
        if doc.get("bomFormat") != "CycloneDX" or doc.get("specVersion") != "1.5":
            return False
        metadata = doc.get("metadata")
        component = metadata.get("component") if isinstance(metadata, Mapping) else None
        if not isinstance(component, Mapping):
            return False
        hashes = component.get("hashes")
        if not isinstance(hashes, list):
            return False
        subjects = [
            item.get("content") for item in hashes
            if isinstance(item, Mapping) and item.get("alg") == "SHA-256"
        ]
        return (
            component.get("name") == EXPECTED_PACKAGE_NAME
            and component.get("version") == version
            and subjects == [wheel_digest]
        )


@dataclass(frozen=True)
class InstalledApplication:
    version: str
    source_commit: str | None
    release_set_digest: str | None
    slot_id: str | None
    database_schema: int
    channel_id: str = DEFAULT_CHANNEL_ID
    current_installation_id: str | None = None
    previous_installation_id: str | None = None
    pointer_generation: int = 0
    pending_operation_id: str | None = None
    recovery_barrier: str | None = None
    revision: int = 0


class ApplicationReleaseChannel(Protocol):
    @property
    def available(self) -> bool: ...

    @property
    def channel_id(self) -> str: ...

    def candidates(self) -> Sequence[ReleaseSetMaterial]: ...


class UnavailableApplicationReleaseChannel:
    available = False
    channel_id = DEFAULT_CHANNEL_ID

    def candidates(self) -> tuple[ReleaseSetMaterial, ...]:
        return ()


@dataclass(frozen=True)
class ApplicationUpdateStatus:
    installed: InstalledApplication
    outcome: UpdateOutcome
    reason_code: UpdateCode
    channel_available: bool

    def to_dict(self) -> dict[str, Any]:
        installed = {
            "version": self.installed.version,
            "source_commit": self.installed.source_commit,
            "release_set_digest": self.installed.release_set_digest,
            "provenance": (
                "VERIFIED" if self.installed.release_set_digest
                else "UNVERIFIED_LEGACY_INSTALL"
            ),
            "slot_id": self.installed.slot_id,
            "database_schema": self.installed.database_schema,
            "channel_id": self.installed.channel_id,
            "current_installation_id": self.installed.current_installation_id,
            "previous_installation_id": self.installed.previous_installation_id,
            "pointer_generation": self.installed.pointer_generation,
            "pending_operation_id": self.installed.pending_operation_id,
            "recovery_barrier": self.installed.recovery_barrier,
            "revision": self.installed.revision,
        }
        return {
            "schema_version": APPLICATION_UPDATE_QUERY_SCHEMA_VERSION,
            "outcome": self.outcome.value,
            "reason_code": self.reason_code.value,
            "channel_available": self.channel_available,
            "installed": installed,
        }


@dataclass(frozen=True)
class ApplicationUpdateCheck:
    outcome: UpdateOutcome
    reason_code: UpdateCode
    release: VerifiedApplicationRelease | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": APPLICATION_UPDATE_QUERY_SCHEMA_VERSION,
            "outcome": self.outcome.value,
            "reason_code": self.reason_code.value,
            "release": self.release.to_dict() if self.release else None,
        }


@dataclass(frozen=True)
class ApplicationUpdatePreview:
    outcome: UpdateOutcome
    reason_code: UpdateCode
    version: str | None = None
    release_set_digest: str | None = None
    source_commit: str | None = None
    source_ref: str | None = None
    published_at: str | None = None
    platform_profile: str | None = None
    source_schema: int | None = None
    target_schema: int | None = None
    download_bytes: int = 0
    staging_bytes: int = 0
    retained_bytes: int = 0
    required_free_bytes: int = 0
    available_free_bytes: int = 0
    backup_required: bool = True
    restart_required: bool = True
    rollback_installation_id: str | None = None
    profile_restore_on_rollback: bool = False
    release_notes_plain_text: str = ""
    expected_installation_revision: int = 0
    preview_digest: str | None = None
    confirmation_token: str | None = None
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": APPLICATION_UPDATE_QUERY_SCHEMA_VERSION,
            "outcome": self.outcome.value,
            "reason_code": self.reason_code.value,
            "version": self.version,
            "release_set_digest": self.release_set_digest,
            "source_commit": self.source_commit,
            "source_ref": self.source_ref,
            "published_at": self.published_at,
            "platform_profile": self.platform_profile,
            "source_schema": self.source_schema,
            "target_schema": self.target_schema,
            "download_bytes": self.download_bytes,
            "staging_bytes": self.staging_bytes,
            "retained_bytes": self.retained_bytes,
            "required_free_bytes": self.required_free_bytes,
            "available_free_bytes": self.available_free_bytes,
            "backup_required": self.backup_required,
            "restart_required": self.restart_required,
            "rollback_installation_id": self.rollback_installation_id,
            "profile_restore_on_rollback": self.profile_restore_on_rollback,
            "release_notes_plain_text": self.release_notes_plain_text,
            "expected_installation_revision": self.expected_installation_revision,
            "preview_digest": self.preview_digest,
            "confirmation_token": self.confirmation_token,
            "expires_at": self.expires_at,
        }


class ApplicationUpdateQueryService:
    """Read-only status/check/preview over one injected release channel."""

    def __init__(
        self,
        *,
        installed: InstalledApplication,
        verifier: ApplicationReleaseVerifier,
        platform_profile: str,
        channel: ApplicationReleaseChannel | None = None,
        free_bytes: int = 0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._installed = installed
        self._verifier = verifier
        self._platform_profile = platform_profile
        self._channel = channel or UnavailableApplicationReleaseChannel()
        self._free_bytes = max(0, int(free_bytes))
        self._now = now or (lambda: datetime.now(timezone.utc))

    def status(self) -> ApplicationUpdateStatus:
        available = bool(self._channel.available and self._verifier.trust.available)
        code = UpdateCode.OK if available else UpdateCode.SIGNED_UPDATE_CHANNEL_UNAVAILABLE
        return ApplicationUpdateStatus(
            installed=self._installed,
            outcome=UpdateOutcome.AVAILABLE if available else UpdateOutcome.UNAVAILABLE,
            reason_code=code,
            channel_available=available,
        )

    def _verified_candidates(self) -> tuple[VerifiedApplicationRelease, ...]:
        if not self._channel.available or not self._verifier.trust.available:
            return ()
        try:
            materials = tuple(self._channel.candidates())
        except Exception:  # adapters map network details; never retain exception text
            return ()
        if len(materials) > 16:
            return ()
        verified: list[VerifiedApplicationRelease] = []
        for material in materials:
            result = self._verifier.verify(
                material,
                installed_schema=self._installed.database_schema,
                platform_profile=self._platform_profile,
            )
            if result.release is not None:
                verified.append(result.release)
        return tuple(verified)

    def check(self) -> ApplicationUpdateCheck:
        if not self._channel.available or not self._verifier.trust.available:
            return ApplicationUpdateCheck(
                UpdateOutcome.UNAVAILABLE,
                UpdateCode.SIGNED_UPDATE_CHANNEL_UNAVAILABLE,
            )
        releases = self._verified_candidates()
        if not releases:
            return ApplicationUpdateCheck(
                UpdateOutcome.UNAVAILABLE, UpdateCode.RELEASE_DECISION_INELIGIBLE
            )
        # Channel ordering is signed policy ordering. This avoids adding a
        # second, subtly different version comparator inside the appliance.
        return ApplicationUpdateCheck(UpdateOutcome.AVAILABLE, UpdateCode.OK, releases[0])

    def preview(self, version: str) -> ApplicationUpdatePreview:
        if not self._channel.available or not self._verifier.trust.available:
            return ApplicationUpdatePreview(
                UpdateOutcome.UNAVAILABLE,
                UpdateCode.SIGNED_UPDATE_CHANNEL_UNAVAILABLE,
            )
        release = next(
            (item for item in self._verified_candidates() if item.version == version),
            None,
        )
        if release is None:
            return ApplicationUpdatePreview(
                UpdateOutcome.UNAVAILABLE, UpdateCode.VERSION_NOT_FOUND
            )
        # Keep candidate + prior release + fresh venv overhead. The estimate is
        # deliberately conservative; the durable apply re-observes disk space.
        staging = release.total_bytes * 2
        retained = release.total_bytes
        required = release.total_bytes + staging + retained + (512 * 1024 * 1024)
        if self._free_bytes < required:
            return ApplicationUpdatePreview(
                UpdateOutcome.UNAVAILABLE,
                UpdateCode.INSUFFICIENT_SPACE,
                version=release.version,
                release_set_digest=release.release_set_digest,
                required_free_bytes=required,
                available_free_bytes=self._free_bytes,
            )

        now = self._now()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("update preview clock must return an aware datetime")
        expires = datetime.fromtimestamp(
            now.timestamp() + MAX_PREVIEW_AGE_SECONDS, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = {
            "contract_version": APPLICATION_UPDATE_QUERY_SCHEMA_VERSION,
            "mode": "APPLY",
            "release_set_digest": release.release_set_digest,
            "version": release.version,
            "expected_current_installation": self._installed.current_installation_id,
            "expected_previous_installation": self._installed.previous_installation_id,
            "expected_pointer_generation": self._installed.pointer_generation,
            "expected_installation_revision": self._installed.revision,
            "source_schema": release.source_schema,
            "target_schema": release.target_schema,
            "required_free_bytes": required,
            "expires_at": expires,
        }
        digest = _sha256(_canonical_bytes(body))
        confirmation = _sha256(
            _canonical_bytes({"preview_digest": digest, "confirm": "APPLICATION_UPDATE"})
        )
        return ApplicationUpdatePreview(
            outcome=UpdateOutcome.READY,
            reason_code=UpdateCode.OK,
            version=release.version,
            release_set_digest=release.release_set_digest,
            source_commit=release.source_commit,
            source_ref=release.source_ref,
            published_at=release.published_at,
            platform_profile=release.platform_profile,
            source_schema=release.source_schema,
            target_schema=release.target_schema,
            download_bytes=release.total_bytes,
            staging_bytes=staging,
            retained_bytes=retained,
            required_free_bytes=required,
            available_free_bytes=self._free_bytes,
            backup_required=True,
            restart_required=True,
            rollback_installation_id=self._installed.current_installation_id,
            profile_restore_on_rollback=(release.target_schema != release.source_schema),
            release_notes_plain_text=release.notes,
            expected_installation_revision=self._installed.revision,
            preview_digest=digest,
            confirmation_token=confirmation,
            expires_at=expires,
        )


__all__ = [
    "APPLICATION_COMPATIBILITY_SCHEMA_VERSION",
    "APPLICATION_RELEASE_SET_VERSION",
    "APPLICATION_UPDATE_QUERY_SCHEMA_VERSION",
    "APPLICATION_UPDATE_VERIFIER_POLICY_VERSION",
    "EXPECTED_RELEASE_REPOSITORY",
    "ApplicationReleaseChannel",
    "ApplicationReleaseVerifier",
    "ApplicationUpdateCheck",
    "ApplicationUpdatePreview",
    "ApplicationUpdateQueryService",
    "ApplicationUpdateStatus",
    "InstalledApplication",
    "ReleaseMember",
    "ReleaseSetMaterial",
    "ReleaseTrustAdapter",
    "ReleaseVerification",
    "UnavailableApplicationReleaseChannel",
    "UnavailableReleaseTrustAdapter",
    "UpdateCode",
    "UpdateOutcome",
    "VerifiedApplicationRelease",
]
