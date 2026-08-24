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

from .fsops import atomic_write_text

HANDOFF_FILENAME = "runtime-handoff.json"

# The subset of committed state whose change requires a re-render. Generic
# settings writes (disclaimers, sharing flags, chat prefs...) must not
# regenerate the artifact.
RUNTIME_FINGERPRINT_KEYS = (
    "current_model",
    "current_ctx",
    "server_port",
    "llama_cpp_path",
    "installed_models",
    "optimizations",
)


class HandoffPublicationError(RuntimeError):
    """The database commit succeeded but the artifact could not be written."""


def runtime_fingerprint(state: dict) -> str:
    """Stable digest of the committed runtime-relevant subset."""
    subset = {key: state.get(key) for key in RUNTIME_FINGERPRINT_KEYS}
    encoded = json.dumps(subset, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def build_payload(state: dict, *, config_revision: int) -> dict:
    """Render the launcher-consumption snapshot."""
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
        "schema_version": 1,
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

    def needs_update(self, fingerprint: str) -> bool:
        return self.stored_fingerprint() != fingerprint

    def publish(self, state: dict, *, config_revision: int | None = None) -> Path:
        """Durably render the artifact for ``state``'s committed revision."""
        if config_revision is None:
            config_revision = int(state.get("revision", 0))
        payload = build_payload(state, config_revision=int(config_revision))
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