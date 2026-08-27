"""P6 §12.1: the Model Library read model.

``ModelLibraryQueryService`` assembles ONE bounded, query-only read model
around ``model_artifacts`` + ``model_installations`` + ``model_library_meta``
plus computed fit / reference / deletion-eligibility fields. It never makes
a catalog title or mutable remote tag the artifact identity: identity is the
immutable content digest + artifact id.

Every §12.1 field is surfaced:

- display name and canonical identity (alias + artifact id + digest);
- source registry/repository and immutable revision;
- digest, size, format, quantization, architecture, tensor metadata;
- trust/storage state and quarantine reason;
- provenance and license metadata;
- installed aliases and active / known-good references;
- fit verdict by target context/slots (computed when a catalog entry exists);
- last verified inference and benchmark summary;
- usage / last-used metadata (never prompt content);
- pinned/favorite state;
- deletion eligibility and blockers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .paths import AppPaths
from .unit_of_work import UnitOfWorkFactory

MODEL_LIBRARY_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ModelLibraryEntry:
    """One library row: immutable identity + computed presentation fields."""

    alias: str
    display_name: str
    path: str
    quant: str | None
    # immutable identity / provenance
    artifact_id: str | None
    content_digest: str | None
    byte_size: int | None
    format: str | None
    architecture: str | None
    tensor_count: int | None
    source_kind: str | None
    source_repo: str | None
    source_revision: str | None
    catalog_id: str | None
    license_id: str | None
    # trust / storage
    trust_state: str | None
    storage_state: str | None
    quarantine_reason_code: str | None
    validation_status: str
    # references
    active: bool
    known_good: bool
    # library meta
    pinned: bool
    last_used_at: str | None
    last_verified_inference_at: str | None
    benchmark_summary: dict[str, Any] = field(default_factory=dict)
    # fit (computed, may be unknown)
    fit_verdict: str | None = None
    fit_detail: str | None = None
    # deletion
    deletion_eligible: bool = False
    deletion_blockers: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "display_name": self.display_name,
            "path": self.path,
            "quant": self.quant,
            "artifact_id": self.artifact_id,
            "content_digest": self.content_digest,
            "byte_size": self.byte_size,
            "format": self.format,
            "architecture": self.architecture,
            "tensor_count": self.tensor_count,
            "source_kind": self.source_kind,
            "source_repo": self.source_repo,
            "source_revision": self.source_revision,
            "catalog_id": self.catalog_id,
            "license_id": self.license_id,
            "trust_state": self.trust_state,
            "storage_state": self.storage_state,
            "quarantine_reason_code": self.quarantine_reason_code,
            "validation_status": self.validation_status,
            "active": self.active,
            "known_good": self.known_good,
            "pinned": self.pinned,
            "last_used_at": self.last_used_at,
            "last_verified_inference_at": self.last_verified_inference_at,
            "benchmark_summary": self.benchmark_summary,
            "fit_verdict": self.fit_verdict,
            "fit_detail": self.fit_detail,
            "deletion_eligible": self.deletion_eligible,
            "deletion_blockers": list(self.deletion_blockers),
        }


class ModelLibraryQueryService:
    """Query-only read model over the durable library tables."""

    def __init__(self, units: UnitOfWorkFactory, paths: AppPaths) -> None:
        self._units = units
        self._paths = paths

    def entries(
        self,
        *,
        context: int | None = None,
        slots: int | None = None,
    ) -> tuple[ModelLibraryEntry, ...]:
        from .repositories import (
            ModelInstallationsRepository,
            ModelLibraryMetaRepository,
        )

        with self._units.read() as conn:
            installations = ModelInstallationsRepository(conn).list()
            meta = ModelLibraryMetaRepository(conn).all()
            artifacts = self._artifacts_by_id(conn)
            active_alias = self._active_alias(conn)
            known_good_alias = self._known_good_alias(conn)

        result = []
        for model in installations:
            alias = model["id"]
            artifact = artifacts.get(model.get("artifact_id") or "") or {}
            row_meta = meta.get(alias) or {}
            fit_verdict, fit_detail = self._fit(
                model, artifact, context=context, slots=slots)
            active = alias == active_alias
            known_good = alias == known_good_alias
            blockers = self._deletion_blockers(
                active=active, known_good=known_good,
                trust_state=artifact.get("trust_state"),
                storage_state=artifact.get("storage_state"))
            result.append(ModelLibraryEntry(
                alias=alias,
                display_name=model.get("display_name") or alias,
                path=model.get("path") or "",
                quant=model.get("quant"),
                artifact_id=model.get("artifact_id"),
                content_digest=artifact.get("content_digest")
                or model.get("content_digest"),
                byte_size=artifact.get("byte_size"),
                format=artifact.get("format"),
                architecture=artifact.get("architecture"),
                tensor_count=artifact.get("tensor_count"),
                source_kind=artifact.get("source_kind"),
                source_repo=artifact.get("source_repo"),
                source_revision=artifact.get("source_revision"),
                catalog_id=artifact.get("catalog_id"),
                license_id=artifact.get("license_id"),
                trust_state=artifact.get("trust_state")
                or model.get("artifact_trust_state"),
                storage_state=artifact.get("storage_state")
                or model.get("artifact_storage_state"),
                quarantine_reason_code=artifact.get("quarantine_reason_code"),
                validation_status=model.get("validation_status") or "unverified",
                active=active,
                known_good=known_good,
                pinned=bool(row_meta.get("pinned")),
                last_used_at=row_meta.get("last_used_at"),
                last_verified_inference_at=row_meta.get(
                    "last_verified_inference_at"),
                benchmark_summary=row_meta.get("benchmark_summary") or {},
                fit_verdict=fit_verdict,
                fit_detail=fit_detail,
                deletion_eligible=not blockers,
                deletion_blockers=blockers,
            ))
        return tuple(result)

    def to_dict(
        self,
        *,
        context: int | None = None,
        slots: int | None = None,
    ) -> dict[str, Any]:
        entries = self.entries(context=context, slots=slots)
        return {
            "schema_version": MODEL_LIBRARY_SCHEMA_VERSION,
            "count": len(entries),
            "entries": [e.to_dict() for e in entries],
        }

    # --- internals -------------------------------------------------------------

    def _artifacts_by_id(self, conn) -> dict[str, dict[str, Any]]:
        rows = conn.execute(
            "SELECT id, content_digest, byte_size, format, architecture, "
            "tensor_count, source_kind, source_repo, source_revision, "
            "catalog_id, license_id, trust_state, storage_state, "
            "quarantine_reason_code FROM model_artifacts"
        ).fetchall()
        return {r["id"]: dict(r) for r in rows}

    def _active_alias(self, conn) -> str | None:
        row = conn.execute(
            "SELECT model_alias FROM known_good_runtime WHERE id = 1"
        ).fetchone()
        if row is not None:
            return row["model_alias"]
        return None

    def _known_good_alias(self, conn) -> str | None:
        row = conn.execute(
            "SELECT model_alias FROM known_good_runtime WHERE id = 1"
        ).fetchone()
        return row["model_alias"] if row is not None else None

    def _fit(self, model: dict, artifact: dict, *, context, slots):
        """Opportunistic fit verdict: only when a catalog entry + quant exist.

        Never uses a mutable tag as identity; the catalog entry is matched by
        the durable ``catalog_id`` recorded on the artifact.
        """
        from .catalog import CATALOG, calculate_fit

        catalog_id = artifact.get("catalog_id")
        quant = model.get("quant")
        if not catalog_id or not quant:
            return None, None
        entry = next((e for e in CATALOG if e.id == catalog_id), None)
        if entry is None or quant not in entry.weights_gib_by_quant:
            return None, None
        ctx = context or 8192
        n_slots = slots or 1
        try:
            result = calculate_fit(entry, quant, ctx, parallel_slots=n_slots)
        except (KeyError, ValueError):
            return None, None
        return result.verdict, result.detail

    def _deletion_blockers(
        self,
        *,
        active: bool,
        known_good: bool,
        trust_state: str | None,
        storage_state: str | None,
    ) -> tuple[str, ...]:
        """Forward-only publication rules (§12.2): active / known-good /
        uncertain artifacts cannot be destructively removed."""
        blockers = []
        if active:
            blockers.append("active-model")
        if known_good:
            blockers.append("known-good-runtime")
        if storage_state == "QUARANTINED":
            blockers.append("quarantined-inspect-first")
        if trust_state in ("VERIFIED",) and not active and not known_good:
            # A verified, non-active artifact is still safe to remove but we
            # surface its trust state as a soft note, not a blocker.
            pass
        return tuple(blockers)


def open_model_library_service(
    database_path, paths: AppPaths
) -> ModelLibraryQueryService:
    return ModelLibraryQueryService(UnitOfWorkFactory(database_path), paths)
