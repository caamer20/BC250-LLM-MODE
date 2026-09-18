# Bonsai application installation

The owner explicitly requested completion of the installation after confirming
that both Bonsai models were absent from the physical app's Model Library.
This authorizes the application update, Prism runtime integration and managed
model installation, with recovery and verification. It does not authorize
GitHub publication or an external release.

## Exact compatibility boundary

Dev13 accepts a single audited Prism build through
`llamacpp update --tag prism-bonsai-2`. The input is a closed identifier, not a URL, path, command,
build flag or skip-verification switch. The pre-staged bundle lives inside
the managed runtime area at `prism-bonsai-2-staged`. Its source checkout,
full pinned build manifest, container identity, architecture and all three
executables must match `prism_runtime.py`. Its full source is retained. The
workflow records reuse of staged binaries; it does not claim fresh compilation.

The same durable runtime workflow then captures the running model and previous
runtime, exchanges the trees atomically, requires a fresh identity-bound start
receipt, verifies model/context/slots and inference, and only then promotes.
No known-good state or profile measurement is seeded manually. A switch back
to a stock runtime is refused while a Bonsai model remains selected; switch to
a standard model first. Failed runtime/model activation retains the normal
restoration path and its exact prior configuration.

Only the publisher-verified official PTQ1 bytes and the independently verified
lossless CRACK PTQ1 bytes gain the specialized layout classification during
managed import. Complete SHA-256 identities—not names or architecture strings—
select this handling. Other Hadamard files and the publisher's PQ2 file still
fail closed. The local CRACK repack does not create a nonexistent Hub variant.
Its source PQ2 identity and lossless-conversion provenance remain attached.

The supported installation settings are at most 8,192 tokens, one slot, Q8
cache, 128-token batch/microbatch, two CPU threads and synchronized Vulkan.
The launcher uses mmap, disables reasoning output and context shifting for
these model files, and checks their bound runtime identity. These limits are
basic compatibility bounds, not measured workload profiles or sustained-use
qualification. The original C++ build is unoptimized.

## Storage and app visibility

Normal durable local import owns publication and model registration. It may
use independent CoW clones on supporting filesystems; it never links the
mutable source and managed model together. Full hashes still verify each
publication. Space reservations cover two complete local copies even when
CoW saves physical blocks. The original public and derived files are retained.

Model Library uses the exact promoted runtime identity to show installed
Bonsai rows as startable. Before promotion it shows the runtime requirement.
Context/slot limits are surfaced separately from the memory estimate. The
official remote entry can be acquired with the matching runtime; original
CRACK PQ2 remains blocked. Existing ordinary models retain their behavior.

A snapshot defect discovered during integration read the promoted runtime
repositories after closing the connection and silently omitted their fields.
Dev13 reads those records within the query transaction so subsequent handoff
regeneration keeps the actual promoted identity.

## Qualification and deployment

Implementation and qualification are in progress. Physical app activation
and model registration have not yet occurred at this source checkpoint.
The staged Prism source and three executables have verified. A separate
CRACK trial with the app's Q8 cache and Vulkan environment passed two short
answers, including SSE, at 8K/one slot; it stopped cleanly. Production remained
on dev9 and its original model during those checks. Final candidate-bound
application, deployment, model-switching and preservation evidence must be
recorded before installation is claimed complete.

## Installation-discovered startup correction

The first dev13 application cutover preserved all 13 private files and the
service/boot configuration. Its first Prism runtime update exposed a real
systemd startup race: `restart` returned before the launcher published its
identity receipt. The runtime workflow retained both trees and restored the
original binary, but marked recovery required because a regenerated handoff
changed only derived revision/fingerprint metadata.

The correction waits up to 20 seconds for a fresh, operation-bound receipt;
the subsequent full tree, binary, model, context, slots and inference checks
remain mandatory. Restoration accepts regenerated metadata only when all
launch/identity fields equal the captured snapshot and the complete observed
artifact equals the current app rendering. Foreign settings remain refused.
An explicit operator reconciliation API verifies the complete prior runtime
restoration before releasing its recovery leases. It appends an audit event
and preserves the failed terminal operation unchanged; it creates no promoted
runtime, known-good record or measurement. The installation uses this only
after inspecting the retained trees and qualifying the correction.
