"""Bounded, metadata-light discovery of GGUF models already present on disk."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .catalog import CATALOG, ModelEntry, validate_artifact

MIN_MODEL_BYTES = 1024 * 1024
UNKNOWN_KV_KIB_PER_TOKEN = 72.0
QUANT_PATTERN = re.compile(
    r"(?<![A-Z0-9])(IQ\d(?:_[A-Z0-9]+)+|Q\d(?:_[A-Z0-9]+)+)(?![A-Z0-9])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LocalModel:
    id: str
    display_name: str
    path: str
    quant: str
    weights_gib: float
    family: str
    params_b: float
    kv_kib_per_token: float
    catalog_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> LocalModel:
        return cls(
            id=str(value["id"]),
            display_name=str(value["display_name"]),
            path=str(value["path"]),
            quant=str(value["quant"]),
            weights_gib=float(value["weights_gib"]),
            family=str(value.get("family", "unknown")),
            params_b=float(value.get("params_b", 0.0)),
            kv_kib_per_token=float(value.get("kv_kib_per_token", UNKNOWN_KV_KIB_PER_TOKEN)),
            catalog_id=str(value["catalog_id"]) if value.get("catalog_id") else None,
        )


@dataclass(frozen=True)
class DiscoveryResult:
    models: tuple[LocalModel, ...]
    rejected: tuple[str, ...]
    roots: tuple[str, ...]


def _candidate_roots(state: dict[str, Any]) -> tuple[Path, ...]:
    roots: list[Path] = [Path(str(state["models_dir"])).expanduser()]
    roots.extend(Path(str(item)).expanduser() for item in state.get("model_search_paths", []))
    home = Path.home()
    roots.extend((home / "models", home / "Models"))
    for record in state.get("installed_models", []):
        if record.get("path"):
            roots.append(Path(str(record["path"])).expanduser().parent)
    # Bazzite desktop users normally live under /var/home, while setup may run
    # as root. Search only conventional model directories, never whole homes.
    if os.geteuid() == 0:
        roots.extend(Path("/var/home").glob("*/models"))
        roots.extend(Path("/var/home").glob("*/Models"))
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root.absolute())
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return tuple(unique)


def _catalog_match(path: Path) -> ModelEntry | None:
    lowered = str(path).lower().replace("_", "-")
    for model in CATALOG:
        if model.id in lowered:
            return model
    name = path.name.lower().replace("_", "-")
    if "defiant-fable" in name:
        return next(model for model in CATALOG if model.id == "defiant-fable-9b")
    if ("qwen3.5" in name or "qwen35" in name) and "9b" in name:
        return next(model for model in CATALOG if model.id == "qwen35-9b")
    if "qwen3" in name and "14b" in name:
        return next(model for model in CATALOG if model.id == "qwen3-14b")
    if "qwen3" in name and "8b" in name:
        return next(model for model in CATALOG if model.id == "qwen3-8b")
    if "llama-3.2" in name and "3b" in name:
        return next(model for model in CATALOG if model.id == "llama32-3b-instruct")
    if ("llama-3.1" in name or "meta-llama-3.1" in name) and "8b" in name:
        return next(model for model in CATALOG if model.id == "llama31-8b-instruct")
    if "qwen2.5-coder" in name and "7b" in name:
        return next(model for model in CATALOG if model.id == "qwen25-coder-7b")
    if "deepseek-r1-distill-qwen" in name and "7b" in name:
        return next(model for model in CATALOG if model.id == "deepseek-r1-qwen-7b")
    if "mistral-nemo" in name:
        return next(model for model in CATALOG if model.id == "mistral-nemo-12b")
    if "phi-4" in name or name.startswith("phi4"):
        return next(model for model in CATALOG if model.id == "phi4-14b")
    return None


def _quant_from_name(name: str, catalog: ModelEntry | None) -> str:
    if catalog:
        for quant in sorted(catalog.weights_gib_by_quant, key=len, reverse=True):
            if quant.lower() in name.lower():
                return quant
    match = QUANT_PATTERN.search(name)
    return match.group(1).upper() if match else "UNKNOWN"


def _local_model(path: Path) -> LocalModel:
    resolved = path.resolve()
    absolute = path.absolute()
    catalog = _catalog_match(absolute)
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
    size = resolved.stat().st_size / (1024**3)
    return LocalModel(
        id=f"local-{digest}",
        display_name=absolute.stem,
        path=str(absolute),
        quant=_quant_from_name(absolute.name, catalog),
        weights_gib=size,
        family=catalog.family if catalog else "unknown",
        params_b=catalog.params_b if catalog else 0.0,
        kv_kib_per_token=catalog.kv_kib_per_token if catalog else UNKNOWN_KV_KIB_PER_TOKEN,
        catalog_id=catalog.id if catalog else None,
    )


def discover_local_models(state: dict[str, Any]) -> DiscoveryResult:
    """Find GGUFs in bounded model roots; stat files without reading model contents."""
    roots = _candidate_roots(state)
    models: list[LocalModel] = []
    rejected: list[str] = []
    seen_files: set[tuple[int, int] | str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            candidates = root.rglob("*.gguf")
            for path in candidates:
                try:
                    if path.name.lower().endswith("-f16.gguf") or not path.is_file():
                        continue
                    stat = path.stat()
                    if stat.st_size < MIN_MODEL_BYTES:
                        continue
                    identity: tuple[int, int] | str = (stat.st_dev, stat.st_ino)
                    if identity in seen_files:
                        continue
                    seen_files.add(identity)
                    validate_artifact("local", path.name)
                    models.append(_local_model(path))
                except (OSError, ValueError) as exc:
                    rejected.append(f"{path}: {exc}")
        except OSError as exc:
            rejected.append(f"{root}: {exc}")
    models.sort(key=lambda item: item.display_name.casefold())
    return DiscoveryResult(tuple(models), tuple(rejected), tuple(str(root) for root in roots))


def fit_entry_for_local(model: LocalModel) -> ModelEntry:
    note = "Existing local GGUF"
    if model.catalog_id is None:
        note += "; VRAM projection uses a conservative 72 KiB/token KV estimate"
    return ModelEntry(
        id=model.id,
        display_name=model.display_name,
        family=model.family,
        task_tags=("local",),
        repo="local",
        allow_globs={model.quant: model.path},
        params_b=model.params_b,
        weights_gib_by_quant={model.quant: model.weights_gib},
        kv_kib_per_token=model.kv_kib_per_token,
        notes=note,
    )


def selected_fit_entry(state: dict[str, Any]) -> ModelEntry:
    if state.get("selected_source") == "local":
        value = state.get("selected_local_model")
        if not isinstance(value, dict):
            raise KeyError("Selected local model details are missing")
        return fit_entry_for_local(LocalModel.from_dict(value))
    from .catalog import model_by_id

    return model_by_id(str(state["selected_model"]))


def installed_fit_entry(record: dict[str, Any]) -> ModelEntry:
    """Rebuild fit metadata for catalog or locally registered installed models."""
    from .catalog import model_by_id

    model_id = str(record["id"])
    if record.get("source") != "local":
        return model_by_id(model_id)
    path = Path(str(record["path"])).expanduser()
    try:
        weights_gib = float(record.get("weights_gib") or path.stat().st_size / (1024**3))
    except OSError as exc:
        raise ValueError(f"Installed model file is unavailable: {path}") from exc
    catalog_id = str(record["catalog_id"]) if record.get("catalog_id") else None
    catalog = model_by_id(catalog_id) if catalog_id else None
    local = LocalModel(
        id=model_id,
        display_name=str(record.get("display_name") or path.stem),
        path=str(path),
        quant=str(record.get("quant", "UNKNOWN")),
        weights_gib=weights_gib,
        family=str(record.get("family") or (catalog.family if catalog else "unknown")),
        params_b=float(record.get("params_b") or (catalog.params_b if catalog else 0.0)),
        kv_kib_per_token=float(
            record.get("kv_kib_per_token")
            or (catalog.kv_kib_per_token if catalog else UNKNOWN_KV_KIB_PER_TOKEN)
        ),
        catalog_id=catalog_id,
    )
    return fit_entry_for_local(local)
