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
    temperature: float = 0.3
    top_p: float = 0.9
    top_k: int = 40
    min_p: float = 0.05
    repeat_penalty: float = 1.05
    avoid: tuple[str, ...] = ("*MAX*", "*fused*", "*mmproj*", "*MTP*")
    checksum_manifest: str | None = None
    temporary_disk_gib: float | None = None
    source_repo: str | None = None
    conversion: bool = False
    true_block_count: int | None = None
    max_context_tokens: int | None = None


CATALOG: tuple[ModelEntry, ...] = (
    ModelEntry(
        id="lfm25-26b",
        display_name="LFM2.5 2.6B",
        family="lfm2",
        task_tags=("chat", "agentic", "long-context", "multi-user", "fast"),
        repo="LiquidAI/LFM2.5-2.6B-GGUF",
        allow_globs={
            "Q5_K_M": "*Q5_K_M.gguf",
            "Q6_K": "*Q6_K.gguf",
            "Q8_0": "*Q8_0.gguf",
            "Q4_K_M": "*Q4_K_M.gguf",
        },
        params_b=2.697,
        weights_gib_by_quant={
            "Q5_K_M": 1.807,
            "Q6_K": 2.069,
            "Q8_0": 2.677,
            "Q4_K_M": 1.559,
        },
        # Only 8 of 30 layers use GQA. At Q8 KV: 8 layers * 8 KV
        # heads * 64 head dimension * K+V = 8 KiB/token.
        kv_kib_per_token=8.0,
        notes=(
            "Official standard GGUF; 128K-trained hybrid architecture with very low KV growth. "
            "Well suited to multiple concurrent users sharing llama.cpp's unified context pool."
        ),
        true_block_count=30,
        max_context_tokens=128000,
    ),
    ModelEntry(
        id="lfm25-12b-instruct",
        display_name="LFM2.5 1.2B Instruct",
        family="lfm2",
        task_tags=("chat", "long-context", "multi-user", "fast", "low-power"),
        repo="LiquidAI/LFM2.5-1.2B-Instruct-GGUF",
        allow_globs={
            "Q5_K_M": "*Q5_K_M.gguf",
            "Q6_K": "*Q6_K.gguf",
            "Q8_0": "*Q8_0.gguf",
            "Q4_K_M": "*Q4_K_M.gguf",
        },
        params_b=1.170,
        weights_gib_by_quant={
            "Q5_K_M": 0.785,
            "Q6_K": 0.897,
            "Q8_0": 1.161,
            "Q4_K_M": 0.681,
        },
        # 6 attention layers with the same 8 KV heads and 64-wide heads.
        kv_kib_per_token=6.0,
        notes=(
            "Official instruct GGUF; smallest general chat option in the catalog. "
            "Its 128K context and tiny KV cache leave exceptional multi-user headroom."
        ),
        true_block_count=16,
        max_context_tokens=128000,
    ),
    ModelEntry(
        id="qwen38-9b",
        display_name="Qwen3.8 9B (Empero distill)",
        family="qwen35",
        task_tags=("chat", "reasoning", "function-calling", "general"),
        repo="empero-ai/Qwen3.8-9B-GGUF",
        allow_globs={
            "Q4_K_M": "Qwen3.8-9B-Q4_K_M.gguf",
            "Q5_K_M": "Qwen3.8-9B-Q5_K_M.gguf",
            "Q6_K": "Qwen3.8-9B-Q6_K.gguf",
            "Q8_0": "Qwen3.8-9B-Q8_0.gguf",
        },
        params_b=9.7,
        # Exact uploaded decimal-GB sizes converted to binary GiB.
        weights_gib_by_quant={
            "Q4_K_M": 5.383,
            "Q5_K_M": 6.186,
            "Q6_K": 7.040,
            "Q8_0": 9.114,
        },
        # The student retains Qwen3.5-9B's 32-layer hybrid architecture:
        # eight full-attention layers, four KV heads, and 256-wide heads.
        kv_kib_per_token=72.0,
        notes=(
            "Standard text-generation GGUF of Empero's Qwen3.8 reasoning distillation. "
            "Q4_K_M is recommended for four-user headroom. Requires a recent llama.cpp "
            "build with Qwen3.5/Gated DeltaNet support; no MTP or vision artifacts are selected."
        ),
        temperature=0.6,
        top_p=0.95,
        top_k=20,
        min_p=0.0,
        checksum_manifest="SHA256SUMS",
        source_repo="empero-ai/Qwen3.8-9B",
        true_block_count=32,
        max_context_tokens=262144,
    ),
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
        # Source shards, the temporary f16 GGUF, and the final quant coexist
        # until the converted model has loaded successfully.
        temporary_disk_gib=46.0,
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
    ModelEntry(
        id="llama32-3b-instruct",
        display_name="Llama 3.2 3B Instruct",
        family="llama",
        task_tags=("chat", "general", "fast", "low-power"),
        repo="bartowski/Llama-3.2-3B-Instruct-GGUF",
        allow_globs={
            "Q5_K_M": "*Q5_K_M.gguf",
            "Q6_K": "*Q6_K.gguf",
            "Q8_0": "*Q8_0.gguf",
        },
        params_b=3.2,
        weights_gib_by_quant={"Q5_K_M": 2.16, "Q6_K": 2.46, "Q8_0": 3.19},
        kv_kib_per_token=56.0,
        notes="Fast, lightweight general chat model with ample context headroom.",
    ),
    ModelEntry(
        id="llama31-8b-instruct",
        display_name="Llama 3.1 8B Instruct",
        family="llama",
        task_tags=("chat", "general", "multilingual"),
        repo="bartowski/Meta-Llama-3.1-8B-Instruct-GGUF",
        allow_globs={
            "Q4_K_M": "*Q4_K_M.gguf",
            "Q5_K_M": "*Q5_K_M.gguf",
            "Q6_K": "*Q6_K.gguf",
        },
        params_b=8.0,
        weights_gib_by_quant={"Q4_K_M": 4.58, "Q5_K_M": 5.34, "Q6_K": 6.15},
        kv_kib_per_token=64.0,
        notes="Mature general-purpose instruct model; standard single-file K-quants.",
    ),
    ModelEntry(
        id="qwen25-coder-7b",
        display_name="Qwen2.5 Coder 7B Instruct",
        family="qwen2",
        task_tags=("chat", "code", "reasoning"),
        repo="bartowski/Qwen2.5-Coder-7B-Instruct-GGUF",
        allow_globs={
            "Q4_K_M": "*Q4_K_M.gguf",
            "Q5_K_M": "*Q5_K_M.gguf",
            "Q6_K": "*Q6_K.gguf",
        },
        params_b=7.6,
        weights_gib_by_quant={"Q4_K_M": 4.36, "Q5_K_M": 5.07, "Q6_K": 5.82},
        kv_kib_per_token=28.0,
        notes="Code generation, debugging, and technical chat with low KV-cache growth.",
    ),
    ModelEntry(
        id="deepseek-r1-qwen-7b",
        display_name="DeepSeek R1 Distill Qwen 7B",
        family="qwen2",
        task_tags=("chat", "reasoning", "math"),
        repo="bartowski/DeepSeek-R1-Distill-Qwen-7B-GGUF",
        allow_globs={
            "Q4_K_M": "*Q4_K_M.gguf",
            "Q5_K_M": "*Q5_K_M.gguf",
            "Q6_K": "*Q6_K.gguf",
        },
        params_b=7.6,
        weights_gib_by_quant={"Q4_K_M": 4.36, "Q5_K_M": 5.07, "Q6_K": 5.82},
        kv_kib_per_token=28.0,
        notes="Compact reasoning/math option; standard Qwen2-layout K-quants.",
    ),
    ModelEntry(
        id="mistral-nemo-12b",
        display_name="Mistral Nemo 12B Instruct",
        family="llama",
        task_tags=("chat", "general", "multilingual"),
        repo="bartowski/Mistral-Nemo-Instruct-2407-GGUF",
        allow_globs={
            "Q4_K_M": "*Q4_K_M.gguf",
            "Q5_K_M": "*Q5_K_M.gguf",
            "Q6_K": "*Q6_K.gguf",
        },
        params_b=12.2,
        weights_gib_by_quant={"Q4_K_M": 6.97, "Q5_K_M": 8.13, "Q6_K": 9.67},
        kv_kib_per_token=80.0,
        notes="Capable 12B multilingual model; Q5 is tight at 32k and Q6 favors shorter contexts.",
    ),
    ModelEntry(
        id="phi4-14b",
        display_name="Phi-4 14B",
        family="phi3",
        task_tags=("chat", "reasoning", "math", "code"),
        repo="bartowski/phi-4-GGUF",
        allow_globs={
            "Q3_K_M": "*Q3_K_M.gguf",
            "Q4_K_M": "*Q4_K_M.gguf",
        },
        params_b=14.7,
        weights_gib_by_quant={"Q3_K_M": 6.85, "Q4_K_M": 8.43},
        kv_kib_per_token=100.0,
        notes="High-capability standard K-quant; use Q4 at 16k or less. I-quants are not offered.",
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


def calculate_fit(
    model: ModelEntry,
    quant: str,
    ctx_tokens: int,
    *,
    kv_scale: float = 1.0,
    parallel_slots: int = 1,
) -> FitResult:
    if quant not in model.weights_gib_by_quant:
        raise KeyError(f"{quant} is not available for {model.display_name}")
    if ctx_tokens <= 0:
        raise ValueError("Context size must be positive")
    if model.max_context_tokens is not None and ctx_tokens > model.max_context_tokens:
        raise ValueError(
            f"{model.display_name} supports at most {model.max_context_tokens} context tokens"
        )
    if kv_scale <= 0:
        raise ValueError("KV scale must be positive")
    if not 1 <= parallel_slots <= 8:
        raise ValueError("Parallel slots must be between 1 and 8")
    weights = model.weights_gib_by_quant[quant]
    kv = ctx_tokens * parallel_slots * model.kv_kib_per_token / (1024 * 1024) * kv_scale
    required = weights + kv + OVERHEAD_GIB
    if required <= COMFORTABLE_VRAM_GIB:
        verdict = "FITS"
    elif required <= FAST_VRAM_GIB:
        verdict = "TIGHT"
    else:
        verdict = "NO-FIT"
    detail = f"{verdict} — ~{required:.2f} GiB of {FAST_VRAM_GIB:.0f} GiB fast VRAM"
    if parallel_slots > 1:
        detail += f" ({ctx_tokens:,} tokens × {parallel_slots} slots)"
    if verdict == "NO-FIT":
        detail += f" (~{GTT_SPILL_GIB:.1f} GiB GTT spill is slower and not counted as a safe fit)"
    return FitResult(weights, kv, OVERHEAD_GIB, required, verdict, detail)
