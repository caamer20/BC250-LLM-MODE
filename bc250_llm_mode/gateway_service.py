"""Package-owned current-boot systemd lifecycle for the gateway runtime."""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import stat
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .fsops import atomic_write_text
from .gateway_runtime import (
    GATEWAY_PORT,
    BridgeObservation,
    GatewayRuntimeError,
    discover_bridge,
)
from .logging_utils import CommandRunner
from .paths import AppPaths
from .privilege import elevated


GATEWAY_SERVICE_SCHEMA_VERSION = 1
GATEWAY_SERVICE_NAME = "bc250-openwebui-gateway.service"
GATEWAY_UNIT_MARKER = "# BC250-LLM-MODE-MANAGED gateway-runtime-v1"
GATEWAY_DIAGNOSTIC_MARKER = "# BC250-LLM-MODE-DIAGNOSTIC gateway-live-repair-v1"
# Exact physical live-repair identities observed 2026-09-01.  These values do
# not authorize a similarly named unit/runner; every byte must match.
KNOWN_DIAGNOSTIC_UNIT_SHA256S = frozenset({
    "93189f4531e60fcae7752183979cb97ec281d7eaff7947ece73fe972cd049a97",
})
KNOWN_DIAGNOSTIC_RUNNER_SHA256S = frozenset({
    "96365758dec9fd81c66545e20b8c69da2d39b66dbe3c0bbfb1ec618b4bc48f71",
})
GATEWAY_RECEIPT_FILENAME = "gateway-service.json"
GATEWAY_LAUNCHER_FILENAME = "run-gateway"
GATEWAY_HEALTH_TIMEOUT_SECONDS = 2.0
GATEWAY_START_TIMEOUT_SECONDS = 15.0
MAX_UNIT_BYTES = 32 * 1024
MAX_HEALTH_BYTES = 16 * 1024
GATEWAY_CONSUMERS = frozenset({"client", "openwebui", "sharing"})


class GatewayServiceError(RuntimeError):
    """A gateway service mutation was refused or failed safely."""


def _sha256(value: bytes | str) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _safe_unit_arg(value: str | Path) -> str:
    text = str(value)
    if not text.startswith("/") or any(char in text for char in ("\n", "\r", "\x00")):
        raise ValueError("systemd argument must be a safe absolute path")
    return '"' + text.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_gateway_launcher(
    *, paths: AppPaths, expected_network_identity: str,
) -> str:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_network_identity):
        raise ValueError("invalid expected network identity")
    current_python = paths.application_current_link / "venv/bin/python"
    installed_python = paths.app_dir / "app-venv/bin/python"
    app_dir = _safe_unit_arg(paths.app_dir)
    identity = expected_network_identity
    # Both interpreter candidates are stable installation paths.  No source
    # checkout, credential, eval, or environment lookup enters this launcher.
    return "\n".join((
        "#!/bin/sh",
        "set -eu",
        f"if [ -x {_safe_unit_arg(current_python)} ]; then",
        f"  exec {_safe_unit_arg(current_python)} -m bc250_llm_mode.gateway_runtime "
        f"--app-dir {app_dir} --expected-network-identity {identity}",
        "fi",
        f"exec {_safe_unit_arg(installed_python)} -m bc250_llm_mode.gateway_runtime "
        f"--app-dir {app_dir} --expected-network-identity {identity}",
        "",
    ))


def render_gateway_unit(
    *, paths: AppPaths, launcher: Path, uid: int | None = None,
) -> str:
    owner_uid = os.getuid() if uid is None else int(uid)
    if owner_uid < 0:
        raise ValueError("invalid service uid")
    identity = ""
    if owner_uid:
        account = pwd.getpwuid(owner_uid)
        identity = f"User={account.pw_name}\nGroup={account.pw_gid}\n"
    app_dir = _safe_unit_arg(paths.app_dir)
    return f"""{GATEWAY_UNIT_MARKER}
# Schema={GATEWAY_SERVICE_SCHEMA_VERSION}
[Unit]
Description=BC250 authenticated OpenAI-compatible gateway
After=bc250-llm.service
StartLimitIntervalSec=60
StartLimitBurst=3

[Service]
Type=simple
{identity}ExecStart={_safe_unit_arg(launcher)}
Restart=on-failure
RestartSec=3
TimeoutStopSec=10
KillMode=mixed
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={app_dir}
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_NETLINK
MemoryMax=256M
TasksMax=64
LimitNOFILE=256
UMask=0077
StandardOutput=journal
StandardError=journal

[Install]
# Deliberately no WantedBy: this is an explicit current-boot service.
"""


@dataclass(frozen=True)
class GatewayServicePlan:
    schema_version: int
    service: str
    unit_path: str
    launcher_path: str
    network_identity: str | None
    existing_identity: str
    mutation_allowed: bool
    current_boot_only: bool
    unit_sha256: str | None
    reason_code: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GatewayService:
    """Typed plan/install/status/start/stop/restart/remove boundary."""

    def __init__(
        self,
        paths: AppPaths,
        *,
        systemd_dir: Path | str = "/etc/systemd/system",
        bridge_observer: Callable[[], BridgeObservation] = discover_bridge,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        listener_observer: Callable[[str, CommandRunner], tuple[str, ...]] | None = None,
        health_observer: Callable[[], dict[str, Any]] | None = None,
        boot_identity: Callable[[], str] | None = None,
    ) -> None:
        self.paths = paths
        self.systemd_dir = Path(systemd_dir)
        self._bridge_observer = bridge_observer
        self._clock = clock
        self._sleep = sleep
        self._listener_observer = listener_observer or self._observe_listeners
        self._health_observer = health_observer or self._observe_health
        self._boot_identity = boot_identity or self._read_boot_identity

    @staticmethod
    def _read_boot_identity() -> str:
        try:
            value = Path("/proc/sys/kernel/random/boot_id").read_text(
                encoding="ascii").strip()
        except OSError:
            return "unknown-boot"
        return value[:128] or "unknown-boot"

    @property
    def unit_path(self) -> Path:
        return self.systemd_dir / GATEWAY_SERVICE_NAME

    @property
    def launcher_path(self) -> Path:
        return self.paths.app_dir / GATEWAY_LAUNCHER_FILENAME

    @property
    def receipt_path(self) -> Path:
        return self.paths.app_dir / GATEWAY_RECEIPT_FILENAME

    def _read_unit(self) -> str | None:
        try:
            raw = self.unit_path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise GatewayServiceError("The existing gateway unit could not be inspected.") from exc
        if len(raw) > MAX_UNIT_BYTES:
            raise GatewayServiceError("The existing gateway unit exceeds its inspection bound.")
        return raw.decode("utf-8", "strict")

    def _read_receipt(self) -> dict[str, Any] | None:
        try:
            raw = self.receipt_path.read_bytes()
        except OSError:
            return None
        if len(raw) > 4096:
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def _existing_identity(self, existing: str | None, desired: str) -> str:
        if existing is None:
            return "absent"
        if existing == desired:
            return "canonical-current"
        receipt = self._read_receipt() or {}
        if (
            existing.startswith(GATEWAY_UNIT_MARKER)
            and receipt.get("unit_sha256") == _sha256(existing)
            and receipt.get("service") == GATEWAY_SERVICE_NAME
        ):
            return "canonical-owned"
        # A diagnostic unit is recognized only when an installer-generated
        # receipt binds its full bytes. A copied marker alone has no authority.
        if (
            existing.startswith(GATEWAY_DIAGNOSTIC_MARKER)
            and receipt.get("diagnostic_unit_sha256") == _sha256(existing)
        ):
            return "recognized-diagnostic"
        if _sha256(existing) in KNOWN_DIAGNOSTIC_UNIT_SHA256S:
            return "recognized-diagnostic"
        return "unknown"

    def _remove_exact_diagnostic_runner(self) -> bool:
        path = self.paths.app_dir / "gateway-runtime.py"
        try:
            info = path.lstat()
            raw = path.read_bytes()
        except OSError:
            return False
        if (
            not stat.S_ISREG(info.st_mode)
            or path.is_symlink()
            or len(raw) > 4096
            or _sha256(raw) not in KNOWN_DIAGNOSTIC_RUNNER_SHA256S
        ):
            return False
        path.unlink()
        return True

    def plan(self) -> GatewayServicePlan:
        try:
            bridge = self._bridge_observer()
        except (GatewayRuntimeError, OSError, ValueError):
            return GatewayServicePlan(
                GATEWAY_SERVICE_SCHEMA_VERSION, GATEWAY_SERVICE_NAME,
                str(self.unit_path), str(self.launcher_path), None, "unobserved",
                False, True, None, "GATEWAY_BRIDGE_UNAVAILABLE")
        launcher = render_gateway_launcher(
            paths=self.paths, expected_network_identity=bridge.identity)
        unit = render_gateway_unit(
            paths=self.paths, launcher=self.launcher_path)
        try:
            existing = self._read_unit()
            identity = self._existing_identity(existing, unit)
        except GatewayServiceError:
            identity = "unknown"
        allowed = identity in {
            "absent", "canonical-current", "canonical-owned", "recognized-diagnostic"
        }
        return GatewayServicePlan(
            GATEWAY_SERVICE_SCHEMA_VERSION, GATEWAY_SERVICE_NAME,
            str(self.unit_path), str(self.launcher_path), bridge.identity,
            identity, allowed, True, _sha256(unit),
            None if allowed else "GATEWAY_UNIT_NOT_OWNED",
        )

    def _write_receipt(self, *, unit: str, bridge: BridgeObservation) -> None:
        prior = self._read_receipt() or {}
        boot_id = self._boot_identity()
        consumers = prior.get("consumers") if prior.get("boot_id") == boot_id else []
        if not isinstance(consumers, list):
            consumers = []
        payload = {
            "schema_version": GATEWAY_SERVICE_SCHEMA_VERSION,
            "service": GATEWAY_SERVICE_NAME,
            "unit_sha256": _sha256(unit),
            "network_identity": bridge.identity,
            "current_boot_only": True,
            "boot_id": boot_id,
            "consumers": sorted(
                item for item in consumers if item in GATEWAY_CONSUMERS),
        }
        atomic_write_text(
            self.receipt_path,
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            mode=0o600,
        )

    def install(self, runner: CommandRunner) -> dict[str, Any]:
        bridge = self._bridge_observer()
        launcher = render_gateway_launcher(
            paths=self.paths, expected_network_identity=bridge.identity)
        unit = render_gateway_unit(paths=self.paths, launcher=self.launcher_path)
        existing = self._read_unit()
        identity = self._existing_identity(existing, unit)
        if identity not in {
            "absent", "canonical-current", "canonical-owned", "recognized-diagnostic"
        }:
            raise GatewayServiceError(
                "An unfamiliar gateway unit owns the managed service name; it was left unchanged."
            )
        if identity == "absent":
            listeners = self._listener_observer(bridge.gateway, runner)
            if listeners:
                raise GatewayServiceError(
                    "Port 9071 already has an unfamiliar listener; it was left unchanged."
                )
        was_active = self._systemd_properties(runner).get("active") is True
        atomic_write_text(self.launcher_path, launcher, mode=0o700)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(unit)
            temporary = handle.name
        try:
            runner.run(elevated([
                "install", "-m", "0644", temporary, str(self.unit_path)]))
        finally:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        runner.run(elevated(["systemctl", "daemon-reload"]))
        runner.run(elevated([
            "systemctl", "disable", GATEWAY_SERVICE_NAME]), check=False)
        self._write_receipt(unit=unit, bridge=bridge)
        if identity == "recognized-diagnostic" and was_active:
            runner.run(elevated([
                "systemctl", "restart", GATEWAY_SERVICE_NAME]))
            self._wait_until_listening(runner, bridge.gateway)
        diagnostic_runner_removed = (
            self._remove_exact_diagnostic_runner()
            if identity == "recognized-diagnostic" else False
        )
        result = self.status(runner)
        result["installed_now"] = identity == "absent"
        result["migrated_diagnostic"] = identity == "recognized-diagnostic"
        result["diagnostic_runner_removed"] = diagnostic_runner_removed
        return result

    def _systemd_properties(self, runner: CommandRunner) -> dict[str, Any]:
        fields = {}
        for prop in (
            "LoadState", "ActiveState", "SubState", "UnitFileState",
            "ExecMainStartTimestamp",
        ):
            result = runner.run([
                "systemctl", "show", GATEWAY_SERVICE_NAME,
                f"--property={prop}", "--value",
            ], check=False, emit_output=False)
            fields[prop] = result.stdout.strip()
        installed = fields["LoadState"] not in {"", "not-found"}
        return {
            "installed": installed,
            "active": installed and fields["ActiveState"] == "active",
            "active_state": fields["ActiveState"] or "unknown",
            "sub_state": fields["SubState"] or "unknown",
            "enabled": fields["UnitFileState"] in {
                "enabled", "enabled-runtime", "static"},
            "unit_file_state": fields["UnitFileState"] or "unknown",
            "invocation_marker": fields["ExecMainStartTimestamp"] or None,
        }

    @staticmethod
    def _observe_health() -> dict[str, Any]:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{GATEWAY_PORT}/health",
                timeout=GATEWAY_HEALTH_TIMEOUT_SECONDS,
            ) as response:
                raw = response.read(MAX_HEALTH_BYTES + 1)
                status = int(response.status)
        except (OSError, urllib.error.URLError):
            return {"responsive": False, "backend_identity": "unavailable"}
        if status != 200 or len(raw) > MAX_HEALTH_BYTES:
            return {"responsive": False, "backend_identity": "unavailable"}
        try:
            payload = json.loads(raw)
        except ValueError:
            return {"responsive": False, "backend_identity": "unavailable"}
        return {
            "responsive": isinstance(payload, Mapping),
            "status": payload.get("status") if isinstance(payload, Mapping) else None,
            "backend_identity": (
                payload.get("backend_identity")
                if isinstance(payload, Mapping) else "unavailable"),
        }

    @staticmethod
    def _observe_listeners(
        bridge_address: str, runner: CommandRunner,
    ) -> tuple[str, ...]:
        result = runner.run([
            "ss", "-H", "-ltn", "sport", "=", f":{GATEWAY_PORT}",
        ], check=False, emit_output=False, max_output_bytes=32 * 1024)
        addresses: list[str] = []
        for line in result.stdout.splitlines()[:32]:
            for token in line.split():
                candidate = token.rsplit(":", 1)
                if len(candidate) == 2 and candidate[1] == str(GATEWAY_PORT):
                    host = candidate[0].strip("[]")
                    if host and host not in addresses:
                        addresses.append(host)
        return tuple(addresses)

    def status(self, runner: CommandRunner) -> dict[str, Any]:
        properties = self._systemd_properties(runner)
        try:
            bridge = self._bridge_observer()
        except (GatewayRuntimeError, OSError, ValueError):
            bridge = None
        listeners = (
            self._listener_observer(bridge.gateway, runner)
            if bridge is not None else ()
        )
        health = self._health_observer() if properties["active"] else {
            "responsive": False, "backend_identity": "unavailable"}
        allowed = {"127.0.0.1"}
        if bridge is not None:
            allowed.add(bridge.gateway)
        listener_set = set(listeners)
        listeners_private = bool(listener_set) and listener_set <= allowed
        listeners_complete = bool(
            bridge is not None
            and listener_set == {"127.0.0.1", bridge.gateway})
        receipt = self._read_receipt() or {}
        consumers = (
            receipt.get("consumers")
            if receipt.get("boot_id") == self._boot_identity() else []
        )
        unit = self._read_unit()
        unit_owned = bool(
            unit and unit.startswith(GATEWAY_UNIT_MARKER)
            and receipt.get("unit_sha256") == _sha256(unit))
        return {
            "schema_version": GATEWAY_SERVICE_SCHEMA_VERSION,
            "service": GATEWAY_SERVICE_NAME,
            **properties,
            "current_boot_only": True,
            "unit_owned": unit_owned,
            "network_identity_current": bool(
                bridge is not None
                and receipt.get("network_identity") == bridge.identity),
            "listener_addresses": list(listeners),
            "listeners_private": listeners_private,
            "listeners_complete": listeners_complete,
            "protocol_ready": bool(
                properties["active"] and unit_owned and listeners_complete
                and health.get("responsive")),
            "backend_identity_verified": (
                health.get("backend_identity") == "verified"),
            "health_status": health.get("status"),
            "current_boot_consumers": sorted(
                item for item in (consumers if isinstance(consumers, list) else [])
                if item in GATEWAY_CONSUMERS),
        }

    def _set_consumers(self, values: set[str]) -> None:
        receipt = self._read_receipt() or {}
        if not receipt or receipt.get("unit_sha256") is None:
            raise GatewayServiceError("The gateway ownership receipt is unavailable.")
        receipt["boot_id"] = self._boot_identity()
        receipt["consumers"] = sorted(values)
        atomic_write_text(
            self.receipt_path,
            json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
            mode=0o600,
        )

    def acquire(self, consumer: str, runner: CommandRunner) -> dict[str, Any]:
        """Start for one explicit current-boot consumer and retain ownership."""
        if consumer not in GATEWAY_CONSUMERS:
            raise ValueError("unknown gateway consumer")
        plan = self.plan()
        if not plan.mutation_allowed:
            raise GatewayServiceError(
                "The managed gateway service cannot be installed safely.")
        if plan.existing_identity != "canonical-current":
            self.install(runner)
        status = self.status(runner)
        if not status.get("active"):
            status = self.start(runner)
        consumers = set(status.get("current_boot_consumers") or ())
        consumers.add(consumer)
        self._set_consumers(consumers)
        result = self.status(runner)
        result["acquired_for"] = consumer
        return result

    def release(self, consumer: str, runner: CommandRunner) -> dict[str, Any]:
        """Release one owner; stop only after the last current-boot owner."""
        if consumer not in GATEWAY_CONSUMERS:
            raise ValueError("unknown gateway consumer")
        self._require_owned()
        status = self.status(runner)
        consumers = set(status.get("current_boot_consumers") or ())
        consumers.discard(consumer)
        self._set_consumers(consumers)
        if not consumers and status.get("active"):
            status = self.stop(runner)
        else:
            status = self.status(runner)
        status["released_for"] = consumer
        return status

    def _require_owned(self) -> None:
        existing = self._read_unit()
        receipt = self._read_receipt() or {}
        if not (
            existing and existing.startswith(GATEWAY_UNIT_MARKER)
            and receipt.get("unit_sha256") == _sha256(existing)
        ):
            raise GatewayServiceError("The gateway unit is not owned by this installation.")

    def _wait_until_listening(
        self, runner: CommandRunner, bridge_address: str,
    ) -> dict[str, Any]:
        deadline = self._clock() + GATEWAY_START_TIMEOUT_SECONDS
        last = self.status(runner)
        while self._clock() < deadline:
            if last.get("protocol_ready"):
                return last
            self._sleep(0.25)
            last = self.status(runner)
        raise GatewayServiceError("The gateway did not establish both private listeners in time.")

    def start(self, runner: CommandRunner) -> dict[str, Any]:
        self._require_owned()
        bridge = self._bridge_observer()
        runner.run(elevated(["systemctl", "disable", GATEWAY_SERVICE_NAME]), check=False)
        runner.run(elevated(["systemctl", "start", GATEWAY_SERVICE_NAME]))
        return self._wait_until_listening(runner, bridge.gateway)

    def stop(self, runner: CommandRunner) -> dict[str, Any]:
        self._require_owned()
        runner.run(elevated([
            "systemctl", "stop", GATEWAY_SERVICE_NAME]), check=False)
        return self.status(runner)

    def restart(self, runner: CommandRunner) -> dict[str, Any]:
        self._require_owned()
        bridge = self._bridge_observer()
        runner.run(elevated(["systemctl", "disable", GATEWAY_SERVICE_NAME]), check=False)
        runner.run(elevated(["systemctl", "restart", GATEWAY_SERVICE_NAME]))
        return self._wait_until_listening(runner, bridge.gateway)

    def remove(self, runner: CommandRunner) -> dict[str, Any]:
        self._require_owned()
        runner.run(elevated([
            "systemctl", "disable", "--now", GATEWAY_SERVICE_NAME]), check=False)
        runner.run(elevated(["rm", str(self.unit_path)]))
        runner.run(elevated(["systemctl", "daemon-reload"]))
        for path in (self.receipt_path, self.launcher_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        return {
            "schema_version": GATEWAY_SERVICE_SCHEMA_VERSION,
            "service": GATEWAY_SERVICE_NAME,
            "removed": True,
            "credentials_preserved": True,
            "models_preserved": True,
            "openwebui_data_preserved": True,
        }


__all__ = [
    "GATEWAY_DIAGNOSTIC_MARKER",
    "GATEWAY_LAUNCHER_FILENAME",
    "GATEWAY_SERVICE_NAME",
    "GATEWAY_SERVICE_SCHEMA_VERSION",
    "GATEWAY_UNIT_MARKER",
    "KNOWN_DIAGNOSTIC_RUNNER_SHA256S",
    "KNOWN_DIAGNOSTIC_UNIT_SHA256S",
    "GatewayService",
    "GatewayServiceError",
    "GatewayServicePlan",
    "render_gateway_launcher",
    "render_gateway_unit",
]
