from __future__ import annotations

import re
from dataclasses import dataclass

from .constants import COMFORTABLE_VRAM_GIB, FAST_VRAM_GIB, GTT_SPILL_GIB, OVERHEAD_GIB

FORBIDDEN_LAYOUT = re.compile(r"(?:^|[-_.])(imatrix[-_.]?max|max|fused)(?:[-_.]|$)", re.IGNORECASE)


@dataclass(frozen=True)
class ModelEntry:
    id: str
    display_name: str
    family: str
    task_tags: tuple[str, ...]
    repo: str
    allow_globs: dict[str, str]
    params_b: float
    weights_gib_by_quant: dict[str, float]
    kv_kib_per_token: float
    notes: str
    avoid: tuple[str, ...] = ("*MAX*", "*fused*", "*mmproj*", "*MTP*")
    source_repo: str | None = None
    conversion: bool = False
    true_block_count: int | None = None


CATALOG: tuple[ModelEntry, ...] = (
    ModelEntry(
        id="qwen35-9b",
        display_name="Qwen3.5 9B Instruct",
        family="qwen35",
        task_tags=("chat", "general", "reasoning"),
        repo="bartowski/Qwen_Qwen3.5-9B-GGUF",
        allow_globs={
            "Q5_K_M": "*Q5_K_M.gguf",
            "Q6_K": "*Q6_K.gguf",
            "Q8_0": "*Q8_0.gguf",
        },
        params_b=9.7,
        weights_gib_by_quant={"Q5_K_M": 6.27, "Q6_K": 6.95, "Q8_0": 8.88},
        kv_kib_per_token=72.0,
        notes="Text-only standard per-tensor GGUF; Q5_K_M is the default.",
        true_block_count=32,
    ),
    ModelEntry(
        id="defiant-fable-9b",
        display_name="The Defiant Fable 9B (uncensored)",
        family="qwen35",
        task_tags=("chat", "creative", "uncensored"),
        repo="pipenetwork/Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-MLX-bf16",
        source_repo="pipenetwork/Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-MLX-bf16",
        # Conversion needs config/tokenizer files as well as sharded safetensors.
        allow_globs={"Q5_K_M": "*", "Q6_K": "*"},
        params_b=9.7,
        weights_gib_by_quant={"Q5_K_M": 6.3, "Q6_K": 7.0},
        kv_kib_per_token=72.0,
        notes="Converted locally to a text-only standard layout; fused MAX release is intentionally rejected.",
        conversion=True,
        true_block_count=32,
    ),
    ModelEntry(
        id="qwen3-8b",
        display_name="Qwen3 8B",
        family="qwen3",
        task_tags=("chat", "general", "fast"),
        repo="Qwen/Qwen3-8B-GGUF",
        allow_globs={"Q4_K_M": "*Q4_K_M.gguf", "Q5_K_M": "*Q5_K_M.gguf"},
        params_b=8.2,
        weights_gib_by_quant={"Q4_K_M": 4.68, "Q5_K_M": 5.45},
        kv_kib_per_token=32.0,
        notes="Smaller general-purpose option with more context headroom.",
    ),
    ModelEntry(
        id="qwen3-14b",
        display_name="Qwen3 14B",
        family="qwen3",
        task_tags=("chat", "general", "slower"),
        repo="ggml-org/Qwen3-14B-GGUF",
        allow_globs={"Q3_K_M": "*Q3_K_M.gguf", "Q4_K_M": "*Q4_K_M.gguf"},
        params_b=14.8,
        weights_gib_by_quant={"Q3_K_M": 6.82, "Q4_K_M": 8.38},
        kv_kib_per_token=40.0,
        notes="More capable but slower; use 16k context or less. Q4 is tight.",
    ),
)


@dataclass(frozen=True)
class FitResult:
    weights_gib: float
    kv_gib: float
    overhead_gib: float
    required_gib: float
    verdict: str
    detail: str


def is_forbidden_artifact(value: str) -> bool:
    return bool(FORBIDDEN_LAYOUT.search(value))


def validate_artifact(repo: str, filename: str) -> None:
    value = f"{repo}/{filename}"
    if is_forbidden_artifact(value):
        raise ValueError(
            "Fused/MAX/imatrix-MAX artifacts cannot load on BC-250 because a single fused tensor allocation "
            "hits the device allocation wall. Choose a standard per-tensor-layout GGUF."
        )
    lowered = filename.lower()
    if "mmproj" in lowered or "vision" in lowered or "mtp" in lowered:
        raise ValueError("Vision projectors and MTP artifacts are excluded from the text-only BC-250 catalog.")


def model_by_id(model_id: str) -> ModelEntry:
    for model in CATALOG:
        if model.id == model_id:
            return model
    raise KeyError(f"Unknown catalog model: {model_id}")


def calculate_fit(model: ModelEntry, quant: str, ctx_tokens: int, *, kv_scale: float = 1.0) -> FitResult:
    if quant not in model.weights_gib_by_quant:
        raise KeyError(f"{quant} is not available for {model.display_name}")
    if ctx_tokens <= 0:
        raise ValueError("Context size must be positive")
    if kv_scale <= 0:
        raise ValueError("KV scale must be positive")
    weights = model.weights_gib_by_quant[quant]
    kv = ctx_tokens * model.kv_kib_per_token / (1024 * 1024) * kv_scale
    required = weights + kv + OVERHEAD_GIB
    if required <= COMFORTABLE_VRAM_GIB:
        verdict = "FITS"
    elif required <= FAST_VRAM_GIB:
        verdict = "TIGHT"
    else:
        verdict = "NO-FIT"
    detail = f"{verdict} — ~{required:.2f} GiB of {FAST_VRAM_GIB:.0f} GiB fast VRAM"
    if verdict == "NO-FIT":
        detail += f" (~{GTT_SPILL_GIB:.1f} GiB GTT spill is slower and not counted as a safe fit)"
    return FitResult(weights, kv, OVERHEAD_GIB, required, verdict, detail)
