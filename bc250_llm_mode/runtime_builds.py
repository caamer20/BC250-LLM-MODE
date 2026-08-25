"""Immutable llama.cpp runtime build identities and typed repositories.

ADR 004 (U1.2): a build ID is ``llamacpp:sha256:<sha256(canonical
manifest)>`` — content-derived, never a tag, path, timestamp, or mutable
status. This module owns the canonical manifest encoding/derivation and
the four migration-005 repositories:

- ``RuntimeBuildRepository``      immutable build records;
- ``RuntimeVerificationRepository`` append-only verification facts;
- ``RuntimeTreeRepository``       operation-owned tree registry;
- ``RuntimeComponentRepository``  the one promoted/rollback component row.

Repositories never commit: callers own the unit-of-work boundary. Raw SQL
is confined to this module (and ``db.py`` migrations). No host, Git,
container, or service access happens here.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

from .legacy_import import utcnow

COMPONENT = "llamacpp"
MANIFEST_VERSION = 1
RECIPE_VERSION = 1
MAX_MANIFEST_BYTES = 65536

BUILD_ID_PREFIX = f"{COMPONENT}:sha256:"
_BUILD_ID_RE = re.compile(r"^llamacpp:sha256:[0-9a-f]{64}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,128}$")

# Closed canonical manifest field set (ADR 004 §1). Requested ref is
# display metadata only; timestamps/paths/operation IDs are forbidden so a
# rebuild of identical content reproduces the identical build ID.
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "component",
        "upstream_repository",
        "requested_ref",
        "source_commit",
        "source_checkout_verified",
        "recipe_version",
        "recipe_digest",
        "cmake_generator",
        "cmake_options",
        "cmake_targets",
        "build_parallelism",
        "container_image_id",
        "container_image_digest",
        "toolchain",
        "target_arch",
        "binaries",
        "smoke_contract_version",
    }
)
_REQUIRED_FIELDS = (
    "schema_version",
    "component",
    "upstream_repository",
    "source_commit",
    "recipe_version",
    "cmake_generator",
    "cmake_targets",
    "toolchain",
    "target_arch",
    "binaries",
)
_SECRET_HINT_RE = re.compile(
    r"(token|secret|password|passwd|credential|cookie|authorization|api[_-]?key)",
    re.IGNORECASE,
)

# Display metadata carried WITH the record but excluded from the identity
# hash: a mutable ref must never affect the build ID (ADR 004 D1).
_DISPLAY_ONLY_FIELDS = frozenset({"requested_ref"})


class RuntimeBuildError(RuntimeError):
    """Stable, bounded runtime-build domain failure (never raw output)."""

    def __init__(self, code: str, summary: str = "") -> None:
        super().__init__(summary or code)
        self.code = code


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """Sorted compact JSON encoding; the ONLY bytes ever hashed/stored."""
    return json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def validate_manifest(manifest: dict[str, Any]) -> None:
    """Closed-field, bounded, secret-refusing manifest validation (D1)."""
    if not isinstance(manifest, dict):
        raise RuntimeBuildError("MANIFEST_INVALID", "manifest must be an object")
    unknown = set(manifest) - _MANIFEST_FIELDS
    if unknown:
        raise RuntimeBuildError(
            "MANIFEST_FIELD_FORBIDDEN", f"unknown manifest fields: {sorted(unknown)}"
        )
    missing = [key for key in _REQUIRED_FIELDS if key not in manifest]
    if missing:
        raise RuntimeBuildError(
            "MANIFEST_INCOMPLETE", f"missing manifest fields: {missing}"
        )
    encoded = canonical_manifest_bytes(manifest)
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise RuntimeBuildError("MANIFEST_TOO_LARGE", "manifest exceeds bound")
    if int(manifest.get("schema_version", 0)) != MANIFEST_VERSION:
        raise RuntimeBuildError("MANIFEST_VERSION_UNSUPPORTED")
    if manifest.get("component") != COMPONENT:
        raise RuntimeBuildError("MANIFEST_COMPONENT_MISMATCH")
    if not _COMMIT_RE.fullmatch(str(manifest.get("source_commit") or "")):
        raise RuntimeBuildError("MANIFEST_SOURCE_COMMIT_INVALID")
    requested = manifest.get("requested_ref")
    if requested is not None and (
        not isinstance(requested, str) or not _REF_RE.fullmatch(requested)
    ):
        raise RuntimeBuildError("MANIFEST_REQUESTED_REF_INVALID")
    binaries = manifest.get("binaries")
    if not isinstance(binaries, list) or not binaries:
        raise RuntimeBuildError("MANIFEST_BINARIES_INVALID")
    for entry in binaries:
        if not isinstance(entry, dict):
            raise RuntimeBuildError("MANIFEST_BINARIES_INVALID")
        digest = entry.get("sha256")
        relpath = entry.get("path")
        if not _DIGEST_RE.fullmatch(str(digest or "")):
            raise RuntimeBuildError("MANIFEST_BINARY_DIGEST_INVALID")
        if not isinstance(relpath, str) or not relpath or relpath.startswith("/"):
            raise RuntimeBuildError("MANIFEST_BINARY_PATH_INVALID")
    toolchain = manifest.get("toolchain")
    if not isinstance(toolchain, dict) or not toolchain:
        raise RuntimeBuildError("MANIFEST_TOOLCHAIN_INVALID")
    # Secret-like keys/values never enter durable manifests.
    for key, value in manifest.items():
        blob = f"{key}={value!r}"[:512]
        if _SECRET_HINT_RE.search(blob):
            raise RuntimeBuildError(
                "MANIFEST_SECRET_SUSPECTED", f"manifest key rejected: {key}"
            )


def derive_build_id(manifest: dict[str, Any]) -> tuple[str, str]:
    """Validate then hash: returns ``(build_id, manifest_digest_hex)``.

    Display-only fields (the requested ref) are excluded before hashing so
    a mutable ref can never change a build identity (D1).
    """
    validate_manifest(manifest)
    identity_manifest = {
        key: value
        for key, value in manifest.items()
        if key not in _DISPLAY_ONLY_FIELDS
    }
    digest = hashlib.sha256(canonical_manifest_bytes(identity_manifest)).hexdigest()
    return (f"{BUILD_ID_PREFIX}{digest}", digest)


def is_legacy_build_id(build_id: str) -> bool:
    return str(build_id).startswith("legacy:")


def valid_build_id(build_id: str) -> bool:
    value = str(build_id)
    return bool(_BUILD_ID_RE.fullmatch(value)) or (
        value.startswith("legacy:") and len(value) <= 128
    )


def valid_digest(value: str) -> bool:
    return bool(_DIGEST_RE.fullmatch(str(value)))


class RuntimeBuildRepository:
    """Immutable build records; re-insert compares exact canonical bytes."""

    def __init__(self, conn, *, clock: Callable[[], str] | None = None) -> None:
        self.conn = conn
        self.clock = clock or utcnow

    def create_immutable(
        self,
        *,
        manifest: dict[str, Any],
        provenance_class: str = "IMMUTABLE_SOURCE",
        created_by_operation_id: str | None = None,
    ) -> dict[str, Any]:
        if provenance_class not in ("IMMUTABLE_SOURCE", "LEGACY_UNVERIFIED"):
            raise RuntimeBuildError("PROVENANCE_CLASS_INVALID")
        build_id, digest = derive_build_id(manifest)
        # Stored bytes are the canonical IDENTITY manifest: display-only
        # fields live solely in their own columns.
        identity_manifest = {
            key: value
            for key, value in manifest.items()
            if key not in _DISPLAY_ONLY_FIELDS
        }
        existing = self.get(build_id)
        canonical = canonical_manifest_bytes(identity_manifest).decode("utf-8")
        if existing is not None:
            if existing["manifest_json"] != canonical:
                raise RuntimeBuildError(
                    "BUILD_RECORD_CORRUPTION",
                    f"build {build_id} exists with different manifest bytes",
                )
            return existing
        try:
            self.conn.execute(
                """
                INSERT INTO runtime_builds (
                    build_id, component, manifest_version, manifest_json,
                    manifest_digest, source_commit, requested_ref,
                    recipe_version, provenance_class, created_by_operation_id,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    build_id,
                    COMPONENT,
                    MANIFEST_VERSION,
                    canonical,
                    digest,
                    str(manifest["source_commit"]),
                    manifest.get("requested_ref"),
                    int(manifest.get("recipe_version") or RECIPE_VERSION),
                    provenance_class,
                    created_by_operation_id,
                    self.clock(),
                ),
            )
        except Exception as exc:
            raise RuntimeBuildError(
                "BUILD_RECORD_WRITE_FAILED", str(exc)[:200]
            ) from exc
        record = self.get(build_id)
        assert record is not None
        return record

    def create_legacy_backfill(
        self,
        *,
        legacy_id: str,
        metadata: dict[str, Any],
        source_commit: str | None = None,
        requested_ref: str | None = None,
    ) -> dict[str, Any]:
        """Deterministic LEGACY_UNVERIFIED row (host adoption path only).

        The synthetic ID/digest are derived deterministically from the
        bounded metadata and never claim cryptographic trust.
        """
        if not legacy_id.startswith("legacy:") or len(legacy_id) > 128:
            raise RuntimeBuildError("LEGACY_ID_INVALID")
        blob = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        existing = self.get(legacy_id)
        if existing is not None:
            return existing
        self.conn.execute(
            """
            INSERT INTO runtime_builds (
                build_id, component, manifest_version, manifest_json,
                manifest_digest, source_commit, requested_ref,
                recipe_version, provenance_class, created_by_operation_id,
                created_at
            ) VALUES (?, ?, 1, ?, ?, ?, ?, 1, 'LEGACY_UNVERIFIED', NULL, ?)
            """,
            (
                legacy_id,
                blob[:MAX_MANIFEST_BYTES],
                digest,
                source_commit if source_commit and _COMMIT_RE.fullmatch(source_commit) else None,
                requested_ref[:128] if requested_ref else None,
                self.clock(),
            ),
        )
        record = self.get(legacy_id)
        assert record is not None
        return record

    def get(self, build_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT build_id, component, manifest_version, manifest_json,
                   manifest_digest, source_commit, requested_ref,
                   recipe_version, provenance_class, created_by_operation_id,
                   created_at
            FROM runtime_builds WHERE build_id = ?
            """,
            (build_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "build_id": row["build_id"],
            "component": row["component"],
            "manifest_version": row["manifest_version"],
            "manifest": json.loads(row["manifest_json"]),
            "manifest_json": row["manifest_json"],
            "manifest_digest": row["manifest_digest"],
            "source_commit": row["source_commit"],
            "requested_ref": row["requested_ref"],
            "recipe_version": row["recipe_version"],
            "provenance_class": row["provenance_class"],
            "created_by_operation_id": row["created_by_operation_id"],
            "created_at": row["created_at"],
        }

    def require(self, build_id: str) -> dict[str, Any]:
        record = self.get(build_id)
        if record is None:
            raise RuntimeBuildError("BUILD_NOT_FOUND", f"no such build: {build_id}")
        return record

    def list_bounded(self, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        rows = self.conn.execute(
            """
            SELECT build_id, manifest_digest, source_commit, requested_ref,
                   provenance_class, created_at
            FROM runtime_builds ORDER BY created_at DESC, build_id LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


class RuntimeVerificationRepository:
    """Append-only observed verification facts; no prompt/text content."""

    KINDS = (
        "SMOKE",
        "ACTIVE_HEALTH",
        "ACTIVE_INFERENCE",
        "RESTORED_HEALTH",
        "RESTORED_INFERENCE",
    )
    MAX_EVIDENCE_BYTES = 4096

    def __init__(self, conn, *, clock: Callable[[], str] | None = None) -> None:
        self.conn = conn
        self.clock = clock or utcnow

    def append(
        self,
        *,
        build_id: str,
        kind: str,
        evidence: dict[str, Any],
        operation_id: str | None = None,
    ) -> int:
        if kind not in self.KINDS:
            raise RuntimeBuildError("VERIFICATION_KIND_INVALID")
        payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        if len(payload) > self.MAX_EVIDENCE_BYTES:
            raise RuntimeBuildError("VERIFICATION_EVIDENCE_TOO_LARGE")
        cursor = self.conn.execute(
            """
            INSERT INTO runtime_build_verifications (
                build_id, operation_id, kind, evidence_json, observed_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (build_id, operation_id, kind, payload, self.clock()),
        )
        return int(cursor.lastrowid or 0)

    def list_for_build(self, build_id: str, *, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        rows = self.conn.execute(
            """
            SELECT id, build_id, operation_id, kind, evidence_json, observed_at
            FROM runtime_build_verifications WHERE build_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (build_id, limit),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "build_id": row["build_id"],
                "operation_id": row["operation_id"],
                "kind": row["kind"],
                "evidence": json.loads(row["evidence_json"]),
                "observed_at": row["observed_at"],
            }
            for row in rows
        ]


class RuntimeTreeRepository:
    """Registry of trees the application owns or has adopted."""

    ROLES = ("ACTIVE_OBSERVED", "CANDIDATE", "ROLLBACK", "RETAINED", "QUARANTINED")
    OWNERSHIP = ("OPERATION_OWNED", "LEGACY_ADOPTED")

    def __init__(self, conn, *, clock: Callable[[], str] | None = None) -> None:
        self.conn = conn
        self.clock = clock or utcnow

    def record_candidate(
        self,
        *,
        tree_id: str,
        build_id: str,
        container_profile: str,
        locator: str,
        manifest_digest: str,
        server_binary_digest: str,
        ownership_class: str = "OPERATION_OWNED",
        created_by_operation_id: str | None = None,
    ) -> dict[str, Any]:
        if not tree_id or len(tree_id) > 128:
            raise RuntimeBuildError("TREE_ID_INVALID")
        if not valid_digest(manifest_digest) or not valid_digest(server_binary_digest):
            raise RuntimeBuildError("TREE_DIGEST_INVALID")
        locator = str(locator)
        if (
            not locator
            or len(locator) > 256
            or locator.startswith("/")
            or ".." in locator.split("/")
        ):
            raise RuntimeBuildError(
                "TREE_LOCATOR_INVALID", "locator must be a managed relative path"
            )
        if container_profile and len(container_profile) > 128:
            raise RuntimeBuildError("TREE_PROFILE_INVALID")
        self.conn.execute(
            """
            INSERT INTO runtime_trees (
                tree_id, build_id, container_profile, locator, role,
                manifest_digest, server_binary_digest, ownership_class,
                created_by_operation_id, last_observed_at
            ) VALUES (?, ?, ?, ?, 'CANDIDATE', ?, ?, ?, ?, ?)
            ON CONFLICT(tree_id) DO UPDATE SET
                manifest_digest=excluded.manifest_digest,
                server_binary_digest=excluded.server_binary_digest,
                last_observed_at=excluded.last_observed_at
            """,
            (
                tree_id,
                build_id,
                container_profile,
                locator,
                manifest_digest,
                server_binary_digest,
                ownership_class,
                created_by_operation_id,
                self.clock(),
            ),
        )
        return self.require(tree_id)

    def get(self, tree_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM runtime_trees WHERE tree_id = ?", (tree_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def require(self, tree_id: str) -> dict[str, Any]:
        record = self.get(tree_id)
        if record is None:
            raise RuntimeBuildError("TREE_NOT_FOUND", f"no such tree: {tree_id}")
        return record

    def observe_location(
        self,
        tree_id: str,
        *,
        manifest_digest: str | None = None,
        server_binary_digest: str | None = None,
    ) -> dict[str, Any]:
        """Refresh observation facts; role/location changes use move_role."""
        sets = ["last_observed_at = ?"]
        params: list[Any] = [self.clock()]
        if manifest_digest is not None:
            if not valid_digest(manifest_digest):
                raise RuntimeBuildError("TREE_DIGEST_INVALID")
            sets.append("manifest_digest = ?")
            params.append(manifest_digest)
        if server_binary_digest is not None:
            if not valid_digest(server_binary_digest):
                raise RuntimeBuildError("TREE_DIGEST_INVALID")
            sets.append("server_binary_digest = ?")
            params.append(server_binary_digest)
        params.append(tree_id)
        cursor = self.conn.execute(
            f"UPDATE runtime_trees SET {', '.join(sets)} WHERE tree_id = ?",
            params,
        )
        if cursor.rowcount != 1:
            raise RuntimeBuildError("TREE_NOT_FOUND", f"no such tree: {tree_id}")
        return self.require(tree_id)

    def move_role(self, tree_id: str, new_role: str) -> dict[str, Any]:
        if new_role not in self.ROLES:
            raise RuntimeBuildError("TREE_ROLE_INVALID")
        cursor = self.conn.execute(
            "UPDATE runtime_trees SET role = ?, last_observed_at = ? "
            "WHERE tree_id = ?",
            (new_role, self.clock(), tree_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeBuildError("TREE_NOT_FOUND", f"no such tree: {tree_id}")
        return self.require(tree_id)

    def by_locator(self, locator: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM runtime_trees WHERE locator = ?", (locator,)
        ).fetchone()
        return dict(row) if row is not None else None

    def protected_tree_ids(
        self, *, exclude_operation_id: str | None = None
    ) -> set[str]:
        """Trees cleanup may NEVER remove: active/promoted/rollback/
        retained/quarantined roles, component-state references, adopted
        trees, and trees owned by any other live operation."""
        rows = self.conn.execute(
            """
            SELECT t.tree_id FROM runtime_trees t
            LEFT JOIN operations o ON o.id = t.created_by_operation_id
            WHERE t.role IN ('ACTIVE_OBSERVED', 'ROLLBACK', 'RETAINED',
                             'QUARANTINED')
               OR t.ownership_class = 'LEGACY_ADOPTED'
               OR t.tree_id IN (
                    SELECT promoted_tree_id FROM runtime_component_state
                    UNION SELECT rollback_tree_id FROM runtime_component_state
               )
               OR (
                    t.created_by_operation_id IS NOT NULL
                    AND t.created_by_operation_id != COALESCE(?, '')
                    AND o.finished_at IS NULL
               )
            """,
            (exclude_operation_id,),
        ).fetchall()
        return {row["tree_id"] for row in rows}


class RuntimeComponentRepository:
    """The ONE authoritative promoted/rollback row for ``llamacpp``."""

    def __init__(self, conn, *, clock: Callable[[], str] | None = None) -> None:
        self.conn = conn
        self.clock = clock or utcnow

    def current(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT component, promoted_build_id, rollback_build_id,
                   generation, promoted_tree_id, rollback_tree_id,
                   last_operation_id, updated_at
            FROM runtime_component_state WHERE component = ?
            """,
            (COMPONENT,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def _require_row(self) -> dict[str, Any]:
        current = self.current()
        if current is None:
            raise RuntimeBuildError(
                "COMPONENT_STATE_ABSENT", "runtime component state not initialized"
            )
        return current

    def promote_verified(
        self,
        *,
        expected_generation: int,
        expected_promoted_build_id: str | None,
        expected_rollback_build_id: str | None,
        promoted_build_id: str,
        rollback_build_id: str | None,
        promoted_tree_id: str | None = None,
        rollback_tree_id: str | None = None,
        operation_id: str | None = None,
        known_good_identity: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """CAS promotion inside the caller's unit of work (D6).

        Stale generation/build expectations fail WITHOUT partial writes.
        When ``known_good_identity`` is provided, the known-good row's
        runtime component identity/fingerprint update in the SAME unit
        while its model configuration is preserved untouched.
        """
        current = self._require_row()
        if int(current["generation"]) != int(expected_generation):
            raise RuntimeBuildError(
                "PROMOTION_GENERATION_STALE",
                f"expected generation {expected_generation}, "
                f"found {current['generation']}",
            )
        if current["promoted_build_id"] != expected_promoted_build_id:
            raise RuntimeBuildError("PROMOTION_LINEAGE_STALE", "promoted drift")
        if current["rollback_build_id"] != expected_rollback_build_id:
            raise RuntimeBuildError("PROMOTION_LINEAGE_STALE", "rollback drift")
        cursor = self.conn.execute(
            """
            UPDATE runtime_component_state SET
                promoted_build_id = ?, rollback_build_id = ?,
                generation = generation + 1,
                promoted_tree_id = COALESCE(?, promoted_tree_id),
                rollback_tree_id = COALESCE(?, rollback_tree_id),
                last_operation_id = COALESCE(?, last_operation_id),
                updated_at = ?
            WHERE component = ? AND generation = ?
              AND promoted_build_id IS ?
              AND rollback_build_id IS ?
            """,
            (
                promoted_build_id,
                rollback_build_id,
                promoted_tree_id,
                rollback_tree_id,
                operation_id,
                self.clock(),
                COMPONENT,
                int(expected_generation),
                expected_promoted_build_id,
                expected_rollback_build_id,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeBuildError("PROMOTION_CONFLICT", "stale promotion lost")
        if known_good_identity is not None:
            self._update_known_good_identity(known_good_identity)
        return self.current()  # type: ignore[return-value]

    def record_restoration(
        self,
        *,
        expected_generation: int,
        expected_promoted_build_id: str | None,
        expected_rollback_build_id: str | None,
        restored_promoted_build_id: str,
        new_rollback_build_id: str | None,
        promoted_tree_id: str | None = None,
        rollback_tree_id: str | None = None,
        operation_id: str | None = None,
        known_good_identity: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Post-restoration lineage toggle (D7): target becomes promoted;
        the former active becomes the next rollback target."""
        return self.promote_verified(
            expected_generation=expected_generation,
            expected_promoted_build_id=expected_promoted_build_id,
            # D7: the cutover revalidates BOTH lineage pointers.
            expected_rollback_build_id=expected_rollback_build_id,
            promoted_build_id=restored_promoted_build_id,
            rollback_build_id=new_rollback_build_id,
            promoted_tree_id=promoted_tree_id,
            rollback_tree_id=rollback_tree_id,
            operation_id=operation_id,
            known_good_identity=known_good_identity,
        )

    def initialize(self, *, initial_build_id: str | None = None) -> dict[str, Any]:
        """Create the single llamacpp row lazily (generation starts at 1)."""
        if self.current() is None:
            self.conn.execute(
                """
                INSERT INTO runtime_component_state (
                    component, promoted_build_id, rollback_build_id,
                    generation, updated_at
                ) VALUES (?, ?, NULL, 1, ?)
                ON CONFLICT(component) DO NOTHING
                """,
                (COMPONENT, initial_build_id, self.clock()),
            )
        return self._require_row()

    def _update_known_good_identity(self, identity: dict[str, Any]) -> None:
        """Update ONLY the runtime identity columns of the known-good row;
        model configuration is preserved verbatim."""
        self.conn.execute(
            """
            UPDATE known_good_runtime SET
                runtime_fingerprint = COALESCE(?, runtime_fingerprint),
                runtime_component_identity = COALESCE(?, runtime_component_identity)
            WHERE id = 1 AND model_alias IS NOT NULL
            """,
            (
                identity.get("runtime_fingerprint"),
                identity.get("runtime_component_identity"),
            ),
        )


__all__ = [
    "COMPONENT",
    "RuntimeBuildError",
    "RuntimeBuildRepository",
    "RuntimeComponentRepository",
    "RuntimeTreeRepository",
    "RuntimeVerificationRepository",
    "canonical_manifest_bytes",
    "derive_build_id",
    "valid_build_id",
    "valid_digest",
    "validate_manifest",
]
