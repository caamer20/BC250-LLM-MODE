# Requested Bonsai 2 models and BC250 compatibility

Both requested models are now discoverable in the development catalog. They
are marked **Needs another runtime**, with installation/start withheld by the
current application. Estimated memory fit is shown separately from runtime
compatibility. Neither has a measured BC250 performance claim.

| Requested model | Selected published file | Weight size | Current limitation |
| --- | --- | --- | --- |
| PrismML Bonsai 2 27B | `Ternary-Bonsai-2-27B-PTQ1_0.gguf` | 5.95 GB / 5.54 GiB | Requires PrismML's custom runtime; its inspected source includes PTQ1_0 Vulkan kernels, but on-card qualification is pending. |
| dealignai Bonsai 2 27B CRACK | `Bonsai-2-27B-PQ2_0-CRACK.gguf` | 7.21 GB / 6.71 GiB | Requires PrismML's custom runtime; the inspected Vulkan backend does not implement PQ2_0. |

For the official model, **PTQ1_0 is the appropriate experimental candidate**:
it has a smaller footprint and a Vulkan implementation in the inspected fork.
PQ2_0's published speed advantage on some other GPUs does not establish that
it is faster or usable on a BC250. The 53.8 GB F16 file is excluded. The model
card's advertised benchmark scores and maximum context are publisher claims,
not results on this device. [Official model](https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf)

The CRACK publisher supplies PQ2_0 only at the inspected revision. Its card
reports a correction for an earlier reasoning-loop defect; the recorded file
identity is from the updated revision. No alternative model or locally repacked
file has been substituted for the requested model.
[CRACK model](https://huggingface.co/dealignai/Bonsai-2-27B-Ternary-CRACK-GGUF)

## Why the normal runtime cannot load these

Both GGUF headers declare `qwen35`, but also declare PrismML Hadamard activation
transforms. Architecture recognition alone is therefore insufficient. The
official file contains private tensor type 143 (PTQ1_0); CRACK contains type
142 (PQ2_0). The inspected headers contain 851 tensors, 64 blocks, a 262,144
trained context and about 26.896 billion language-model parameters.

PrismML distinguishes Bonsai 2 from its earlier ternary generation: even a
stock-recognized Q2_0 packing of Bonsai 2 needs the extra transforms. Loading
such a file with a stock runtime can produce incorrect output instead of a
clean refusal. [Format requirements](https://github.com/PrismML-Eng/Bonsai-demo/blob/main/MODEL-FORMATS.md)

The application now scans the bounded metadata beyond `general.architecture`
and refuses files requiring these transforms, including renamed local imports.
It also recognizes the observed private file-type identifiers. Tokenizer
arrays are skipped without retaining their contents; entry, byte, array and
nesting limits remain explicit. Existing ordinary catalog fingerprints remain
unchanged; a runtime requirement contributes to the new entries' fingerprints.

## Experimental runtime preparation

The separate test source is pinned to PrismML commit
`5d80cff0b8cb9f2bf823cfc4e71e3abb97f290d6`. Its Vulkan source includes PTQ1_0
dequantization, matrix/vector and row-access support, together with the required
transform implementation. PQ2_0 is absent from that backend.
[Inspected Vulkan implementation](https://github.com/PrismML-Eng/llama.cpp/blob/5d80cff0b8cb9f2bf823cfc4e71e3abb97f290d6/ggml/src/ggml-vulkan/ggml-vulkan.cpp)

An isolated Vulkan build is in progress under
`/var/tmp/bc250-bonsai-experimental`, using the existing compiler guest, one
build job and serialized shader compilation. No source changes are applied to
the fork. The application runtime, services, model selection, context and boot
settings are not being replaced. This is preparation for compatibility work,
not promotion through the application's runtime lifecycle.

The catalog uses a conservative 64 KiB/token cache estimate plus its standard
overhead allowance. Weight size alone does not predict total GPU or host-memory
use. In particular, the active small model's 128,000-token context must not be
carried over to a 27B model without a fresh fit decision. Start experimental
qualification at a small context and preserve the existing fit/TIGHT gates.

The [research record](bonsai-model-compatibility-2026-09-18.json) contains the
exact repository revisions, publisher hashes, header observations and limits.
Only 16 MiB prefixes were inspected; full model hashes and inference have not
been verified. Runtime installation, load/generation, peak memory, temperature,
latency, throughput and restoration evidence are still required before either
entry can be offered as a working BC250 model.
