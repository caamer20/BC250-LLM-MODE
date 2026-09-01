"""P5 §11.3: the support bundle — redacted by construction.

A support bundle is a bounded, cancellable, READ-ONLY export of diagnostic
material that is safe to hand to a third party. It is redacted BY
CONSTRUCTION, not by review:

- conversation content (raw prompts / completions) is NEVER read;
- credential/secret files are read ONLY to feed the scrubber, never emitted;
- the raw database, backups, model binaries, and staging are never copied;
- settings values whose names look secret are masked;
- free text is scrubbed for tokens, auth headers, and private-key blocks;
- absolute paths are normalized to ``<profile>/…`` labels;
- exact model filenames are replaceable with ``<model-N>`` labels because
  filenames may themselves be sensitive (configurable);
- every included file is size-bounded and the whole bundle is size-bounded;
- a manifest records the redaction policy, per-file digests, and a single
  bundle digest.

Generation checks an injectable ``cancel`` callable between steps and raises
:class:`SupportBundleCancelled` without leaving a misleading manifest.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .paths import AppPaths
from .unit_of_work import UnitOfWorkFactory

SUPPORT_BUNDLE_SCHEMA_VERSION = 1

# --- bounds (generation is bounded) -------------------------------------------
MAX_LOG_FILES = 8
MAX_LOG_TAIL_BYTES = 64 * 1024          # per log file
MAX_FILE_BYTES = 256 * 1024             # per emitted text file
MAX_TOTAL_BYTES = 4 * 1024 * 1024       # whole bundle payload

REDACTED = "[REDACTED]"
PROFILE_LABEL = "<profile>"

# Setting-key substrings whose values are masked unconditionally.
SECRET_KEY_SUBSTRINGS = (
    "token", "secret", "password", "passwd", "credential",
    "api_key", "apikey", "private_key", "auth",
)

# Free-text secret shapes scrubbed from any emitted text.
_SECRET_PATTERNS = (
    # PEM private-key blocks.
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?"
        r"-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    # Authorization / Bearer headers.
    re.compile(r"(?i)\b(bearer|authorization)\b[\s:=]+[A-Za-z0-9._~+/=-]{8,}"),
    # Hugging Face user tokens.
    re.compile(r"\bhf_[A-Za-z0-9]{16,}\b"),
    # OpenAI-style keys.
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    # Generic long hex/base64 secret assignments (key=..., key: ...).
    re.compile(
        r"(?i)\b(\w*(token|secret|password|passwd|api_?key|credential)\w*)"
        r"[\s]*[=:]\s*['\"]?[A-Za-z0-9._~+/=-]{12,}['\"]?"),
)


class SupportBundleCancelled(RuntimeError):
    """Raised when the operator cancels bundle generation."""


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class Redactor:
    """Deterministic, bounded text redaction for support material."""

    def __init__(
        self,
        profile_root: Path | str,
        *,
        redact_model_filenames: bool = True,
    ) -> None:
        self._profile_root = str(Path(profile_root))
        self._redact_model_filenames = redact_model_filenames
        self._secrets: set[str] = set()
        self._model_map: dict[str, str] = {}

    # -- configuration ---------------------------------------------------------

    def add_secret(self, value: str | None) -> None:
        if value and len(value) >= 8:
            self._secrets.add(value)

    def register_model_filename(self, filename: str | None) -> None:
        if not filename or not self._redact_model_filenames:
            return
        name = os.path.basename(str(filename))
        if name and name not in self._model_map:
            self._model_map[name] = f"<model-{len(self._model_map) + 1}>"

    # -- redaction -------------------------------------------------------------

    def redact_path(self, path: str | Path | None) -> str:
        if path is None:
            return ""
        text = str(path)
        if self._profile_root and self._profile_root in text:
            text = text.replace(self._profile_root, PROFILE_LABEL)
        return self._redact_filenames(text)

    def redact(self, text: str) -> str:
        if not text:
            return ""
        out = text
        for secret in self._secrets:
            out = out.replace(secret, REDACTED)
        for pattern in _SECRET_PATTERNS:
            out = pattern.sub(REDACTED, out)
        if self._profile_root:
            out = out.replace(self._profile_root, PROFILE_LABEL)
        return self._redact_filenames(out)

    def _redact_filenames(self, text: str) -> str:
        if not self._redact_model_filenames:
            return text
        out = text
        for name, label in self._model_map.items():
            out = out.replace(name, label)
        return out

    @property
    def policy(self) -> dict[str, Any]:
        return {
            "profile_paths_normalized": True,
            "model_filenames_redacted": self._redact_model_filenames,
            "secret_keys_masked": sorted(SECRET_KEY_SUBSTRINGS),
            "free_text_patterns_scrubbed": [
                "private_key_block", "auth_header", "hf_token",
                "openai_key", "secret_assignment",
            ],
            "conversations_included": False,
            "raw_database_included": False,
            "credential_files_included": False,
        }


def mask_settings(settings: dict[str, Any], redactor: Redactor) -> dict[str, Any]:
    """Mask secret-named keys and redact string values recursively."""

    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            out = {}
            for key, item in value.items():
                lowered = str(key).lower()
                if any(sub in lowered for sub in SECRET_KEY_SUBSTRINGS):
                    out[key] = REDACTED if item not in (None, "", False) else item
                else:
                    out[key] = walk(item)
            return out
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, str):
            return redactor.redact(value)
        return value

    return walk(dict(settings))


@dataclass(frozen=True)
class SupportBundleManifest:
    schema_version: int
    generated_at: str
    bundle_dir: str
    files: tuple[dict[str, Any], ...]
    bundle_sha256: str
    total_bytes: int
    truncated: bool
    redaction_policy: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "bundle_dir": self.bundle_dir,
            "files": list(self.files),
            "bundle_sha256": self.bundle_sha256,
            "total_bytes": self.total_bytes,
            "truncated": self.truncated,
            "redaction_policy": self.redaction_policy,
        }


class SupportBundleService:
    """Builds a bounded, redacted support bundle from read units + paths."""

    def __init__(
        self,
        units: UnitOfWorkFactory,
        paths: AppPaths,
        *,
        home: Any = None,
        doctor: Any = None,
        platform: Any = None,
        notifications: Any = None,
        readiness: Any = None,
        clock: Callable[[], str] | None = None,
        redact_model_filenames: bool = True,
    ) -> None:
        self._units = units
        self._paths = paths
        self._home = home
        self._doctor = doctor
        self._platform = platform
        self._notifications = notifications
        self._readiness = readiness
        self._clock = clock or _utcnow
        self._redact_model_filenames = redact_model_filenames

    def attach_readiness_source(self, readiness: Any) -> None:
        """Attach the composed query source after application composition."""
        self._readiness = readiness

    # --- public API ------------------------------------------------------------

    def build(
        self,
        output_dir: Path | str,
        *,
        cancel: Callable[[], bool] | None = None,
    ) -> SupportBundleManifest:
        bundle_dir = Path(output_dir)
        bundle_dir.mkdir(parents=True, exist_ok=True)
        redactor = self._make_redactor()
        self._check(cancel)

        files: list[dict[str, Any]] = []
        total = 0
        truncated = False

        def emit(rel_path: str, text: str) -> None:
            nonlocal total, truncated
            self._check(cancel)
            data = text.encode("utf-8")
            if len(data) > MAX_FILE_BYTES:
                data = data[:MAX_FILE_BYTES]
                truncated = True
            if total + len(data) > MAX_TOTAL_BYTES:
                truncated = True
                return
            target = bundle_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            files.append({
                "path": rel_path,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            })
            total += len(data)

        # 1) home snapshot — the SAME composed service the CLI/GUI use.
        emit("home.json", self._dump(self._home_snapshot(), redactor))
        # 2) doctor report.
        emit("doctor.json", self._dump(self._doctor_report(), redactor))
        if self._readiness is not None:
            emit("readiness.json", self._dump(
                self._readiness_report(), redactor))
        # 3) disposable host-platform observations (never durable truth).
        if self._platform is not None:
            emit("platform.json", self._dump(self._platform.status(), redactor))
        # 4) notification capability/preferences/closed receipt summaries.
        if self._notifications is not None:
            emit(
                "notifications.json",
                self._dump(self._notifications.status(), redactor),
            )
        # 5) redacted settings.
        emit("settings.json", self._dump(self._settings(redactor), redactor))
        # 6) bounded operations summary.
        emit("operations.json", self._dump(self._operations(), redactor))
        # 7) bounded, redacted log tails.
        for rel, text in self._log_tails(redactor, cancel):
            emit(rel, text)

        manifest = SupportBundleManifest(
            schema_version=SUPPORT_BUNDLE_SCHEMA_VERSION,
            generated_at=self._clock(),
            bundle_dir=str(bundle_dir),
            files=tuple(files),
            bundle_sha256=self._bundle_digest(files),
            total_bytes=total,
            truncated=truncated,
            redaction_policy=redactor.policy,
        )
        (bundle_dir / "manifest.json").write_text(
            json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
        return manifest

    def verify(self, bundle_dir: Path | str) -> dict[str, Any]:
        """Self-check one local bundle without trusting manifest paths.

        The verifier accepts only the bounded files emitted by ``build``;
        traversal, duplicates, symlinks, special files, digest changes, and
        extra files all fail closed.  It returns no file content.
        """
        root = Path(bundle_dir)
        manifest_path = root / "manifest.json"
        try:
            raw = manifest_path.read_bytes()
            if len(raw) > MAX_FILE_BYTES:
                raise ValueError("manifest oversized")
            document = json.loads(raw)
            entries = document.get("files")
            if not isinstance(entries, list) or len(entries) > MAX_LOG_FILES + 8:
                raise ValueError("invalid file inventory")
            expected: dict[str, dict[str, Any]] = {}
            total = 0
            for item in entries:
                if not isinstance(item, dict):
                    raise ValueError("invalid inventory entry")
                relative = str(item.get("path") or "")
                candidate = Path(relative)
                if (not relative or candidate.is_absolute()
                        or ".." in candidate.parts or relative in expected):
                    raise ValueError("unsafe inventory path")
                path = root / candidate
                if path.is_symlink() or not path.is_file():
                    raise ValueError("inventory target is not a regular file")
                data = path.read_bytes()
                if len(data) > MAX_FILE_BYTES:
                    raise ValueError("inventory target oversized")
                digest = hashlib.sha256(data).hexdigest()
                if digest != item.get("sha256") or len(data) != item.get("bytes"):
                    raise ValueError("inventory identity mismatch")
                expected[relative] = item
                total += len(data)
                if total > MAX_TOTAL_BYTES:
                    raise ValueError("bundle oversized")
            actual = set()
            for path in root.rglob("*"):
                if path.is_symlink() or (path.exists() and not path.is_file()
                                         and not path.is_dir()):
                    raise ValueError("special bundle entry")
                if path.is_file() and path != manifest_path:
                    actual.add(path.relative_to(root).as_posix())
            if actual != set(expected):
                raise ValueError("bundle contains missing or extra files")
            if total != document.get("total_bytes"):
                raise ValueError("bundle size mismatch")
            if self._bundle_digest(list(expected.values())) \
                    != document.get("bundle_sha256"):
                raise ValueError("bundle digest mismatch")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {"valid": False, "code": "SUPPORT_BUNDLE_INVALID"}
        return {
            "valid": True,
            "code": "SUPPORT_BUNDLE_VERIFIED",
            "file_count": len(expected),
            "total_bytes": total,
            "bundle_sha256": document["bundle_sha256"],
        }

    # --- internals -------------------------------------------------------------

    def _check(self, cancel: Callable[[], bool] | None) -> None:
        if cancel is not None and cancel():
            raise SupportBundleCancelled("support bundle generation cancelled")

    def _make_redactor(self) -> Redactor:
        redactor = Redactor(
            self._paths.app_dir,
            redact_model_filenames=self._redact_model_filenames,
        )
        def add_secret_file(secret_path: Path) -> None:
            try:
                with secret_path.open("rb") as handle:
                    raw = handle.read(513)
                if len(raw) <= 512:
                    redactor.add_secret(raw.decode("utf-8").strip())
            except (OSError, UnicodeDecodeError):
                return

        # Feed the scrubber with live secrets WITHOUT emitting them.
        for secret_path in (
            self._paths.app_dir / "gateway-credential",
            self._paths.app_dir / "hf-token.env",
        ):
            add_secret_file(secret_path)
        # EXP-2 keeps one secret per independently revocable client.  Read at
        # most the schema's active-client bound and only to seed redaction;
        # secret files are never emitted into the bundle.
        managed = self._paths.app_dir / "connection-secrets"
        try:
            candidates = sorted(managed.iterdir(), key=lambda item: item.name)[:32]
        except OSError:
            candidates = []
        for secret_path in candidates:
            if secret_path.is_file() and not secret_path.is_symlink():
                add_secret_file(secret_path)
        # Register model filenames for optional redaction.
        try:
            with self._units.read() as conn:
                from .repositories import ModelInstallationsRepository
                for model in ModelInstallationsRepository(conn).list():
                    redactor.register_model_filename(model.get("path"))
        except Exception:  # noqa: BLE001 - redaction must not fail the bundle
            pass
        return redactor

    def _home_snapshot(self) -> dict[str, Any]:
        if self._home is not None:
            return self._home.snapshot().to_dict()
        from .home import HomeQueryService
        return HomeQueryService(self._units, self._paths).snapshot().to_dict()

    def _doctor_report(self) -> dict[str, Any]:
        if self._doctor is not None:
            return self._doctor.run().to_dict()
        from .doctor import DoctorService
        return DoctorService(self._units, self._paths).run().to_dict()

    def _readiness_report(self) -> dict[str, Any]:
        try:
            return self._readiness.snapshot(
                target_journey="native_chat").to_dict()
        except Exception:  # noqa: BLE001 - never retain arbitrary exception text
            return {
                "overall_state": "UNKNOWN",
                "primary_problem_code": "READINESS_OBSERVATION_UNAVAILABLE",
            }

    def _settings(self, redactor: Redactor) -> dict[str, Any]:
        try:
            with self._units.read() as conn:
                from .repositories import SettingsRepository
                settings = SettingsRepository(conn).all()
                settings.pop("__revision", None)
                return mask_settings(settings, redactor)
        except Exception as exc:  # noqa: BLE001
            return {"error": redactor.redact(repr(exc))}

    def _operations(self) -> dict[str, Any]:
        try:
            from .operations.query_service import OperationQueryService
            summary = OperationQueryService(
                self._units, clock=self._clock).active_summary()
            return {"active_summary": summary.to_dict()}
        except Exception as exc:  # noqa: BLE001
            return {"error": repr(exc)}

    def _log_tails(
        self,
        redactor: Redactor,
        cancel: Callable[[], bool] | None,
    ) -> list[tuple[str, str]]:
        tails: list[tuple[str, str]] = []
        logs_dir = self._paths.logs_dir
        try:
            entries = sorted(
                (e for e in os.scandir(logs_dir) if e.is_file()),
                key=lambda e: e.name,
            )[:MAX_LOG_FILES]
        except OSError:
            return tails
        for entry in entries:
            self._check(cancel)
            try:
                size = entry.stat().st_size
                with open(entry.path, "rb") as handle:
                    if size > MAX_LOG_TAIL_BYTES:
                        handle.seek(size - MAX_LOG_TAIL_BYTES)
                    data = handle.read(MAX_LOG_TAIL_BYTES)
                text = data.decode("utf-8", errors="replace")
            except OSError:
                continue
            tails.append(
                (f"logs/{entry.name}.tail", redactor.redact(text)))
        return tails

    @staticmethod
    def _bundle_digest(files: list[dict[str, Any]]) -> str:
        digest = hashlib.sha256()
        for record in sorted(files, key=lambda r: r["path"]):
            digest.update(record["path"].encode("utf-8"))
            digest.update(record["sha256"].encode("utf-8"))
        return digest.hexdigest()

    def _dump(self, payload: dict[str, Any], redactor: Redactor) -> str:
        # Serialize, then run the text scrubber as a final pass so no secret
        # shape can survive into the bundle.
        return redactor.redact(json.dumps(payload, indent=2, default=str))


def open_support_bundle_service(
    database_path, paths: AppPaths, **kwargs
) -> SupportBundleService:
    return SupportBundleService(UnitOfWorkFactory(database_path), paths, **kwargs)
