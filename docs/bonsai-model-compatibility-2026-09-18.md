# Requested Bonsai 2 models and BC250 compatibility

Both requested models are now discoverable in the development catalog. They
are marked **Needs another runtime**, with installation/start withheld by the
current application. Estimated memory fit is shown separately from runtime
compatibility. The official PTQ1 pack passes a separate, short BC250 inference
smoke test. Neither has a qualified BC250 performance claim or app integration.

| Requested model | Selected published file | Weight size | Current limitation |
| --- | --- | --- | --- |
| PrismML Bonsai 2 27B | `Ternary-Bonsai-2-27B-PTQ1_0.gguf` | 5.95 GB / 5.54 GiB | Requires PrismML's custom runtime; basic isolated Vulkan inference passes at 8K, while app integration and sustained/performance qualification remain pending. |
| dealignai Bonsai 2 27B CRACK | `Bonsai-2-27B-PQ2_0-CRACK.gguf` | 7.21 GB / 6.71 GiB | Requires PrismML's custom runtime; the inspected Vulkan backend does not implement PQ2_0 and the built BC250 support query explicitly reports it unsupported. |

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

An isolated Vulkan compatibility build completed under
`/var/tmp/bc250-bonsai-experimental`, using the existing compiler guest, one
build job and serialized shader compilation. GCC attempts exceeded the bounded
compiler budget; Clang 22.1.8 used signed Fedora packages extracted only into
the experimental directory. No guest packages were installed and the fork's
source remained unchanged. The optimized Clang attempt was stopped after a
long Vulkan translation-unit compile. Unoptimized C++ passed that step, then
reached the RSS cutoff on a 132 MB generated shader-data file.

The final compiler wrapper splits that generated file's complete declarations
into 16 chunks, verifies byte-identical reassembly, compiles them sequentially
and checks all 2,078 exported symbols after linking. No shader bytes are
changed. The successful attempt took 341 seconds, peaked at 555 MiB of build
process RSS and retained at least 1,670 MiB of host memory. The monitor retained
its 1,536 MiB build RSS cutoff and 512 MiB host reserve throughout. The exact
recipe, wrapper, reports and hashes are recorded in the JSON research record.
This is a **compatibility build with C++ optimization disabled**, so its
timings must not be presented as production performance.

The test binary identifies `AMD BC-250 (RADV GFX1013)`. Five small PTQ matrix
cases and one signed 1,024-wide Hadamard case pass against the CPU reference.
One F16-input comparison skips because the CPU reference does not support it;
the support-only query also identifies unsupported broadcast/permutation
shapes. These results establish only the recorded cases, not every kernel or
model workload.

The full official model then loaded in a temporary server at
`127.0.0.1:18080`, with an observed 8,192-token context and one slot. Two short
fixed-answer checks pass, including a complete SSE response. Fast-VRAM fit
was checked against the device's actual 12 GiB budget and the existing model's
allocation, with extra reserve. The trial retained the original model service.
Its 42-second observation peaked at 1,994 MiB process RSS, 9,414 MiB **total**
fast-VRAM use including the original model, and 74°C. These are brief
diagnostics; they do not qualify sustained temperature, quality or speed.
The trial monitor allowed at most 2 GiB RSS, preserved 512 MiB host memory and
768 MiB fast VRAM, and stopped at 75°C or its time limit. No cutoff was reached.

The temporary server exited cleanly and fast-VRAM use returned to the exact
starting value. The original model subsequently answered a fixed prompt.
The initial 16-token preservation probe had no final content; the successful
128-token probe used 35 tokens including reasoning. Application link/version,
service invocations/PIDs/units, production checkout and boot target remain
unchanged; model and SearXNG health are 200. Dev9 remains active, and the
experimental listener is gone. No runtime promotion, known-good identity or
profile measurement record was created.

The catalog uses a conservative 64 KiB/token cache estimate plus its standard
overhead allowance. Weight size alone does not predict total GPU or host-memory
use. In particular, the active small model's 128,000-token context must not be
carried over to a 27B model without a fresh fit decision. Start experimental
qualification at a small context and preserve the existing fit/TIGHT gates.

The [research record](bonsai-model-compatibility-2026-09-18.json) contains the
exact repository revisions, publisher hashes, header observations and limits.
The official PTQ1 file has also been downloaded in full outside the production
model store. Its 5,946,648,928 bytes match the publisher SHA-256
`53107f530aa52eb00912263ab1ee29bd199261c87cd7b4ad4ca1318c1fe33ee3`.
CRACK remains a 16 MiB header-prefix inspection and has not been loaded.
Proper application runtime integration/promotion, an optimized build and
sustained model/workload/restore qualification are still required before the
official entry can be offered as a normal app model. CRACK additionally needs
a compatible backend or separately verified compatible packing. No converted
model was substituted for the requested published artifact.

## Application validation

Dev12 code commit `20d83c851b638edb272715c57d3d1f9988a2c2a4` is locally
qualified on macOS/Python 3.14.7. The combined default and slow inventory selects
1,897 tests: **1,878 pass and 19 expected Linux-only checks skip**. All 52 slow
gates pass. The clean source → sdist → wheel build and fresh hash-locked
environment verify all 195 Python modules / 196 package files. Its exact wheel
SHA-256 is `e754fe0994450ff03ad54472657985082e8e2ba889f5cab91ec4620d2975405c`.

Installed checks pass 64/64 feature cases and 31/31 runtime/recovery cases.
Native Tk checks pass 28 route/scale cases and the experience journeys. Both
Bonsai entries also pass real-widget selection and keyboard-action checks at
100% and 200% scale: the runtime explanation is visible and installation is
not offered. These additional checks overlap the suite. The sandboxed macOS
window launch aborted; the native checks then passed outside that sandbox,
with temporary application state and host observations/actions suppressed.

The [application qualification record](bonsai-qualification-2026-09-18.json)
binds the exact artifacts and raw reports in `dist/dev12-bonsai-20260918`.
The artifact-set verifier passes; the unsigned release decision remains
**BLOCKED**. Dev12 has not been deployed or qualified on Linux. Dev11's
separate Linux results remain bound to its unchanged wheel. Broader model,
desktop, participant, security and signed-release gates remain pending.
