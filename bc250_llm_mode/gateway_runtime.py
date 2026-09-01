"""Production runtime for the authenticated integration gateway.

The process owns two HTTP listeners only: loopback and the gateway address of
the dedicated ``bc250-openwebui`` Podman bridge.  Bridge metadata is observed
with a bounded fixed-argv command and validated as RFC1918 IPv4.  A network
identity captured by the service installer must still match at process start,
so silent bridge recreation fails closed instead of publishing on an
unreviewed interface.

No credential is accepted on argv or through the environment.  Request
authentication reads the named-client fingerprint metadata from SQLite; the
presented Bearer value exists only in request memory.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import shutil
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .connection_setup import MultiClientAuthenticationStore
from .gateway import build_multi_client_gateway_server, make_gateway_socket_server
from .paths import AppPaths
from .repositories import SettingsRepository
from .unit_of_work import UnitOfWorkFactory


GATEWAY_RUNTIME_SCHEMA_VERSION = 1
GATEWAY_PORT = 9071
OPENWEBUI_NETWORK = "bc250-openwebui"
NETWORK_INSPECT_TIMEOUT_SECONDS = 5.0
NETWORK_INSPECT_MAX_BYTES = 64 * 1024
BACKEND_PROBE_TIMEOUT_SECONDS = 1.5
BACKEND_PROBE_MAX_BYTES = 64 * 1024
BACKEND_CACHE_SECONDS = 2.0
SHUTDOWN_TIMEOUT_SECONDS = 5.0

_RFC1918 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


class GatewayRuntimeError(RuntimeError):
    """A fail-closed runtime precondition was not met."""


@dataclass(frozen=True)
class BridgeObservation:
    network: str
    driver: str
    subnet: str
    gateway: str
    identity: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": GATEWAY_RUNTIME_SCHEMA_VERSION,
            "network": self.network,
            "driver": self.driver,
            "subnet": self.subnet,
            "gateway": self.gateway,
            "identity": self.identity,
        }


def _network_identity(*, network: str, driver: str, subnet: str, gateway: str) -> str:
    payload = json.dumps(
        {"driver": driver, "gateway": gateway, "network": network, "subnet": subnet},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def parse_bridge_inspect(
    payload: bytes | str, *, expected_network: str = OPENWEBUI_NETWORK,
) -> BridgeObservation:
    """Parse one bounded Podman network inspection and reject ambiguity."""
    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    if not raw or len(raw) > NETWORK_INSPECT_MAX_BYTES:
        raise GatewayRuntimeError("Open WebUI bridge inspection is missing or oversized.")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise GatewayRuntimeError("Open WebUI bridge inspection is invalid.") from exc
    if isinstance(value, list):
        if len(value) != 1:
            raise GatewayRuntimeError("Open WebUI bridge inspection is ambiguous.")
        value = value[0]
    if not isinstance(value, Mapping):
        raise GatewayRuntimeError("Open WebUI bridge inspection is invalid.")
    name = str(value.get("name") or value.get("Name") or "")
    driver = str(value.get("driver") or value.get("Driver") or "").lower()
    if name != expected_network or driver != "bridge":
        raise GatewayRuntimeError("The managed Open WebUI network is not a bridge.")
    subnets = value.get("subnets") or value.get("Subnets")
    if not isinstance(subnets, list) or len(subnets) != 1:
        raise GatewayRuntimeError("The managed Open WebUI bridge subnet is ambiguous.")
    row = subnets[0]
    if not isinstance(row, Mapping):
        raise GatewayRuntimeError("The managed Open WebUI bridge subnet is invalid.")
    subnet_text = str(row.get("subnet") or row.get("Subnet") or "")
    gateway_text = str(row.get("gateway") or row.get("Gateway") or "")
    try:
        subnet = ipaddress.ip_network(subnet_text, strict=False)
        gateway = ipaddress.ip_address(gateway_text)
    except ValueError as exc:
        raise GatewayRuntimeError("The managed Open WebUI bridge address is invalid.") from exc
    if (
        not isinstance(subnet, ipaddress.IPv4Network)
        or not isinstance(gateway, ipaddress.IPv4Address)
        or not any(gateway in allowed for allowed in _RFC1918)
        or gateway not in subnet
        or gateway in {subnet.network_address, subnet.broadcast_address}
    ):
        raise GatewayRuntimeError(
            "The managed Open WebUI gateway must be one unicast RFC1918 bridge address."
        )
    subnet_clean = str(subnet)
    gateway_clean = str(gateway)
    return BridgeObservation(
        network=name,
        driver=driver,
        subnet=subnet_clean,
        gateway=gateway_clean,
        identity=_network_identity(
            network=name, driver=driver, subnet=subnet_clean, gateway=gateway_clean),
    )


def discover_bridge(
    *,
    expected_network: str = OPENWEBUI_NETWORK,
    run: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
) -> BridgeObservation:
    podman = shutil.which("podman")
    if not podman:
        raise GatewayRuntimeError("Podman is required for the managed Open WebUI bridge.")
    command = [podman, "network", "inspect", expected_network]
    runner = run or _run_bounded_inspect
    try:
        result = runner(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=NETWORK_INSPECT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GatewayRuntimeError("Could not inspect the managed Open WebUI bridge.") from exc
    if result.returncode != 0:
        raise GatewayRuntimeError("The managed Open WebUI bridge is absent.")
    return parse_bridge_inspect(result.stdout, expected_network=expected_network)


def _run_bounded_inspect(
    command: Sequence[str], **_kwargs: Any,
) -> subprocess.CompletedProcess[bytes]:
    """Run Podman inspection with a hard deadline and stdout memory bound."""
    process = subprocess.Popen(  # noqa: S603 - fixed caller-owned argv
        list(command), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    expired = threading.Event()

    def terminate() -> None:
        expired.set()
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass

    timer = threading.Timer(NETWORK_INSPECT_TIMEOUT_SECONDS, terminate)
    timer.daemon = True
    timer.start()
    output = b""
    try:
        assert process.stdout is not None
        output = process.stdout.read(NETWORK_INSPECT_MAX_BYTES + 1)
        if len(output) > NETWORK_INSPECT_MAX_BYTES:
            terminate()
        returncode = process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        terminate()
        returncode = process.wait()
    finally:
        timer.cancel()
    if expired.is_set():
        if len(output) > NETWORK_INSPECT_MAX_BYTES:
            raise GatewayRuntimeError("Open WebUI bridge inspection exceeded its bound.")
        raise subprocess.TimeoutExpired(list(command), NETWORK_INSPECT_TIMEOUT_SECONDS)
    return subprocess.CompletedProcess(list(command), returncode, output, b"")


def _bounded_json(url: str, *, timeout: float) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        raw = response.read(BACKEND_PROBE_MAX_BYTES + 1)
    if len(raw) > BACKEND_PROBE_MAX_BYTES:
        raise GatewayRuntimeError("Backend identity response exceeded its bound.")
    return json.loads(raw) if raw else None


class BackendIdentityProbe:
    """Current model/config observation with a short, thread-safe cache."""

    def __init__(
        self,
        units: UnitOfWorkFactory,
        *,
        port: int,
        clock: Callable[[], float] = time.monotonic,
        json_get: Callable[..., Any] = _bounded_json,
    ) -> None:
        self._units = units
        self.port = int(port)
        self._clock = clock
        self._json_get = json_get
        self._lock = threading.Lock()
        self._cached_at = float("-inf")
        self._cached = False

    def _observe(self) -> bool:
        with self._units.read() as conn:
            settings = SettingsRepository(conn)
            desired = settings.get("current_model")
            configured_port = int(settings.get("server_port", self.port))
        if not desired or configured_port != self.port:
            return False
        base = f"http://127.0.0.1:{self.port}"
        health = self._json_get(
            f"{base}/health", timeout=BACKEND_PROBE_TIMEOUT_SECONDS)
        models = self._json_get(
            f"{base}/v1/models", timeout=BACKEND_PROBE_TIMEOUT_SECONDS)
        rows = models.get("data") if isinstance(models, Mapping) else None
        observed = (
            rows[0].get("id")
            if isinstance(rows, list) and rows and isinstance(rows[0], Mapping)
            else None
        )
        health_ok = isinstance(health, Mapping) and str(
            health.get("status") or "").lower() in {"ok", "ready", "healthy"}
        return bool(health_ok and observed == desired)

    def ready(self) -> bool:
        now = self._clock()
        with self._lock:
            if now - self._cached_at <= BACKEND_CACHE_SECONDS:
                return self._cached
            try:
                observed = self._observe()
            except (
                OSError, ValueError, KeyError, TypeError,
                urllib.error.URLError, GatewayRuntimeError,
            ):
                observed = False
            self._cached = bool(observed)
            self._cached_at = now
            return self._cached


def _configured_server_port(units: UnitOfWorkFactory) -> int:
    with units.read() as conn:
        value = int(SettingsRepository(conn).get("server_port", 8080))
    if not 1 <= value <= 65535 or value == GATEWAY_PORT:
        raise GatewayRuntimeError("The configured model port is invalid.")
    return value


def run_gateway_runtime(
    *,
    app_dir: Path,
    expected_network_identity: str,
    bridge_observer: Callable[[], BridgeObservation] = discover_bridge,
    stop_event: threading.Event | None = None,
    server_factory: Callable[..., Any] = make_gateway_socket_server,
) -> int:
    paths = AppPaths.from_app_dir(app_dir)
    paths.validate()
    if not paths.database_path.is_file() or paths.database_path.is_symlink():
        raise GatewayRuntimeError("The appliance database is unavailable.")
    bridge = bridge_observer()
    if bridge.identity != expected_network_identity:
        raise GatewayRuntimeError("The managed Open WebUI bridge identity changed.")
    units = UnitOfWorkFactory(paths.database_path)
    backend_port = _configured_server_port(units)
    backend = BackendIdentityProbe(units, port=backend_port)
    gateway = build_multi_client_gateway_server(
        authentication_store=MultiClientAuthenticationStore(units),
        backend_base=f"http://127.0.0.1:{backend_port}",
        should_report_backend=backend.ready,
    )
    servers = []
    try:
        servers.append(server_factory(
            gateway, host="127.0.0.1", port=GATEWAY_PORT))
        servers.append(server_factory(
            gateway, host=bridge.gateway, port=GATEWAY_PORT))
    except BaseException:
        for server in servers:
            server.server_close()
        raise

    stopped = stop_event or threading.Event()
    prior_handlers: dict[int, Any] = {}

    def request_stop(_signum=None, _frame=None) -> None:
        stopped.set()

    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGTERM, signal.SIGINT):
            prior_handlers[signum] = signal.signal(signum, request_stop)
    threads = [
        threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.25},
            name=f"gateway-listener-{index}",
            daemon=True,
        )
        for index, server in enumerate(servers)
    ]
    for thread in threads:
        thread.start()
    try:
        while not stopped.wait(0.5):
            if any(not thread.is_alive() for thread in threads):
                raise GatewayRuntimeError("A gateway listener stopped unexpectedly.")
    finally:
        for server in servers:
            server.shutdown()
        deadline = time.monotonic() + SHUTDOWN_TIMEOUT_SECONDS
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        for server in servers:
            server.server_close()
        for signum, handler in prior_handlers.items():
            signal.signal(signum, handler)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bc250-gateway-runtime")
    parser.add_argument("--app-dir", required=True, type=Path)
    parser.add_argument("--expected-network-identity", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    identity = str(args.expected_network_identity)
    if not identity.startswith("sha256:") or len(identity) != 71:
        raise GatewayRuntimeError("The expected bridge identity is invalid.")
    return run_gateway_runtime(
        app_dir=args.app_dir,
        expected_network_identity=identity,
    )


if __name__ == "__main__":  # pragma: no cover - exercised as installed module
    try:
        raise SystemExit(main())
    except GatewayRuntimeError as exc:
        # Fixed prefix and bounded reviewed messages only; never request data.
        raise SystemExit(f"gateway runtime refused to start: {exc}") from None


__all__ = [
    "BACKEND_CACHE_SECONDS",
    "BridgeObservation",
    "BackendIdentityProbe",
    "GATEWAY_PORT",
    "GATEWAY_RUNTIME_SCHEMA_VERSION",
    "GatewayRuntimeError",
    "OPENWEBUI_NETWORK",
    "discover_bridge",
    "main",
    "parse_bridge_inspect",
    "run_gateway_runtime",
]
