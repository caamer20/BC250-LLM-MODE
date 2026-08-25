"""Production ``AcquisitionHostAdapter`` (U1.1 §4/§6).

One production implementation of the typed acquisition port: descriptor-
safe local import, immutable hub resolution/range transfer via
``hub_source``, managed publication through ``artifact_storage`` only,
lease-fenced reservations, and exact-evidence probes that observe reality.
"""

from __future__ import annotations

import os
import re
import shutil
import stat as stat_module
from dataclasses import asdict
from pathlib import Path

from . import artifact_storage as storage
from .model_artifact import gguf_layout_verdict
from .operations.acquisition import (
    ACQUISITION_RESOURCE,
    CandidateEvidence,
    CleanupEvidence,
    DiskPreflightEvidence,
    ModelAcquireRequestV1,
    ModelImportRequestV1,
    PublicationEvidence,
    RegistrationEvidence,
    SourceIdentity,
    TransferEvidence,
    ValidationEvidence,
)
from .operations.recovery import RecoveryClass
from .operations.workflow import ProbeResult
from .repositories import (
    ModelArtifactRepository,
    ModelInstallationsRepository,
    StorageReservationRepository,
)

MAX_SOURCE_BYTES = 80 * 1024 * 1024 * 1024


class HostError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def sanitize_alias(text: str) -> str:
    alias = re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-.")
    return alias[:48] or "model"


def artifact_id_for(digest: str) -> str:
    return "gguf-" + digest.split(":", 1)[1][:24]


def _filesystem_identity(path: Path) -> str:
    st = path.stat()
    return f"dev-{st.st_dev}"


class AcquisitionHostAdapter:
    """Composed once by Application; never touches frontend state."""

    def __init__(
        self,
        paths,
        units,
        *,
        hub_client=None,
        process_port=None,
        clock=None,
        catalog_lookup=None,
    ) -> None:
        self.paths = paths
        self.units = units
        self.hub = hub_client
        self.process_port = process_port
        self.clock = clock or (lambda: "")
        self.catalog_lookup = catalog_lookup  # model_id -> ModelEntry | None

    def _repos(self, conn):
        return (
            ModelArtifactRepository(conn, clock=self.clock),
            ModelInstallationsRepository(conn),
            StorageReservationRepository(conn, clock=self.clock),
        )

    def _staging(self, operation_id: str) -> Path:
        storage.validate_operation_id(operation_id)
        root = storage.ensure_private_dir(
            self.paths.model_staging_dir / operation_id / "transfer"
        )
        if not storage.contained(self.paths.model_staging_dir, root):
            raise HostError("STAGING_OWNERSHIP_INVALID", "staging escape")
        return root

    def _candidate_path(self, operation_id: str) -> Path:
        candidate_dir = storage.ensure_private_dir(
            self.paths.model_staging_dir / operation_id / "candidate"
        )
        return candidate_dir / "candidate.gguf"

    @staticmethod
    def _identity_from(ctx) -> SourceIdentity:
        stored = (ctx.prior_outputs.get("resolve_source") or {}).get(
            "source_identity"
        ) or {}
        files = tuple(dict(f) for f in stored.get("files", ()))
        return SourceIdentity(
            fingerprint=stored.get("fingerprint", ""),
            files=files,
            total_bytes=int(stored.get("total_bytes", 0)),
            revision=stored.get("revision"),
        )

    def _lease(self, ctx):
        revision = ctx.lease_revisions.get(ACQUISITION_RESOURCE)
        return {"lease_owner": ctx.worker_id, "lease_revision": revision}

    @staticmethod
    def _hash_fd(fd: int) -> tuple[str, int]:
        import hashlib

        h = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, storage.CHUNK_BYTES)
            if not chunk:
                break
            h.update(chunk)
            total += len(chunk)
        return f"sha256:{h.hexdigest()}", total

    def observe_local_source(self, request: ModelImportRequestV1):
        source = Path(request.source_path)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(source, flags)
        except OSError as exc:
            raise HostError("LOCAL_SOURCE_NOT_REGULAR", "cannot open source") from exc
        try:
            st = os.fstat(fd)
            if not stat_module.S_ISREG(st.st_mode):
                raise HostError("LOCAL_SOURCE_NOT_REGULAR", "not a regular file")
            digest, size = self._hash_fd(fd)
        finally:
            os.close(fd)
        return SourceIdentity(
            fingerprint=f"local:{digest}:{size}:{st.st_mtime_ns}",
            # Redacted label: the real basename never enters durable evidence
            # (U1.1 §9.3); the absolute path lives only in the request row.
            files=({"path": "local-source.gguf", "size": size},),
            total_bytes=size,
        )

    def resolve_catalog_source(self, request: ModelAcquireRequestV1):
        entry = (
            self.catalog_lookup(request.model_id) if self.catalog_lookup else None
        )
        if entry is None:
            raise HostError("CATALOG_MODEL_UNKNOWN", request.model_id)
        pattern = entry.allow_globs.get(request.quantization)
        if pattern is None:
            raise HostError("CATALOG_QUANT_UNSUPPORTED", request.quantization)
        filename = self._single_file_for_pattern(pattern, entry.avoid)
        revision = self.hub.resolve_revision(entry.repo)
        blob = self.hub.observe_blob(entry.repo, revision, filename)
        fingerprint = (
            f"commit:{revision}:{blob.filename}:{blob.expected_size}:{blob.validator}"
        )
        return SourceIdentity(
            fingerprint=fingerprint,
            files=(
                {
                    "path": blob.filename,
                    "size": blob.expected_size,
                    "validator": blob.validator,
                    "repo": blob.repo,
                    "revision": blob.revision,
                },
            ),
            total_bytes=blob.expected_size,
            revision=revision,
        )

    @staticmethod
    def _single_file_for_pattern(pattern: str, avoid) -> str:
        avoid_res = [
            re.compile("^" + re.escape(a).replace(r"\*", ".*") + "$") for a in avoid
        ]
        base = pattern.rsplit("/", 1)[-1]
        for r in avoid_res:
            if r.fullmatch(base):
                raise HostError("SOURCE_MANIFEST_INVALID", "avoid glob selected")
        return base

    def preflight_storage(self, request, source: SourceIdentity):
        models_dir = self.paths.models_dir
        models_dir.mkdir(parents=True, exist_ok=True)
        usage = os.statvfs(models_dir)
        available = int(usage.f_bavail * usage.f_frsize)
        required = source.total_bytes * 3 + (64 << 20)
        reserved = min(available, max(source.total_bytes * 2, 0))
        if source.total_bytes > MAX_SOURCE_BYTES:
            raise HostError("MODEL_STORAGE_INSUFFICIENT", "source exceeds policy")
        if available < required:
            raise HostError(
                "MODEL_STORAGE_INSUFFICIENT",
                f"required {required} available {available}",
            )
        return DiskPreflightEvidence(
            filesystem_identity=_filesystem_identity(models_dir),
            required_bytes=required,
            available_bytes=available,
            reserved_bytes=reserved,
        )

    def reserve_storage(self, ctx, source: SourceIdentity):
        preflight = self.preflight_storage(ctx.request, source)
        fence = self._lease(ctx)
        if fence["lease_revision"] is None:
            raise HostError("LEASE_LOST", "no model-storage lease held")
        with self.units.begin() as conn:
            _a, _i, reservations = self._repos(conn)
            if reservations.get(ctx.operation_id) is None:
                reservations.reserve(
                    operation_id=ctx.operation_id,
                    filesystem_identity=preflight.filesystem_identity,
                    required_bytes=preflight.required_bytes,
                    available_bytes=preflight.available_bytes,
                    reserved_bytes=preflight.reserved_bytes,
                    **fence,
                )
        return preflight

    def observe_reservation(self, request_or_ctx) -> ProbeResult:
        operation_id = getattr(request_or_ctx, "operation_id", None)
        with self.units.begin() as conn:
            _a, _i, reservations = self._repos(conn)
            row = reservations.get(operation_id)
            if row is not None and row["state"] == "RESERVED":
                return ProbeResult(RecoveryClass.COMPLETE, "RESERVED")
        return ProbeResult(RecoveryClass.ABSENT, "NO_RESERVATION")

    def release_on_cancellation(self, request, *, operation_id: str = ""):
        """§3.4 cancellation finalizer: release reservation; keep partial."""
        from .repositories import RepositoryConflict

        retained_bytes = 0
        with self.units.begin() as conn:
            _a, _i, reservations = self._repos(conn)
            row = reservations.get(operation_id)
            if row is not None and row["state"] == "RESERVED":
                try:
                    reservations.release(operation_id)
                except RepositoryConflict:
                    pass
        transfer_dir = (
            self.paths.model_staging_dir / operation_id / "transfer"
        )
        if transfer_dir.exists():
            for p in transfer_dir.glob("*.partial"):
                retained_bytes += p.stat().st_size
        return {"retained_bytes": retained_bytes}

    # -- port: transfer -------------------------------------------------------------
    def copy_local(self, ctx) -> TransferEvidence:
        identity = self._identity_from(ctx)
        source = Path(ctx.request.source_path)
        dest = self._staging(ctx.operation_id) / "source.partial"
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(source, flags)
        try:
            before = os.fstat(fd)
            total = 0
            with open(dest, "ab" if dest.exists() else "wb") as out:
                while True:
                    chunk = os.read(fd, storage.CHUNK_BYTES)
                    if not chunk:
                        break
                    out.write(chunk)
                    total += len(chunk)
                    ctx.pulse(
                        phase="transfer",
                        current=total,
                        total=identity.total_bytes,
                        unit="bytes",
                        cancellation_safe=True,
                    )
                out.flush()
                os.fsync(out.fileno())
            after = os.fstat(fd)
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ino,
            ):
                raise HostError("LOCAL_SOURCE_CHANGED", "source mutated during copy")
        finally:
            os.close(fd)
        digest, size = storage.streaming_sha256(dest)
        return TransferEvidence(
            fingerprint=digest,
            bytes_complete=size,
            total_bytes=identity.total_bytes,
            validator_digest=digest.split(":")[1][:16],
        )

    def transfer_catalog(self, ctx) -> TransferEvidence:
        identity = self._identity_from(ctx)
        file_info = dict(identity.files[0])
        blob_name = file_info["path"]
        import json

        class _Blob:
            filename = blob_name
            revision = str(file_info.get("revision", ""))
            repo = str(file_info.get("repo", ""))
            expected_size = int(file_info["size"])
            validator = str(file_info.get("validator", ""))

        dest = self._staging(ctx.operation_id) / (blob_name + ".partial")
        self.hub.stream_range(
            _Blob(), dest, expected_fingerprint=identity.fingerprint, pulse=ctx.pulse
        )
        digest, size = storage.streaming_sha256(dest)
        receipt_path = dest.with_name(dest.name + "-receipt.json")
        receipt_path.write_text(json.dumps({"digest": digest}, sort_keys=True))
        return TransferEvidence(
            fingerprint=digest,
            bytes_complete=size,
            total_bytes=identity.total_bytes,
            validator_digest=digest.split(":")[1][:16],
        )

    def probe_transfer(self, ctx) -> ProbeResult:
        identity = self._identity_from(ctx)
        transfer_dir = self.paths.model_staging_dir / ctx.operation_id / "transfer"
        partial = None
        if transfer_dir.exists():
            for p in sorted(transfer_dir.iterdir()):
                if p.suffix == ".partial":
                    partial = p
                    break
        if partial is None:
            return ProbeResult(RecoveryClass.ABSENT, "NO_PARTIAL")
        size = partial.stat().st_size
        payload = {
            "transfer": {
                "bytes_complete": size,
                "total_bytes": identity.total_bytes,
                "fingerprint": "",
                "validator_digest": "",
            }
        }
        if size and size == identity.total_bytes:
            digest, _ = storage.streaming_sha256(partial)
            payload["transfer"]["fingerprint"] = digest
            payload["transfer"]["validator_digest"] = digest.split(":")[1][:16]
            return ProbeResult(RecoveryClass.COMPLETE, "TRANSFERRED", payload)
        return ProbeResult(
            RecoveryClass.PARTIALLY_RESUMABLE, "PARTIAL_PRESENT", payload
        )

    # -- port: candidate ----------------------------------------------------------------
    def materialize_candidate(self, ctx) -> CandidateEvidence:
        identity = self._identity_from(ctx)
        transfer_dir = self.paths.model_staging_dir / ctx.operation_id / "transfer"
        partial = None
        if transfer_dir.exists():
            for p in sorted(transfer_dir.iterdir()):
                if p.suffix == ".partial":
                    partial = p
                    break
        if partial is None:
            raise HostError("NO_PARTIAL", "nothing staged to materialize")
        candidate = self._candidate_path(ctx.operation_id)
        if not candidate.exists():
            shutil.move(str(partial), str(candidate))
        digest, size = storage.streaming_sha256(candidate)
        if size != identity.total_bytes:
            raise HostError("GGUF_DIGEST_MISMATCH", "candidate size mismatch")
        return CandidateEvidence(
            staged_path_rel="candidate/candidate.gguf",
            byte_size=size,
            content_digest=digest,
            recipe_identity="direct-gguf-v1",
        )

    def probe_candidate(self, ctx) -> ProbeResult:
        stored = (ctx.prior_outputs.get("materialize_candidate") or {}).get(
            "candidate"
        ) or {}
        candidate = self._candidate_path(ctx.operation_id)
        if not candidate.exists():
            return ProbeResult(
                RecoveryClass.ABSENT, "NO_CANDIDATE", {"candidate": stored}
            )
        digest, size = storage.streaming_sha256(candidate)
        if stored and digest != stored.get("content_digest"):
            return ProbeResult(RecoveryClass.DISCARDABLE, "GGUF_DIGEST_MISMATCH")
        evidence = CandidateEvidence(
            staged_path_rel="candidate/candidate.gguf",
            byte_size=size,
            content_digest=digest,
            recipe_identity=str(stored.get("recipe_identity", "direct-gguf-v1")),
        )
        return ProbeResult(
            RecoveryClass.COMPLETE, "CANDIDATE_READY", {"candidate": asdict(evidence)}
        )

    def hash_and_validate_candidate(self, ctx) -> ValidationEvidence:
        candidate = self._candidate_path(ctx.operation_id)
        if not candidate.exists():
            raise HostError("NO_CANDIDATE", "validation target missing")
        verdict = gguf_layout_verdict(candidate)
        digest, size = storage.streaming_sha256(candidate)
        ok = verdict == "standard" and size > 0
        return ValidationEvidence(
            verdict="ok" if ok else "invalid",
            format="GGUF" if verdict != "rejected_not_gguf" else "unknown",
            layout_verdict=verdict,
            reason_code=None if ok else "GGUF_LAYOUT_FORBIDDEN",
            detail={"content_digest": digest, "byte_size": size},
        )

    # -- port: publication ---------------------------------------------------------------
    def _publication_receipt_path(self, operation_id: str, digest: str) -> Path:
        shard = self.paths.model_artifacts_dir / digest.split(":", 1)[1][:2]
        return shard / f"publication-{operation_id}.json"

    def publish_or_reuse(self, ctx) -> PublicationEvidence:
        validation = ValidationEvidence(
            **(((ctx.prior_outputs.get("validate_candidate") or {}).get("validation")) or {})
        )
        if validation.verdict != "ok":
            raise HostError("GGUF_LAYOUT_FORBIDDEN", "candidate not validated")
        candidate = self._candidate_path(ctx.operation_id)
        stored = (ctx.prior_outputs.get("materialize_candidate") or {}).get(
            "candidate"
        ) or {}
        content_digest = str(stored.get("content_digest", ""))
        if not content_digest:
            raise HostError("PUBLICATION_IDENTITY_UNCERTAIN", "no candidate digest")
        final = storage.publish_no_replace(
            candidate, self.paths.model_artifacts_dir, content_digest
        )
        reused = self._publication_receipt_path(
            ctx.operation_id, content_digest
        ) and storage.read_receipt(self._publication_receipt_path(ctx.operation_id, content_digest))
        disposition = "reused" if reused else "published"
        storage.write_receipt(
            self._publication_receipt_path(ctx.operation_id, content_digest),
            {
                "content_digest": content_digest,
                "operation_id": ctx.operation_id,
                "disposition": disposition,
                "final_rel": str(final.relative_to(self.paths.models_dir)),
            },
        )
        return PublicationEvidence(
            content_digest=content_digest,
            final_path_rel=str(final.relative_to(self.paths.models_dir)),
            file_identity="published",
            disposition=disposition,
        )

    def quarantine_candidate(self, ctx) -> PublicationEvidence:
        stored = (ctx.prior_outputs.get("materialize_candidate") or {}).get(
            "candidate"
        ) or {}
        validation = ValidationEvidence(
            **(((ctx.prior_outputs.get("validate_candidate") or {}).get("validation")) or {})
        )
        content_digest = str(stored.get("content_digest", ""))
        candidate = self._candidate_path(ctx.operation_id)
        dest = storage.quarantine_candidate(
            candidate,
            self.paths.model_quarantine_dir,
            ctx.operation_id,
            content_digest,
            validation.reason_code or "GGUF_INVALID",
        )
        return PublicationEvidence(
            content_digest=content_digest,
            final_path_rel=str(dest.relative_to(self.paths.models_dir)),
            file_identity="quarantined",
            disposition="quarantined",
        )

    def probe_publication(self, ctx) -> ProbeResult:
        validation = ((ctx.prior_outputs.get("validate_candidate") or {}).get("validation")) or {}
        stored = (ctx.prior_outputs.get("materialize_candidate") or {}).get("candidate") or {}
        content_digest = str(stored.get("content_digest", ""))
        if validation.get("verdict") != "ok":
            qdir = self.paths.model_quarantine_dir / ctx.operation_id
            qdest = qdir / f"{content_digest}.gguf"
            if not qdest.exists():
                return ProbeResult(RecoveryClass.ABSENT, "NOT_QUARANTINED")
            evidence = PublicationEvidence(
                content_digest=content_digest,
                final_path_rel=str(qdest.relative_to(self.paths.models_dir)),
                file_identity="quarantined",
                disposition="quarantined",
            )
            return ProbeResult(
                RecoveryClass.COMPLETE, "QUARANTINED", {"publication": asdict(evidence)}
            )
        receipt = storage.read_receipt(
            self._publication_receipt_path(ctx.operation_id, content_digest)
        )
        if receipt is None:
            return ProbeResult(RecoveryClass.ABSENT, "NOT_PUBLISHED")
        final = self.paths.models_dir / str(receipt.get("final_rel", ""))
        identity_digest = storage._identity_via_nofollow(final)
        if identity_digest is None:
            return ProbeResult(RecoveryClass.ABSENT, "NOT_PUBLISHED")
        if identity_digest != content_digest:
            return ProbeResult(
                RecoveryClass.UNCERTAIN_MANUAL, "PUBLICATION_IDENTITY_UNCERTAIN"
            )
        evidence = PublicationEvidence(
            content_digest=content_digest,
            final_path_rel=str(receipt.get("final_rel", "")),
            file_identity=str(receipt.get("disposition", "published")),
            disposition=str(receipt.get("disposition", "published")),
        )
        return ProbeResult(
            RecoveryClass.COMPLETE, "PUBLISHED", {"publication": asdict(evidence)}
        )

    # -- port: registration ------------------------------------------------------------------
    def _alias_for(self, ctx) -> str:
        request = ctx.request
        alias = getattr(request, "alias", None)
        if isinstance(alias, str) and alias.strip():
            return sanitize_alias(alias)
        model_id = getattr(request, "model_id", None)
        if isinstance(model_id, str) and model_id:
            return sanitize_alias(model_id)
        source = Path(getattr(request, "source_path", "model.gguf"))
        return sanitize_alias(source.stem)

    def register_installation(self, ctx) -> RegistrationEvidence:
        publication = (
            (ctx.prior_outputs.get("publish_artifact") or {}).get("publication")
        ) or {}
        disposition = str(publication.get("disposition", ""))
        content_digest = str(publication.get("content_digest", ""))
        artifact_id = artifact_id_for(content_digest)
        quarantined = disposition == "quarantined"
        alias = None if quarantined else self._alias_for(ctx)
        byte_size = int(
            (ctx.prior_outputs.get("materialize_candidate") or {})
            .get("candidate", {})
            .get("byte_size", 0)
        )
        source_kind = "local" if hasattr(ctx.request, "source_path") else "hub"
        with self.units.begin() as conn:
            artifacts, installs, _r = self._repos(conn)
            existing = artifacts.get_by_digest(content_digest)
            if existing is None:
                canonical = str(
                    self.paths.models_dir / publication["final_path_rel"]
                )
                if quarantined:
                    validation = (
                        (ctx.prior_outputs.get("validate_candidate") or {}).get(
                            "validation"
                        )
                        or {}
                    )
                    artifacts.record_quarantine(
                        artifact_id=artifact_id,
                        content_digest=content_digest,
                        byte_size=byte_size,
                        canonical_path=str(
                            self.paths.model_quarantine_dir
                            / ctx.operation_id
                            / f"{content_digest}.gguf"
                        ),
                        reason_code=validation.get("reason_code") or "GGUF_INVALID",
                        source_kind=source_kind,
                    )
                else:
                    artifacts.record_verified(
                        artifact_id=artifact_id,
                        content_digest=content_digest,
                        byte_size=byte_size,
                        canonical_path=canonical,
                        source_kind=source_kind,
                    )
            if disposition == "reused":
                reg_disposition = "reused"
            elif quarantined:
                reg_disposition = "quarantined"
            else:
                reg_disposition = "installed"
            if alias is not None:
                installs.install_alias(
                    alias=alias,
                    artifact_id=artifact_id,
                    quant=getattr(ctx.request, "quantization", "") or "",
                    display_name=alias,
                )
        return RegistrationEvidence(
            artifact_id=artifact_id, alias=alias, disposition=reg_disposition
        )

    def probe_registration(self, ctx) -> ProbeResult:
        publication = (
            (ctx.prior_outputs.get("publish_artifact") or {}).get("publication")
        ) or {}
        content_digest = str(publication.get("content_digest", ""))
        artifact_id = artifact_id_for(content_digest)
        quarantined = publication.get("disposition") == "quarantined"
        with self.units.begin() as conn:
            artifacts, installs, _r = self._repos(conn)
            row = artifacts.get(artifact_id)
            if row is None:
                return ProbeResult(RecoveryClass.ABSENT, "NOT_REGISTERED")
            if quarantined:
                evidence = RegistrationEvidence(
                    artifact_id=artifact_id, alias=None, disposition="quarantined"
                )
            else:
                alias = self._alias_for(ctx)
                installed = installs.get_by_alias(alias)
                if (
                    installed is None
                    or installed.get("artifact_id") != artifact_id
                ):
                    return ProbeResult(RecoveryClass.ABSENT, "ALIAS_NOT_LINKED")
                evidence = RegistrationEvidence(
                    artifact_id=artifact_id,
                    alias=alias,
                    disposition="installed",
                )
        return ProbeResult(
            RecoveryClass.COMPLETE, "REGISTERED", {"registration": asdict(evidence)}
        )

    def finalize_owned_staging(self, ctx) -> CleanupEvidence:
        op_root = self.paths.model_staging_dir / ctx.operation_id
        removed = []
        if op_root.exists():
            shutil.rmtree(op_root)
            removed.append(op_root.name)
        fence = self._lease(ctx)
        with self.units.begin() as conn:
            _a, _i, reservations = self._repos(conn)
            row = reservations.get(ctx.operation_id)
            if row is not None and row["state"] == "RESERVED":
                try:
                    reservations.release(ctx.operation_id, **fence)
                except Exception:
                    pass  # retained warning; probe decides
        with self.units.begin() as conn:
            _a, _i, reservations = self._repos(conn)
            row = reservations.get(ctx.operation_id)
            released = row is None or row["state"] == "RELEASED"
        return CleanupEvidence(
            removed_paths=tuple(removed),
            retained_paths=(),
            reservation_released=released,
        )

    def probe_finalization(self, ctx) -> ProbeResult:
        op_root = self.paths.model_staging_dir / ctx.operation_id
        gone = not op_root.exists()
        with self.units.begin() as conn:
            _a, _i, reservations = self._repos(conn)
            row = reservations.get(ctx.operation_id)
            released = row is None or row["state"] == "RELEASED"
        if gone and released:
            return ProbeResult(RecoveryClass.COMPLETE, "FINALIZED")
        return ProbeResult(RecoveryClass.REVERTIBLE, "CLEANUP_RETAINED")

    def compensate_acquisition(self, ctx):
        """Forward-only: registration recovery is forward; nothing deleted."""
        return {}

    def observe_compensation(self, ctx) -> ProbeResult:
        return ProbeResult(RecoveryClass.COMPLETE, "COMPENSATED")