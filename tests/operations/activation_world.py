"""Fake MODEL_ACTIVATE host world (Session 5C plan §6/§10/§14).

Real durable on-disk effect state shared by every executor instance: the
candidate artifact file, desired configuration, published handoff, fake
server process state, and the known-good row. The host implements the
typed ``ActivationHost`` port idempotently — every mutator classifies
reality through its read-only probe first. No systemd, HTTP, Vulkan, or
production imports.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from bc250_llm_mode.fsops import atomic_write_text
from bc250_llm_mode.operations.activation import (
    CandidateRuntimeV1,
    ConfigEvidenceV1,
    HandoffEvidenceV1,
    HealthEvidenceV1,
    InferenceEvidenceV1,
    KnownGoodEvidenceV1,
    ModelActivateRequestV1,
    PriorRuntimeSnapshotV1,
    RestartEvidenceV1,
    RestorationEvidenceV1,
)
from bc250_llm_mode.operations.recovery import RecoveryClass
from bc250_llm_mode.operations.workflow import ProbeResult


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class FakeActivationHost:
    """Typed fake implementation of the Session 5C activation port."""

    root: Path

    # -- durable file layout -------------------------------------------------
    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    @property
    def desired_path(self) -> Path:
        return self.root / "desired.json"

    @property
    def handoff_path(self) -> Path:
        return self.root / "runtime-handoff.json"

    @property
    def server_path(self) -> Path:
        return self.root / "server.json"

    @property
    def known_good_path(self) -> Path:
        return self.root / "known-good.json"

    @property
    def prior_snapshot_path(self) -> Path:
        return self.root / "prior-snapshot.json"

    @property
    def effects_path(self) -> Path:
        return self.root / "effects.json"

    def _read(self, path: Path) -> Any:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, path: Path, value: Any) -> None:
        atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True))

    # -- seeding ---------------------------------------------------------------
    def seed_model(self, alias: str, content: str = "gguf-default") -> Path:
        self.models_dir.mkdir(parents=True, exist_ok=True)
        path = self.models_dir / f"{alias}.gguf"
        path.write_text(content, encoding="utf-8")
        return path

    def rewrite_model(self, alias: str, content: str) -> None:
        self.seed_model(alias, content)

    def seed_desired(
        self,
        *,
        model_alias: str | None = None,
        context_per_slot: int = 4096,
        parallel_slots: int = 2,
        revision: int = 1,
    ) -> None:
        self._write(
            self.desired_path,
            {
                "config": {
                    "model_alias": model_alias,
                    "context_per_slot": context_per_slot,
                    "parallel_slots": parallel_slots,
                },
                "revision": revision,
            },
        )

    def seed_stopped_prior(self, alias: str = "model-a") -> None:
        self.seed_desired(model_alias=alias)
        self._write(
            self.server_path,
            {"running": None, "health_ok": False, "infer_ok": False},
        )

    def seed_active_prior(
        self, alias: str = "model-a", *, healthy: bool = True
    ) -> None:
        self.seed_desired(model_alias=alias)
        self._publish_for(alias)
        self._write(
            self.server_path,
            {"running": alias, "health_ok": healthy, "infer_ok": healthy},
        )
        self._write(
            self.known_good_path,
            {
                "model_alias": alias,
                "context": 4096,
                "slots": 2,
                "fingerprint": f"kg-{alias}",
                "component_identity": "fake-build-1",
            },
        )

    def _publish_for(self, alias: str) -> dict[str, Any]:
        desired = self._read(self.desired_path) or {}
        config = desired.get("config") or {}
        payload = {
            "schema_version": 1,
            "config_revision": int(desired.get("revision", 1)),
            "runtime_fingerprint": f"fp-{alias}-{desired.get('revision', 1)}",
            "model_id": alias,
            "ctx_total": int(config.get("context_per_slot", 4096))
            * int(config.get("parallel_slots", 2)),
            "slots": int(config.get("parallel_slots", 2)),
        }
        self._write(self.handoff_path, payload)
        self._record_effect(f"seed:{payload['runtime_fingerprint']}", "publish")
        return payload

    def _record_effect(self, effect_id: str, kind: str) -> bool:
        """Record an external effect idempotently; True iff newly applied."""
        effects = self._read(self.effects_path) or {"applied": [], "counts": {}}
        key = f"{kind}:{effect_id}"
        if key in effects["applied"]:
            return False
        effects["applied"].append(key)
        effects["counts"][kind] = int(effects["counts"].get(kind, 0)) + 1
        self._write(self.effects_path, effects)
        return True

    # -- convenience views -------------------------------------------------------
    def effect_count(self, kind: str) -> int:
        effects = self._read(self.effects_path) or {"counts": {}}
        return int(effects.get("counts", {}).get(kind, 0))

    def desired(self) -> dict[str, Any]:
        return self._read(self.desired_path) or {}

    def config(self) -> dict[str, Any]:
        return self.desired().get("config") or {}

    def server(self) -> dict[str, Any]:
        return self._read(self.server_path) or {
            "running": None,
            "health_ok": False,
            "infer_ok": False,
        }

    def known_good(self) -> dict[str, Any] | None:
        return self._read(self.known_good_path)

    def handoff(self) -> dict[str, Any] | None:
        return self._read(self.handoff_path)

    def running_alias(self) -> str | None:
        return self.server().get("running")

    def set_server_running(self, alias: str | None, *, healthy: bool = True) -> None:
        self._write(
            self.server_path,
            {"running": alias, "health_ok": healthy, "infer_ok": healthy},
        )

    def _resolve(self, request: ModelActivateRequestV1) -> CandidateRuntimeV1:
        alias = request.model_alias
        path = self.models_dir / f"{alias}.gguf"
        if not path.exists():
            raise FileNotFoundError(alias)
        config = self.config()
        context = (
            request.context_per_slot
            or int(config.get("context_per_slot", 4096))
        )
        slots = (
            request.parallel_slots or int(config.get("parallel_slots", 2))
        )
        stat = path.stat()
        return CandidateRuntimeV1(
            model_alias=alias,
            canonical_path=str(path),
            quant="q4",
            display_alias=alias.replace("-", " "),
            validation_status="validated",
            provenance_class="managed",
            byte_size=stat.st_size,
            file_identity=f"{stat.st_size}:{stat.st_mtime_ns}",
            content_digest=_digest(path),
            layout_verdict="standard",
            context_per_slot=context,
            parallel_slots=slots,
            settings={"kv_cache_type": "q8_0"},
            fit_verdict="OK",
            fit_detail="fits",
            runtime_fingerprint=f"fp-{alias}",
            component_identity="fake-build-1",
        )

    def _identity_matches(
        self, request: ModelActivateRequestV1, candidate: CandidateRuntimeV1
    ) -> bool:
        try:
            fresh = self._resolve(request)
        except (FileNotFoundError, OSError):
            return False
        return (
            fresh.byte_size == candidate.byte_size
            and fresh.file_identity == candidate.file_identity
            and fresh.content_digest == candidate.content_digest
            and fresh.layout_verdict == candidate.layout_verdict
            and fresh.context_per_slot == candidate.context_per_slot
            and fresh.parallel_slots == candidate.parallel_slots
        )

    # -- port: resolve / observe candidate -------------------------------------
    def resolve_candidate(
        self, request: ModelActivateRequestV1
    ) -> CandidateRuntimeV1:
        return self._resolve(request)

    def observe_candidate(
        self, request: ModelActivateRequestV1, candidate: CandidateRuntimeV1
    ) -> ProbeResult:
        if not (self.models_dir / f"{request.model_alias}.gguf").exists():
            return ProbeResult(
                RecoveryClass.UNCERTAIN_MANUAL, "ARTIFACT_MISSING"
            )
        if self._identity_matches(request, candidate):
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "CANDIDATE_RESOLVED",
                output=asdict(candidate),
            )
        return ProbeResult(
            RecoveryClass.REVERTIBLE, "ARTIFACT_IDENTITY_CHANGED"
        )

    # -- port: capture prior ------------------------------------------------------
    def capture_prior(
        self, request: ModelActivateRequestV1
    ) -> PriorRuntimeSnapshotV1:
        desired = self.desired()
        config = self.config()
        handoff = self.handoff()
        server = self.server()
        running = server.get("running")
        healthy = bool(server.get("health_ok")) and bool(server.get("infer_ok"))
        known_good = self.known_good()
        prior = PriorRuntimeSnapshotV1(
            service_state=(
                "ACTIVE_VERIFIED" if running and healthy else "STOPPED"
                if not running
                else "UNVERIFIED"
            ),
            revision=int(desired.get("revision", 0)),
            config=dict(config),
            handoff_fingerprint=(
                handoff.get("runtime_fingerprint") if handoff else None
            ),
            handoff_payload=handoff,
            known_good=dict(known_good) if known_good else None,
            component_identity="fake-build-1",
            observed_model_alias=running,
            observed_context_total=(
                int(config.get("context_per_slot", 4096))
                * int(config.get("parallel_slots", 2))
                if running
                else None
            ),
            observed_slots=config.get("parallel_slots") if running else None,
            inference_verified=bool(healthy) if running else False,
        )
        # Durable copy for the aggregate restoration protocol.
        self._write(self.prior_snapshot_path, asdict(prior))
        return prior

    # -- port: commit config -------------------------------------------------------
    def commit_candidate(
        self, request: ModelActivateRequestV1, external_effect_id: str
    ) -> ConfigEvidenceV1:
        desired = self.desired()
        expected = request.expected_runtime_revision
        if expected is not None and int(desired.get("revision", 0)) != expected:
            raise RevisionConflict(
                f"expected revision {expected}, found {desired.get('revision')}"
            )
        config = self.config()
        new_config = dict(config)
        new_config["model_alias"] = request.model_alias
        if request.context_per_slot is not None:
            new_config["context_per_slot"] = request.context_per_slot
        if request.parallel_slots is not None:
            new_config["parallel_slots"] = request.parallel_slots
        if self._record_effect(external_effect_id, "config_commit"):
            self._write(
                self.desired_path,
                {
                    "config": new_config,
                    "revision": int(desired.get("revision", 0)) + 1,
                },
            )
        return ConfigEvidenceV1(
            revision=int(self.desired().get("revision", 0)),
            model_alias=new_config["model_alias"],
            context_per_slot=int(new_config.get("context_per_slot", 4096)),
            parallel_slots=int(new_config.get("parallel_slots", 2)),
        )

    def _config_matches_request(
        self,
        request: ModelActivateRequestV1,
        desired: dict[str, Any],
    ) -> bool:
        config = desired.get("config") or {}
        if config.get("model_alias") != request.model_alias:
            return False
        if (
            request.context_per_slot is not None
            and int(config.get("context_per_slot", -1))
            != request.context_per_slot
        ):
            return False
        if (
            request.parallel_slots is not None
            and int(config.get("parallel_slots", -1)) != request.parallel_slots
        ):
            return False
        return True

    def observe_config(
        self,
        request: ModelActivateRequestV1,
        prior: PriorRuntimeSnapshotV1 | None = None,
    ) -> ProbeResult:
        desired = self.desired()
        if self._config_matches_request(request, desired):
            config = desired.get("config") or {}
            evidence = ConfigEvidenceV1(
                revision=int(desired.get("revision", 0)),
                model_alias=config["model_alias"],
                context_per_slot=int(config.get("context_per_slot", 4096)),
                parallel_slots=int(config.get("parallel_slots", 2)),
            )
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "CANDIDATE_CONFIG_COMMITTED",
                output=asdict(evidence),
            )
        snapshot = (
            asdict(prior)
            if prior is not None
            else self._read(self.prior_snapshot_path)
        )
        if snapshot and desired.get("config") == (snapshot.get("config") or {}):
            # Exact prior content (lineage may have moved forward).
            return ProbeResult(RecoveryClass.ABSENT, "EXACT_PRIOR_CONFIG")
        if snapshot is None:
            return ProbeResult(RecoveryClass.ABSENT, "NO_PRIOR_EVIDENCE")
        return ProbeResult(
            RecoveryClass.UNCERTAIN_MANUAL, "UNRELATED_CONFIG_REVISION"
        )

    # -- port: publish handoff ------------------------------------------------------
    def _expected_handoff(self) -> dict[str, Any] | None:
        desired = self.desired()
        config = self.config()
        alias = config.get("model_alias")
        if not alias:
            return None
        return {
            "schema_version": 1,
            "config_revision": int(desired.get("revision", 0)),
            "runtime_fingerprint": f"fp-{alias}-{desired.get('revision', 0)}",
            "model_id": alias,
            "ctx_total": int(config.get("context_per_slot", 4096))
            * int(config.get("parallel_slots", 2)),
            "slots": int(config.get("parallel_slots", 2)),
        }

    def publish_candidate(
        self, candidate: CandidateRuntimeV1, external_effect_id: str
    ) -> HandoffEvidenceV1:
        payload = self._expected_handoff()
        if payload is None:
            raise RuntimeError("no committed candidate config to render")
        self._write(self.handoff_path, payload)
        self._record_effect(f"{external_effect_id}:{payload['config_revision']}", "publish")
        return HandoffEvidenceV1(
            fingerprint=payload["runtime_fingerprint"],
            config_revision=payload["config_revision"],
            model_id=payload["model_id"],
            schema_version=payload["schema_version"],
        )

    def observe_handoff(
        self,
        candidate: CandidateRuntimeV1,
        prior: PriorRuntimeSnapshotV1 | None = None,
    ) -> ProbeResult:
        handoff = self.handoff()
        expected = self._expected_handoff()
        snapshot = (
            asdict(prior)
            if prior is not None
            else self._read(self.prior_snapshot_path)
        )
        if handoff == expected and expected is not None:
            evidence = HandoffEvidenceV1(
                fingerprint=expected["runtime_fingerprint"],
                config_revision=expected["config_revision"],
                model_id=expected["model_id"],
                schema_version=expected["schema_version"],
            )
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "CANDIDATE_HANDOFF_PUBLISHED",
                output=asdict(evidence),
            )
        if expected is None or not isinstance(handoff, dict):
            return ProbeResult(RecoveryClass.ABSENT, "NO_HANDOFF")
        if snapshot and handoff == snapshot.get("handoff_payload"):
            return ProbeResult(RecoveryClass.ABSENT, "EXACT_PRIOR_HANDOFF")
        if handoff.get("model_id") == candidate.model_alias and (
            handoff.get("config_revision") != expected["config_revision"]
            or handoff.get("ctx_total") != expected["ctx_total"]
        ):
            # Own replaceable candidate at a stale revision: safe to re-run.
            return ProbeResult(RecoveryClass.REVERTIBLE, "STALE_CANDIDATE_HANDOFF")
        return ProbeResult(
            RecoveryClass.UNCERTAIN_MANUAL, "THIRD_PARTY_HANDOFF_PAYLOAD"
        )

    # -- port: restart / health / inference --------------------------------------
    def restart_candidate(
        self,
        candidate: CandidateRuntimeV1,
        prior: PriorRuntimeSnapshotV1,
        external_effect_id: str,
    ) -> RestartEvidenceV1:
        server = self.server()
        if server.get("running") == candidate.model_alias and (
            server.get("health_ok") and server.get("infer_ok")
        ):
            return RestartEvidenceV1(restarted_now=False, was_already_active=True)
        self._record_effect(
            f"{external_effect_id}:{candidate.model_alias}", "restart"
        )
        self.set_server_running(candidate.model_alias)
        return RestartEvidenceV1(restarted_now=True, was_already_active=False)

    def _prior_running_alias(self) -> str | None:
        prior = self._read(self.prior_snapshot_path)
        if not prior:
            return None
        if prior.get("service_state") == "STOPPED":
            return None
        return prior.get("observed_model_alias")

    def observe_restart(
        self, candidate: CandidateRuntimeV1, prior: PriorRuntimeSnapshotV1
    ) -> ProbeResult:
        server = self.server()
        running = server.get("running")
        healthy = bool(server.get("health_ok")) and bool(server.get("infer_ok"))
        if running == candidate.model_alias and healthy:
            evidence = RestartEvidenceV1(
                restarted_now=False, was_already_active=True
            )
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "CANDIDATE_RUNTIME_VERIFIED",
                output=asdict(evidence),
            )
        if running is None and prior.service_state == "STOPPED":
            # Restart provably not begun; a stopped prior needs no restart.
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "PRIOR_STOPPED_UNCHANGED",
                output=asdict(
                    RestartEvidenceV1(
                        restarted_now=False, was_already_active=False
                    )
                ),
            )
        prior_alias = self._prior_running_alias() or prior.observed_model_alias
        if running == prior_alias and healthy:
            # Exact prior remains active after an interrupted intent: do not
            # guess whether the service consumed the candidate handoff.
            return ProbeResult(RecoveryClass.REVERTIBLE, "PRIOR_STILL_ACTIVE")
        if running == candidate.model_alias and not healthy:
            return ProbeResult(RecoveryClass.REVERTIBLE, "CANDIDATE_UNHEALTHY")
        return ProbeResult(
            RecoveryClass.UNCERTAIN_MANUAL, "SERVICE_IDENTITY_AMBIGUOUS"
        )

    def check_health(self, candidate: CandidateRuntimeV1) -> HealthEvidenceV1:
        server = self.server()
        config = self.config()
        healthy = (
            server.get("running") == candidate.model_alias
            and bool(server.get("health_ok"))
            and int(config.get("context_per_slot", 4096))
            == candidate.context_per_slot
            and int(config.get("parallel_slots", 2)) == candidate.parallel_slots
        )
        return HealthEvidenceV1(
            healthy=bool(healthy),
            model_alias=candidate.model_alias,
            context_total=candidate.context_per_slot * candidate.parallel_slots,
            slots=candidate.parallel_slots,
        )

    def check_inference(self, candidate: CandidateRuntimeV1) -> InferenceEvidenceV1:
        server = self.server()
        success = (
            server.get("running") == candidate.model_alias
            and bool(server.get("infer_ok"))
        )
        return InferenceEvidenceV1(
            success=bool(success),
            generated_count=1 if success else 0,
            elapsed_bucket="sub_second" if success else "none",
        )

    # -- port: known-good ------------------------------------------------------------
    def promote_known_good(
        self, candidate: CandidateRuntimeV1, external_effect_id: str
    ) -> KnownGoodEvidenceV1:
        row = {
            "model_alias": candidate.model_alias,
            "context": int(candidate.context_per_slot),
            "slots": int(candidate.parallel_slots),
            "fingerprint": f"kg-{candidate.model_alias}",
            "component_identity": candidate.component_identity,
        }
        if self._record_effect(external_effect_id, "promote"):
            self._write(self.known_good_path, row)
        return KnownGoodEvidenceV1(**row)

    def observe_known_good(
        self,
        candidate: CandidateRuntimeV1,
        prior: PriorRuntimeSnapshotV1 | None = None,
    ) -> ProbeResult:
        row = self.known_good()
        expected = KnownGoodEvidenceV1(
            model_alias=candidate.model_alias,
            context=int(candidate.context_per_slot),
            slots=int(candidate.parallel_slots),
            fingerprint=f"kg-{candidate.model_alias}",
            component_identity=candidate.component_identity,
        )
        if row == asdict(expected):
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "KNOWN_GOOD_PROMOTED",
                output=asdict(expected),
            )
        snapshot = (
            asdict(prior)
            if prior is not None
            else self._read(self.prior_snapshot_path)
        )
        if snapshot and row == (snapshot.get("known_good") or {}):
            return ProbeResult(RecoveryClass.ABSENT, "EXACT_PRIOR_KNOWN_GOOD")
        if row is None:
            return ProbeResult(RecoveryClass.ABSENT, "NO_KNOWN_GOOD_ROW")
        return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "THIRD_PARTY_KNOWN_GOOD")

    # -- port: aggregate restoration (plan §9) ----------------------------------------
    def restoration_proven(self, prior: dict[str, Any]) -> bool:
        config = self.config()
        if config != (prior.get("config") or {}):
            return False
        handoff = self.handoff()
        prior_handoff = prior.get("handoff_payload")
        want = dict(prior_handoff) if prior_handoff else None
        if (handoff or None) != want:
            return False
        server = self.server()
        if prior.get("service_state") == "ACTIVE_VERIFIED":
            if server.get("running") != prior.get("observed_model_alias"):
                return False
            if not (server.get("health_ok") and server.get("infer_ok")):
                return False
        elif server.get("running") is not None:
            return False
        kg = self.known_good()
        prior_kg = prior.get("known_good")
        if (kg or None) != (dict(prior_kg) if prior_kg else None):
            return False
        return True

    def restore_prior(
        self,
        prior: PriorRuntimeSnapshotV1,
        candidate: CandidateRuntimeV1,
        restoration_id: str,
    ) -> RestorationEvidenceV1:
        snapshot = asdict(prior) if prior is not None else None
        if not snapshot:
            raise RuntimeError("no durable prior snapshot for restoration")
        service_state = snapshot.get("service_state", "STOPPED")
        if self.restoration_proven(snapshot):
            return RestorationEvidenceV1(
                restored=True,
                stage_codes=["ALREADY_PROVEN"],
                service_state=service_state,
            )
        if not self._record_effect(restoration_id, "restoration"):
            return RestorationEvidenceV1(
                restored=self.restoration_proven(snapshot),
                stage_codes=["RESTORATION_EFFECT_ALREADY_RAN"],
                service_state=service_state,
            )
        stage_codes: list[str] = []
        # 3. Restore prior desired config as a NEW revision (never rewind).
        desired = self.desired()
        restored = dict(desired)
        restored["config"] = dict(snapshot.get("config") or {})
        restored["revision"] = max(
            int(desired.get("revision", 0)), int(snapshot.get("revision", 0))
        ) + 1
        restored["restored_content_of_revision"] = snapshot.get("revision")
        self._write(self.desired_path, restored)
        stage_codes.append("CONFIG_RESTORED")
        # 4. Restore the prior known-good row exactly, including absence.
        prior_kg = snapshot.get("known_good")
        if prior_kg:
            self._write(self.known_good_path, dict(prior_kg))
        elif self.known_good_path.exists():
            self.known_good_path.unlink()
        stage_codes.append("KNOWN_GOOD_RESTORED")
        # 5. Publish the prior handoff exactly, or remove own candidate one.
        prior_handoff = snapshot.get("handoff_payload")
        if prior_handoff:
            self._write(self.handoff_path, dict(prior_handoff))
            stage_codes.append("HANDOFF_RESTORED")
        else:
            if self.handoff_path.exists():
                self.handoff_path.unlink()
            stage_codes.append("HANDOFF_REMOVED")
        # 6/7. Service state through the sole service owner only.
        server = self.server()
        if service_state == "ACTIVE_VERIFIED":
            alias = snapshot.get("observed_model_alias")
            if server.get("running") != alias:
                self._record_effect(f"{restoration_id}:{alias}", "restart")
                self.set_server_running(alias)
                stage_codes.append("SERVICE_RESTARTED")
            if not (
                self.server().get("health_ok") and self.server().get("infer_ok")
            ):
                raise RuntimeError("restored runtime failed verification")
            stage_codes.append("SERVICE_ACTIVE_VERIFIED")
        else:
            if server.get("running") is not None:
                self._record_effect(f"{restoration_id}:stop", "stop")
                self.set_server_running(None, healthy=False)
                stage_codes.append("SERVICE_STOPPED")
        return RestorationEvidenceV1(
            restored=True,
            stage_codes=stage_codes,
            service_state=service_state,
        )

    def observe_restoration(
        self,
        prior: PriorRuntimeSnapshotV1,
        candidate: CandidateRuntimeV1,
    ) -> ProbeResult:
        durable = self._read(self.prior_snapshot_path)
        snapshot = durable or asdict(prior)
        if self.restoration_proven(snapshot):
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "PRIOR_RUNTIME_RESTORED",
                output={
                    "service_state": snapshot.get("service_state", "STOPPED")
                },
            )
        if durable:
            return ProbeResult(RecoveryClass.REVERTIBLE, "RESTORATION_INCOMPLETE")
        return ProbeResult(
            RecoveryClass.UNCERTAIN_MANUAL, "NO_DURABLE_PRIOR_EVIDENCE"
        )
