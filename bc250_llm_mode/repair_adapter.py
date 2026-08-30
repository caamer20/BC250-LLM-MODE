"""Production bindings for the closed EXP-5 repair command service.

This adapter composes existing typed services and durable operations.  It is
the only place that maps repair IDs to effects; it has no dynamic routing,
imports, command strings, or widget dependencies.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

from .legacy_import import utcnow
from .operations.model import OperationState, TERMINAL_STATES
from .operations.repositories import (
    LeaseRepository,
    OperationRepository,
    WorkerLockRepository,
)
from .repair_commands import (
    RepairBinding,
    RepairObservation,
    RepairOwnerResult,
    RepairProbe,
)


class ApplicationRepairAdapter:
    def __init__(self, application: Any, *, clock=utcnow) -> None:
        self._app = application
        self._units = application.units
        self._clock = clock

    def bindings(self) -> dict[str, RepairBinding]:
        """Exhaustive, reviewable ID mapping (ADR 012 D1)."""
        return {
            "retry-legacy-import": self._binding("retry-legacy-import"),
            "upgrade-newer-schema": self._binding("upgrade-newer-schema"),
            "inspect-verified-backup": self._binding("inspect-verified-backup"),
            "reclaim-orphaned-content": self._binding("reclaim-orphaned-content"),
            "release-expired-worker-locks": self._binding(
                "release-expired-worker-locks"),
            "recover-durable-operation": self._binding(
                "recover-durable-operation"),
            "regenerate-runtime-handoff": self._binding(
                "regenerate-runtime-handoff"),
            "restore-known-good-lineage": self._binding(
                "restore-known-good-lineage"),
            "rotate-gateway-credentials": self._binding(
                "rotate-gateway-credentials"),
            "revoke-gateway-credentials": self._binding(
                "revoke-gateway-credentials"),
            "disable-unsafe-sharing": self._binding("disable-unsafe-sharing"),
            "quarantine-invalid-model": self._binding(
                "quarantine-invalid-model"),
            "rebuild-service-launcher": self._binding(
                "rebuild-service-launcher"),
            "return-to-desktop": self._binding("return-to-desktop"),
            "rebuild-support-bundle": self._binding("rebuild-support-bundle"),
        }

    def _binding(self, action_id: str) -> RepairBinding:
        return RepairBinding(
            action_id,
            partial(self.observe, action_id),
            partial(self.execute, action_id),
            partial(self.verify, action_id),
        )

    # -- observation ------------------------------------------------------

    def observe(self, action_id: str, target: str | None) -> RepairObservation:
        if action_id not in {
            "retry-legacy-import", "upgrade-newer-schema",
            "inspect-verified-backup", "rebuild-support-bundle",
        }:
            with self._units.read() as conn:
                from .repositories import SettingsRepository
                acknowledged = bool(SettingsRepository(conn).get(
                    "disclaimer_ack", False))
            if not acknowledged:
                return RepairObservation(
                    evidence={"safety_acknowledged": False},
                    reason_code="SAFETY_ACKNOWLEDGMENT_REQUIRED",
                )
        if action_id == "retry-legacy-import":
            return RepairObservation(reason_code="OPERATIONAL_PROFILE_PRESENT")
        if action_id == "upgrade-newer-schema":
            return RepairObservation(reason_code="SIGNED_UPDATE_CHANNEL_UNAVAILABLE")
        if action_id == "inspect-verified-backup":
            if target is None:
                return RepairObservation(reason_code="TARGET_REQUIRED")
            checked = self._app.backup.verify_backup(target)
            conditions = {"verified_backup_available"} if checked.get("valid") else set()
            return RepairObservation(
                frozenset(conditions), evidence={
                    "found": bool(checked.get("found")),
                    "valid": bool(checked.get("valid")),
                }, reason_code=None if conditions else "BACKUP_NOT_VERIFIED",
            )
        if action_id == "reclaim-orphaned-content":
            # Commit 23 replaces this fail-closed bridge with the durable
            # cleanup preview owner. Existing dry-run suggestions are not
            # positive abandoned-operation evidence.
            return RepairObservation(reason_code="CLEANUP_OWNER_NOT_READY")
        if action_id == "release-expired-worker-locks":
            with self._units.read() as conn:
                lock = WorkerLockRepository(conn, clock=self._clock).get()
            expired = bool(lock and lock["expires_at"] <= self._clock())
            return RepairObservation(
                frozenset({"expired_worker_locks"} if expired else ()),
                (("worker-lock", int(lock["lease_revision"])),) if lock else (),
                {"expired": expired, "present": lock is not None},
                None if expired else "NO_EXPIRED_WORKER_LOCK",
            )
        if action_id == "recover-durable-operation":
            if target is None:
                return RepairObservation(reason_code="TARGET_REQUIRED")
            with self._units.read() as conn:
                record = OperationRepository(conn).get(target)
                leases = LeaseRepository(conn).leases_for_operation(target)
            if record is None:
                return RepairObservation(reason_code="OPERATION_UNKNOWN")
            now = self._clock()
            expired = all(item.expires_at <= now for item in leases)
            conditions = set()
            if record.active:
                conditions.add("operation_interrupted")
            if record.state is not OperationState.RECOVERY_REQUIRED:
                conditions.add("operation_policy_recoverable")
            if expired:
                conditions.add("operation_leases_expired")
            revisions = [("operation", record.state_revision)]
            revisions.extend(
                (f"lease:{index}", item.lease_revision)
                for index, item in enumerate(leases)
            )
            reason = None
            if record.state is OperationState.RECOVERY_REQUIRED:
                reason = "RECOVERY_BARRIER_MANUAL"
            elif not record.active:
                reason = "ALREADY_TERMINAL"
            elif not expired:
                reason = "LEASE_HELD"
            return RepairObservation(
                frozenset(conditions), tuple(revisions),
                {"state": record.state.value, "lease_count": len(leases)}, reason,
            )
        if action_id == "regenerate-runtime-handoff":
            from .runtime_handoff import RuntimeHandoffRenderer, runtime_fingerprint

            state = self._app.read_model()
            current = self._app.runtime_config.current()
            renderer = RuntimeHandoffRenderer(self._app.paths.app_dir)
            expected = runtime_fingerprint(state)
            stale = renderer.stored_fingerprint() != expected
            known_good = self._app.runtime_config.known_good()
            conditions = set()
            if stale:
                conditions.add("handoff_missing_or_stale")
            if current.get("model_alias") and known_good:
                conditions.add("runtime_active_verified")
            reason = None if len(conditions) == 2 else (
                "HANDOFF_CURRENT" if not stale else "RUNTIME_NOT_VERIFIED"
            )
            return RepairObservation(
                frozenset(conditions), (("runtime-config", int(current["revision"])),),
                {"stale": stale, "known_good": bool(known_good)}, reason,
            )
        if action_id == "restore-known-good-lineage":
            status = self._app.runtime_lifecycle.status()
            ready = bool(
                status.get("rollback_available")
                and not status.get("recovery_barrier")
                and not status.get("active_operation")
            )
            promoted = (status.get("promoted") or {}).get("build_id") or "none"
            rollback = (status.get("rollback") or {}).get("build_id") or "none"
            return RepairObservation(
                frozenset({"known_good_lineage_present"} if ready else ()),
                (("runtime-generation", int(status.get("generation") or 0)),),
                {"promoted": promoted, "rollback": rollback},
                None if ready else "ROLLBACK_NOT_AVAILABLE",
            )
        if action_id in {
            "rotate-gateway-credentials", "revoke-gateway-credentials",
        }:
            if target is None:
                return RepairObservation(reason_code="TARGET_REQUIRED")
            clients = {
                item["client_id"]: item
                for item in self._app.connection_credentials.list_clients(
                    include_revoked=True)
            }
            client = clients.get(target)
            ready = bool(client and not client.get("revoked_at"))
            return RepairObservation(
                frozenset({"gateway_provisioned"} if ready else ()),
                (("client", int(client["revision"])),) if client else (),
                {
                    "present": client is not None,
                    "active": ready,
                    "generation": int(client.get("active_generation") or 0)
                    if client else 0,
                }, None if ready else "CLIENT_NOT_ACTIVE",
            )
        if action_id == "disable-unsafe-sharing":
            state = self._app.read_model()
            status = self._app.sharing.status(state, self._app.runner())
            unsafe = bool(
                status.get("public_funnel")
                or (state.get("https_sharing_enabled") and not status.get("enabled"))
            )
            with self._units.read() as conn:
                from .repositories import SettingsRepository
                revision = SettingsRepository(conn).revision()
            return RepairObservation(
                frozenset({"sharing_enabled_unsafe"} if unsafe else ()),
                (("settings", revision),),
                {"unsafe": unsafe, "funnel": bool(status.get("public_funnel"))},
                None if unsafe else "SHARING_NOT_UNSAFE",
            )
        if action_id == "quarantine-invalid-model":
            if target is None:
                return RepairObservation(reason_code="TARGET_REQUIRED")
            plan = self._app.model_remove.dry_run(target)
            with self._units.read() as conn:
                row = conn.execute(
                    "SELECT i.validation_status, a.storage_state, a.trust_state "
                    "FROM model_installations i LEFT JOIN model_artifacts a "
                    "ON a.id = i.artifact_id WHERE i.alias = ?", (target,),
                ).fetchone()
                revision = conn.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM model_installations"
                ).fetchone()[0]
            invalid = bool(
                row and row["storage_state"] == "MANAGED"
                and row["trust_state"] != "VERIFIED"
                and plan.get("removable")
            )
            return RepairObservation(
                frozenset({"managed_model_invalid"} if invalid else ()),
                (("model-set", int(revision)),),
                {"found": bool(row), "managed_invalid": invalid},
                None if invalid else "MODEL_NOT_ELIGIBLE_FOR_QUARANTINE",
            )
        if action_id == "rebuild-service-launcher":
            state = self._app.read_model()
            launcher = self._app.paths.app_dir / "run-model.sh"
            service = Path("/etc/systemd/system") / str(
                state.get("service_name") or "bc250-llm.service")
            stale = not launcher.is_file() or not service.is_file()
            with self._units.read() as conn:
                from .repositories import SettingsRepository
                revision = SettingsRepository(conn).revision()
            return RepairObservation(
                frozenset({"service_files_stale"} if stale else ()),
                (("settings", revision),),
                {"launcher_missing": not launcher.is_file(),
                 "unit_missing": not service.is_file()},
                None if stale else "SERVICE_FILES_CURRENT",
            )
        if action_id == "return-to-desktop":
            platform = self._app.platform.status()
            available = platform.get("service_manager") == "systemd"
            with self._units.read() as conn:
                from .repositories import SettingsRepository
                revision = SettingsRepository(conn).revision()
            return RepairObservation(
                frozenset({"desktop_return_available"} if available else ()),
                (("settings", revision),),
                {"systemd": available},
                None if available else "UNSUPPORTED_PLATFORM",
            )
        if action_id == "rebuild-support-bundle":
            if target is None:
                return RepairObservation(reason_code="TARGET_REQUIRED")
            destination = self._support_root() / target
            return RepairObservation(
                evidence={"destination_available": not destination.exists()},
                reason_code="TARGET_EXISTS" if destination.exists() else None,
            )
        raise KeyError(action_id)

    # -- execution --------------------------------------------------------

    def execute(
        self, action_id: str, target: str | None, observation: RepairObservation
    ) -> RepairOwnerResult:
        revisions = dict(observation.expected_revisions)
        if action_id in {"retry-legacy-import", "upgrade-newer-schema"}:
            return RepairOwnerResult("REFUSED", "OWNER_UNAVAILABLE")
        if action_id == "inspect-verified-backup":
            valid = bool(target and self._app.backup.verify_backup(target).get("valid"))
            return RepairOwnerResult(
                "SUCCEEDED" if valid else "REFUSED",
                "BACKUP_VERIFIED" if valid else "BACKUP_NOT_VERIFIED",
            )
        if action_id == "reclaim-orphaned-content":
            cleanup = getattr(self._app, "storage_cleanup", None)
            if cleanup is None:
                return RepairOwnerResult("REFUSED", "CLEANUP_OWNER_NOT_READY")
            outcome = cleanup.quarantine_target(target, requested_by="repair")
            return self._owner_from(outcome, "CLEANUP_QUARANTINE_REQUESTED")
        if action_id == "release-expired-worker-locks":
            with self._units.begin() as conn:
                repo = WorkerLockRepository(conn, clock=self._clock)
                current = repo.require()
                if int(current["lease_revision"]) != revisions["worker-lock"]:
                    raise RuntimeError("stale worker lock")
                claimed = repo.acquire(owner="repair-command", ttl_seconds=30)
                repo.release(
                    owner="repair-command",
                    expected_revision=int(claimed["lease_revision"]),
                )
            return RepairOwnerResult(
                "SUCCEEDED", "WORKER_LOCK_RELEASED",
                owner_revision=int(claimed["lease_revision"]),
            )
        if action_id == "recover-durable-operation":
            result = self._app.operation_commands.recover(target, confirm=True)
            return RepairOwnerResult(
                "SUCCEEDED" if result.ok else (
                    "RECOVERY_REQUIRED" if result.state == "RECOVERY_REQUIRED"
                    else "REFUSED"),
                result.reason_code, result.operation_id,
            )
        if action_id == "regenerate-runtime-handoff":
            result = self._app.runtime_config.regenerate_handoff(
                expected_revision=revisions["runtime-config"])
            return RepairOwnerResult(
                "SUCCEEDED" if result["published"] else "REFUSED",
                result["code"], owner_revision=result["revision"],
            )
        if action_id == "restore-known-good-lineage":
            result = self._app.runtime_lifecycle.rollback(
                requested_by="repair",
                target_build_id=str(observation.evidence["rollback"]),
                expected_active_build_id=str(observation.evidence["promoted"]),
            )
            return RepairOwnerResult(
                "SUCCEEDED" if result.ok else (
                    "RECOVERY_REQUIRED" if result.status == "RECOVERY_REQUIRED"
                    else "REFUSED"),
                result.status, result.operation_id,
            )
        if action_id == "rotate-gateway-credentials":
            result = self._app.connection_credentials.rotate_client(
                target, expected_revision=revisions["client"], overlap_seconds=300,
            )
            return RepairOwnerResult(
                "SUCCEEDED", "CLIENT_GENERATION_ROTATED",
                owner_revision=int(result.client["revision"]),
                one_time_secret=result.secret,
            )
        if action_id == "revoke-gateway-credentials":
            result = self._app.connection_credentials.revoke_client(
                target, expected_revision=revisions["client"])
            return RepairOwnerResult(
                "SUCCEEDED", "CLIENT_REVOKED",
                owner_revision=int(result.client["revision"]),
                prior_state_survives=False,
            )
        if action_id == "disable-unsafe-sharing":
            state = self._app.read_model()
            self._app.sharing.emergency_disable(state, self._app.runner())
            return RepairOwnerResult("SUCCEEDED", "SHARING_DISABLED")
        if action_id == "quarantine-invalid-model":
            result = self._app.model_remove.remove(target, requested_by="repair")
            return RepairOwnerResult(
                "SUCCEEDED" if result.ok else (
                    "RECOVERY_REQUIRED" if result.status == "RECOVERY_REQUIRED"
                    else "REFUSED"),
                result.status, result.operation_id,
            )
        if action_id == "rebuild-service-launcher":
            from .server import install_service

            state = self._app.read_model()
            install_service(state, self._app.runner(), enable_and_start=False)
            return RepairOwnerResult("SUCCEEDED", "SERVICE_FILES_REBUILT")
        if action_id == "return-to-desktop":
            state = self._app.read_model()
            self._app.host_mode.return_to_desktop(
                state, self._app.runner(), activate_now=False)
            return RepairOwnerResult("SUCCEEDED", "DESKTOP_RETURN_REQUESTED")
        if action_id == "rebuild-support-bundle":
            destination = self._support_root() / str(target)
            manifest = self._app.support_bundle.build(destination)
            return RepairOwnerResult(
                "SUCCEEDED", "SUPPORT_BUNDLE_BUILT",
                owner_revision=len(manifest.files),
            )
        raise KeyError(action_id)

    # -- verification -----------------------------------------------------

    def verify(
        self, action_id: str, target: str | None,
        prior: RepairObservation | None,
    ) -> RepairProbe:
        if action_id in {"retry-legacy-import", "upgrade-newer-schema",
                         "reclaim-orphaned-content"}:
            return RepairProbe(False, "OWNER_UNAVAILABLE")
        if action_id == "inspect-verified-backup":
            passed = bool(target and self._app.backup.verify_backup(target).get("valid"))
            return RepairProbe(passed, "BACKUP_VERIFIED" if passed else "BACKUP_INVALID")
        if action_id == "release-expired-worker-locks":
            with self._units.read() as conn:
                absent = WorkerLockRepository(conn, clock=self._clock).get() is None
            return RepairProbe(absent, "WORKER_LOCK_RELEASED" if absent else "WORKER_LOCK_PRESENT")
        if action_id == "recover-durable-operation":
            with self._units.read() as conn:
                record = OperationRepository(conn).get(str(target))
            passed = bool(record and record.state in TERMINAL_STATES
                          and record.state is not OperationState.RECOVERY_REQUIRED)
            return RepairProbe(
                passed,
                "OPERATION_RECOVERY_VERIFIED" if passed else "OPERATION_NOT_RECOVERED",
            )
        if action_id == "regenerate-runtime-handoff":
            from .runtime_handoff import RuntimeHandoffRenderer, runtime_fingerprint

            state = self._app.read_model()
            current = self._app.runtime_config.current()
            stored = RuntimeHandoffRenderer(self._app.paths.app_dir).observe()
            passed = bool(
                stored and stored.get("runtime_fingerprint") == runtime_fingerprint(state)
                and int(stored.get("config_revision", -1)) == int(current["revision"])
            )
            return RepairProbe(passed, "HANDOFF_MATCHES_RUNTIME" if passed else "HANDOFF_MISMATCH")
        if action_id == "restore-known-good-lineage":
            status = self._app.runtime_lifecycle.status()
            expected = (prior.evidence.get("rollback") if prior else None)
            promoted = (status.get("promoted") or {}).get("build_id")
            passed = bool(
                promoted and not status.get("recovery_barrier")
                and not status.get("active_operation")
                and (expected is None or promoted == expected)
            )
            return RepairProbe(passed, "KNOWN_GOOD_LINEAGE_ACTIVE" if passed else "ROLLBACK_NOT_VERIFIED")
        if action_id in {
            "rotate-gateway-credentials", "revoke-gateway-credentials",
        }:
            records = {
                item["client_id"]: item
                for item in self._app.connection_credentials.list_clients(
                    include_revoked=True)
            }
            current = records.get(str(target))
            expected_revision = dict(prior.expected_revisions).get("client") \
                if prior else None
            changed = bool(
                current and (expected_revision is None
                             or int(current["revision"]) > expected_revision)
            )
            revoked = bool(current and current.get("revoked_at"))
            passed = changed and (
                revoked if action_id == "revoke-gateway-credentials" else not revoked
            )
            code = "CLIENT_REVOKED" if action_id.startswith("revoke") \
                else "CLIENT_GENERATION_ROTATED"
            return RepairProbe(passed, code if passed else "CLIENT_CHANGE_NOT_VERIFIED",
                               prior_state_survives=not revoked)
        if action_id == "disable-unsafe-sharing":
            state = self._app.read_model()
            status = self._app.sharing.status(state, self._app.runner())
            passed = not status.get("webui_enabled") \
                and not status.get("api_enabled") \
                and not status.get("public_funnel")
            return RepairProbe(passed, "SHARING_DISABLED" if passed else "SHARING_STILL_VISIBLE")
        if action_id == "quarantine-invalid-model":
            with self._units.read() as conn:
                alias = conn.execute(
                    "SELECT 1 FROM model_installations WHERE alias = ?", (target,),
                ).fetchone()
            passed = alias is None
            return RepairProbe(passed, "MODEL_QUARANTINE_VERIFIED" if passed else "MODEL_ALIAS_PRESENT")
        if action_id == "rebuild-service-launcher":
            state = self._app.read_model()
            launcher = self._app.paths.app_dir / "run-model.sh"
            unit = Path("/etc/systemd/system") / str(
                state.get("service_name") or "bc250-llm.service")
            passed = launcher.is_file() and unit.is_file()
            return RepairProbe(passed, "SERVICE_FILES_VERIFIED" if passed else "SERVICE_FILES_STALE")
        if action_id == "return-to-desktop":
            state = self._app.read_model()
            passed = state.get("boot_policy", "desktop") == "desktop" \
                and not bool(state.get("llm_autostart"))
            return RepairProbe(passed, "DESKTOP_NEXT_BOOT_VERIFIED" if passed else "DESKTOP_RETURN_NOT_VERIFIED")
        if action_id == "rebuild-support-bundle":
            result = self._app.support_bundle.verify(self._support_root() / str(target))
            return RepairProbe(bool(result["valid"]), str(result["code"]))
        raise KeyError(action_id)

    def _support_root(self) -> Path:
        return self._app.paths.app_dir / "support-bundles"

    @staticmethod
    def _owner_from(outcome: Any, fallback: str) -> RepairOwnerResult:
        status = str(getattr(outcome, "status", "REFUSED"))
        operation_id = getattr(outcome, "operation_id", None)
        return RepairOwnerResult(
            "SUCCEEDED" if bool(getattr(outcome, "ok", False)) else (
                "RECOVERY_REQUIRED" if status == "RECOVERY_REQUIRED" else "REFUSED"
            ),
            status or fallback,
            operation_id,
        )


def build_application_repair_bindings(
    application: Any, *, clock=utcnow,
) -> dict[str, RepairBinding]:
    return ApplicationRepairAdapter(application, clock=clock).bindings()


__all__ = ["ApplicationRepairAdapter", "build_application_repair_bindings"]
