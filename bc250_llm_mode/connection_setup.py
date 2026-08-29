"""Bounded Connection Assistant contracts, credential commands, and probes.

This module deliberately has no Tk imports.  The GUI and CLI consume the same
plain-data snapshot and command services.  Secret bytes are handled only by
the command/probe boundary and never appear in default serialization.
"""

from __future__ import annotations

import datetime as _datetime
import hmac
import json
import os
import secrets
import stat
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from .connection_credentials import (
    CLIENT_SCOPES,
    ConnectionAccessRepository,
    ConnectionClientRecord,
    ConnectionClientRepository,
    ConnectionCredentialError,
)
from .gateway import MAX_SECRET_LEN, fingerprint_of
from .legacy_import import utcnow


CONNECTION_SNAPSHOT_SCHEMA_VERSION = 1
CLIENT_CARD_SCHEMA_VERSION = 1
PROBE_SCHEMA_VERSION = 1
LOCAL_GATEWAY_BASE_URL = "http://127.0.0.1:9071/v1"
MAX_PROBE_BODY_BYTES = 64 * 1024
MAX_PROBE_TIMEOUT_SECONDS = 20.0
DEFAULT_PROBE_TIMEOUT_SECONDS = 10.0
PROBE_OBSERVATION_PREFIX = "connection_probe."
_SECRET_DIRECTORY = "connection-secrets"
_LEGACY_SECRET_FILENAME = "gateway-credential"


@dataclass(frozen=True)
class ClientCard:
    card_id: str
    title: str
    credential_kind: str
    support_level: str
    support_evidence: str
    field_labels: tuple[str, ...]
    streaming: str
    timeout_seconds: int
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["schema_version"] = CLIENT_CARD_SCHEMA_VERSION
        value["field_labels"] = list(self.field_labels)
        value["notes"] = list(self.notes)
        return value


# These are reviewed, bundled records.  Hardware-tested is intentionally not
# claimed until exact-candidate second-device evidence exists.
CLIENT_CARDS = (
    ClientCard(
        "openwebui", "Open WebUI", "openwebui", "protocol-tested",
        "Gateway protocol and container configuration fixtures",
        ("Base URL", "API Key", "Model"), "Enabled", 120,
        ("Use the OpenAI-compatible connection, not an /api URL.",),
    ),
    ClientCard(
        "pocketpal", "PocketPal", "pocketpal", "example-only",
        "Physical phone qualification is pending for this candidate",
        ("Base URL", "API Key", "Model", "Streaming", "Timeout"),
        "Enabled", 120,
        ("The phone must be connected to the same tailnet.",),
    ),
    ClientCard(
        "openai", "OpenAI-compatible app", "openai", "protocol-tested",
        "OpenAI-compatible request/response fixtures",
        ("Base URL", "API Key", "Model", "Streaming", "Timeout"),
        "Enabled", 120,
    ),
    ClientCard(
        "curl", "curl", "curl", "protocol-tested",
        "Bounded HTTP models and chat fixtures",
        ("Base URL", "Authorization", "Model"), "Optional", 20,
    ),
    ClientCard(
        "python", "Python OpenAI client", "openai", "example-only",
        "Example configuration; SDK-version qualification is pending",
        ("base_url", "api_key", "model", "timeout"), "Enabled", 120,
    ),
    ClientCard(
        "sse", "Raw SSE diagnostic", "sse", "protocol-tested",
        "One-event bounded SSE fixture",
        ("Base URL", "Authorization", "Model", "stream"), "Required", 20,
    ),
)
_CARD_BY_ID = {card.card_id: card for card in CLIENT_CARDS}


def client_card(card_id: str) -> ClientCard:
    try:
        return _CARD_BY_ID[str(card_id).strip().lower()]
    except KeyError as exc:
        raise ConnectionCredentialError("unknown connection client card") from exc


def _clean_dns_name(value: Any) -> str | None:
    name = str(value or "").strip().rstrip(".")
    if not name or "/" in name or "\\" in name or len(name) > 253:
        return None
    return name


def endpoint_urls(dns_name: str | None) -> dict[str, str | None]:
    """Return only the reviewed OpenAI-compatible endpoint topology."""
    dns_name = _clean_dns_name(dns_name)
    base = f"https://{dns_name}:10000/v1" if dns_name else None
    return {
        "local_base_url": LOCAL_GATEWAY_BASE_URL,
        "local_models_url": f"{LOCAL_GATEWAY_BASE_URL}/models",
        "local_chat_completions_url": f"{LOCAL_GATEWAY_BASE_URL}/chat/completions",
        "webui_url": f"https://{dns_name}:8443/" if dns_name else None,
        "base_url": base,
        "models_url": f"{base}/models" if base else None,
        "chat_completions_url": f"{base}/chat/completions" if base else None,
    }


def _public_alias(model_observation: dict[str, Any]) -> str | None:
    candidate = model_observation.get("public_alias") or model_observation.get("model_id")
    if not isinstance(candidate, str):
        return None
    alias = candidate.strip()
    lowered = alias.lower()
    if (
        not alias or len(alias) > 160 or "\\" in alias
        or alias.startswith(("/", "~")) or lowered.endswith(".gguf")
        or any(part == ".." for part in alias.split("/"))
    ):
        return None
    return alias


@dataclass(frozen=True)
class ConnectionSnapshot:
    schema_version: int
    generated_at: str
    ready: bool
    next_action: str | None
    model: dict[str, Any]
    gateway: dict[str, Any]
    openwebui: dict[str, Any]
    tailscale: dict[str, Any]
    sharing: dict[str, Any]
    urls: dict[str, Any]
    probes: dict[str, Any]
    checks: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["checks"] = list(self.checks)
        return value


def build_connection_snapshot(
    *,
    generated_at: str,
    model: dict[str, Any],
    gateway: dict[str, Any],
    openwebui: dict[str, Any],
    tailscale: dict[str, Any],
    sharing: dict[str, Any],
    probes: dict[str, Any] | None = None,
) -> ConnectionSnapshot:
    """Pure ordered readiness state machine from bounded observations."""
    alias = _public_alias(model)
    dns_name = _clean_dns_name(tailscale.get("dns_name") or sharing.get("dns_name"))
    urls = endpoint_urls(dns_name)
    probe_state = dict(probes or {})
    model_ok = bool(model.get("healthy")) and alias is not None
    credential_ok = bool(
        gateway.get("healthy") and gateway.get("enabled")
        and int(gateway.get("ready_clients", 0)) > 0)
    local_auth_ok = probe_state.get("local_authorized") == "passed"
    local_negative_ok = probe_state.get("local_unauthorized") == "passed"
    tailnet_ok = bool(tailscale.get("connected")) and dns_name is not None
    mappings_ok = bool(
        sharing.get("webui_proxy") == "http://127.0.0.1:3000"
        and sharing.get("api_proxy") == "http://127.0.0.1:9071"
    )
    funnel_ok = sharing.get("public_funnel") is False
    tailnet_models_ok = probe_state.get("tailnet_authorized_models") == "passed"
    tailnet_negative_ok = probe_state.get("tailnet_unauthorized") == "passed"
    tailnet_stream_ok = probe_state.get("tailnet_stream") == "passed"

    ordered = (
        ("model", model_ok, "Start and verify the selected model."),
        ("credential", credential_ok, "Create a client credential."),
        ("local-authorized", local_auth_ok, "Run the authorized local connection test."),
        ("local-unauthorized", local_negative_ok, "Run the required unauthorized local test."),
        ("tailscale-dns", tailnet_ok, "Connect Tailscale and wait for its DNS name."),
        ("serve-mappings", mappings_ok, "Publish the reviewed WebUI and gateway Serve mappings."),
        ("funnel-disabled", funnel_ok, "Disable Tailscale Funnel for both endpoints."),
        ("tailnet-unauthorized", tailnet_negative_ok,
         "Run the required unauthorized tailnet test."),
        ("tailnet-models", tailnet_models_ok, "Run the authorized tailnet models test."),
        ("tailnet-stream", tailnet_stream_ok,
         "Run the bounded tailnet streaming test."),
    )
    checks = tuple(
        {"id": check_id, "passed": passed, "next_action": None if passed else action}
        for check_id, passed, action in ordered
    )
    failed = next((item for item in checks if not item["passed"]), None)
    ready = failed is None
    safe_model = {**model, "public_alias": alias}
    safe_model.pop("path", None)
    safe_model.pop("model_path", None)
    safe_gateway = {
        "healthy": bool(gateway.get("healthy")),
        "enabled": bool(gateway.get("enabled")),
        "active_clients": int(gateway.get("active_clients", 0)),
        "ready_clients": int(gateway.get("ready_clients", 0)),
        "supported_paths": ["/health", "/v1/models", "/v1/chat/completions"],
    }
    return ConnectionSnapshot(
        schema_version=CONNECTION_SNAPSHOT_SCHEMA_VERSION,
        generated_at=generated_at,
        ready=ready,
        next_action=failed["next_action"] if failed else None,
        model=safe_model,
        gateway=safe_gateway,
        openwebui=dict(openwebui),
        tailscale={
            "installed": bool(tailscale.get("installed")),
            "daemon_active": bool(tailscale.get("daemon_active")),
            "connected": bool(tailscale.get("connected")),
            "dns_name": dns_name,
        },
        sharing={
            "webui_mapping_exact": bool(
                sharing.get("webui_proxy") == "http://127.0.0.1:3000"),
            "api_mapping_exact": bool(
                sharing.get("api_proxy") == "http://127.0.0.1:9071"),
            "public_funnel": sharing.get("public_funnel"),
        },
        urls=urls,
        probes=probe_state,
        checks=checks,
    )


@dataclass(frozen=True)
class CredentialCommandResult:
    action: str
    client: dict[str, Any] | None
    secret: str | None = None

    def to_dict(self, *, reveal_secret: bool = False) -> dict[str, Any]:
        value = {"action": self.action, "client": self.client}
        if reveal_secret and self.secret is not None:
            value["secret"] = self.secret
        else:
            value["secret_revealed"] = False
        return value


class ConnectionCredentialCommandService:
    """Atomic metadata + mode-0600 secret-file credential lifecycle."""

    def __init__(
        self,
        units,
        app_dir: str | Path,
        *,
        clock: Callable[[], str] = utcnow,
        id_provider: Callable[[], str] | None = None,
        secret_provider: Callable[[], str] | None = None,
    ) -> None:
        self._units = units
        self._app_dir = Path(app_dir)
        self._secret_dir = self._app_dir / _SECRET_DIRECTORY
        self._clock = clock
        self._id_provider = id_provider or (lambda: uuid.uuid4().hex)
        self._secret_provider = secret_provider or (lambda: secrets.token_urlsafe(48))

    def _path(self, client_id: str, generation: int, storage: str = "managed") -> Path:
        if storage == "legacy-singleton":
            return self._app_dir / _LEGACY_SECRET_FILENAME
        from .connection_credentials import validate_client_id

        validate_client_id(client_id)
        if not (1 <= int(generation) <= 1_000_000):
            raise ConnectionCredentialError("credential generation out of bounds")
        return self._secret_dir / f"{client_id}-{int(generation)}.secret"

    def _ensure_secret_dir(self) -> None:
        if self._secret_dir.exists() and self._secret_dir.is_symlink():
            raise ConnectionCredentialError("refusing symlinked credential directory")
        self._secret_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._secret_dir, 0o700)

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _publish_secret(self, path: Path, secret: str) -> None:
        if not (8 <= len(secret) <= MAX_SECRET_LEN):
            raise ConnectionCredentialError("generated credential length out of bounds")
        self._ensure_secret_dir()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb", closefd=False) as handle:
                handle.write(secret.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(fd)
        self._fsync_dir(path.parent)

    @staticmethod
    def _unlink(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except OSError:
            return

    @staticmethod
    def _redacted(record: ConnectionClientRecord) -> dict[str, Any]:
        value = record.to_dict()
        value.pop("active_fingerprint", None)
        value.pop("secret_storage", None)
        value["fingerprint_prefix"] = record.active_fingerprint[:8]
        return value

    def list_clients(self, *, include_revoked: bool = True) -> list[dict[str, Any]]:
        with self._units.read() as conn:
            records = ConnectionClientRepository(conn).list(include_revoked=include_revoked)
        return [self._redacted(record) for record in records]

    def access_state(self) -> dict[str, Any]:
        with self._units.read() as conn:
            return ConnectionAccessRepository(conn).get()

    def add_client(
        self,
        *,
        label: str,
        client_kind: str,
        scopes: Iterable[str] = CLIENT_SCOPES,
    ) -> CredentialCommandResult:
        client_id = self._id_provider()
        secret = self._secret_provider()
        path = self._path(client_id, 1)
        self._publish_secret(path, secret)
        try:
            with self._units.begin() as conn:
                record = ConnectionClientRepository(conn, clock=self._clock).create(
                    client_id=client_id, label=label, client_kind=client_kind,
                    scopes=scopes, fingerprint=fingerprint_of(secret),
                )
        except BaseException:
            self._unlink(path)
            raise
        return CredentialCommandResult("created", self._redacted(record), secret)

    def rotate_client(
        self,
        client_id: str,
        *,
        expected_revision: int,
        overlap_seconds: int = 0,
    ) -> CredentialCommandResult:
        if not (0 <= int(overlap_seconds) <= 900):
            raise ConnectionCredentialError("overlap must be 0 or 1-900 seconds")
        if int(overlap_seconds) != 0 and int(overlap_seconds) < 1:
            raise ConnectionCredentialError("overlap must be 0 or 1-900 seconds")
        with self._units.read() as conn:
            prior = ConnectionClientRepository(conn).get(client_id)
        if prior is None:
            from .repositories import RepositoryConflict
            raise RepositoryConflict("CLIENT_NOT_FOUND", "client does not exist")
        generation = prior.active_generation + 1
        secret = self._secret_provider()
        new_path = self._path(client_id, generation)
        self._publish_secret(new_path, secret)
        overlap_expires_at = None
        if overlap_seconds:
            now = _parse_timestamp(self._clock())
            overlap_expires_at = _format_timestamp(
                now + _datetime.timedelta(seconds=int(overlap_seconds)))
        try:
            with self._units.begin() as conn:
                record = ConnectionClientRepository(conn, clock=self._clock).rotate(
                    client_id, expected_revision=expected_revision,
                    new_fingerprint=fingerprint_of(secret),
                    new_generation=generation,
                    overlap_expires_at=overlap_expires_at,
                )
        except BaseException:
            self._unlink(new_path)
            raise
        if not overlap_seconds:
            self._unlink(self._path(client_id, prior.active_generation, prior.secret_storage))
        return CredentialCommandResult("rotated", self._redacted(record), secret)

    def revoke_client(self, client_id: str, *, expected_revision: int) -> CredentialCommandResult:
        with self._units.begin() as conn:
            repo = ConnectionClientRepository(conn, clock=self._clock)
            prior = repo.get(client_id)
            record = repo.revoke(client_id, expected_revision=expected_revision)
        if prior is not None:
            for generation in range(1, prior.active_generation + 1):
                self._unlink(self._path(client_id, generation, prior.secret_storage))
        return CredentialCommandResult("revoked", self._redacted(record))

    def disable_all(self, *, expected_revision: int) -> dict[str, Any]:
        with self._units.begin() as conn:
            state = ConnectionAccessRepository(conn, clock=self._clock).disable_all(
                expected_revision=expected_revision)
        try:
            entries = tuple(self._secret_dir.iterdir())
        except OSError:
            entries = ()
        for entry in entries:
            if entry.is_file() and entry.name.endswith(".secret"):
                self._unlink(entry)
        # The legacy credential is also invalidated durably by migration-011
        # metadata; remove its file best-effort without touching other files.
        self._unlink(self._app_dir / _LEGACY_SECRET_FILENAME)
        return {"action": "disabled-all", "access": state}

    def _read_secret(self, record: ConnectionClientRecord) -> str | None:
        path = self._path(record.client_id, record.active_generation, record.secret_storage)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = None
        try:
            fd = os.open(path, flags)
            file_stat = os.fstat(fd)
            if not stat.S_ISREG(file_stat.st_mode) or (file_stat.st_mode & 0o077):
                return None
            raw = os.read(fd, MAX_SECRET_LEN + 1)
        except OSError:
            return None
        finally:
            if fd is not None:
                os.close(fd)
        if not (8 <= len(raw) <= MAX_SECRET_LEN):
            return None
        try:
            secret = raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            return None
        if not hmac.compare_digest(fingerprint_of(secret), record.active_fingerprint):
            return None
        return secret

    def secret_for_probe(self, client_id: str) -> str:
        with self._units.read() as conn:
            record = ConnectionClientRepository(conn).get(client_id)
            access = ConnectionAccessRepository(conn).get()
        if record is None or record.revoked_at or not access["enabled"]:
            raise ConnectionCredentialError("client credential is unavailable")
        secret = self._read_secret(record)
        if secret is None:
            raise ConnectionCredentialError("client credential file is missing or mismatched")
        return secret

    def readiness(self) -> dict[str, Any]:
        with self._units.read() as conn:
            records = ConnectionClientRepository(conn).list(include_revoked=False)
            access = ConnectionAccessRepository(conn).get()
        ready = sum(1 for record in records if self._read_secret(record) is not None)
        return {
            "enabled": bool(access["enabled"]),
            "active_clients": len(records),
            "ready_clients": ready,
            "revision": access["revision"],
        }

    def resolve_openwebui_credential_file(self) -> str | None:
        """Internal container adapter path; never included in snapshots."""
        with self._units.read() as conn:
            records = ConnectionClientRepository(conn).list(include_revoked=False)
            access = ConnectionAccessRepository(conn).get()
        if not access["enabled"]:
            return None
        ranked = sorted(
            records,
            key=lambda item: (
                0 if item.client_kind == "openwebui" else 1,
                0 if item.client_id == "legacy-install" else 1,
                item.created_at,
            ),
        )
        for record in ranked:
            if self._read_secret(record) is not None:
                return str(self._path(
                    record.client_id, record.active_generation,
                    record.secret_storage))
        return None


def _parse_timestamp(value: str) -> _datetime.datetime:
    parsed = _datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_datetime.timezone.utc)
    return parsed.astimezone(_datetime.timezone.utc)


def _format_timestamp(value: _datetime.datetime) -> str:
    return value.astimezone(_datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class AuthenticatedConnectionClient:
    client_id: str
    scopes: tuple[str, ...]


class MultiClientAuthenticationStore:
    """Gateway adapter resolving all active/overlap fingerprints."""

    def __init__(self, units, *, clock: Callable[[], str] = utcnow) -> None:
        self._units = units
        self._clock = clock

    def authenticate(self, presented: str) -> AuthenticatedConnectionClient | None:
        if not (1 <= len(presented) <= MAX_SECRET_LEN):
            return None
        presented_fp = fingerprint_of(presented)
        with self._units.read() as conn:
            candidates = ConnectionClientRepository(conn).authentication_fingerprints(
                now=self._clock())
        match = None
        # Compare every eligible fingerprint, even after a match.
        for candidate in candidates:
            if hmac.compare_digest(presented_fp, candidate.fingerprint):
                match = AuthenticatedConnectionClient(
                    candidate.client_id, candidate.scopes)
        return match

    def record_use(self, client_id: str, endpoint_class: str) -> None:
        """Best-effort redacted usage freshness; no address/body/model data."""
        from .repositories import RepositoryConflict

        try:
            with self._units.begin() as conn:
                repo = ConnectionClientRepository(conn, clock=self._clock)
                current = repo.get(client_id)
                if current is None or current.revoked_at is not None:
                    return
                if current.last_used_at and current.last_endpoint_class == endpoint_class:
                    try:
                        age = _parse_timestamp(self._clock()) - _parse_timestamp(
                            current.last_used_at)
                        if age < _datetime.timedelta(seconds=60):
                            return
                    except ValueError:
                        pass
                repo.record_use(
                    client_id, endpoint_class=endpoint_class,
                    expected_revision=current.revision)
        except RepositoryConflict:
            # Concurrent requests may race the receipt CAS.  Usage evidence is
            # explicitly non-authoritative, so the winner is sufficient.
            return


@dataclass(frozen=True)
class ProbeHTTPResponse:
    status: int
    json_value: Any = None
    valid_sse_event: bool = False
    truncated: bool = False


class ProbeTransport(Protocol):
    def request(
        self, *, method: str, url: str, token: str | None,
        body: dict[str, Any] | None = None, stream: bool = False,
        timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    ) -> ProbeHTTPResponse: ...


class BoundedHTTPProbeTransport:
    """Small urllib adapter with hard response/deadline bounds."""

    def request(
        self, *, method: str, url: str, token: str | None,
        body: dict[str, Any] | None = None, stream: bool = False,
        timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    ) -> ProbeHTTPResponse:
        timeout = max(0.1, min(float(timeout), MAX_PROBE_TIMEOUT_SECONDS))
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8") if body else None
        headers = {"Accept": "text/event-stream" if stream else "application/json"}
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=encoded, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            status = int(response.status)
            if stream:
                consumed = 0
                while consumed < MAX_PROBE_BODY_BYTES:
                    line = response.readline(min(4096, MAX_PROBE_BODY_BYTES - consumed + 1))
                    if not line:
                        break
                    consumed += len(line)
                    if line.startswith(b"data:"):
                        payload = line[5:].strip()
                        if payload == b"[DONE]":
                            continue
                        try:
                            value = json.loads(payload)
                        except ValueError:
                            continue
                        if isinstance(value, dict):
                            return ProbeHTTPResponse(status, valid_sse_event=True)
                return ProbeHTTPResponse(
                    status, valid_sse_event=False,
                    truncated=consumed >= MAX_PROBE_BODY_BYTES)
            raw = response.read(MAX_PROBE_BODY_BYTES + 1)
            truncated = len(raw) > MAX_PROBE_BODY_BYTES
            raw = raw[:MAX_PROBE_BODY_BYTES]
            try:
                value = json.loads(raw) if raw else None
            except ValueError:
                value = None
            return ProbeHTTPResponse(status, value, truncated=truncated)


@dataclass(frozen=True)
class ConnectionProbeReport:
    schema_version: int
    observed_at: str
    client_id: str
    results: dict[str, str]
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ConnectionProbeService:
    """Mandatory auth-positive/negative probes with no response persistence."""

    def __init__(
        self, units, credentials: ConnectionCredentialCommandService,
        *, transport: ProbeTransport | None = None,
        clock: Callable[[], str] = utcnow,
    ) -> None:
        self._units = units
        self._credentials = credentials
        self._transport = transport or BoundedHTTPProbeTransport()
        self._clock = clock

    @staticmethod
    def _models_has_alias(response: ProbeHTTPResponse, alias: str) -> bool:
        if response.status != 200 or response.truncated or not isinstance(response.json_value, dict):
            return False
        rows = response.json_value.get("data")
        return bool(isinstance(rows, list) and any(
            isinstance(row, dict) and row.get("id") == alias for row in rows[:64]))

    def run(
        self, *, client_id: str, public_alias: str,
        tailnet_base_url: str | None = None,
    ) -> ConnectionProbeReport:
        alias = _public_alias({"public_alias": public_alias})
        if alias is None:
            raise ConnectionCredentialError("observed public model alias is unavailable")
        secret = self._credentials.secret_for_probe(client_id)
        local_models = f"{LOCAL_GATEWAY_BASE_URL}/models"
        unauthorized_local = self._transport.request(
            method="GET", url=local_models, token=None)
        authorized_local = self._transport.request(
            method="GET", url=local_models, token=secret)
        results = {
            "local_unauthorized": (
                "passed" if unauthorized_local.status in {401, 403} else "failed"),
            "local_authorized": (
                "passed" if self._models_has_alias(authorized_local, alias) else "failed"),
            "tailnet_unauthorized": "not-checked",
            "tailnet_authorized_models": "not-checked",
            "tailnet_stream": "not-checked",
        }
        if tailnet_base_url:
            if not tailnet_base_url.startswith("https://") or not tailnet_base_url.endswith("/v1"):
                raise ConnectionCredentialError("tailnet base URL must be the reviewed HTTPS /v1 endpoint")
            tail_models = f"{tailnet_base_url}/models"
            unauthorized_tail = self._transport.request(
                method="GET", url=tail_models, token=None)
            authorized_tail = self._transport.request(
                method="GET", url=tail_models, token=secret)
            stream = self._transport.request(
                method="POST", url=f"{tailnet_base_url}/chat/completions",
                token=secret, stream=True,
                body={
                    "model": alias,
                    "messages": [{"role": "user", "content": "Reply with one word."}],
                    "max_tokens": 1,
                    "stream": True,
                }, timeout=MAX_PROBE_TIMEOUT_SECONDS,
            )
            results.update(
                tailnet_unauthorized=(
                    "passed" if unauthorized_tail.status in {401, 403} else "failed"),
                tailnet_authorized_models=(
                    "passed" if self._models_has_alias(authorized_tail, alias) else "failed"),
                tailnet_stream=(
                    "passed" if stream.status == 200 and stream.valid_sse_event else "failed"),
            )
        observed_at = self._clock()
        required = [results["local_unauthorized"], results["local_authorized"]]
        if tailnet_base_url:
            required.extend((results["tailnet_unauthorized"],
                             results["tailnet_authorized_models"],
                             results["tailnet_stream"]))
        report = ConnectionProbeReport(
            PROBE_SCHEMA_VERSION, observed_at, client_id, results,
            passed=all(value == "passed" for value in required),
        )
        # Persist only terminal classifications/freshness.  No body, URL,
        # address, token, model, prompt, or response data crosses this line.
        with self._units.begin() as conn:
            conn.execute(
                "INSERT INTO runtime_observations(key, payload_json, observed_at, stale) "
                "VALUES (?, ?, ?, 0) ON CONFLICT(key) DO UPDATE SET "
                "payload_json=excluded.payload_json, observed_at=excluded.observed_at, stale=0",
                (f"{PROBE_OBSERVATION_PREFIX}{client_id}",
                 json.dumps({"results": results, "passed": report.passed},
                            sort_keys=True, separators=(",", ":")), observed_at),
            )
        return report


class ConnectionSetupQueryService:
    """One live, read-only source for every connection-facing frontend."""

    def __init__(
        self,
        units,
        credentials: ConnectionCredentialCommandService,
        *,
        state_supplier: Callable[[], dict[str, Any]],
        runner_factory: Callable[[], Any],
        model_server: Any,
        openwebui: Any,
        tailscale: Any,
        sharing: Any,
        transport: ProbeTransport | None = None,
        clock: Callable[[], str] = utcnow,
    ) -> None:
        self._units = units
        self._credentials = credentials
        self._state_supplier = state_supplier
        self._runner_factory = runner_factory
        self._model_server = model_server
        self._openwebui = openwebui
        self._tailscale = tailscale
        self._sharing = sharing
        self._transport = transport or BoundedHTTPProbeTransport()
        self._clock = clock

    @staticmethod
    def _safe_status(call: Callable[[], Any]) -> dict[str, Any]:
        try:
            value = call()
        except Exception:  # live observation is unavailable, not application truth
            return {"available": False}
        return dict(value) if isinstance(value, dict) else {"available": False}

    def _model_observation(self, state: dict[str, Any], runner: Any) -> dict[str, Any]:
        service = self._safe_status(lambda: self._model_server.status(state, runner))
        port = int(state.get("server_port", 8080))
        response = self._safe_request(
            method="GET", url=f"http://127.0.0.1:{port}/v1/models", token=None)
        alias = None
        if response.status == 200 and isinstance(response.json_value, dict):
            rows = response.json_value.get("data")
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                alias = _public_alias({"model_id": rows[0].get("id")})
        return {
            "healthy": bool(service.get("active") and response.status == 200 and alias),
            "service_active": bool(service.get("active")),
            "public_alias": alias,
        }

    def _gateway_observation(self) -> dict[str, Any]:
        response = self._safe_request(
            method="GET", url="http://127.0.0.1:9071/health", token=None)
        payload = response.json_value if isinstance(response.json_value, dict) else {}
        readiness = self._credentials.readiness()
        return {
            **readiness,
            "healthy": bool(
                response.status == 200
                and payload.get("status") == "ready"
                and payload.get("backend_identity") == "verified"),
        }

    def _safe_request(self, **kwargs: Any) -> ProbeHTTPResponse:
        try:
            return self._transport.request(**kwargs)
        except (OSError, TimeoutError, urllib.error.URLError, ValueError):
            return ProbeHTTPResponse(0)

    def _probe_freshness(self, client_id: str | None) -> dict[str, Any]:
        with self._units.read() as conn:
            if client_id:
                rows = conn.execute(
                    "SELECT key, payload_json, observed_at FROM runtime_observations "
                    "WHERE key = ? AND stale = 0 LIMIT 1",
                    (f"{PROBE_OBSERVATION_PREFIX}{client_id}",),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT key, payload_json, observed_at FROM runtime_observations "
                    "WHERE key LIKE ? AND stale = 0 ORDER BY observed_at DESC LIMIT 1",
                    (f"{PROBE_OBSERVATION_PREFIX}%",),
                ).fetchall()
        if not rows:
            return {"observed_at": None}
        row = rows[0]
        try:
            payload = json.loads(row["payload_json"])
        except (ValueError, TypeError):
            return {"observed_at": row["observed_at"]}
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, dict):
            results = {}
        clean = {
            key: value for key, value in results.items()
            if key in {
                "local_unauthorized", "local_authorized",
                "tailnet_unauthorized", "tailnet_authorized_models",
                "tailnet_stream",
            } and value in {"passed", "failed", "not-checked"}
        }
        clean["observed_at"] = row["observed_at"]
        return clean

    def snapshot(self, *, client_id: str | None = None) -> ConnectionSnapshot:
        state = dict(self._state_supplier())
        runner = self._runner_factory()
        model = self._model_observation(state, runner)
        gateway = self._gateway_observation()
        # sharing.py's compatibility checks consume transient gateway fields;
        # this changes only the disposable state dictionary.
        state.update(
            gateway_provisioned=gateway["active_clients"] > 0,
            gateway_verified=gateway["ready_clients"] > 0 and gateway["enabled"],
            gateway_backend_identity=(
                "verified" if gateway["ready_clients"] > 0 and gateway["enabled"]
                else "unverified"),
        )
        openwebui = self._safe_status(lambda: self._openwebui.status(state, runner))
        tailscale = self._safe_status(lambda: self._tailscale.status(runner))
        sharing = self._safe_status(lambda: self._sharing.status(state, runner))
        return build_connection_snapshot(
            generated_at=self._clock(), model=model, gateway=gateway,
            openwebui={
                "installed": bool(openwebui.get("installed")),
                "running": bool(openwebui.get("running")),
                "healthy": bool(openwebui.get("running")
                                and openwebui.get("digest_verified")),
            },
            tailscale=tailscale, sharing=sharing,
            probes=self._probe_freshness(client_id),
        )


def instructions_for(
    card_id: str, *, urls: dict[str, Any], public_alias: str | None,
) -> dict[str, Any]:
    card = client_card(card_id)
    alias = _public_alias({"public_alias": public_alias})
    base_url = urls.get("base_url")
    available = bool(base_url and alias)
    return {
        "schema_version": CLIENT_CARD_SCHEMA_VERSION,
        "card": card.to_dict(),
        "available": available,
        "values": {
            "Base URL": base_url,
            "API Key": "Use the one-time key for this client",
            "Model": alias,
            "Streaming": card.streaming,
            "Timeout": f"{card.timeout_seconds} seconds",
        },
        "unavailable_reason": None if available else (
            "A Tailscale DNS endpoint and observed public model alias are required."),
    }
