"""Open WebUI query and command facade.

All container creation and replacement is delegated to the typed,
candidate/rollback transaction in :mod:`openwebui_runtime`.  This module keeps
the historical public function names used by the composition layer and CLI.
"""

from __future__ import annotations

import json
import shutil
from typing import Any

from .logging_utils import CommandRunner
from .openwebui_runtime import (
    OPENWEBUI_ARCHITECTURE,
    OPENWEBUI_CONTAINER,
    OPENWEBUI_DATA_DESTINATION,
    OPENWEBUI_DATA_VOLUME,
    OPENWEBUI_GATEWAY_URL,
    OPENWEBUI_IMAGE,
    OPENWEBUI_IMAGE_DIGEST,
    OPENWEBUI_IMAGE_VERSION,
    OPENWEBUI_LEGACY_CONTAINER,
    OPENWEBUI_LEGACY_DATA_BIND,
    OPENWEBUI_NETWORK,
    OPENWEBUI_PROVIDER_ADAPTER_VERSION,
    OPENWEBUI_RECEIPT_FILENAME,
    OPENWEBUI_SPEC_VERSION,
    OpenWebUIContainerSpec,
    OpenWebUIRuntimeError,
    OpenWebUITransaction,
    observe_container_data_source,
)
from .paths import AppPaths


# Compatibility names consumed by existing callers, docs, and tests.
CONTAINER = OPENWEBUI_CONTAINER
LEGACY_CONTAINER = OPENWEBUI_LEGACY_CONTAINER
IMAGE_TAG_DISPLAY = f"v{OPENWEBUI_IMAGE_VERSION}"
IMAGE_DIGEST_SHA256 = OPENWEBUI_IMAGE_DIGEST
IMAGE_REF = OPENWEBUI_IMAGE
IMAGE_ARCHITECTURE = OPENWEBUI_ARCHITECTURE
DATA_VOLUME = OPENWEBUI_DATA_VOLUME
DATA_DESTINATION = OPENWEBUI_DATA_DESTINATION
LEGACY_DATA_BIND = OPENWEBUI_LEGACY_DATA_BIND
ALLOWED_DATA_SOURCES = frozenset({DATA_VOLUME, LEGACY_DATA_BIND})
NETWORK = OPENWEBUI_NETWORK
UI_PUBLISH = "127.0.0.1:3000:8080"
BACKEND_HOST = "host.containers.internal"
GATEWAY_PORT = 9071
GATEWAY_BACKEND_HOST = BACKEND_HOST
BACKEND_URL = OPENWEBUI_GATEWAY_URL
READ_ONLY_ROOT = True
WRITABLE_TMPFS = "/tmp:rw,size=1g,mode=0755,nosuid,nodev"
NON_ROOT_USER = False


def _ensure_network(_state: dict[str, Any], runner: CommandRunner) -> None:
    result = runner.run(
        ["podman", "network", "exists", NETWORK], check=False,
        emit_output=False, timeout_seconds=10, max_output_bytes=1024,
    )
    if result.returncode != 0:
        runner.run(
            ["podman", "network", "create", NETWORK],
            timeout_seconds=20, max_output_bytes=16 * 1024,
        )


def ensure_open_webui_network(
    state: dict[str, Any], runner: CommandRunner,
) -> dict[str, Any]:
    if not shutil.which("podman"):
        raise RuntimeError("Podman is required for the Open WebUI private network.")
    _ensure_network(state, runner)
    return {"network": NETWORK, "created_or_present": True}


def _container_topology(
    _state: dict[str, Any], runner: CommandRunner, container: str,
) -> str:
    result = runner.run(
        ["podman", "inspect", "--format", "{{json .NetworkSettings.Networks}}", container],
        check=False, emit_output=False, timeout_seconds=10,
        max_output_bytes=64 * 1024,
    )
    try:
        networks = json.loads(result.stdout.strip() or "{}")
    except ValueError:
        return "unknown"
    if not isinstance(networks, dict):
        return "unknown"
    if "host" in networks:
        return "legacy-host"
    if NETWORK in networks:
        return "contained"
    return "unknown"


def _container_data_source(runner: CommandRunner, container: str) -> str:
    return observe_container_data_source(runner, container)


def _container_spec_version(runner: CommandRunner, container: str) -> int | None:
    result = runner.run(
        [
            "podman", "inspect", "--format",
            "{{index .Config.Labels \"io.bc250-llm-mode.openwebui-spec\"}}",
            container,
        ],
        check=False, emit_output=False, timeout_seconds=10, max_output_bytes=1024,
    )
    try:
        return int(result.stdout.strip()) if result.returncode == 0 else None
    except ValueError:
        return None


def _create_command(
    container: str,
    *,
    credential_file: str,
    secret_key_file: str,
    data_source: str = DATA_VOLUME,
) -> list[str]:
    return OpenWebUIContainerSpec(
        name=container,
        data_source=data_source,
        secret_key_file=secret_key_file,
        client_credential_file=credential_file,
    ).create_command()


def _verify_image_digest(
    _state: dict[str, Any], runner: CommandRunner,
) -> dict[str, Any]:
    if not shutil.which("podman"):
        return {"verified": False, "detail": "podman missing"}
    result = runner.run(
        ["podman", "image", "inspect", IMAGE_REF, "--format", "{{json .RepoDigests}}"],
        check=False, emit_output=False, timeout_seconds=15,
        max_output_bytes=64 * 1024,
    )
    if result.returncode != 0:
        return {
            "verified": False,
            "detail": f"image not pulled to pinned digest {IMAGE_DIGEST_SHA256}",
        }
    try:
        digests = json.loads(result.stdout.strip() or "[]")
    except ValueError:
        return {"verified": False, "detail": "could not parse image digests"}
    if isinstance(digests, list) and any(
        IMAGE_DIGEST_SHA256 in value for value in digests if isinstance(value, str)
    ):
        return {"verified": True, "detail": IMAGE_DIGEST_SHA256}
    return {"verified": False, "detail": "image digest mismatch"}


def _container_name(
    state: dict[str, Any], runner: CommandRunner,
) -> tuple[str, bool]:
    preferred = str(state.get("openwebui_container") or CONTAINER)
    for name in dict.fromkeys((preferred, CONTAINER, LEGACY_CONTAINER)):
        result = runner.run(
            ["podman", "container", "exists", name], check=False,
            emit_output=False, timeout_seconds=10, max_output_bytes=1024,
        )
        if result.returncode == 0:
            return name, True
    return preferred, False


def open_webui_status(
    state: dict[str, Any], runner: CommandRunner,
) -> dict[str, Any]:
    if not shutil.which("podman"):
        return {
            "available": False, "installed": False, "running": False,
            "status": "podman missing", "url": "http://127.0.0.1:3000",
        }
    container, exists = _container_name(state, runner)
    process = "not installed"
    topology = "absent"
    spec_version = None
    data_source = None
    if exists:
        result = runner.run(
            ["podman", "inspect", "--format", "{{.State.Status}}", container],
            check=False, emit_output=False, timeout_seconds=10,
            max_output_bytes=1024,
        )
        process = result.stdout.strip() or "unknown"
        topology = _container_topology(state, runner, container)
        spec_version = _container_spec_version(runner, container)
        try:
            data_source = _container_data_source(runner, container)
        except OpenWebUIRuntimeError:
            data_source = "unverified"
        state["openwebui_container"] = container
        state["openwebui_installed"] = True
    digest = _verify_image_digest(state, runner) if exists else {
        "verified": False, "detail": "not installed",
    }
    gateway_provisioned = bool(state.get("gateway_provisioned"))
    receipt = _read_runtime_receipt(state)
    expected = None
    try:
        expected = _expected_model(state)
    except OpenWebUIRuntimeError:
        pass
    receipt_matches = bool(
        receipt.get("schema_version") == OPENWEBUI_SPEC_VERSION
        and receipt.get("container") == CONTAINER
        and receipt.get("image") == IMAGE_REF
        and receipt.get("provider_adapter") == OPENWEBUI_PROVIDER_ADAPTER_VERSION
        and receipt.get("expected_model") == expected
    )
    provider_ready = bool(receipt_matches and receipt.get("provider_ready"))
    expected_model_visible = bool(
        receipt_matches and receipt.get("expected_model_visible"))
    stream_ready = bool(receipt_matches and receipt.get("stream_ready"))
    spec_verified = (
        spec_version == OPENWEBUI_SPEC_VERSION
        and topology == "contained"
        and data_source in ALLOWED_DATA_SOURCES
    )
    return {
        "available": True,
        "installed": exists,
        "running": process == "running",
        "status": process,
        "container": container,
        "topology": topology,
        "spec_version": spec_version,
        "spec_verified": spec_verified,
        "data_source": data_source,
        "backend_route": (
            "gateway" if exists and digest["verified"] and gateway_provisioned
            else "pending-gateway"
        ),
        "provider_adapter": OPENWEBUI_PROVIDER_ADAPTER_VERSION,
        "provider_ready": provider_ready,
        "expected_model_visible": expected_model_visible,
        "stream_ready": stream_ready,
        "end_to_end_verified": stream_ready,
        "digest_verified": digest["verified"],
        "digest": IMAGE_DIGEST_SHA256,
        "architecture": IMAGE_ARCHITECTURE,
        "image_identity": IMAGE_REF,
        "gateway_state": (
            "verified" if digest["verified"] and gateway_provisioned else "pending"
        ),
        "url": "http://127.0.0.1:3000",
    }


def _paths(state: dict[str, Any]) -> AppPaths:
    value = state.get("app_dir")
    return AppPaths.from_app_dir(value) if value else AppPaths.for_home()


def _read_runtime_receipt(state: dict[str, Any]) -> dict[str, Any]:
    path = _paths(state).app_dir / OPENWEBUI_RECEIPT_FILENAME
    try:
        raw = path.read_bytes()
    except OSError:
        return {}
    if len(raw) > 16 * 1024:
        return {}
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _expected_model(state: dict[str, Any]) -> str:
    known_good = state.get("known_good_runtime")
    candidates = (
        state.get("current_model"),
        known_good.get("model_alias") if isinstance(known_good, dict) else None,
        state.get("selected_model"),
    )
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise OpenWebUIRuntimeError(
        "Start a model before configuring Open WebUI so its public model name can be verified."
    )


def _credential(state: dict[str, Any], explicit: str | None = None) -> str:
    value = explicit or state.get("gateway_credential_file")
    if not isinstance(value, str) or not value:
        raise OpenWebUIRuntimeError(
            "Create an Open WebUI client credential before starting Open WebUI."
        )
    return value


def _converge(
    state: dict[str, Any], runner: CommandRunner,
    *, credential_file: str | None = None,
) -> dict[str, Any]:
    if not shutil.which("podman"):
        raise OpenWebUIRuntimeError(
            "Podman is required for Open WebUI; run environment setup first."
        )
    transaction = OpenWebUITransaction(paths=_paths(state), runner=runner)
    result = transaction.converge(
        client_credential_file=_credential(state, credential_file),
        expected_model=_expected_model(state),
    )
    state.update(
        openwebui_installed=True,
        openwebui_container=result.container,
        openwebui_spec_version=OPENWEBUI_SPEC_VERSION,
        openwebui_provider_adapter=result.provider_adapter,
        openwebui_provider_ready=result.provider_ready,
        openwebui_expected_model_visible=result.expected_model_visible,
        openwebui_stream_ready=result.stream_ready,
        openwebui_last_transaction=result.transaction_id,
    )
    observed = open_webui_status(state, runner)
    observed.update(
        transaction_id=result.transaction_id,
        data_source_preserved=result.data_source,
        provider_ready=result.provider_ready,
        expected_model_visible=result.expected_model_visible,
        stream_ready=result.stream_ready,
        rollback_removed=result.rollback_removed,
    )
    runner.emit(
        "Open WebUI is ready on http://127.0.0.1:3000: its contained runtime, "
        "provider, selected model, and streamed completion all verified."
    )
    return observed


def install_open_webui(
    state: dict[str, Any], runner: CommandRunner,
    *, credential_file: str | None = None,
) -> dict[str, Any]:
    return _converge(state, runner, credential_file=credential_file)


def update_open_webui(
    state: dict[str, Any], runner: CommandRunner,
) -> dict[str, Any]:
    before = open_webui_status(state, runner)
    if not before.get("installed"):
        raise OpenWebUIRuntimeError("Open WebUI is not installed; use Install first.")
    result = _converge(state, runner)
    result["updated"] = True
    result["running_state_restored"] = (
        bool(result.get("running")) == bool(before.get("running"))
    )
    return result


def start_open_webui_impl(
    state: dict[str, Any], runner: CommandRunner,
    credential_file: str | None = None,
) -> dict[str, Any]:
    return _converge(state, runner, credential_file=credential_file)


def start_open_webui(
    state: dict[str, Any], runner: CommandRunner,
) -> dict[str, Any]:
    return start_open_webui_impl(
        state, runner, credential_file=state.get("gateway_credential_file"))


def stop_open_webui(
    state: dict[str, Any], runner: CommandRunner,
) -> dict[str, Any]:
    status = open_webui_status(state, runner)
    if status.get("installed"):
        runner.run(
            ["podman", "stop", "--time", "10", str(status["container"])],
            check=False, timeout_seconds=15,
        )
    state["openwebui_provider_ready"] = False
    state["openwebui_expected_model_visible"] = False
    state["openwebui_stream_ready"] = False
    return open_webui_status(state, runner)


def restart_open_webui_impl(
    state: dict[str, Any], runner: CommandRunner,
    credential_file: str | None = None,
) -> dict[str, Any]:
    return _converge(state, runner, credential_file=credential_file)


def restart_open_webui(
    state: dict[str, Any], runner: CommandRunner,
) -> dict[str, Any]:
    return restart_open_webui_impl(
        state, runner, credential_file=state.get("gateway_credential_file"))
