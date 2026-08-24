"""Fake MODEL_ACQUIRE/MODEL_IMPORT host world (Session 6A plan §10/§11).

Real durable on-disk state under an injected temporary root: source bytes,
operation-owned staging, content-addressed final artifacts, and quarantine.
Every mutator classifies reality through its read-only probe first and
counts externally visible effects so recovery tests can assert exactness.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any

from bc250_llm_mode.operations.acquisition import (
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
from bc250_llm_mode.operations.recovery import RecoveryClass
from bc250_llm_mode.operations.workflow import ProbeResult

CHUNK = 4


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeAcquisitionHost:
    """Typed fake implementation of the U1.1 acquisition port."""

    def __init__(
        self,
        root: Path,
        *,
        payload: bytes = b"gguf-fake-bytes",
        validation_verdict: str = "ok",
        invalid_reason: str = "GGUF_INVALID",
        on_pulse=None,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.payload = payload
        self.digest = "sha256:" + _sha256_bytes(payload)
        self.validation_verdict = validation_verdict
        self.invalid_reason = invalid_reason
        self.on_pulse = on_pulse
        # External-effect counters.
        self.counts = {
            "transfer": 0,
            "materialize": 0,
            "publication": 0,
            "registration": 0,
            "compensation": 0,
        }
        self.reservations: dict[str, dict[str, Any]] = {}
        self.registered: dict[str, dict[str, Any]] = {}
        self.compensated: set[str] = set()

    # -- layout ---------------------------------------------------------------
    @property
    def artifacts_root(self) -> Path:
        return self.root / "artifacts"

    def staging_dir(self, operation_id: str) -> Path:
        return self.root / "staging" / operation_id

    def partial_path(self, operation_id: str) -> Path:
        return self.staging_dir(operation_id) / "source.partial"

    def candidate_path(self, operation_id: str) -> Path:
        return self.staging_dir(operation_id) / "candidate.gguf"

    def final_path(self) -> Path:
        d = self.artifacts_root / self.digest.split(":")[1][:2]
        return d / f"{self.digest}.gguf"

    def quarantine_path(self, operation_id: str) -> Path:
        return self.root / "quarantine" / operation_id / f"{self.digest}.gguf"

    def seed_local_source(self) -> Path:
        src = self.root / "external" / "downloaded-model.gguf"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(self.payload)
        return src

    # -- port: resolve/observe -------------------------------------------------
    def resolve_catalog_source(self, request: ModelAcquireRequestV1):
        fingerprint = (
            f"commit:{request.model_id}@{request.quantization}:"
            + _sha256_bytes(request.model_id.encode())[:12]
        )
        return SourceIdentity(
            fingerprint=fingerprint,
            files=({"path": "model.gguf", "size": len(self.payload)},),
            total_bytes=len(self.payload),
            revision="pinned",
        )

    def observe_local_source(self, request: ModelImportRequestV1):
        src = Path(request.source_path)
        data = src.read_bytes()
        stat = src.stat()
        return SourceIdentity(
            fingerprint=(
                f"local:{_sha256_bytes(data)}:{stat.st_size}:{int(stat.st_mtime)}"
            ),
            files=({"path": src.name, "size": len(data)},),
            total_bytes=len(data),
        )

    # -- port: storage reservation ----------------------------------------------
    def preflight_storage(self, request, source: SourceIdentity):
        return DiskPreflightEvidence(
            filesystem_identity="fs-test",
            required_bytes=source.total_bytes * 3 + 1024,
            available_bytes=10**9,
            reserved_bytes=source.total_bytes * 2 + 512,
        )

    def reserve_storage(self, ctx, source):
        self.reservations[ctx.operation_id] = {
            "reserved": source.total_bytes * 2 + 512,
            "released": False,
        }
        return DiskPreflightEvidence(
            filesystem_identity="fs-test",
            required_bytes=source.total_bytes * 3 + 1024,
            available_bytes=10**9,
            reserved_bytes=source.total_bytes * 2 + 512,
        )

    def observe_reservation(self, request) -> ProbeResult:
        active = [r for r in self.reservations.values() if not r["released"]]
        if active:
            return ProbeResult(RecoveryClass.COMPLETE, "RESERVED")
        return ProbeResult(RecoveryClass.ABSENT, "NO_RESERVATION")

    # -- port: transfer -----------------------------------------------------------
    def _copy_into_staging(self, ctx) -> TransferEvidence:
        op = ctx.operation_id
        dest = self.partial_path(op)
        dest.parent.mkdir(parents=True, exist_ok=True)
        existing = dest.stat().st_size if dest.exists() else 0
        mode = "ab" if existing else "wb"
        with dest.open(mode) as fh:
            written = existing
            while written < len(self.payload):
                chunk = self.payload[written : written + CHUNK]
                fh.write(chunk)
                fh.flush()
                written += len(chunk)
                try:
                    # §8.2: cancellation-safe pulse between bounded chunks.
                    ctx.pulse(
                        phase="transfer",
                        current=written,
                        total=len(self.payload),
                        unit="bytes",
                        cancellation_safe=True,
                    )
                    if self.on_pulse is not None:
                        self.on_pulse()
                except BaseException:
                    # Adapter closes the response safely, then re-raises.
                    raise
        return TransferEvidence(
            fingerprint=self.digest,
            bytes_complete=len(self.payload),
            total_bytes=len(self.payload),
            validator_digest=_sha256_bytes(self.payload)[:16],
        )

    def transfer_catalog(self, ctx) -> TransferEvidence:
        self.counts["transfer"] += 1
        return self._copy_into_staging(ctx)

    def copy_local(self, ctx) -> TransferEvidence:
        self.counts["transfer"] += 1
        return self._copy_into_staging(ctx)

    def probe_transfer(self, ctx) -> ProbeResult:
        dest = self.partial_path(ctx.operation_id)
        if not dest.exists():
            return ProbeResult(
                RecoveryClass.ABSENT,
                "NO_PARTIAL",
                {"source_fingerprint": self.digest},
            )
        size = dest.stat().st_size
        evidence = TransferEvidence(
            fingerprint=self.digest,
            bytes_complete=size,
            total_bytes=len(self.payload),
            validator_digest=_sha256_bytes(self.payload)[:16],
        )
        payload = {"transfer": asdict(evidence)}
        if size == len(self.payload):
            return ProbeResult(RecoveryClass.COMPLETE, "TRANSFERRED", payload)
        return ProbeResult(
            RecoveryClass.PARTIALLY_RESUMABLE, "PARTIAL_PRESENT", payload
        )

    # -- port: candidate ------------------------------------------------------------
    def materialize_candidate(self, ctx) -> CandidateEvidence:
        self.counts["materialize"] += 1
        src = self.partial_path(ctx.operation_id)
        dest = self.candidate_path(ctx.operation_id)
        shutil.copyfile(src, dest)
        return CandidateEvidence(
            staged_path_rel="candidate.gguf",
            byte_size=len(self.payload),
            content_digest=self.digest,
            recipe_identity="direct-copy-v1",
        )

    def probe_candidate(self, ctx) -> ProbeResult:
        dest = self.candidate_path(ctx.operation_id)
        if not dest.exists():
            return ProbeResult(
                RecoveryClass.ABSENT,
                "NO_CANDIDATE",
                {
                    "source_fingerprint": self.digest,
                    "candidate": asdict(
                        CandidateEvidence(
                            staged_path_rel="candidate.gguf",
                            byte_size=len(self.payload),
                            content_digest=self.digest,
                            recipe_identity="direct-copy-v1",
                        )
                    ),
                },
            )
        data = dest.read_bytes()
        if _sha256_bytes(data) != self.digest.split(":", 1)[1]:
            return ProbeResult(
                RecoveryClass.DISCARDABLE, "CANDIDATE_DIGEST_MISMATCH"
            )
        return ProbeResult(RecoveryClass.COMPLETE, "CANDIDATE_READY")

    # -- port: validation -------------------------------------------------------------
    def hash_and_validate_candidate(self, ctx) -> ValidationEvidence:
        verdict_ok = self.validation_verdict == "ok"
        return ValidationEvidence(
            verdict=self.validation_verdict,
            format="GGUF",
            architecture="llama" if verdict_ok else None,
            reason_code=None if verdict_ok else self.invalid_reason,
        )

    # -- port: publication --------------------------------------------------------------
    def _receipt_path(self, operation_id: str) -> Path:
        return (
            self.final_path().parent / f"receipt-{operation_id}.json"
        )

    def publish_or_reuse(self, ctx) -> PublicationEvidence:
        final = self.final_path()
        disposition = "published"
        if final.exists():
            data = final.read_bytes()
            if _sha256_bytes(data) == self.digest.split(":", 1)[1]:
                disposition = "reused"
            else:
                raise RuntimeError("PUBLICATION_COLLISION")
        else:
            self.counts["publication"] += 1
            final.parent.mkdir(parents=True, exist_ok=True)
            tmp = final.with_suffix(".tmp-publish")
            tmp.write_bytes(self.payload)
            try:
                tmp.rename(final)  # no-replace semantics on a fresh path
            except FileExistsError:
                raise RuntimeError("PUBLICATION_COLLISION")
        self._receipt_path(ctx.operation_id).write_text(
            json.dumps({"disposition": disposition})
        )
        return PublicationEvidence(
            content_digest=self.digest,
            final_path_rel=str(final.relative_to(self.root)),
            file_identity=disposition,
            disposition=disposition,
        )

    def probe_publication(self, ctx) -> ProbeResult:
        validation = (ctx.prior_outputs.get("validate_candidate") or {}).get(
            "validation"
        ) or {}
        if validation.get("verdict") != "ok":
            qpath = self.quarantine_path(ctx.operation_id)
            if not qpath.exists():
                return ProbeResult(RecoveryClass.ABSENT, "NOT_QUARANTINED")
            evidence = PublicationEvidence(
                content_digest=self.digest,
                final_path_rel=str(qpath.relative_to(self.root)),
                file_identity="quarantined",
                disposition="quarantined",
            )
            return ProbeResult(
                RecoveryClass.COMPLETE, "QUARANTINED", {"publication": asdict(evidence)}
            )
        final = self.final_path()
        if not final.exists():
            return ProbeResult(RecoveryClass.ABSENT, "NOT_PUBLISHED")
        data = final.read_bytes()
        if _sha256_bytes(data) != self.digest.split(":", 1)[1]:
            return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL,
                               "PUBLICATION_IDENTITY_UNCERTAIN")
        disposition = "published"
        receipt = self._receipt_path(ctx.operation_id)
        if receipt.exists():
            try:
                disposition = json.loads(receipt.read_text()).get(
                    "disposition", "published"
                )
            except (ValueError, OSError):
                pass
        evidence = PublicationEvidence(
            content_digest=self.digest,
            final_path_rel=str(final.relative_to(self.root)),
            file_identity=disposition,
            disposition=disposition,
        )
        return ProbeResult(
            RecoveryClass.COMPLETE, "PUBLISHED", {"publication": asdict(evidence)}
        )

    def quarantine_candidate(self, ctx) -> PublicationEvidence:
        dest = self.quarantine_path(ctx.operation_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = self.candidate_path(ctx.operation_id)
        if not dest.exists() and src.exists():
            shutil.move(str(src), str(dest))
        return PublicationEvidence(
            content_digest=self.digest,
            final_path_rel=str(dest.relative_to(self.root)),
            file_identity="quarantined",
            disposition="quarantined",
        )

    # -- port: registration ----------------------------------------------------------------
    def register_installation(self, ctx) -> RegistrationEvidence:
        publication = (ctx.prior_outputs.get("publish_artifact") or {}).get(
            "publication"
        ) or {}
        quarantined = publication.get("disposition") == "quarantined"
        artifact_id = ("q-" if quarantined else "art-") + self.digest.split(":")[1][:12]
        existing = self.registered.get(ctx.operation_id)
        if existing is None:
            self.counts["registration"] += 1
            if quarantined:
                disposition, alias = "quarantined", None
            elif publication.get("disposition") == "reused":
                disposition, alias = "reused", "imported-model"
            else:
                disposition, alias = "installed", "imported-model"
            existing = {
                "artifact_id": artifact_id,
                "alias": alias,
                "disposition": disposition,
            }
            self.registered[ctx.operation_id] = existing
        return RegistrationEvidence(**existing)

    def probe_registration(self, ctx) -> ProbeResult:
        existing = self.registered.get(ctx.operation_id)
        if existing is None:
            return ProbeResult(RecoveryClass.ABSENT, "NOT_REGISTERED")
        return ProbeResult(
            RecoveryClass.COMPLETE,
            "REGISTERED",
            {"registration": asdict(RegistrationEvidence(**existing))},
        )

    # -- port: cleanup/compensation ------------------------------------------------------------
    def finalize_owned_staging(self, ctx) -> CleanupEvidence:
        staging = self.staging_dir(ctx.operation_id)
        removed: list[str] = []
        if staging.exists():
            shutil.rmtree(staging)
            removed.append(staging.name)
        for reservation in self.reservations.values():
            reservation["released"] = True
        return CleanupEvidence(
            removed_paths=tuple(removed),
            retained_paths=(),
            reservation_released=True,
        )

    def probe_finalization(self, ctx) -> ProbeResult:
        released = all(r["released"] for r in self.reservations.values())
        gone = not self.staging_dir(ctx.operation_id).exists()
        if released and gone:
            return ProbeResult(RecoveryClass.COMPLETE, "FINALIZED")
        return ProbeResult(RecoveryClass.REVERTIBLE, "CLEANUP_INCOMPLETE")

    def compensate_acquisition(self, ctx) -> dict[str, Any]:
        # U1.1 §3.2: published content-addressed artifacts are FORWARD-ONLY
        # safe effects. Compensation unregisters only this operation's
        # registration row and never deletes valid managed bytes.
        self.counts["compensation"] += 1
        self.registered.pop(ctx.operation_id, None)
        self.compensated.add(ctx.operation_id)
        return {}

    def release_on_cancellation(
        self, request, *, operation_id: str = ""
    ) -> dict[str, Any]:
        """§3.4: release the reservation; retain the resumable partial."""
        for reservation in self.reservations.values():
            reservation["released"] = True
        staging_root = self.root / "staging"
        retained = 0
        if operation_id:
            op_dir = staging_root / operation_id
            if op_dir.exists():
                for p in op_dir.rglob("source.partial"):
                    retained += p.stat().st_size
        else:
            if staging_root.exists():
                for p in staging_root.rglob("source.partial"):
                    retained += p.stat().st_size
        return {"retained_bytes": retained}

    def observe_compensation(self, ctx) -> ProbeResult:
        return ProbeResult(RecoveryClass.COMPLETE, "COMPENSATED")
