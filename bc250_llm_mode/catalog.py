from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .constants import COMFORTABLE_VRAM_GIB, FAST_VRAM_GIB, GTT_SPILL_GIB, OVERHEAD_GIB

FORBIDDEN_LAYOUT = re.compile(r"(?:^|[-_.])(imatrix[-_.]?max|max|fused)(?:[-_.]|$)", re.IGNORECASE)

# Entries that shipped in the v0.7.0 public beta have seen real use; anything
# added afterwards is a compatibility candidate until it passes an on-card
# BC-250 load/generation test. See the release-tier model in ARCHITECTURE.md.
V070_SHIPPED_IDS = frozenset({
    "lfm25-26b", "lfm25-12b-instruct", "qwen38-9b", "defiant-fable-9b",
    "qwen35-9b", "llama32-3b-instruct", "qwen3-14b", "qwen3-8b",
    "qwen25-coder-7b", "deepseek-r1-qwen-7b", "mistral-nemo-12b",
    "phi4-14b",
})


def validation_tier(model: "ModelEntry") -> str:
    """Release tier: 'supported' or 'preview' until hardware evidence exists."""
    if model.validation_tier != "auto":
        return model.validation_tier
    return "supported" if model.id in V070_SHIPPED_IDS else "preview"


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
    validation_tier: str = "auto"


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
        id="qwen38-9b-distill",
        display_name="Qwen3.8 9B Distill (converted)",
        family="qwen35",
        task_tags=("chat", "reasoning", "function-calling", "general"),
        repo="empero-ai/Qwen3.8-9B-Distill",
        source_repo="empero-ai/Qwen3.8-9B-Distill",
        # The publisher ships a single BF16 safetensors checkpoint and no GGUF,
        # so every artifact here is converted locally to a standard per-tensor
        # text-only layout; fused MAX repacks remain forbidden.
        allow_globs={"Q5_K_M": "*", "Q6_K": "*"},
        params_b=9.653,
        weights_gib_by_quant={"Q5_K_M": 6.24, "Q6_K": 6.92},
        kv_kib_per_token=72.0,
        notes=(
            "Empero's full-parameter reasoning distillation of Qwen3.8, distilled "
            "onto the Qwen3.5-9B architecture and converted locally from the "
            "official BF16 safetensors because no GGUF release exists. Text-only: "
            "the multimodal projector is excluded."
        ),
        temperature=0.6,
        top_p=0.95,
        top_k=20,
        min_p=0.0,
        true_block_count=32,
        max_context_tokens=262144,
        conversion=True,
        temporary_disk_gib=46.0,
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
    ModelEntry(
        id="qwen3-4b-instruct",
        display_name="Qwen3 4B Instruct 2507",
        family="qwen3",
        task_tags=("chat", "general", "fast", "low-power", "long-context"),
        repo="Qwen/Qwen3-4B-Instruct-2507-GGUF",
        allow_globs={
            "Q4_K_M": "*Q4_K_M.gguf",
            "Q5_K_M": "*Q5_K_M.gguf",
            "Q6_K": "*Q6_K.gguf",
            "Q8_0": "*Q8_0.gguf",
        },
        params_b=4.0,
        weights_gib_by_quant={"Q4_K_M": 2.34, "Q5_K_M": 2.71, "Q6_K": 3.19, "Q8_0": 4.15},
        # 36 attention layers with 8 KV heads and 128-wide heads.
        kv_kib_per_token=144.0,
        notes=(
            "Official non-thinking instruct refresh with an exceptionally large "
            "quality-per-gib ratio; even Q8_0 leaves room for long contexts."
        ),
        true_block_count=36,
        max_context_tokens=262144,
    ),
    ModelEntry(
        id="qwen25-3b-instruct",
        display_name="Qwen2.5 3B Instruct",
        family="qwen2",
        task_tags=("chat", "general", "fast", "low-power", "multi-user"),
        repo="bartowski/Qwen2.5-3B-Instruct-GGUF",
        allow_globs={
            "Q4_K_M": "*Q4_K_M.gguf",
            "Q5_K_M": "*Q5_K_M.gguf",
            "Q6_K": "*Q6_K.gguf",
            "Q8_0": "*Q8_0.gguf",
        },
        params_b=3.09,
        weights_gib_by_quant={"Q4_K_M": 1.78, "Q5_K_M": 2.05, "Q6_K": 2.35, "Q8_0": 3.06},
        # 36 layers but only 2 KV heads of 128 width: very slow KV growth.
        kv_kib_per_token=36.0,
        notes="Tiny general assistant with minimal KV pressure; excellent concurrent-user headroom.",
        true_block_count=36,
        max_context_tokens=32768,
    ),
    ModelEntry(
        id="mistral-7b-instruct-v03",
        display_name="Mistral 7B Instruct v0.3",
        family="llama",
        task_tags=("chat", "general", "multilingual"),
        repo="bartowski/Mistral-7B-Instruct-v0.3-GGUF",
        allow_globs={
            "Q4_K_M": "*Q4_K_M.gguf",
            "Q5_K_M": "*Q5_K_M.gguf",
            "Q6_K": "*Q6_K.gguf",
            "Q8_0": "*Q8_0.gguf",
        },
        params_b=7.25,
        weights_gib_by_quant={"Q4_K_M": 4.37, "Q5_K_M": 5.15, "Q6_K": 5.95, "Q8_0": 7.67},
        # 32 layers, 8 KV heads, 128-wide heads.
        kv_kib_per_token=128.0,
        notes="Classic efficient instruct baseline; function calling and a mature ecosystem.",
        true_block_count=32,
        max_context_tokens=32768,
    ),
    ModelEntry(
        id="deepseek-r1-llama-8b",
        display_name="DeepSeek R1 Distill Llama 8B",
        family="llama",
        task_tags=("chat", "reasoning", "math"),
        repo="bartowski/DeepSeek-R1-Distill-Llama-8B-GGUF",
        allow_globs={
            "Q4_K_M": "*Q4_K_M.gguf",
            "Q5_K_M": "*Q5_K_M.gguf",
            "Q6_K": "*Q6_K.gguf",
        },
        params_b=8.0,
        weights_gib_by_quant={"Q4_K_M": 4.58, "Q5_K_M": 5.34, "Q6_K": 6.15},
        # Llama 3.1 layout: 32 layers, 8 KV heads, 128-wide heads.
        kv_kib_per_token=128.0,
        notes="Strong chain-of-thought reasoning on the Llama 3.1 backbone; expect verbose <think> output.",
        temperature=0.6,
        top_p=0.95,
        true_block_count=32,
        max_context_tokens=131072,
    ),
    ModelEntry(
        id="gemma-2-9b-it",
        display_name="Gemma 2 9B IT",
        family="gemma2",
        task_tags=("chat", "general", "multilingual"),
        repo="bartowski/gemma-2-9b-it-GGUF",
        allow_globs={
            "Q4_K_M": "*Q4_K_M.gguf",
            "Q5_K_M": "*Q5_K_M.gguf",
            "Q6_K": "*Q6_K.gguf",
        },
        params_b=9.24,
        weights_gib_by_quant={"Q4_K_M": 5.44, "Q5_K_M": 6.31, "Q6_K": 7.29},
        # 42 layers with 8 KV heads and wide 256-wide heads: heavy KV growth.
        kv_kib_per_token=336.0,
        notes=(
            "Strong prose quality for its size, but the wide KV cache means short contexts; "
            "plan for 8K and treat longer settings as out of budget."
        ),
        true_block_count=42,
        max_context_tokens=8192,
    ),
    ModelEntry(
        id="llama32-1b-instruct",
        display_name="Llama 3.2 1B Instruct",
        family="llama",
        task_tags=("chat", "fast", "low-power", "multi-user"),
        repo="bartowski/Llama-3.2-1B-Instruct-GGUF",
        allow_globs={
            "Q4_K_M": "*Q4_K_M.gguf",
            "Q5_K_M": "*Q5_K_M.gguf",
            "Q6_K": "*Q6_K.gguf",
            "Q8_0": "*Q8_0.gguf",
        },
        params_b=1.24,
        weights_gib_by_quant={"Q4_K_M": 0.72, "Q5_K_M": 0.87, "Q6_K": 0.99, "Q8_0": 1.32},
        # 16 layers, 8 KV heads, narrow 64-wide heads.
        kv_kib_per_token=32.0,
        notes="Ultra-light tier: near-full precision fits alongside huge context or many users.",
        true_block_count=16,
        max_context_tokens=131072,
    ),
    ModelEntry(
        id="ornith-1.5-9b",
        display_name="Ornith 1.5 9B",
        family="ornith",
        task_tags=("chat", "general", "reasoning", "multilingual"),
        repo="ornith-ai/Ornith-1.5-9B-GGUF",
        # Exact filenames so the publisher's separate mmproj vision file is
        # never selected; this catalog is text-only.
        allow_globs={
            "Q4_K_M": "Ornith-1.5-9B-Q4_K_M.gguf",
            "Q5_K_M": "Ornith-1.5-9B-Q5_K_M.gguf",
            "Q6_K": "Ornith-1.5-9B-Q6_K.gguf",
            "Q8_0": "Ornith-1.5-9B-Q8_0.gguf",
        },
        params_b=9.0,
        weights_gib_by_quant={"Q4_K_M": 5.43, "Q5_K_M": 6.26, "Q6_K": 7.23, "Q8_0": 9.35},
        # Architecture details are not published yet; the conservative default
        # keeps multi-user projections honest until an on-card measurement.
        kv_kib_per_token=72.0,
        notes=(
            "Newest general-purpose release in the catalog; strong reasoning and "
            "tool-calling with Qwen-style chat templating and thinking support. "
            "Compatibility candidate until a Vulkan load test completes."
        ),
        true_block_count=32,
    ),
    ModelEntry(
        id="qwen38-2b-distill",
        display_name="Qwen3.8 2B Distill",
        family="qwen35",
        task_tags=("chat", "reasoning", "fast", "low-power", "long-context"),
        repo="empero-ai/Qwen3.8-2B-Distill-GGUF",
        allow_globs={
            "Q4_K_M": "Qwen3.8-2B-Q4_K_M.gguf",
            "Q5_K_M": "Qwen3.8-2B-Q5_K_M.gguf",
            "Q6_K": "Qwen3.8-2B-Q6_K.gguf",
            "Q8_0": "Qwen3.8-2B-Q8_0.gguf",
        },
        params_b=2.0,
        weights_gib_by_quant={"Q4_K_M": 1.30, "Q5_K_M": 1.51, "Q6_K": 1.73, "Q8_0": 2.15},
        kv_kib_per_token=72.0,
        notes=(
            "Official Empero GGUF of the small Qwen3.8 reasoning distillation; "
            "the highest-context reasoning option that still leaves huge multi-user "
            "headroom. Compatibility candidate until a Vulkan load test completes."
        ),
        temperature=0.6,
        top_p=0.95,
        top_k=20,
        min_p=0.0,
        max_context_tokens=262144,
    ),
    ModelEntry(
        id="qwen25-coder-3b",
        display_name="Qwen2.5 Coder 3B Instruct",
        family="qwen2",
        task_tags=("code", "chat", "fast", "low-power"),
        repo="Qwen/Qwen2.5-Coder-3B-Instruct-GGUF",
        allow_globs={
            "Q4_K_M": "Qwen2.5-Coder-3B-Instruct-Q4_K_M.gguf",
            "Q5_K_M": "Qwen2.5-Coder-3B-Instruct-Q5_K_M.gguf",
            "Q6_K": "Qwen2.5-Coder-3B-Instruct-Q6_K.gguf",
            "Q8_0": "Qwen2.5-Coder-3B-Instruct-Q8_0.gguf",
        },
        params_b=3.09,
        weights_gib_by_quant={"Q4_K_M": 1.98, "Q5_K_M": 2.28, "Q6_K": 2.62, "Q8_0": 3.29},
        # 36 layers with only 2 KV heads of 128 width.
        kv_kib_per_token=36.0,
        notes="Smallest dedicated code model in the catalog; near-full precision fits at 32K.",
        true_block_count=36,
        max_context_tokens=32768,
    ),
    ModelEntry(
        id="qwen25-coder-14b",
        display_name="Qwen2.5 Coder 14B Instruct",
        family="qwen2",
        task_tags=("code",),
        repo="bartowski/Qwen2.5-Coder-14B-Instruct-GGUF",
        allow_globs={
            "Q3_K_M": "*Q3_K_M.gguf",
            "Q4_K_M": "*Q4_K_M.gguf",
        },
        params_b=14.7,
        weights_gib_by_quant={"Q3_K_M": 7.01, "Q4_K_M": 8.99},
        # 48 layers, 8 KV heads, 128-wide heads: heavy KV growth per token.
        kv_kib_per_token=192.0,
        notes=(
            "The largest coding-dedicated model this card can hold; plan for a "
            "single user at 8K or less (192 KiB/token KV cache). Repo/eval fill-in "
            "and long-file edits belong on the smaller coder tiers."
        ),
        true_block_count=48,
        max_context_tokens=32768,
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


QUANT_RANK = ("Q8_0", "Q6_K", "Q5_K_M", "Q4_K_M", "Q3_K_M")


def _quant_rank(quant: str) -> int:
    return QUANT_RANK.index(quant) if quant in QUANT_RANK else len(QUANT_RANK)


def search_catalog(query: str = "") -> tuple[ModelEntry, ...]:
    """Case-insensitive substring match over id, name, family, and task tags."""
    lowered = query.strip().lower()
    if not lowered:
        return CATALOG
    return tuple(
        model
        for model in CATALOG
        if lowered in model.id.lower()
        or lowered in model.display_name.lower()
        or lowered in model.family.lower()
        or any(lowered in tag for tag in model.task_tags)
    )


def best_quant(
    model: ModelEntry,
    ctx_tokens: int,
    *,
    kv_scale: float = 1.0,
    parallel_slots: int = 1,
) -> tuple[str, FitResult] | None:
    """Highest-fidelity quant that safely fits, falling back to the best TIGHT one."""
    fits: list[tuple[str, FitResult]] = []
    tight: list[tuple[str, FitResult]] = []
    for quant, result in (
        (
            quant,
            calculate_fit(
                model, quant, ctx_tokens, kv_scale=kv_scale, parallel_slots=parallel_slots
            ),
        )
        for quant in model.weights_gib_by_quant
    ):
        if result.verdict == "FITS":
            fits.append((quant, result))
        elif result.verdict == "TIGHT":
            tight.append((quant, result))
    for pool in (fits, tight):
        if pool:
            return min(pool, key=lambda item: (_quant_rank(item[0]), item[1].required_gib))
    return None


def catalog_rows(
    query: str = "",
    *,
    ctx_tokens: int = 8192,
    kv_scale: float = 1.0,
    parallel_slots: int = 1,
) -> list[dict[str, Any]]:
    """Search-result rows with a best-fit quantization and VRAM verdict each.

    Shared by the CLI, the terminal chat, and the GUI catalog browser so all
    three surfaces present identical recommendations.
    """
    rows: list[dict[str, Any]] = []
    for model in search_catalog(query):
        row: dict[str, Any] = {
            "id": model.id,
            "display_name": model.display_name,
            "family": model.family,
            "params_b": model.params_b,
            "task_tags": list(model.task_tags),
            "validation_tier": validation_tier(model),
            "notes": model.notes,
        }
        pick: tuple[str, FitResult] | None = None
        fit_error: str | None = None
        try:
            pick = best_quant(model, ctx_tokens, kv_scale=kv_scale, parallel_slots=parallel_slots)
        except ValueError as exc:
            fit_error = str(exc)
        if pick is not None:
            row.update(
                recommended_quant=pick[0],
                verdict=pick[1].verdict,
                required_gib=round(pick[1].required_gib, 2),
                detail=pick[1].detail,
            )
        else:
            row.update(
                recommended_quant=None,
                verdict="NO-FIT",
                required_gib=None,
                detail=fit_error or "No quantization fits at this context size",
            )
        rows.append(row)
    return rows


def recommend_models(
    ctx_tokens: int,
    *,
    parallel_slots: int = 1,
    kv_scale: float = 1.0,
    tag: str | None = None,
    limit: int | None = None,
) -> tuple[tuple[ModelEntry, str, FitResult], ...]:
    """Rank catalog entries by the highest-fidelity quantization that safely FITS.

    TIGHT and NO-FIT projections are never recommended. Ordering is quantization
    fidelity first, then parameter count, so the head of the tuple is the most
    capable model that fits the requested context × slots budget.
    """
    ranked: list[tuple[ModelEntry, str, FitResult]] = []
    for model in CATALOG:
        if tag and not any(tag.lower() in candidate.lower() for candidate in model.task_tags):
            continue
        try:
            pick = best_quant(model, ctx_tokens, kv_scale=kv_scale, parallel_slots=parallel_slots)
        except ValueError:
            # Context above the model's trained limit: never a recommendation.
            continue
        if pick is None or pick[1].verdict != "FITS":
            continue
        ranked.append((model, pick[0], pick[1]))
    ranked.sort(key=lambda item: (_quant_rank(item[1]), -item[0].params_b))
    if limit is not None:
        ranked = ranked[: max(0, limit)]
    return tuple(ranked)


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
