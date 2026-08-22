from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from .catalog import ModelEntry, validate_artifact
from .logging_utils import CommandRunner


GIB = 1024**3
DOWNLOAD_RESERVE_GIB = 1.0


def required_download_space_gib(model: ModelEntry, quant: str) -> float:
    """Conservative free-space requirement without buffering model data in RAM."""
    if model.temporary_disk_gib is not None:
        return model.temporary_disk_gib
    return model.weights_gib_by_quant[quant] * 1.05 + DOWNLOAD_RESERVE_GIB


def _existing_tree_gib(root: Path) -> float:
    if not root.exists():
        return 0.0
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total / GIB


def _ensure_disk_space(destination: Path, required_gib: float) -> float:
    probe = destination
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    existing_gib = _existing_tree_gib(destination.parent)
    remaining_gib = max(0.25, required_gib - existing_gib)
    free_gib = shutil.disk_usage(probe).free / GIB
    if free_gib < remaining_gib:
        raise RuntimeError(
            f"Insufficient model-storage space: {free_gib:.1f} GiB free, "
            f"but this operation may still require approximately {remaining_gib:.1f} GiB "
            f"({existing_gib:.1f} GiB is already present). "
            "Free space or choose a smaller model before downloading."
        )
    return remaining_gib


def verify_sha256_manifest(artifact: Path, manifest: Path) -> None:
    """Verify one artifact against a conventional SHA256SUMS file, streaming it."""
    expected: str | None = None
    for line in manifest.read_text(encoding="utf-8").splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) != 2:
            continue
        name = fields[1].lstrip("*")
        if Path(name).name == artifact.name:
            expected = fields[0].lower()
            break
    if expected is None:
        raise RuntimeError(f"{manifest.name} has no checksum for {artifact.name}")
    digest = hashlib.sha256()
    with artifact.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest().lower()
    if actual != expected:
        raise RuntimeError(
            f"SHA-256 verification failed for {artifact.name}; remove the corrupt file and retry"
        )


def download_model(
    state: dict[str, Any], model: ModelEntry, quant: str, runner: CommandRunner
) -> Path:
    if quant not in model.allow_globs:
        raise ValueError(f"Unsupported quant {quant} for {model.display_name}")
    pattern = model.allow_globs[quant]
    validate_artifact(model.repo, pattern)
    destination = Path(str(state["models_dir"])).expanduser() / model.id / "source"
    required_gib = required_download_space_gib(model, quant)
    remaining_gib = _ensure_disk_space(destination, required_gib)
    runner.emit(
        f"Disk preflight passed: up to {remaining_gib:.1f} GiB additional space required "
        f"for an approximately {required_gib:.1f} GiB operation"
    )
    destination.mkdir(parents=True, exist_ok=True)
    venv = str(state.get("venv_path", "/root/.venvs/hf"))
    container = str(state.get("container_name", "llm"))
    # The token travels through a private env-file, never as a command-line
    # argument: argv is world-readable in /proc/<pid>/cmdline while hf runs,
    # and CommandRunner logs every argv to setup.log.
    token_file = None
    if os.environ.get("HF_TOKEN"):
        handle = tempfile.NamedTemporaryFile(
            "w", prefix="bc250-hf-", suffix=".env", delete=False
        )
        handle.write(f"HF_TOKEN={os.environ['HF_TOKEN']}\n")
        handle.close()
        os.chmod(handle.name, 0o600)
        token_file = handle.name
    try:
        auth = ["--env-file", token_file] if token_file else []
        command = [
            "podman", "exec", "--user", "root", *auth,
            container, f"{venv}/bin/hf", "download", model.repo,
            "--include", pattern, "--local-dir", str(destination),
        ]
        if not token_file:
            runner.emit("HF_TOKEN is not set; continuing anonymously (Hugging Face may apply rate limits).")
        runner.run(command)
        if not model.checksum_manifest:
            return _select_artifact(state, model, quant, pattern, destination)
        manifest_command = [
            "podman", "exec", "--user", "root", *auth,
            container, f"{venv}/bin/hf", "download",
            model.repo, model.checksum_manifest, "--local-dir", str(destination),
        ]
        runner.run(manifest_command)
        manifest = destination / model.checksum_manifest
        if not manifest.is_file():
            raise RuntimeError(f"Checksum manifest was not downloaded: {manifest}")
        selected = _select_artifact(state, model, quant, pattern, destination)
        runner.emit(f"Verifying SHA-256 for {selected.name} without loading it into host RAM")
        verify_sha256_manifest(selected, destination / model.checksum_manifest)
        runner.emit("SHA-256 verification passed")
        return selected
    finally:
        if token_file:
            try:
                os.unlink(token_file)
            except OSError:
                pass


def _select_artifact(state, model, quant, pattern, destination):
    candidates = [
        path
        for path in destination.rglob("*")
        if path.is_file()
        and path.stat().st_size > 1024 * 1024
        and (fnmatch(path.name, pattern) or fnmatch(str(path.relative_to(destination)), pattern))
    ]
    candidates = [path for path in candidates if path.suffix == ".gguf" or model.conversion]
    if not candidates:
        raise RuntimeError(f"Download completed but no non-trivial artifact matched {pattern}")
    for candidate in candidates:
        validate_artifact(model.repo, candidate.name)
    selected = max(candidates, key=lambda item: item.stat().st_size)
    state["selected_model"] = model.id
    state["selected_quant"] = quant
    state["download_dir"] = str(destination)
    state["setup_phase"] = max(int(state.get("setup_phase", 0)), 7)
    return selected
