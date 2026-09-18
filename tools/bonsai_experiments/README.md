# Pinned Bonsai experiments

These are operator-run reproducibility tools for the September 18 BC250
experiments. They are not application features, automatic installers, or
general-purpose model converters. They use the exact paths and artifact
identities documented in the experiment records. Run with ordinary Python
assertions enabled, never `python -O`.

No model weights, credentials, conversations, private databases, or raw device
reports belong in this directory. Generated reports go under `/var/tmp` on the
device; inspected local copies belong in ignored `dist/`. Only sanitized
summaries are published in `docs/`.

## CRACK representation conversion

`convert_crack.py` and `repack.c` are the exact scripts used for the verified
CRACK conversion. The Python entry point requires the original publisher file
with SHA-256
`5b24ea3eebc3e0bccd05fb474eb88b10c57699d71a5db2f29485e3789a70d55d`.
Its fixed source, destination and work paths are declared at the top of the
script. Compile the C helper into the work directory:

```sh
cc -O2 -shared -fPIC repack.c -o /var/tmp/bc250-bonsai-experimental/crack-conversion/repack.so
python3 convert_crack.py
```

The integer-only repack retains all 128 quantized codes and the exact FP16
scale bits in each block. It refuses non-ternary codes and non-finite scales.
It copies all other tensors, including Hadamard transforms, byte for byte.
Metadata changes only the declared file type; tensor descriptors change only
the quantized type and data offsets. The saved file is re-read to verify every
block and unchanged tensor. The original full-file hash is checked before and
after conversion, and publication occurs only after verification.

The script is deliberately restricted to this one source model. It does not
register a model or activate a runtime. The result still needs Prism Hadamard
support. See [conversion evidence](../../docs/bonsai-crack-conversion-2026-09-18.json).

## Optimized build and measurements

`build_optimized.py` runs inside the existing development guest. It requires
the clean Prism source at commit
`5d80cff0b8cb9f2bf823cfc4e71e3abb97f290d6`, the previously verified extracted
Clang toolchain, and serialized GLSL compiler. It creates a separate O2 build,
uses one compiler job, and stops its own process group if its memory or time
limits are exceeded. It does not replace the active runtime. The build report
records executable hashes and resource observations.

`bounded_cxx.py` is the audited compiler launcher used for generated shader
constant files larger than 32 MiB. It splits only complete declarations,
verifies byte-identical reassembly, compiles sequentially, links with `ld -r`,
and verifies the exported symbol set. It does not change shader code.

`check_kernels.py` runs small PTQ and signed Hadamard GPU operations against
the CPU reference with FP16 disabled and enabled. An unsupported CPU reference
is a skip, not a pass. These checks do not establish general numerical quality.

`benchmark.py` uses the installed app's Python environment on the host. It
requires the known CRACK model at 8K / one slot, no active operation, no
recovery barrier, and no active API request. It temporarily stops the normal
model service, starts isolated loopback trials, tests fixed answers and two
192-token synthetic generations, records GPU clocks/utilization/temperature,
then restores and verifies the prior service and configuration in `finally`.
It never reads user conversations or changes GPU clock/power controls.

These measurements compare a fixed workload. They do not establish a universal
tokens/sec claim, long-context capacity, thermal throttling from temperature
alone, or a signed release. Use the candidate-bound report in `docs/` for the
actual result and limits.
