"""Isolated application-release staging (ADR 013 D5).

This adapter never switches ``current``/``previous`` and never mutates the
running environment.  It validates an exact verifier-produced release and a
held wheel, builds an operation-owned candidate under ``releases/.staging``,
runs bounded offline install/smoke commands, and writes a content-bound
receipt.  Publication is a separate durable critical section.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .application_update import VerifiedApplicationRelease
from .fsops import atomic_write_text, ensure_private_dir, fsync_directory
from .paths import AppPaths
from .runtime_process import (
    CommandKind,
    ProcessCommandSpec,
    ProcessFailure,
    RuntimeProcessRunner,
)


STAGING_RECEIPT_SCHEMA_VERSION = 1
MAX_WHEEL_BYTES = 1024 * 1024 * 1024
HASH_CHUNK_BYTES = 1024 * 1024
_IDENTITY_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}")
_WHEEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,239}\.whl")


class ApplicationStagingError(RuntimeError):
    """Stable staging refusal; detail never includes output or raw paths."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ApplicationStageRequest:
    operation_id: str
    release: VerifiedApplicationRelease
    bundle_root: Path
    wheel_name: str
    wheel_size: int


@dataclass(frozen=True)
class ApplicationStageResult:
    stage_identity: str
    release_set_digest: str
    receipt_digest: str
    smoke_codes: tuple[str, ...]
    already_complete: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_identity": self.stage_identity,
            "release_set_digest": self.release_set_digest,
            "receipt_digest": self.receipt_digest,
            "smoke_codes": list(self.smoke_codes),
            "already_complete": self.already_complete,
        }


class ApplicationInstaller(Protocol):
    def install(
        self, *, venv_dir: Path, wheel_path: Path,
        release: VerifiedApplicationRelease,
    ) -> tuple[str, ...]: ...

    def probe(
        self, *, venv_dir: Path, release: VerifiedApplicationRelease,
    ) -> tuple[bool, tuple[str, ...]]: ...


class ExactWheelInstaller:
    """Bounded no-index installer for an already verified exact wheel.

    It intentionally uses ``--no-deps``. A future advertised release must
    provide a reviewed self-contained dependency strategy; until then the
    normal outcome is a fail-safe ``PIP_CHECK_FAILED`` and production update
    remains unavailable. No package-index fallback exists.
    """

    def __init__(self, runner: RuntimeProcessRunner | None = None) -> None:
        self.runner = runner or RuntimeProcessRunner()

    @staticmethod
    def _spec(kind: CommandKind, argv: tuple[str, ...], *, cwd: Path) -> ProcessCommandSpec:
        return ProcessCommandSpec(
            kind=kind,
            argv=argv,
            working_directory=str(cwd),
            env_values=(
                ("PYTHONNOUSERSITE", "1"),
                ("PIP_NO_INDEX", "1"),
                ("PIP_DISABLE_PIP_VERSION_CHECK", "1"),
            ),
            timeout_seconds=180.0,
            max_output_bytes=16 * 1024,
        )

    def install(
        self, *, venv_dir: Path, wheel_path: Path,
        release: VerifiedApplicationRelease,
    ) -> tuple[str, ...]:
        root = venv_dir.parent
        try:
            self.runner.run(self._spec(
                CommandKind.CONFIGURE,
                (sys.executable, "-m", "venv", str(venv_dir)), cwd=root,
            ))
            python = venv_dir / "bin" / "python"
            self.runner.run(self._spec(
                CommandKind.CONFIGURE,
                (
                    str(python), "-m", "pip", "install", "--no-index",
                    "--no-deps", "--disable-pip-version-check", str(wheel_path),
                ),
                cwd=root,
            ))
            self.runner.run(self._spec(
                CommandKind.SMOKE,
                (str(python), "-m", "pip", "check"), cwd=root,
            ))
        except ProcessFailure as exc:
            if exc.code in {"PROCESS_TIMEOUT", "PROCESS_CANCELLED"}:
                raise ApplicationStagingError("INSTALL_TIMEOUT") from exc
            raise ApplicationStagingError("OFFLINE_INSTALL_FAILED") from exc
        return ("VENV_CREATED", "EXACT_WHEEL_INSTALLED", "PIP_CHECK_PASSED")

    def probe(
        self, *, venv_dir: Path, release: VerifiedApplicationRelease,
    ) -> tuple[bool, tuple[str, ...]]:
        python = venv_dir / "bin" / "python"
        if python.is_symlink() or not python.is_file():
            return False, ("VENV_PYTHON_MISSING",)
        code = (
            "import sys; from bc250_llm_mode import __version__; "
            "from bc250_llm_mode import app, application_update, db; "
            "sys.exit(0 if __version__ == sys.argv[1] else 4)"
        )
        try:
            self.runner.run(self._spec(
                CommandKind.SMOKE,
                (str(python), "-c", code, release.version),
                cwd=venv_dir.parent,
            ))
        except ProcessFailure:
            return False, ("PACKAGE_IMPORT_SMOKE_FAILED",)
        return True, ("PACKAGE_IMPORT_SMOKE_PASSED", "NO_MODEL_STARTED")


class ApplicationInstallationStager:
    def __init__(
        self, paths: AppPaths, *, installer: ApplicationInstaller | None = None
    ) -> None:
        self.paths = paths
        self.installer = installer or ExactWheelInstaller()

    @staticmethod
    def _digest_file(path: Path, *, expected_size: int) -> str:
        if expected_size < 1 or expected_size > MAX_WHEEL_BYTES:
            raise ApplicationStagingError("BUNDLE_MEMBER_REFUSED")
        before = path.stat()
        if before.st_size != expected_size or not path.is_file() or path.is_symlink():
            raise ApplicationStagingError("BUNDLE_MEMBER_REFUSED")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
                digest.update(chunk)
        after = path.stat()
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        ):
            raise ApplicationStagingError("BUNDLE_MUTATED")
        return digest.hexdigest()

    @staticmethod
    def _receipt(stage: Path) -> dict[str, Any] | None:
        receipt = stage / ".bc250-release.json"
        if receipt.is_symlink() or not receipt.is_file():
            return None
        try:
            raw = receipt.read_bytes()
            if len(raw) > 32 * 1024:
                return None
            value = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _probe_stage(
        self, stage: Path, *, request: ApplicationStageRequest,
    ) -> ApplicationStageResult | None:
        receipt = self._receipt(stage)
        if receipt is None:
            return None
        expected = {
            "schema_version": STAGING_RECEIPT_SCHEMA_VERSION,
            "operation_id": request.operation_id,
            "stage_identity": stage.name,
            "release_set_digest": request.release.release_set_digest,
            "wheel_digest": request.release.wheel_digest,
            "version": request.release.version,
            "smoke_codes": receipt.get("smoke_codes"),
        }
        if receipt != expected or not isinstance(receipt.get("smoke_codes"), list):
            return None
        ok, _probe_codes = self.installer.probe(
            venv_dir=stage / "venv", release=request.release
        )
        if not ok:
            return None
        receipt_bytes = (
            json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        return ApplicationStageResult(
            stage_identity=stage.name,
            release_set_digest=request.release.release_set_digest,
            receipt_digest=hashlib.sha256(receipt_bytes).hexdigest(),
            smoke_codes=tuple(receipt["smoke_codes"]),
            already_complete=True,
        )

    def stage(self, request: ApplicationStageRequest) -> ApplicationStageResult:
        if not isinstance(request.release, VerifiedApplicationRelease):
            raise ApplicationStagingError("RELEASE_DECISION_INELIGIBLE")
        if not _IDENTITY_RE.fullmatch(request.operation_id):
            raise ApplicationStagingError("OPERATION_IDENTITY_INVALID")
        if not _WHEEL_RE.fullmatch(request.wheel_name):
            raise ApplicationStagingError("BUNDLE_MEMBER_REFUSED")
        bundle = request.bundle_root
        if not bundle.is_absolute() or bundle.is_symlink() or not bundle.is_dir():
            raise ApplicationStagingError("BUNDLE_MEMBER_REFUSED")
        wheel = bundle / request.wheel_name
        try:
            if wheel.parent.resolve(strict=True) != bundle.resolve(strict=True):
                raise ApplicationStagingError("BUNDLE_MEMBER_REFUSED")
            wheel_digest = self._digest_file(wheel, expected_size=request.wheel_size)
        except (OSError, RuntimeError) as exc:
            raise ApplicationStagingError("BUNDLE_MEMBER_REFUSED") from exc
        if wheel_digest != request.release.wheel_digest:
            raise ApplicationStagingError("BUNDLE_MUTATED")

        release_digest = request.release.release_set_digest
        if not re.fullmatch(r"[0-9a-f]{64}", release_digest):
            raise ApplicationStagingError("RELEASE_IDENTITY_INVALID")
        ensure_private_dir(self.paths.application_releases_dir)
        ensure_private_dir(self.paths.application_release_staging_dir)
        stage_identity = f"{request.operation_id}-{release_digest[:16]}"
        stage = self.paths.application_release_staging_dir / stage_identity
        if stage.is_symlink():
            raise ApplicationStagingError("STAGING_IDENTITY_MISMATCH")
        if stage.exists():
            complete = self._probe_stage(stage, request=request)
            if complete is not None:
                return complete
            raise ApplicationStagingError("STAGING_IDENTITY_MISMATCH")
        try:
            stage.mkdir(mode=0o700)
            fsync_directory(stage.parent)
            install_codes = self.installer.install(
                venv_dir=stage / "venv", wheel_path=wheel,
                release=request.release,
            )
            ok, smoke_codes = self.installer.probe(
                venv_dir=stage / "venv", release=request.release,
            )
            if not ok:
                raise ApplicationStagingError("SMOKE_FAILED")
            all_codes = tuple(install_codes) + tuple(smoke_codes)
            if (
                not all_codes
                or len(all_codes) > 16
                or any(not isinstance(code, str) or not _IDENTITY_RE.fullmatch(code)
                       for code in all_codes)
            ):
                raise ApplicationStagingError("SMOKE_FAILED")
            receipt = {
                "schema_version": STAGING_RECEIPT_SCHEMA_VERSION,
                "operation_id": request.operation_id,
                "stage_identity": stage_identity,
                "release_set_digest": release_digest,
                "wheel_digest": request.release.wheel_digest,
                "version": request.release.version,
                "smoke_codes": list(all_codes),
            }
            receipt_text = json.dumps(
                receipt, sort_keys=True, separators=(",", ":")
            ) + "\n"
            atomic_write_text(stage / ".bc250-release.json", receipt_text)
            fsync_directory(stage)
        except ApplicationStagingError:
            # Keep ambiguous/partial operation-owned staging for durable probe
            # and typed cleanup. Never guess that recursive deletion is safe.
            raise
        except Exception as exc:
            raise ApplicationStagingError("STAGING_FAILED") from exc
        return ApplicationStageResult(
            stage_identity=stage_identity,
            release_set_digest=release_digest,
            receipt_digest=hashlib.sha256(receipt_text.encode()).hexdigest(),
            smoke_codes=all_codes,
        )


__all__ = [
    "ApplicationInstallationStager",
    "ApplicationInstaller",
    "ApplicationStageRequest",
    "ApplicationStageResult",
    "ApplicationStagingError",
    "ExactWheelInstaller",
    "STAGING_RECEIPT_SCHEMA_VERSION",
]
