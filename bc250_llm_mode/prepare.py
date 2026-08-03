"""Streaming-safe GGUF verification, patching, conversion, and cleanup."""

from __future__ import annotations

import os
import shutil
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .catalog import ModelEntry, validate_artifact
from .local_models import LocalModel
from .logging_utils import CommandRunner

HEADER_LIMIT = 4 * 1024 * 1024
GGUF_UINT32 = 4


@dataclass(frozen=True)
class MetadataPatch:
    key: str
    expected_wrong_value: int
    correct_value: int


def patch_uint32_metadata(path: str | Path, patches: Iterable[MetadataPatch]) -> list[str]:
    """Patch guarded uint32 fields while reading at most the first 4 MiB into RAM."""
    target = Path(path)
    with target.open("rb") as handle:
        header = bytearray(handle.read(HEADER_LIMIT))
    changes: list[tuple[int, MetadataPatch]] = []
    messages: list[str] = []
    for patch in patches:
        needle = patch.key.encode("utf-8")
        start = 0
        matched = False
        while True:
            key_at = header.find(needle, start)
            if key_at < 0:
                break
            type_at = key_at + len(needle)
            if type_at + 8 <= len(header):
                value_type = struct.unpack_from("<I", header, type_at)[0]
                current = struct.unpack_from("<I", header, type_at + 4)[0]
                if value_type == GGUF_UINT32:
                    matched = True
                    if current == patch.correct_value:
                        messages.append(f"{patch.key} already equals {patch.correct_value}; skipped")
                    elif current == patch.expected_wrong_value:
                        changes.append((type_at + 4, patch))
                    else:
                        raise ValueError(
                            f"Refusing to patch {patch.key}: expected {patch.expected_wrong_value}, found {current}"
                        )
                    break
            start = key_at + 1
        if not matched:
            raise KeyError(f"Metadata key {patch.key!r} with uint32 type was not found in first 4 MiB")
    if not changes:
        return messages
    backup = target.with_suffix(target.suffix + ".bak")
    if not backup.exists():
        shutil.copyfile(target, backup)
    with target.open("r+b", buffering=0) as handle:
        for offset, patch in changes:
            handle.seek(offset)
            handle.write(struct.pack("<I", patch.correct_value))
            messages.append(f"Patched {patch.key}: {patch.expected_wrong_value} -> {patch.correct_value}")
        handle.flush()
        os.fsync(handle.fileno())
    with target.open("rb") as handle:
        confirmed = handle.read(HEADER_LIMIT)
    for offset, patch in changes:
        actual = struct.unpack_from("<I", confirmed, offset)[0]
        if actual != patch.correct_value:
            raise OSError(f"Verification failed after patching {patch.key}")
    return messages


def _field_scalar(field: Any) -> Any:
    # gguf-py versions differ; prefer the public contents() helper when available.
    if hasattr(field, "contents"):
        value = field.contents()
    elif hasattr(field, "parts") and field.parts:
        value = field.parts[-1]
    else:
        value = field
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value


def inspect_gguf(path: str | Path) -> tuple[str, int, set[int], bool, bool]:
    try:
        from gguf import GGUFReader
    except ImportError as exc:
        raise RuntimeError("The gguf Python package is required for model verification.") from exc
    reader = GGUFReader(str(path), mode="r")
    fields = reader.fields
    architecture = str(_field_scalar(fields["general.architecture"]))
    block_key = f"{architecture}.block_count"
    if block_key not in fields:
        raise ValueError(f"GGUF is missing required metadata {block_key}")
    block_count = int(_field_scalar(fields[block_key]))
    blocks: set[int] = set()
    has_nextn_tensor = False
    for tensor in reader.tensors:
        name = str(tensor.name)
        if name.startswith("blk."):
            parts = name.split(".")
            if len(parts) > 1 and parts[1].isdigit():
                blocks.add(int(parts[1]))
        if ".nextn" in name:
            has_nextn_tensor = True
    has_nextn_field = f"{architecture}.nextn_predict_layers" in fields
    return architecture, block_count, blocks, has_nextn_field, has_nextn_tensor


def verify_and_heal_gguf(path: str | Path, runner: CommandRunner | None = None) -> dict[str, Any]:
    target = Path(path)
    validate_artifact("local", target.name)
    if target.stat().st_size < 1024 * 1024:
        raise ValueError("GGUF is too small to be a model")
    architecture, block_count, blocks, has_nextn_field, has_nextn_tensor = inspect_gguf(target)
    actual_count = len(blocks)
    messages: list[str] = []
    if actual_count != block_count:
        if block_count > actual_count and blocks == set(range(actual_count)):
            messages.extend(
                patch_uint32_metadata(
                    target,
                    [MetadataPatch(f"{architecture}.block_count", block_count, actual_count)],
                )
            )
        else:
            raise ValueError(f"GGUF block mismatch: metadata={block_count}, tensor blocks={sorted(blocks)}")
    if has_nextn_field and not has_nextn_tensor:
        # Known Qwen3.5 converter bug. The guarded patch accepts only a nonzero declaration.
        try:
            _, current_count, _, _, _ = inspect_gguf(target)
            del current_count
            # Typical wrong value is one. Try known converter declarations conservatively.
            for wrong in (1, 2, 3, 4):
                try:
                    messages.extend(
                        patch_uint32_metadata(
                            target,
                            [MetadataPatch(f"{architecture}.nextn_predict_layers", wrong, 0)],
                        )
                    )
                    break
                except ValueError:
                    continue
        except KeyError:
            pass
    architecture2, block_count2, blocks2, _, _ = inspect_gguf(target)
    if block_count2 != len(blocks2):
        raise ValueError(f"GGUF remains inconsistent after healing: {block_count2} != {len(blocks2)}")
    if runner:
        for message in messages or ["GGUF metadata and tensor block count verified"]:
            runner.emit(message)
    return {
        "architecture": architecture2,
        "block_count": block_count2,
        "tensor_block_count": len(blocks2),
        "messages": messages,
    }


def prepare_model(
    state: dict[str, Any], model: ModelEntry, quant: str, downloaded: str | Path, runner: CommandRunner
) -> Path:
    source = Path(downloaded)
    output_dir = Path(str(state["models_dir"])).expanduser() / model.id
    output_dir.mkdir(parents=True, exist_ok=True)
    if not model.conversion:
        ggufs = list(source.parent.rglob("*.gguf")) if source.is_file() else list(source.rglob("*.gguf"))
        matches = [path for path in ggufs if quant.lower() in path.name.lower()]
        if not matches:
            raise RuntimeError(f"No downloaded {quant} GGUF was found")
        final = max(matches, key=lambda path: path.stat().st_size)
    else:
        container = str(state.get("container_name", "llm"))
        llama_root = str(state.get("llama_cpp_path", "/root/llama.cpp"))
        source_dir = source if source.is_dir() else source.parent
        f16 = output_dir / f"{model.id}-f16.gguf"
        final = output_dir / f"{model.id}-{quant}.gguf"
        if not final.exists():
            if not f16.exists():
                runner.run([
                    "podman", "exec", "--user", "root", container, f"{state['venv_path']}/bin/python",
                    f"{llama_root}/convert_hf_to_gguf.py", str(source_dir),
                    "--outtype", "f16", "--outfile", str(f16),
                ])
            runner.run([
                "podman", "exec", "--user", "root", container, f"{llama_root}/build/bin/llama-quantize",
                str(f16), str(final), quant,
            ])
        if model.true_block_count:
            # Only patch if the known wrong converter value is present.
            try:
                patch_uint32_metadata(final, [
                    MetadataPatch(f"{model.family}.block_count", model.true_block_count + 1, model.true_block_count),
                ])
            except KeyError:
                pass
            try:
                patch_uint32_metadata(final, [
                    MetadataPatch(f"{model.family}.nextn_predict_layers", 1, 0),
                ])
            except KeyError:
                pass
    verification = verify_and_heal_gguf(final, runner)
    record = {
        "id": model.id,
        "display_name": model.display_name,
        "quant": quant,
        "path": str(final),
        "architecture": verification["architecture"],
    }
    installed = [item for item in state.get("installed_models", []) if item.get("id") != model.id]
    installed.append(record)
    state.update(installed_models=installed, current_model=model.id)
    state["setup_phase"] = max(int(state.get("setup_phase", 0)), 8)
    return final


def prepare_local_model(
    state: dict[str, Any], model: LocalModel, runner: CommandRunner
) -> Path:
    """Verify and register an already-present GGUF without copying or downloading it."""
    final = Path(model.path).expanduser().resolve()
    if not final.is_file():
        raise RuntimeError(f"Selected local GGUF no longer exists: {final}")
    verification = verify_and_heal_gguf(final, runner)
    record = {
        "id": model.id,
        "display_name": model.display_name,
        "quant": model.quant,
        "path": str(final),
        "architecture": verification["architecture"],
        "source": "local",
        "catalog_id": model.catalog_id,
    }
    installed = [item for item in state.get("installed_models", []) if item.get("id") != model.id]
    installed.append(record)
    state.update(installed_models=installed, current_model=model.id)
    state["setup_phase"] = max(int(state.get("setup_phase", 0)), 8)
    runner.emit(f"Registered existing model without downloading: {final}")
    return final


def cleanup_conversion_intermediates(state: dict[str, Any], model: ModelEntry, runner: CommandRunner) -> None:
    """Call only after a successful server health check."""
    if not model.conversion:
        return
    root = (Path(str(state["models_dir"])).expanduser() / model.id).resolve()
    for target in (root / f"{model.id}-f16.gguf", root / "source"):
        try:
            resolved = target.resolve()
            if root not in resolved.parents or not resolved.exists():
                continue
            if resolved.is_dir():
                shutil.rmtree(resolved)
            else:
                resolved.unlink()
            runner.emit(f"Removed conversion intermediate {resolved}")
        except OSError as exc:
            runner.emit(f"Could not remove intermediate {target}: {exc}")
