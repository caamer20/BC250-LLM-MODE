"""Runtime configuration service and handoff renderer (ADR 001 artifact).

``runtime-handoff.json`` is a *rendered artifact* of committed durable
state — the generated launcher execs the argv it describes at daemon
start. It is NOT authoritative state and is not a dual write: it is
regenerated only after committed changes to runtime/model/profile
content, plus on demand at runtime start when missing or stale.

The renderer is the only component allowed to write it; generic settings
writes never touch it. Publication failure is reported independently of
the database commit that triggered the render.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from dataclasses import dataclass

from .fsops import atomic_write_text

HANDOFF_FILENAME = "runtime-handoff.json"
HANDOFF_SCHEMA_VERSION_V2 = 2

# The subset of committed state whose change requires a re-render. Generic
# settings writes (disclaimers, sharing flags, chat prefs...) must not
# regenerate the artifact. U1.2: the runtime COMPONENT identity participates
# so swapping content at the same path regenerates the artifact (F6B.1.4).
RUNTIME_FINGERPRINT_KEYS = (
    "current_model",
    "current_ctx",
    "server_port",
    "llama_cpp_path",
    "installed_models",
    "optimizations",
    "runtime_component_id",
    "runtime_manifest_digest",
)

# Required v2-only fields (ADR 004 D5): the handoff binds configuration to
# the exact immutable runtime component that must serve it.
RUNTIME_IDENTITY_KEYS = (
    "runtime_component_id",
    "runtime_source_commit",
    "runtime_server_sha256",
    "runtime_manifest_digest",
    "runtime_operation_id",
)


@dataclass(frozen=True)
class RuntimeIdentityV2:
    """Immutable component identity bound into a v2 handoff."""

    component_id: str          # llamacpp:sha256:<hex> or legacy:<id>
    source_commit: str         # full commit or "" for legacy adoption
    server_sha256: str
    manifest_digest: str
    operation_id: str = ""     # empty only for stable regeneration


class HandoffPublicationError(RuntimeError):
    """The database commit succeeded but the artifact could not be written."""


def runtime_fingerprint(state: dict) -> str:
    """Stable digest of the committed runtime-relevant subset."""
    subset = {key: state.get(key) for key in RUNTIME_FINGERPRINT_KEYS}
    encoded = json.dumps(subset, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def build_payload(
    state: dict,
    *,
    config_revision: int,
    runtime_identity: "RuntimeIdentityV2 | None" = None,
) -> dict:
    """Render the launcher-consumption snapshot.

    Without ``runtime_identity`` the LEGACY v1 shape is rendered (schema
    stays 1). With identity, the artifact becomes schema v2 and binds the
    exact immutable component (ADR 004 §14.1).
    """
    opts = state.get("optimizations") or {}
    effective = opts if opts.get("runtime_enabled", True) else {}
    current_id = state.get("current_model")
    model_path = ""
    alias = str(current_id or "local")
    sampling = {}
    for record in state.get("installed_models") or []:
        if record.get("id") == current_id:
            model_path = str(record.get("path", ""))
            alias = str(
                record.get("display_name") or alias
            ).replace("\n", " ").strip()
            sampling = {
                k: record[k]
                for k in ("temperature", "top_p", "top_k", "min_p")
                if k in record
            }
            break
    slots = int(effective.get("parallel_slots", 4))
    payload = {
        "schema_version": 2 if runtime_identity is not None else 1,
        "config_revision": int(config_revision),
        "runtime_fingerprint": runtime_fingerprint(state),
        "model_id": current_id,
        "llama_cpp_path": str(state.get("llama_cpp_path", "/root/llama.cpp")),
        "model_path": model_path,
        "alias": alias,
        "ctx_total": int(state.get("current_ctx", 8192)) * slots,
        "port": int(state.get("server_port", 8080)),
        "flash_attention": effective.get("flash_attention", "auto"),
        "batch_size": int(effective.get("batch_size", 2048)),
        "ubatch_size": int(effective.get("ubatch_size", 512)),
        "kv_cache_type": effective.get("kv_cache_type", "q8_0"),
        "parallel_slots": slots,
        "threads": effective.get("threads"),
        "fast_sync": bool(effective.get("fast_sync")),
    }
    payload.update(sampling)
    if runtime_identity is not None:
        payload.update({
            "runtime_component_id": runtime_identity.component_id,
            "runtime_source_commit": runtime_identity.source_commit,
            "runtime_server_sha256": runtime_identity.server_sha256,
            "runtime_manifest_digest": runtime_identity.manifest_digest,
            "runtime_operation_id": runtime_identity.operation_id,
        })
    return payload


class RuntimeHandoffRenderer:
    """Sole writer of ``runtime-handoff.json``."""

    def __init__(self, app_dir: Path | str) -> None:
        self.app_dir = Path(app_dir)

    @property
    def path(self) -> Path:
        return self.app_dir / HANDOFF_FILENAME

    def stored_fingerprint(self) -> str | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return payload.get("runtime_fingerprint")

    def observe(self, *, require_v2: bool = False) -> dict | None:
        """Strict read-only observation (Session 5C plan §10.2).

        Returns the full payload ONLY when it is valid and complete for its
        declared schema. ``require_v2=True`` additionally rejects legacy
        schema-1 artifacts — managed runtime starts demand the bound
        component identity (ADR 004 §14.1). Malformed content is the
        caller's UNCERTAIN signal.
        """
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        required = (
            "schema_version", "config_revision", "runtime_fingerprint",
            "model_id", "model_path", "alias", "llama_cpp_path",
            "port", "ctx_total", "parallel_slots",
            "flash_attention", "batch_size", "ubatch_size", "kv_cache_type",
        )
        if any(key not in payload for key in required):
            return None
        try:
            schema = int(payload["schema_version"])
            revision = int(payload["config_revision"])
            port = int(payload["port"])
            slots = int(payload["parallel_slots"])
            ctx_total = int(payload["ctx_total"])
            batch = int(payload["batch_size"])
            ubatch = int(payload["ubatch_size"])
        except (TypeError, ValueError):
            return None
        if schema not in (1, HANDOFF_SCHEMA_VERSION_V2):
            return None
        if require_v2 and schema != HANDOFF_SCHEMA_VERSION_V2:
            return None
        if revision < 1 or not 1024 <= port <= 65535:
            return None
        if not 1 <= slots <= 8:
            return None
        if not slots * 512 <= ctx_total <= slots * 262144:
            return None
        if not 16 <= batch <= 8192 or not 16 <= ubatch <= 8192:
            return None
        for key in ("runtime_fingerprint", "model_id", "model_path", "alias",
                    "llama_cpp_path"):
            value = payload[key]
            if not isinstance(value, str) or not value.strip():
                return None
        if schema == HANDOFF_SCHEMA_VERSION_V2:
            for key in RUNTIME_IDENTITY_KEYS:
                if key not in payload or not isinstance(payload[key], str) \
                        or not payload[key].strip():
                    return None
            digest = payload["runtime_manifest_digest"]
            server = payload["runtime_server_sha256"]
            if len(digest) != 64 or any(c not in "0123456789abcdef"
                                        for c in digest):
                return None
            if len(server) != 64 or any(c not in "0123456789abcdef"
                                        for c in server):
                return None
        return payload

    def needs_update(self, fingerprint: str) -> bool:
        return self.stored_fingerprint() != fingerprint

    def publish(
        self,
        state: dict,
        *,
        config_revision: int | None = None,
        runtime_identity: "RuntimeIdentityV2 | None" = None,
    ) -> Path:
        """Durably render the artifact for ``state``'s committed revision."""
        if config_revision is None:
            config_revision = int(state.get("revision", 0))
        payload = build_payload(
            state,
            config_revision=int(config_revision),
            runtime_identity=runtime_identity,
        )
        try:
            atomic_write_text(self.path, json.dumps(payload, indent=2))
        except OSError as exc:
            raise HandoffPublicationError(
                f"Could not publish {self.path}: {exc}"
            ) from exc
        return self.path

    def regenerate_if_stale(self, state: dict) -> bool:
        """Publish when the artifact is missing or older than ``state``."""
        fingerprint = runtime_fingerprint(state)
        if not self.needs_update(fingerprint):
            return False
        self.publish(state)
        return True


def regenerate_for_app_state(state: dict) -> bool:
    """Best-effort regeneration at daemon start; never blocks startup."""
    try:
        renderer = RuntimeHandoffRenderer(Path(str(state.get("app_dir", "."))))
        return renderer.regenerate_if_stale(state)
    except (OSError, HandoffPublicationError):
        return False


class RuntimeHandoffService:
    """Decides when committed changes require a fresh artifact.

    Transitional home for handoff policy while the compatibility facade
    exists; after the facade removal, domain commands call
    ``on_committed`` explicitly and this class outlives the facade.
    """

    def __init__(self, renderer: RuntimeHandoffRenderer) -> None:
        self.renderer = renderer

    def on_committed(self, state: dict, *, config_revision: int) -> bool:
        """Publish iff runtime-relevant content changed; returns published.

        ``config_revision`` is the revision the database now carries (the
        caller just committed it). Raises HandoffPublicationError on
        failure so callers can report it separately from the
        already-successful database commit.
        """
        fingerprint = runtime_fingerprint(state)
        if not self.renderer.needs_update(fingerprint):
            return False
        self.renderer.publish(state, config_revision=config_revision)
        return True

    def ensure_current(self, state: dict) -> bool:
        """Idempotent start-of-runtime refresh (missing or stale)."""
        return self.renderer.regenerate_if_stale(state)