"""Explicit EXP-4 maintenance checks with redacted durable observations."""

from __future__ import annotations

from typing import Any, Callable

from .legacy_import import utcnow
from .doctor import FINDING_IDS
from .maintenance_center import (
    CRITICAL_FREE_BYTES,
    MAINTENANCE_SNAPSHOT_SCHEMA_VERSION,
    MaintenanceCenterQueryService,
)
from .repositories import ObservationRepository


_CONNECTION_CHECK_IDS = frozenset({
    "model", "gateway", "credential", "local-authorized",
    "local-unauthorized", "tailscale-dns", "serve-mappings",
    "funnel-disabled", "tailnet-unauthorized", "tailnet-models",
    "tailnet-stream",
})


class MaintenanceCheckService:
    """Run requested probes and retain only closed privacy-safe evidence."""

    def __init__(
        self,
        units: Any,
        *,
        snapshot: MaintenanceCenterQueryService,
        doctor: Any,
        storage: Any,
        connection_observer: Callable[[], Any] | None = None,
        services_observer: Callable[[], dict[str, Any]] | None = None,
        thermal_reader: Callable[[], float | None] | None = None,
        notification_observer: Callable[[dict[str, Any], str], Any] | None = None,
        clock: Callable[[], str] = utcnow,
    ) -> None:
        self._units = units
        self._snapshot = snapshot
        self._doctor = doctor
        self._storage = storage
        self._connection_observer = connection_observer
        self._services_observer = services_observer
        self._thermal_reader = thermal_reader
        self._notification_observer = notification_observer
        self._clock = clock

    @staticmethod
    def _safe(call: Callable[[], Any] | None) -> tuple[Any, bool]:
        if call is None:
            return None, False
        try:
            return call(), True
        except Exception:  # noqa: BLE001 - retain no exception text
            return None, False

    def run(self) -> dict[str, Any]:
        observed_at = self._clock()
        doctor_report, doctor_ok = self._safe(self._doctor.run)
        storage_report, storage_ok = self._safe(self._storage.report)
        connection, connection_ok = self._safe(self._connection_observer)
        services, services_ok = self._safe(self._services_observer)
        temperature, thermal_ok = self._safe(self._thermal_reader)

        findings: list[dict[str, Any]] = []
        if doctor_ok:
            for item in tuple(getattr(doctor_report, "findings", ()))[:32]:
                finding_id = str(getattr(item, "id", ""))
                severity = str(getattr(item, "severity", ""))
                if finding_id in FINDING_IDS and severity in {
                    "PASS", "INFO", "WARN", "FAIL"
                }:
                    findings.append({"id": finding_id, "severity": severity})
        by_id = {item["id"]: item["severity"] for item in findings}
        doctor_payload = {
            "completed": doctor_ok,
            "overall": str(getattr(doctor_report, "overall", "UNAVAILABLE"))
            if doctor_ok else "UNAVAILABLE",
            "findings": findings,
        }
        inference_payload = {
            "ok": doctor_ok and by_id.get("INFERENCE") == "PASS"
        }
        topology_payload = {
            "safe": doctor_ok and by_id.get("INSECURE_TOPOLOGY") == "PASS"
        }

        storage_payload = {
            "available": storage_ok,
            "free_bytes": max(0, int((storage_report or {}).get("free_bytes") or 0)),
            "total_bytes": max(0, int((storage_report or {}).get("total_bytes") or 0)),
            "critical": bool(
                storage_ok and int((storage_report or {}).get("free_bytes") or 0)
                < CRITICAL_FREE_BYTES
            ),
            "reclaimable_bytes": max(
                0, int((storage_report or {}).get("reclaimable_bytes") or 0)
            ),
        }

        connection_dict = (
            connection.to_dict() if hasattr(connection, "to_dict")
            else connection if isinstance(connection, dict) else {}
        )
        checks = connection_dict.get("checks") or []
        sharing = connection_dict.get("sharing")
        sharing = sharing if isinstance(sharing, dict) else {}
        connection_payload = {
            "available": connection_ok,
            "ready": bool(connection_dict.get("ready")),
            "failed_checks": [
                str(item.get("id")) for item in checks[:16]
                if isinstance(item, dict)
                and str(item.get("id")) in _CONNECTION_CHECK_IDS
                and not bool(item.get("passed"))
            ],
            "safe": sharing.get("public_funnel") is False if connection_ok else False,
        }

        raw_services = services if isinstance(services, dict) else {}
        services_payload = {
            key: bool(raw_services.get(key))
            for key in ("model_server", "openwebui", "tailscale", "sharing")
        }
        services_payload["available"] = services_ok
        thermal_payload = {
            "sensor_available": bool(thermal_ok and temperature is not None),
            "temperature_c": (
                round(max(-40.0, min(150.0, float(temperature))), 1)
                if thermal_ok and temperature is not None else None
            ),
        }

        observations = {
            "last_doctor_report": doctor_payload,
            "last_inference_probe": inference_payload,
            "last_topology_probe": topology_payload if not connection_ok
            else connection_payload,
            "last_storage_check": storage_payload,
            "last_optional_services_probe": services_payload,
            "last_thermal_sample": thermal_payload,
        }
        stale_by_key = {
            "last_doctor_report": not doctor_ok,
            "last_inference_probe": not doctor_ok,
            "last_topology_probe": not (connection_ok or doctor_ok),
            "last_storage_check": not storage_ok,
            "last_optional_services_probe": not services_ok,
            "last_thermal_sample": not thermal_ok,
        }
        with self._units.begin() as conn:
            repository = ObservationRepository(conn)
            for key, value in observations.items():
                repository.set(
                    key, value, observed_at=observed_at,
                    stale=stale_by_key[key],
                )

        snapshot = self._snapshot.snapshot().to_dict()
        notifications: Any = ()
        if self._notification_observer is not None:
            try:
                notifications = self._notification_observer(snapshot, observed_at)
            except Exception:  # noqa: BLE001 - check truth is already committed
                notifications = ()
        return {
            "schema_version": MAINTENANCE_SNAPSHOT_SCHEMA_VERSION,
            "checked_at": observed_at,
            "doctor_overall": doctor_payload["overall"],
            "sources_completed": {
                "doctor": doctor_ok,
                "storage": storage_ok,
                "connections": connection_ok,
                "optional_services": services_ok,
                "thermal": thermal_ok,
                "application_update": False,
            },
            "notification_outcomes": list(notifications or ()),
            "snapshot": snapshot,
        }


__all__ = ["MaintenanceCheckService"]
