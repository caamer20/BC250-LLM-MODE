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


def _sampling_fields(model: ModelEntry) -> dict[str, float | int]:
    return {
        "temperature": model.temperature,
        "top_p": model.top_p,
        "top_k": model.top_k,
        "min_p": model.min_p,
        "repeat_penalty": model.repeat_penalty,
    }


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
