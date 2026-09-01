"""Typed, transactional runtime boundary for the pinned Open WebUI image.

The module deliberately owns the complete Podman create contract.  It never
places a credential value in argv or the environment.  The version-frozen
provider adapter reads the mounted credential inside the container and uses
Open WebUI's awaited ``Config`` abstraction instead of editing SQLite.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import stat
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .fsops import atomic_write_bytes, ensure_private_dir
from .logging_utils import CommandRunner
from .paths import AppPaths


OPENWEBUI_SPEC_VERSION = 1
OPENWEBUI_PROVIDER_ADAPTER_VERSION = "open-webui-0.11.1-config-v1"
OPENWEBUI_IMAGE_VERSION = "0.11.1"
OPENWEBUI_IMAGE_DIGEST = (
    "sha256:e3a36f3aefb2408ac01d8aa2bba24f75d2569ffb6de6e7d2865c0045a38592ac"
)
OPENWEBUI_IMAGE = f"ghcr.io/open-webui/open-webui@{OPENWEBUI_IMAGE_DIGEST}"
OPENWEBUI_ARCHITECTURE = "amd64"
OPENWEBUI_CONTAINER = "bc250-open-webui"
OPENWEBUI_LEGACY_CONTAINER = "open-webui"
OPENWEBUI_NETWORK = "bc250-openwebui"
OPENWEBUI_DATA_VOLUME = "bc250-open-webui"
OPENWEBUI_LEGACY_DATA_BIND = "/var/lib/open-webui"
OPENWEBUI_DATA_DESTINATION = "/app/backend/data"
OPENWEBUI_SECRET_DESTINATION = "/app/backend/.webui_secret_key"
OPENWEBUI_CLIENT_DESTINATION = "/run/secrets/bc250-openwebui-client"
OPENWEBUI_GATEWAY_URL = "http://host.containers.internal:9071/v1"
OPENWEBUI_HTTP_URL = "http://127.0.0.1:3000"
OPENWEBUI_HTTP_TIMEOUT_SECONDS = 120.0
OPENWEBUI_STOP_TIMEOUT_SECONDS = 15.0
OPENWEBUI_INSPECT_MAX_BYTES = 256 * 1024
OPENWEBUI_PROVIDER_OUTPUT_MAX_BYTES = 16 * 1024
OPENWEBUI_SECRET_BYTES = 48
OPENWEBUI_RECEIPT_FILENAME = "openwebui-runtime.json"
OPENWEBUI_LABEL_OWNER = "io.bc250-llm-mode.owner"
OPENWEBUI_LABEL_SPEC = "io.bc250-llm-mode.openwebui-spec"
OPENWEBUI_LABEL_ROLE = "io.bc250-llm-mode.role"
_SAFE_CONTAINER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")


class OpenWebUIRuntimeError(RuntimeError):
    """Open WebUI mutation stopped at a safe recovery checkpoint."""


def _safe_path(value: str | Path, *, name: str) -> str:
    text = str(Path(value).expanduser())
    if not text.startswith("/") or any(char in text for char in ("\x00", "\n", "\r", ":")):
        raise ValueError(f"{name} must be a safe absolute path")
    return text


def _safe_container(value: str, *, name: str = "container") -> str:
    if not _SAFE_CONTAINER.fullmatch(value):
        raise ValueError(f"invalid {name} name")
    return value


@dataclass(frozen=True)
class OpenWebUIContainerSpec:
    """The only accepted Open WebUI container contract."""

    name: str
    data_source: str
    secret_key_file: str
    client_credential_file: str
    image: str = OPENWEBUI_IMAGE
    architecture: str = OPENWEBUI_ARCHITECTURE
    network: str = OPENWEBUI_NETWORK
    publish: str = "127.0.0.1:3000:8080"
    memory: str = "2g"
    cpus: str = "4"
    pids_limit: int = 256
    fsize_bytes: int = 1_073_741_824
    tmpfs: str = "/tmp:rw,size=1g,mode=0755,nosuid,nodev"

    def validate(self) -> None:
        _safe_container(self.name)
        if self.data_source not in {
            OPENWEBUI_DATA_VOLUME, OPENWEBUI_LEGACY_DATA_BIND,
        }:
            raise ValueError("unapproved Open WebUI data source")
        _safe_path(self.secret_key_file, name="Open WebUI secret-key file")
        _safe_path(self.client_credential_file, name="Open WebUI client credential")
        if self.image != OPENWEBUI_IMAGE or self.architecture != OPENWEBUI_ARCHITECTURE:
            raise ValueError("Open WebUI image identity is not the application pin")
        if self.network != OPENWEBUI_NETWORK or self.network == "host":
            raise ValueError("Open WebUI must use its dedicated private network")
        if self.publish != "127.0.0.1:3000:8080":
            raise ValueError("Open WebUI must publish only on host loopback")
        if (self.memory, self.cpus, self.pids_limit, self.fsize_bytes) != (
            "2g", "4", 256, 1_073_741_824,
        ):
            raise ValueError("Open WebUI resource bounds differ from the reviewed contract")
        if self.tmpfs != "/tmp:rw,size=1g,mode=0755,nosuid,nodev":
            raise ValueError("Open WebUI writable tmpfs differs from the measured contract")

    def create_command(self) -> list[str]:
        self.validate()
        data_mount = f"{self.data_source}:{OPENWEBUI_DATA_DESTINATION}"
        if self.data_source == OPENWEBUI_LEGACY_DATA_BIND:
            data_mount += ":Z"
        command = [
            "podman", "create", "--name", self.name,
            "--label", f"{OPENWEBUI_LABEL_OWNER}=openwebui",
            "--label", f"{OPENWEBUI_LABEL_SPEC}={OPENWEBUI_SPEC_VERSION}",
            "--label", f"{OPENWEBUI_LABEL_ROLE}=candidate",
            "--network", self.network,
            "--publish", self.publish,
            "--add-host", "host.containers.internal:host-gateway",
            "--env", "PORT=8080",
            "--security-opt", "no-new-privileges",
            "--cap-drop", "all",
            "--memory", self.memory,
            "--memory-swap", "4g",
            "--cpus", self.cpus,
            "--pids-limit", str(self.pids_limit),
            "--ulimit", f"fsize={self.fsize_bytes}:{self.fsize_bytes}",
            "--read-only",
            "--tmpfs", self.tmpfs,
            "--restart", "no",
            "--volume", data_mount,
            "--volume",
            f"{self.secret_key_file}:{OPENWEBUI_SECRET_DESTINATION}:ro,Z",
            "--volume",
            f"{self.client_credential_file}:{OPENWEBUI_CLIENT_DESTINATION}:ro,Z",
            self.image,
        ]
        validate_openwebui_command(command)
        return command

    def redacted(self) -> dict[str, Any]:
        value = asdict(self)
        value["secret_key_file"] = "<private-file>"
        value["client_credential_file"] = "<private-file>"
        return value


def validate_openwebui_command(command: list[str]) -> None:
    """Reject dangerous drift even when a caller constructs argv itself."""
    if command[:2] != ["podman", "create"] or command[-1] != OPENWEBUI_IMAGE:
        raise ValueError("not the reviewed Open WebUI create command")
    forbidden_exact = {
        "--privileged", "--device", "--pid", "--publish-all", "-P",
    }
    if any(item in forbidden_exact for item in command):
        raise ValueError("unsafe Open WebUI container option")
    for index, item in enumerate(command):
        lowered = item.lower()
        if lowered in {"--network", "--net"} and command[index + 1] == "host":
            raise ValueError("host networking is forbidden")
        if lowered in {"--publish", "-p"} and not command[index + 1].startswith("127.0.0.1:"):
            raise ValueError("wildcard or LAN publishing is forbidden")
        if "8080" in item and item.startswith("http"):
            raise ValueError("raw model backend routing is forbidden")
        if "api_key" in lowered or "bearer " in lowered or "sk-pending" in lowered:
            raise ValueError("credentials are forbidden in Open WebUI argv")
        if "seccomp=unconfined" in lowered:
            raise ValueError("unconfined seccomp is forbidden")
    required = {
        "--read-only", "--cap-drop", "--pids-limit", "--ulimit",
        "--memory", "--cpus", "--restart",
    }
    if not required.issubset(command):
        raise ValueError("Open WebUI containment or resource bounds are incomplete")


def verify_private_secret_file(path: str | Path, *, label: str) -> Path:
    target = Path(_safe_path(path, name=label))
    try:
        info = target.lstat()
    except OSError as exc:
        raise OpenWebUIRuntimeError(f"{label} is missing.") from exc
    if target.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise OpenWebUIRuntimeError(f"{label} must be a regular private file.")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise OpenWebUIRuntimeError(f"{label} must have mode 0600.")
    if info.st_size < 16 or info.st_size > 4096:
        raise OpenWebUIRuntimeError(f"{label} has an invalid size.")
    return target


class OpenWebUISecretService:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths

    @property
    def path(self) -> Path:
        return self.paths.app_dir / "secrets/open-webui-secret-key"

    def ensure(self) -> Path:
        ensure_private_dir(self.path.parent)
        if self.path.exists():
            return verify_private_secret_file(self.path, label="Open WebUI secret-key file")
        atomic_write_bytes(self.path, secrets.token_bytes(OPENWEBUI_SECRET_BYTES), mode=0o600)
        return verify_private_secret_file(self.path, label="Open WebUI secret-key file")


def plan_provider_config(
    current: dict[str, Any], *, credential: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pure mirror of the pinned adapter's one-provider reconciliation.

    The credential is returned only inside the mutation payload used by the
    secret-owning adapter boundary.  The receipt is redacted.  Callers must not
    serialize or log the mutation payload.
    """
    if not isinstance(credential, str) or not (1 <= len(credential) <= 4096):
        raise OpenWebUIRuntimeError("The mounted Open WebUI credential is invalid.")
    urls = list(current.get("openai.api_base_urls") or [])
    keys = list(current.get("openai.api_keys") or [])
    configs = dict(current.get("openai.api_configs") or {})
    if not all(isinstance(value, str) for value in urls + keys):
        raise OpenWebUIRuntimeError("Open WebUI provider configuration is malformed.")
    matches = [
        index for index, value in enumerate(urls)
        if value.rstrip("/") == OPENWEBUI_GATEWAY_URL
    ]
    if len(matches) > 1:
        raise OpenWebUIRuntimeError("The app-owned Open WebUI provider is ambiguous.")
    if len(keys) < len(urls):
        keys.extend([""] * (len(urls) - len(keys)))
    elif len(keys) > len(urls):
        keys = keys[:len(urls)]
    if matches:
        index = matches[0]
        action = "updated"
    else:
        index = len(urls)
        urls.append(OPENWEBUI_GATEWAY_URL)
        keys.append("")
        action = "installed"
    keys[index] = credential
    prior = configs.get(str(index), configs.get(OPENWEBUI_GATEWAY_URL, {}))
    owned = dict(prior) if isinstance(prior, dict) else {}
    owned.update({
        "enable": True,
        "prefix_id": "bc250",
        "connection_type": "external",
    })
    configs[str(index)] = owned
    return (
        {
            "openai.enable": True,
            "openai.api_base_urls": urls,
            "openai.api_keys": keys,
            "openai.api_configs": configs,
        },
        {
            "adapter": OPENWEBUI_PROVIDER_ADAPTER_VERSION,
            "action": action,
            "provider_index": index,
            "provider_count": len(urls),
            "credential_match": True,
        },
    )


# This source is sent over stdin to the pinned image's Python.  It contains no
# credential, prompt, completion, host path, or user data.  The adapter is tied
# to Open WebUI 0.11.1's awaited Config.get_many/Config.upsert contract.
_PROVIDER_RECONCILE_SCRIPT = r'''
import asyncio, json, os
from pathlib import Path
os.environ["WEBUI_SECRET_KEY"] = (
    Path("/app/backend/.webui_secret_key").read_text(encoding="utf-8").strip()
)
from open_webui.models.config import Config

URL = "http://host.containers.internal:9071/v1"
KEY_FILE = Path("/run/secrets/bc250-openwebui-client")

async def main():
    key = KEY_FILE.read_text(encoding="utf-8").strip()
    if not key or len(key) > 4096:
        raise RuntimeError("mounted client credential is invalid")
    values = await Config.get_many(
        "openai.enable", "openai.api_base_urls", "openai.api_keys",
        "openai.api_configs",
    )
    urls = list(values.get("openai.api_base_urls") or [])
    keys = list(values.get("openai.api_keys") or [])
    configs = dict(values.get("openai.api_configs") or {})
    matches = [index for index, value in enumerate(urls) if value.rstrip("/") == URL]
    if len(matches) > 1:
        raise RuntimeError("app-owned provider identity is ambiguous")
    if len(keys) < len(urls):
        keys.extend([""] * (len(urls) - len(keys)))
    elif len(keys) > len(urls):
        keys = keys[:len(urls)]
    if matches:
        index = matches[0]
        action = "updated"
    else:
        index = len(urls)
        urls.append(URL)
        keys.append("")
        action = "installed"
    keys[index] = key
    prior = configs.get(str(index), configs.get(URL, {}))
    if not isinstance(prior, dict):
        prior = {}
    owned = dict(prior)
    owned.update({
        "enable": True,
        "prefix_id": "bc250",
        "connection_type": "external",
    })
    configs[str(index)] = owned
    await Config.upsert({
        "openai.enable": True,
        "openai.api_base_urls": urls,
        "openai.api_keys": keys,
        "openai.api_configs": configs,
    })
    check = await Config.get_many(
        "openai.enable", "openai.api_base_urls", "openai.api_keys",
        "openai.api_configs",
    )
    check_urls = list(check.get("openai.api_base_urls") or [])
    check_keys = list(check.get("openai.api_keys") or [])
    verified = (
        check.get("openai.enable") is True
        and index < len(check_urls) and check_urls[index].rstrip("/") == URL
        and index < len(check_keys) and hmac.compare_digest(check_keys[index], key)
    )
    print(json.dumps({
        "adapter": "open-webui-0.11.1-config-v1",
        "action": action,
        "provider_index": index,
        "provider_count": len(check_urls),
        "credential_match": bool(verified),
    }, sort_keys=True))

import hmac
asyncio.run(main())
'''.strip()


_PROVIDER_VERIFY_SCRIPT = r'''
import asyncio, hmac, json, os, urllib.request
from pathlib import Path
os.environ["WEBUI_SECRET_KEY"] = (
    Path("/app/backend/.webui_secret_key").read_text(encoding="utf-8").strip()
)
from open_webui.models.config import Config

URL = "http://host.containers.internal:9071/v1"
KEY_FILE = Path("/run/secrets/bc250-openwebui-client")

async def main():
    key = KEY_FILE.read_text(encoding="utf-8").strip()
    values = await Config.get_many(
        "openai.enable", "openai.api_base_urls", "openai.api_keys",
        "openai.api_configs",
    )
    urls = list(values.get("openai.api_base_urls") or [])
    keys = list(values.get("openai.api_keys") or [])
    matches = [index for index, value in enumerate(urls) if value.rstrip("/") == URL]
    if len(matches) != 1:
        raise RuntimeError("app-owned provider is missing or ambiguous")
    index = matches[0]
    if index >= len(keys) or not hmac.compare_digest(keys[index], key):
        raise RuntimeError("app-owned provider credential is stale")
    headers = {"Authorization": "Bearer " + key}
    request = urllib.request.Request(URL + "/models", headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read(65537))
    ids = [item.get("id") for item in payload.get("data", []) if isinstance(item, dict)]
    print(json.dumps({
        "adapter": "open-webui-0.11.1-config-v1",
        "provider_ready": True,
        "model_ids": ids[:16],
    }, sort_keys=True))

asyncio.run(main())
'''.strip()


_PROVIDER_STREAM_SCRIPT = r'''
import json, urllib.request
from pathlib import Path

URL = "http://host.containers.internal:9071/v1"
key = Path("/run/secrets/bc250-openwebui-client").read_text(encoding="utf-8").strip()
model = "__MODEL_ALIAS__"
body = json.dumps({
    "model": model,
    "messages": [{"role": "user", "content": "Reply with OK."}],
    "stream": True,
    "max_tokens": 64,
}).encode("utf-8")
request = urllib.request.Request(
    URL + "/chat/completions", data=body, method="POST",
    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
)
saw_data = saw_done = saw_content = False
with urllib.request.urlopen(request, timeout=30) as response:
    for raw in response:
        if len(raw) > 65536:
            raise RuntimeError("stream frame exceeds bound")
        line = raw.decode("utf-8", "strict").strip()
        if not line.startswith("data:"):
            continue
        saw_data = True
        value = line[5:].strip()
        if value == "[DONE]":
            saw_done = True
            break
        frame = json.loads(value)
        for choice in frame.get("choices", []):
            delta = choice.get("delta", {}) if isinstance(choice, dict) else {}
            if any(
                isinstance(delta.get(field), str) and delta[field]
                for field in ("content", "reasoning_content")
            ):
                saw_content = True
if not (saw_data and saw_done and saw_content):
    raise RuntimeError("stream verification did not complete")
print(json.dumps({
    "adapter": "open-webui-0.11.1-config-v1",
    "stream_ready": True,
    "saw_done": True,
    "saw_content": True,
}, sort_keys=True))
'''.strip()


def _parse_redacted_json(result: Any, *, context: str) -> dict[str, Any]:
    if result.returncode != 0:
        raise OpenWebUIRuntimeError(f"{context} failed without changing provider secrets.")
    try:
        value = json.loads(result.stdout.strip())
    except (TypeError, ValueError) as exc:
        raise OpenWebUIRuntimeError(f"{context} returned an invalid redacted result.") from exc
    if not isinstance(value, dict) or value.get("adapter") != OPENWEBUI_PROVIDER_ADAPTER_VERSION:
        raise OpenWebUIRuntimeError(f"{context} adapter identity did not match the image pin.")
    return value


class OpenWebUIProviderAdapter:
    """Version-frozen provider mutation and content-free verification."""

    def reconcile(self, runner: CommandRunner, container: str) -> dict[str, Any]:
        result = runner.run(
            ["podman", "exec", "-i", _safe_container(container), "python", "-"],
            input_text=_PROVIDER_RECONCILE_SCRIPT,
            emit_output=False,
            timeout_seconds=30,
            max_output_bytes=OPENWEBUI_PROVIDER_OUTPUT_MAX_BYTES,
            check=False,
        )
        value = _parse_redacted_json(result, context="Open WebUI provider reconciliation")
        if value.get("credential_match") is not True:
            raise OpenWebUIRuntimeError("Open WebUI provider credential did not verify.")
        return value

    def verify_model(
        self, runner: CommandRunner, container: str, expected_model: str,
    ) -> dict[str, Any]:
        if not expected_model or len(expected_model) > 128:
            raise OpenWebUIRuntimeError("The expected public model alias is unavailable.")
        result = runner.run(
            ["podman", "exec", "-i", _safe_container(container), "python", "-"],
            input_text=_PROVIDER_VERIFY_SCRIPT,
            emit_output=False,
            timeout_seconds=30,
            max_output_bytes=OPENWEBUI_PROVIDER_OUTPUT_MAX_BYTES,
            check=False,
        )
        value = _parse_redacted_json(result, context="Open WebUI model discovery")
        model_ids = value.get("model_ids")
        if not isinstance(model_ids, list) or expected_model not in model_ids:
            raise OpenWebUIRuntimeError("The selected model is not visible to Open WebUI.")
        return {
            "adapter": OPENWEBUI_PROVIDER_ADAPTER_VERSION,
            "provider_ready": True,
            "expected_model_visible": True,
            "model": expected_model,
        }

    def verify_stream(
        self, runner: CommandRunner, container: str, expected_model: str,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_.:/+-]{1,128}", expected_model):
            raise OpenWebUIRuntimeError("The public model alias is not safe for verification.")
        script = _PROVIDER_STREAM_SCRIPT.replace(
            '"__MODEL_ALIAS__"', json.dumps(expected_model))
        result = runner.run(
            ["podman", "exec", "-i", _safe_container(container), "python", "-"],
            input_text=script,
            emit_output=False,
            timeout_seconds=45,
            max_output_bytes=OPENWEBUI_PROVIDER_OUTPUT_MAX_BYTES,
            check=False,
        )
        value = _parse_redacted_json(result, context="Open WebUI streamed completion")
        if not all(value.get(key) is True for key in ("stream_ready", "saw_done", "saw_content")):
            raise OpenWebUIRuntimeError("Open WebUI streamed completion did not finish.")
        return value


def observe_container_data_source(runner: CommandRunner, container: str) -> str:
    result = runner.run(
        ["podman", "inspect", "--format", "{{json .Mounts}}", _safe_container(container)],
        check=False, emit_output=False, timeout_seconds=10,
        max_output_bytes=OPENWEBUI_INSPECT_MAX_BYTES,
    )
    try:
        mounts = json.loads(result.stdout.strip() or "[]")
    except ValueError as exc:
        raise OpenWebUIRuntimeError("Open WebUI data storage could not be verified.") from exc
    if result.returncode != 0 or not isinstance(mounts, list):
        raise OpenWebUIRuntimeError("Open WebUI data storage could not be verified.")
    matches = [
        item for item in mounts
        if isinstance(item, dict) and item.get("Destination") == OPENWEBUI_DATA_DESTINATION
    ]
    if len(matches) != 1:
        raise OpenWebUIRuntimeError("Open WebUI must have exactly one verified data mount.")
    item = matches[0]
    source = item.get("Name") if str(item.get("Type")).lower() == "volume" else item.get("Source")
    if source not in {OPENWEBUI_DATA_VOLUME, OPENWEBUI_LEGACY_DATA_BIND}:
        raise OpenWebUIRuntimeError(
            "Open WebUI uses an unrecognized data mount; the existing container was left unchanged."
        )
    return str(source)


def verify_image_identity(runner: CommandRunner) -> None:
    digest = runner.run(
        ["podman", "image", "inspect", OPENWEBUI_IMAGE, "--format", "{{json .RepoDigests}}"],
        check=False, emit_output=False, timeout_seconds=15,
        max_output_bytes=OPENWEBUI_INSPECT_MAX_BYTES,
    )
    arch = runner.run(
        ["podman", "image", "inspect", OPENWEBUI_IMAGE, "--format", "{{.Architecture}}"],
        check=False, emit_output=False, timeout_seconds=15,
        max_output_bytes=1024,
    )
    try:
        digests = json.loads(digest.stdout.strip() or "[]")
    except ValueError:
        digests = []
    verified = (
        digest.returncode == 0 and isinstance(digests, list)
        and any(OPENWEBUI_IMAGE_DIGEST in item for item in digests if isinstance(item, str))
        and arch.returncode == 0 and arch.stdout.strip() == OPENWEBUI_ARCHITECTURE
    )
    if not verified:
        raise OpenWebUIRuntimeError("The pinned Open WebUI image digest or architecture did not verify.")


def _status(runner: CommandRunner, container: str) -> str:
    result = runner.run(
        ["podman", "inspect", "--format", "{{.State.Status}}", _safe_container(container)],
        check=False, emit_output=False, timeout_seconds=10, max_output_bytes=1024,
    )
    return result.stdout.strip().lower() if result.returncode == 0 else "absent"


def _wait_for_stopped(
    runner: CommandRunner, container: str, *, deadline_seconds: float,
    monotonic: Callable[[], float], sleeper: Callable[[float], None],
) -> None:
    deadline = monotonic() + deadline_seconds
    while monotonic() <= deadline:
        if _status(runner, container) in {"exited", "stopped", "configured", "created"}:
            return
        sleeper(0.2)
    raise OpenWebUIRuntimeError("Open WebUI did not stop at the safe promotion checkpoint.")


def _wait_for_http(
    *, timeout_seconds: float = OPENWEBUI_HTTP_TIMEOUT_SECONDS,
    opener: Callable[..., Any] = urllib.request.urlopen,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    deadline = monotonic() + timeout_seconds
    while monotonic() <= deadline:
        try:
            request = urllib.request.Request(f"{OPENWEBUI_HTTP_URL}/", method="GET")
            with opener(request, timeout=2) as response:
                if 200 <= int(response.status) < 500:
                    return
        except (OSError, urllib.error.URLError, TimeoutError):
            pass
        sleeper(0.5)
    raise OpenWebUIRuntimeError("Open WebUI application warm-up exceeded 120 seconds.")


@dataclass(frozen=True)
class OpenWebUITransactionResult:
    transaction_id: str
    container: str
    data_source: str
    prior_container: str | None
    prior_running: bool
    provider_adapter: str
    provider_ready: bool
    expected_model_visible: bool
    stream_ready: bool
    rollback_removed: bool


class OpenWebUITransaction:
    """Candidate/rollback transaction; the old container survives until verify."""

    def __init__(
        self,
        *,
        paths: AppPaths,
        runner: CommandRunner,
        provider: OpenWebUIProviderAdapter | None = None,
        http_probe: Callable[[], None] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        transaction_id: str | None = None,
    ) -> None:
        self.paths = paths
        self.runner = runner
        self.provider = provider or OpenWebUIProviderAdapter()
        self.http_probe = http_probe or _wait_for_http
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.transaction_id = transaction_id or secrets.token_hex(6)
        if not re.fullmatch(r"[0-9a-f]{12}", self.transaction_id):
            raise ValueError("invalid Open WebUI transaction identity")

    def _exists(self, name: str) -> bool:
        return self.runner.run(
            ["podman", "container", "exists", name], check=False,
            emit_output=False, timeout_seconds=10, max_output_bytes=1024,
        ).returncode == 0

    def _discover_existing(self) -> str | None:
        for name in (OPENWEBUI_CONTAINER, OPENWEBUI_LEGACY_CONTAINER):
            if self._exists(name):
                return name
        return None

    def _ensure_network(self) -> None:
        exists = self.runner.run(
            ["podman", "network", "exists", OPENWEBUI_NETWORK], check=False,
            emit_output=False, timeout_seconds=10, max_output_bytes=1024,
        )
        if exists.returncode != 0:
            self.runner.run(
                ["podman", "network", "create", OPENWEBUI_NETWORK],
                timeout_seconds=20, max_output_bytes=16 * 1024,
            )

    def _rollback(
        self, *, old_name: str | None, old_running: bool,
        candidate: str, rollback: str, promoted: bool,
    ) -> None:
        if promoted and self._exists(OPENWEBUI_CONTAINER):
            self.runner.run(
                ["podman", "stop", "--time", "10", OPENWEBUI_CONTAINER],
                check=False, timeout_seconds=OPENWEBUI_STOP_TIMEOUT_SECONDS,
            )
            self.runner.run(
                ["podman", "rename", OPENWEBUI_CONTAINER, candidate], check=False,
                timeout_seconds=10,
            )
        if old_name and self._exists(rollback):
            self.runner.run(
                ["podman", "rename", rollback, old_name], timeout_seconds=10)
            if old_running:
                self.runner.run(["podman", "start", old_name], timeout_seconds=30)
                self.http_probe()
        if self._exists(candidate):
            self.runner.run(["podman", "rm", candidate], check=False, timeout_seconds=15)

    def converge(
        self,
        *,
        client_credential_file: str | Path,
        expected_model: str,
        start_when_absent: bool = True,
    ) -> OpenWebUITransactionResult:
        credential = verify_private_secret_file(
            client_credential_file, label="Open WebUI client credential")
        secret_key = OpenWebUISecretService(self.paths).ensure()
        existing = self._discover_existing()
        old_running = existing is not None and _status(self.runner, existing) == "running"
        data_source = (
            observe_container_data_source(self.runner, existing)
            if existing else OPENWEBUI_DATA_VOLUME
        )
        pulled = self.runner.run(
            ["podman", "pull", OPENWEBUI_IMAGE], check=False,
            timeout_seconds=1800, max_output_bytes=4 * 1024 * 1024,
        )
        if pulled.returncode != 0:
            raise OpenWebUIRuntimeError(
                "The pinned Open WebUI image could not be pulled; the existing container was unchanged."
            )
        verify_image_identity(self.runner)
        self._ensure_network()
        candidate = _safe_container(f"bc250-openwebui-candidate-{self.transaction_id}")
        rollback = _safe_container(f"bc250-openwebui-rollback-{self.transaction_id}")
        spec = OpenWebUIContainerSpec(
            name=candidate,
            data_source=data_source,
            secret_key_file=str(secret_key),
            client_credential_file=str(credential),
        )
        self.runner.run(spec.create_command(), timeout_seconds=60)
        promoted = False
        try:
            # A non-zero stop result is not itself fatal: Podman/conmon can
            # report an error after the process has reached exited.  The
            # bounded observed state is the promotion gate.
            if existing and old_running:
                self.runner.run(
                    ["podman", "stop", "--time", "10", existing],
                    check=False, timeout_seconds=OPENWEBUI_STOP_TIMEOUT_SECONDS,
                )
                _wait_for_stopped(
                    self.runner, existing,
                    deadline_seconds=OPENWEBUI_STOP_TIMEOUT_SECONDS,
                    monotonic=self.monotonic, sleeper=self.sleeper,
                )
            if existing:
                self.runner.run(["podman", "rename", existing, rollback], timeout_seconds=10)
            self.runner.run(
                ["podman", "rename", candidate, OPENWEBUI_CONTAINER], timeout_seconds=10)
            promoted = True
            self.runner.run(["podman", "start", OPENWEBUI_CONTAINER], timeout_seconds=60)
            self.http_probe()
            provider = self.provider.reconcile(self.runner, OPENWEBUI_CONTAINER)
            # Reload the persistent Config into the long-running application.
            self.runner.run(
                ["podman", "restart", "--time", "10", OPENWEBUI_CONTAINER],
                timeout_seconds=60,
            )
            self.http_probe()
            model = self.provider.verify_model(
                self.runner, OPENWEBUI_CONTAINER, expected_model)
            stream = self.provider.verify_stream(
                self.runner, OPENWEBUI_CONTAINER, expected_model)
            if existing and self._exists(rollback):
                self.runner.run(["podman", "rm", rollback], timeout_seconds=20)
            final_running = old_running if existing else start_when_absent
            if not final_running:
                self.runner.run(
                    ["podman", "stop", "--time", "10", OPENWEBUI_CONTAINER],
                    check=False, timeout_seconds=OPENWEBUI_STOP_TIMEOUT_SECONDS,
                )
                _wait_for_stopped(
                    self.runner, OPENWEBUI_CONTAINER,
                    deadline_seconds=OPENWEBUI_STOP_TIMEOUT_SECONDS,
                    monotonic=self.monotonic, sleeper=self.sleeper,
                )
            receipt = {
                "schema_version": OPENWEBUI_SPEC_VERSION,
                "transaction_id": self.transaction_id,
                "container": OPENWEBUI_CONTAINER,
                "image": OPENWEBUI_IMAGE,
                "architecture": OPENWEBUI_ARCHITECTURE,
                "data_source": data_source,
                "provider_adapter": OPENWEBUI_PROVIDER_ADAPTER_VERSION,
                "provider_ready": True,
                "expected_model": expected_model,
                "expected_model_visible": True,
                "stream_ready": True,
                "verified_at": datetime.now(timezone.utc).isoformat(),
            }
            atomic_write_bytes(
                self.paths.app_dir / OPENWEBUI_RECEIPT_FILENAME,
                json.dumps(
                    receipt, sort_keys=True, separators=(",", ":"),
                ).encode("utf-8"),
                mode=0o600,
            )
            return OpenWebUITransactionResult(
                transaction_id=self.transaction_id,
                container=OPENWEBUI_CONTAINER,
                data_source=data_source,
                prior_container=existing,
                prior_running=old_running,
                provider_adapter=str(provider["adapter"]),
                provider_ready=True,
                expected_model_visible=bool(model["expected_model_visible"]),
                stream_ready=bool(stream["stream_ready"]),
                rollback_removed=not self._exists(rollback),
            )
        except BaseException as exc:
            try:
                self._rollback(
                    old_name=existing, old_running=old_running,
                    candidate=candidate, rollback=rollback, promoted=promoted,
                )
            except BaseException as rollback_exc:
                raise OpenWebUIRuntimeError(
                    "Open WebUI verification failed and automatic rollback needs guided recovery."
                ) from rollback_exc
            if isinstance(exc, OpenWebUIRuntimeError):
                raise
            raise OpenWebUIRuntimeError(
                "Open WebUI verification failed; the prior container and data were restored."
            ) from exc
