"""Hostile-archive-safe offline application release import (ADR 013 D8).

An archive is only a transport.  Every import is streamed into a private,
operation-owned staging directory, reconstituted as the same ``ReleaseSetMaterial``
used by the online channel, and passed through the one release verifier.  Only a
verified content identity is published; paths and caller assertions never become
trust inputs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .application_installation import (
    ApplicationInstallationError,
    ApplicationUpdateImportRepository,
    ImportSource,
    ImportState,
)
from .application_update import (
    MAX_MEMBER_BYTES,
    MAX_MEMBERS,
    ApplicationReleaseVerifier,
    ReleaseMember,
    ReleaseSetMaterial,
    UpdateCode,
    UpdateOutcome,
    VerifiedApplicationRelease,
)
from .fsops import atomic_write_text, ensure_private_dir, fsync_directory
from .paths import AppPaths

BUNDLE_CONTROL_NAME = "release-set.json"
BUNDLE_RECEIPT_NAME = ".bc250-import.json"
BUNDLE_RECEIPT_SCHEMA_VERSION = 1
MAX_CONTROL_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 6 * 1024 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024
_HEX64 = re.compile(r"[0-9a-f]{64}")

_ROLE_FIELDS = {
    "release-decision": ("decision", "json"),
    "release-manifest": ("manifest", "json"),
    "artifact-inventory": ("inventory", "json"),
    "checksums": ("checksums", "text"),
    "cyclonedx-sbom": ("sbom", "json"),
    "verified-evidence": ("evidence", "json"),
    "build-provenance": ("provenance", "json"),
    "database-compatibility": ("compatibility", "json"),
    "release-notes": ("release_notes", "text"),
    "release-signature": ("signature", "bytes"),
}


class ApplicationBundleError(RuntimeError):
    """Stable import refusal; never includes an attacker-controlled path."""

    def __init__(self, code: str | UpdateCode) -> None:
        self.code = code.value if isinstance(code, UpdateCode) else str(code)
        super().__init__(self.code)


@dataclass(frozen=True)
class ImportedApplicationBundle:
    outcome: UpdateOutcome
    reason_code: UpdateCode
    release: VerifiedApplicationRelease | None = None
    already_present: bool = False

    @property
    def ok(self) -> bool:
        return self.release is not None and self.outcome is UpdateOutcome.AVAILABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "outcome": self.outcome.value,
            "reason_code": self.reason_code.value,
            "release": self.release.to_dict() if self.release else None,
            "already_present": self.already_present,
        }


def _duplicate_rejecting_json(raw: bytes) -> Mapping[str, Any]:
    if len(raw) > MAX_CONTROL_BYTES:
        raise ApplicationBundleError(UpdateCode.BUNDLE_MEMBER_REFUSED)

    def pairs(items):
        result: dict[str, Any] = {}
        for key, value in items:
            if not isinstance(key, str) or key in result:
                raise ApplicationBundleError(UpdateCode.BUNDLE_MALFORMED)
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs)
    except ApplicationBundleError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ApplicationBundleError(UpdateCode.BUNDLE_MALFORMED) from exc
    if not isinstance(value, Mapping):
        raise ApplicationBundleError(UpdateCode.BUNDLE_MALFORMED)
    return value


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ApplicationBundleError(UpdateCode.BUNDLE_MALFORMED) from exc


def _members_from_envelope(envelope: Mapping[str, Any]) -> tuple[ReleaseMember, ...]:
    raw_members = envelope.get("members")
    if not isinstance(raw_members, list) or not 1 <= len(raw_members) < MAX_MEMBERS:
        raise ApplicationBundleError(UpdateCode.BUNDLE_MALFORMED)
    result: list[ReleaseMember] = []
    for raw in raw_members:
        if not isinstance(raw, Mapping) or set(raw) != {
            "name", "role", "sha256", "size", "media_type"
        }:
            raise ApplicationBundleError(UpdateCode.BUNDLE_MALFORMED)
        member = ReleaseMember(
            raw.get("name"), raw.get("role"), raw.get("sha256"),
            raw.get("size"), raw.get("media_type"),
        )
        if not member.valid() or member.role == "release-signature":
            raise ApplicationBundleError(UpdateCode.BUNDLE_MEMBER_REFUSED)
        result.append(member)
    return tuple(result)


def _material_from_tree(root: Path) -> tuple[ReleaseSetMaterial, dict[str, ReleaseMember]]:
    control_path = root / BUNDLE_CONTROL_NAME
    if control_path.is_symlink() or not control_path.is_file():
        raise ApplicationBundleError(UpdateCode.BUNDLE_MALFORMED)
    if not 1 <= control_path.stat().st_size <= MAX_CONTROL_BYTES:
        raise ApplicationBundleError(UpdateCode.BUNDLE_MEMBER_REFUSED)
    control_raw = control_path.read_bytes()
    envelope = _duplicate_rejecting_json(control_raw)
    if control_raw != _canonical_json_bytes(envelope):
        raise ApplicationBundleError(UpdateCode.BUNDLE_MALFORMED)
    signed = _members_from_envelope(envelope)

    # The signature is deliberately absent from the signed member list (a
    # signature cannot include its own digest).  Its canonical archive name
    # and media type are fixed by the transport contract.
    signature_path = root / "release-signature.sig"
    if signature_path.is_symlink() or not signature_path.is_file():
        raise ApplicationBundleError(UpdateCode.UNKNOWN_BUNDLE_MEMBER)
    signature_stat = signature_path.stat()
    if not 1 <= signature_stat.st_size <= MAX_CONTROL_BYTES:
        raise ApplicationBundleError(UpdateCode.BUNDLE_MEMBER_REFUSED)
    signature = signature_path.read_bytes()
    signature_member = ReleaseMember(
        "release-signature.sig", "release-signature",
        hashlib.sha256(signature).hexdigest(), len(signature),
        "application/octet-stream",
    )
    members = (*signed, signature_member)
    by_role = {item.role: item for item in members}
    if len(by_role) != len(members):
        raise ApplicationBundleError(UpdateCode.UNKNOWN_BUNDLE_MEMBER)

    values: dict[str, Any] = {}
    for role, (field, kind) in _ROLE_FIELDS.items():
        member = by_role.get(role)
        if member is None:
            raise ApplicationBundleError(UpdateCode.UNKNOWN_BUNDLE_MEMBER)
        path = root / member.name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != member.size:
            raise ApplicationBundleError(UpdateCode.BUNDLE_MEMBER_REFUSED)
        if member.size > MAX_CONTROL_BYTES and role not in {"python-wheel", "python-sdist"}:
            raise ApplicationBundleError(UpdateCode.BUNDLE_MEMBER_REFUSED)
        if kind == "bytes":
            value: Any = path.read_bytes()
        elif kind == "text":
            try:
                value = path.read_text(encoding="utf-8")
            except UnicodeError as exc:
                raise ApplicationBundleError(UpdateCode.BUNDLE_MALFORMED) from exc
        else:
            raw = path.read_bytes()
            value = _duplicate_rejecting_json(raw)
            if raw != _canonical_json_bytes(value):
                raise ApplicationBundleError(UpdateCode.BUNDLE_MALFORMED)
        values[field] = value
    return ReleaseSetMaterial(
        envelope=envelope,
        members=tuple(members),
        **values,
    ), by_role


def _safe_archive_name(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 240:
        raise ApplicationBundleError(UpdateCode.BUNDLE_MEMBER_REFUSED)
    if name != unicodedata.normalize("NFC", name):
        raise ApplicationBundleError(UpdateCode.BUNDLE_MEMBER_REFUSED)
    path = Path(name)
    if path.is_absolute() or len(path.parts) != 1 or name in {".", ".."}:
        raise ApplicationBundleError(UpdateCode.BUNDLE_MEMBER_REFUSED)
    return name


class OfflineApplicationBundleStore:
    """Verified offline bundle importer, channel, and durable resolver."""

    channel_id = "verified-offline"

    def __init__(
        self,
        paths: AppPaths,
        units: Any,
        verifier: ApplicationReleaseVerifier,
        *,
        platform_profile: str,
        installed_schema: int | Callable[[], int],
    ) -> None:
        self.paths = paths
        self.units = units
        self.verifier = verifier
        self.platform_profile = platform_profile
        self.installed_schema = installed_schema

    @property
    def available(self) -> bool:
        return bool(self.verifier.trust.available)

    def _schema(self) -> int:
        value = self.installed_schema() if callable(self.installed_schema) else self.installed_schema
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ApplicationBundleError(UpdateCode.DATABASE_INCOMPATIBLE)
        return value

    @staticmethod
    def _digest_path(path: Path, expected: ReleaseMember) -> None:
        before = path.stat()
        if (
            path.is_symlink() or not path.is_file() or before.st_size != expected.size
            or expected.size > MAX_MEMBER_BYTES
        ):
            raise ApplicationBundleError(UpdateCode.BUNDLE_MEMBER_REFUSED)
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(COPY_CHUNK_BYTES), b""):
                digest.update(chunk)
        after = path.stat()
        if (
            digest.hexdigest() != expected.sha256
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise ApplicationBundleError(UpdateCode.BUNDLE_MEMBER_REFUSED)

    def _verify_tree(self, root: Path):
        material, by_role = _material_from_tree(root)
        expected_names = {BUNDLE_CONTROL_NAME, *(item.name for item in material.members)}
        observed_names = {
            item.name for item in root.iterdir() if item.name != BUNDLE_RECEIPT_NAME
        }
        if observed_names != expected_names:
            raise ApplicationBundleError(UpdateCode.UNKNOWN_BUNDLE_MEMBER)
        for member in material.members:
            self._digest_path(root / member.name, member)
        verification = self.verifier.verify(
            material,
            installed_schema=self._schema(),
            platform_profile=self.platform_profile,
        )
        if not verification.verified or verification.release is None:
            raise ApplicationBundleError(verification.reason_code)
        return material, by_role, verification.release

    def import_bundle(self, archive: str | Path) -> ImportedApplicationBundle:
        if not self.available:
            return ImportedApplicationBundle(
                UpdateOutcome.UNAVAILABLE,
                UpdateCode.SIGNED_UPDATE_CHANNEL_UNAVAILABLE,
            )
        source = Path(archive).expanduser()
        try:
            source_stat = source.stat()
        except OSError as exc:
            raise ApplicationBundleError(UpdateCode.BUNDLE_MALFORMED) from exc
        if (
            source.is_symlink() or not source.is_file()
            or not 1 <= source_stat.st_size <= MAX_ARCHIVE_BYTES
        ):
            raise ApplicationBundleError(UpdateCode.BUNDLE_MEMBER_REFUSED)
        ensure_private_dir(self.paths.application_update_import_staging_dir)
        stage = Path(tempfile.mkdtemp(
            prefix="offline-", dir=self.paths.application_update_import_staging_dir
        ))
        os.chmod(stage, 0o700)
        try:
            seen: set[str] = set()
            seen_folded: set[str] = set()
            total = 0
            with tarfile.open(source, mode="r:*") as archive_file:
                count = 0
                for info in archive_file:
                    count += 1
                    if count > MAX_MEMBERS + 1:
                        raise ApplicationBundleError(UpdateCode.BUNDLE_MALFORMED)
                    name = _safe_archive_name(info.name)
                    folded = name.casefold()
                    if name in seen or folded in seen_folded:
                        raise ApplicationBundleError(UpdateCode.BUNDLE_MEMBER_REFUSED)
                    seen.add(name)
                    seen_folded.add(folded)
                    if not info.isreg() or info.issparse() or info.size < 0:
                        raise ApplicationBundleError(UpdateCode.BUNDLE_MEMBER_REFUSED)
                    total += info.size
                    if info.size > MAX_MEMBER_BYTES or total > MAX_ARCHIVE_BYTES:
                        raise ApplicationBundleError(UpdateCode.BUNDLE_MEMBER_REFUSED)
                    handle = archive_file.extractfile(info)
                    if handle is None:
                        raise ApplicationBundleError(UpdateCode.BUNDLE_MALFORMED)
                    target = stage / name
                    digest = hashlib.sha256()
                    remaining = info.size
                    with target.open("xb") as output:
                        os.chmod(target, 0o600)
                        while remaining:
                            chunk = handle.read(min(COPY_CHUNK_BYTES, remaining))
                            if not chunk:
                                raise ApplicationBundleError(UpdateCode.BUNDLE_MALFORMED)
                            output.write(chunk)
                            digest.update(chunk)
                            remaining -= len(chunk)
                        if handle.read(1):
                            raise ApplicationBundleError(UpdateCode.BUNDLE_MALFORMED)
                        output.flush()
                        os.fsync(output.fileno())
                if count < 2:
                    raise ApplicationBundleError(UpdateCode.BUNDLE_MALFORMED)
            source_after = source.stat()
            if (
                source_stat.st_dev, source_stat.st_ino, source_stat.st_size,
                source_stat.st_mtime_ns,
            ) != (
                source_after.st_dev, source_after.st_ino, source_after.st_size,
                source_after.st_mtime_ns,
            ):
                raise ApplicationBundleError(UpdateCode.BUNDLE_MEMBER_REFUSED)
            material, by_role, release = self._verify_tree(stage)
            digest = release.release_set_digest
            if not _HEX64.fullmatch(digest):
                raise ApplicationBundleError(UpdateCode.BUNDLE_MALFORMED)
            target = self.paths.application_update_bundles_dir / digest
            if target.exists():
                _existing_material, _existing_roles, existing = self._verify_tree(target)
                if existing.release_set_digest != digest:
                    raise ApplicationBundleError(UpdateCode.BUNDLE_MEMBER_REFUSED)
                shutil.rmtree(stage)
                with self.units.begin() as conn:
                    repo = ApplicationUpdateImportRepository(conn)
                    row = repo.get(digest)
                    if row is None:
                        repo.record_verified(existing, source_class=ImportSource.OFFLINE)
                    elif row.state is not ImportState.VERIFIED:
                        raise ApplicationInstallationError("import identity is terminal")
                return ImportedApplicationBundle(
                    UpdateOutcome.AVAILABLE, UpdateCode.OK, existing, True
                )
            receipt = {
                "schema_version": BUNDLE_RECEIPT_SCHEMA_VERSION,
                "release_set_digest": digest,
                "version": release.version,
                "wheel_name": by_role["python-wheel"].name,
                "wheel_size": by_role["python-wheel"].size,
            }
            atomic_write_text(
                stage / BUNDLE_RECEIPT_NAME,
                json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
            )
            os.rename(stage, target)
            fsync_directory(target.parent)
            with self.units.begin() as conn:
                repo = ApplicationUpdateImportRepository(conn)
                row = repo.get(digest)
                if row is None:
                    repo.record_verified(release, source_class=ImportSource.OFFLINE)
                elif row.state is not ImportState.VERIFIED:
                    raise ApplicationInstallationError("import identity is terminal")
            return ImportedApplicationBundle(
                UpdateOutcome.AVAILABLE, UpdateCode.OK, release, False
            )
        except ApplicationBundleError:
            if stage.exists():
                shutil.rmtree(stage)
            raise
        except (OSError, tarfile.TarError, ApplicationInstallationError) as exc:
            if stage.exists():
                shutil.rmtree(stage)
            raise ApplicationBundleError(UpdateCode.BUNDLE_MALFORMED) from exc

    def _roots(self) -> tuple[Path, ...]:
        root = self.paths.application_update_bundles_dir
        if not root.exists() or root.is_symlink():
            return ()
        return tuple(sorted(
            (item for item in root.iterdir()
             if item.name != ".staging" and _HEX64.fullmatch(item.name)
             and item.is_dir() and not item.is_symlink()),
            key=lambda item: item.name,
        )[:16])

    def candidates(self) -> tuple[ReleaseSetMaterial, ...]:
        result: list[ReleaseSetMaterial] = []
        for root in self._roots():
            try:
                material, _roles, _release = self._verify_tree(root)
            except ApplicationBundleError:
                continue
            result.append(material)
        return tuple(result)

    def resolve(self, release_set_digest: str):
        from .application_update_adapter import HeldApplicationRelease

        if not isinstance(release_set_digest, str) or not _HEX64.fullmatch(release_set_digest):
            raise ApplicationBundleError(UpdateCode.BUNDLE_MEMBER_REFUSED)
        root = self.paths.application_update_bundles_dir / release_set_digest
        if root.is_symlink() or not root.is_dir():
            raise ApplicationBundleError(UpdateCode.VERSION_NOT_FOUND)
        _material, roles, release = self._verify_tree(root)
        wheel = roles["python-wheel"]
        return HeldApplicationRelease(release, root, wheel.name, wheel.size)

    def release_available(self, release_set_digest: str) -> bool:
        try:
            self.resolve(release_set_digest)
        except ApplicationBundleError:
            return False
        return True

    # ApplicationReleaseResolver uses ``available(digest)`` while the channel
    # contract uses an ``available`` property.  A tiny wrapper in composition
    # avoids overloading one name with incompatible semantics.


class OfflineReleaseResolver:
    def __init__(self, store: OfflineApplicationBundleStore) -> None:
        self.store = store

    def resolve(self, release_set_digest: str):
        return self.store.resolve(release_set_digest)

    def available(self, release_set_digest: str) -> bool:
        return self.store.release_available(release_set_digest)


__all__ = [
    "ApplicationBundleError", "BUNDLE_CONTROL_NAME", "ImportedApplicationBundle",
    "OfflineApplicationBundleStore", "OfflineReleaseResolver",
]
