# Bonsai CRACK performance investigation

The owner requested an optimized runtime, an FP16 comparison, GPU clock and
temperature measurements, and publication of the project upgrades. The
experiments found no demonstrated token-generation speed gain from O2 or FP16.
They did expose an oversized default host-RAM prompt cache. Dev14 disables
that extra cache for the verified Prism runtime, retaining the live slot's GPU KV
cache and the previously qualified runtime/precision choice.

## Measured results

All runs used the same verified CRACK PTQ1 file at 8,192 tokens / one slot,
Q8 KV cache, batch/microbatch 128, two CPU threads, flash attention auto,
reasoning off, and no context shifting. The corrected comparison explicitly
used `--cache-ram 0`. Each mode passed five fixed answers, two 192-token
synthetic generations, and a final SSE check. Generated text hashes matched
across all six long responses. No user conversation was read or retained.

| Runtime / precision | Output tok/s, runs 1 / 2 | Initial / peak °C | Median GPU MHz during generation | Minimum host memory available |
| --- | ---: | ---: | ---: | ---: |
| Installed O0 / FP16 disabled | 3.41 / 2.85 | 64 / 86 | 1,440 | 2,074 MiB |
| Candidate O2 / FP16 disabled | 2.86 / 2.62 | 75 / 87 | 1,290 | 2,061 MiB |
| Candidate O2 / FP16 enabled | 2.83 / 2.54 | 75 / 87 | 1,250 | 2,046 MiB |

These are generation timings, separate from prompt evaluation. Initial
temperatures differed and the automatic governor changed clock frequency.
The table therefore does not isolate a compiler or precision effect. It
shows no demonstrated speed improvement worth replacing the qualified
runtime or changing its precision policy. It also shows why a short, cool
response is not a sustained-throughput claim.

The existing governor was active with a 1,000–1,850 MHz range, throttling at
85°C and recovering at 75°C. No GPU controls were changed. The successful
comparison used an independent 90°C cutoff, below the application's configured
95°C stop value; that value is not a claim that the application's optional
watchdog was enabled. Earlier 75°C context trials and an 82°C diagnostic
performance cutoff stopped below the normal governor's throttle point and
must not be described as physical thermal limits. Clocks fell as temperatures
reached 85–87°C. No direct utilization or throttle counter was exposed in the
inspected interfaces, so temperature alone is not reported as a hardware fault.
Power observations are for the SoC, including CPU.

## Host-memory correction

The pinned fork defaults `--cache-ram` to **8,192 MiB**, which exceeds this
device's approximately 3.5 GiB host-memory allocation. This cache retains
additional prompt states; it is separate from the active slot's GPU KV cache.
With the default, available host memory fell to 382–390 MiB between requests
and the 512 MiB experiment guard stopped the trials. A completed baseline
response before that stop measured 3.56 tok/s.

With the extra cache disabled, all three modes completed their repeated
requests with at least 2,045 MiB host memory available. The application fix
adds the supported `--cache-ram 0` switch only to the exact verified Prism runtime, including when it serves
an ordinary model. Ordinary runtimes retain their existing command compatibility. This
corrects memory pressure; it is not advertised as a faster decoder.

## Optimized candidate

The isolated O2 build uses unchanged Prism source
`5d80cff0b8cb9f2bf823cfc4e71e3abb97f290d6`, the retained verified Clang 22.1.8
toolchain, one compiler job, and the byte/symbol-verified shader-data compiler
launcher. Compilation completed in 1,785 seconds, with 858 MiB peak build RSS
and 569 MiB minimum host memory available. The active service remained running
during compilation.

Both precision modes pass five small PTQ matrix checks and one signed
Hadamard check against CPU references. A PTQ F16-input case has no supported
CPU reference and remains skipped in each mode. The full-model checks above
are basic compatibility observations, not a broad quality evaluation or soak.

The O2 server SHA-256 is
`bf3c64dd148d8719938efa58de381073b4558aa2901293e6af89e08bbba6e948`.
It remains an isolated candidate; it was not promoted. FP16 remains disabled
in the application. Inspection also confirmed that this fork does not read
`GGML_VK_FORCE_SYNC`; setting that variable is not evidence of synchronization
or an available performance lever.

All temporary servers are stopped, and the normal CRACK service, inference,
configuration, service definition, governor configuration, and boot target
were verified after the comparison. The development application's subsequent
dev14 qualification and deployment are tracked in the [exact qualification record](bonsai-cache-qualification-2026-09-18.json).
Qualification passes; application activation awaits separate owner approval. Context
remains 8K; long-context qualification is still pending.

See the [sanitized measured record](bonsai-performance-2026-09-18.json) and
[reproducibility tools](../tools/bonsai_experiments/README.md). Raw reports,
device logs, model weights, conversations, credentials, and databases are
excluded from Git. Human acceptance, independent security, soak, and signed
release gates remain pending.
