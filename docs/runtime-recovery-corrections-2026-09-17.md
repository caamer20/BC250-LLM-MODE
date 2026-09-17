# Runtime recovery corrections, September 17

Source `0.9.0.dev10` on `codex/runtime-identity-dev10` is in qualification.
The existing Bazzite installation still runs dev9. No runtime cutover, GitHub
push, tag, package publication, signing or trust-root change has occurred for
this work. The dev9 deployment record continues to identify the installed
application exactly.

## Why this work is needed

Profile calibration correctly refused to run without an immutable promoted
runtime and an identity-bound known-good restoration target. The installed
legacy runtime provides neither. Checking its supported replacement path
exposed production-adapter defects that scripted command responses had hidden.
The existing runtime remains running while these corrections are qualified.

The small LFM2.5 model was imported through the supported local acquisition
workflow as `lfm25-26b-verified`, operation
`0d09023c-5f5c-47a4-b145-d1873dd42d49`. Its SHA-256 is
`babb80c3249e1578e47d481bf494844a83b4cbfead6fc614a6450908b0f60c65`.
This is a separate managed copy; the active alias, original model bytes,
context 128,000 and one slot were preserved. No calibration result was written.

## Corrections and concrete checks

Real Git fixtures reproduced failures for valid tags, branches and full
commits. Resolution now observes exact refs and annotated-tag peels, rejects
ambiguous names, and fetches the recorded commit. Worktree recovery accepts
Git's `.git` file while requiring the exact HEAD and clean contents. A full
commit request identifies the immutable object; its existence and commit type
are proved during the heartbeat-enabled fetch step.

Build configuration observes the actual container image from host Podman,
binds source and toolchain into the receipt, and uses the real CMake output
directory and separate target arguments. Static llama.cpp libraries prevent
absolute build-directory dependencies after cutover. Upstream's quantizer
does not implement `--version`; its explicit help/usage response is checked
instead, with its invocation path removed before hashing the smoke output.

The process runner previously ignored stdin payloads. Real subprocess tests
now cover byte delivery, simultaneous output, bounded tails, timeout and lost
lease cleanup. A physical disposable-process experiment also confirmed that
a guest process survives a terminated Podman client. The fixed guest
supervisor requires host heartbeats and kills its child process group when
those heartbeats stop. The same experiment with the supervisor passed.

The shipped atomic helper now receives and parses the correct argv. Initial
publication uses `RENAME_NOREPLACE`; ordinary cutover uses `RENAME_EXCHANGE`.
The displaced runtime remains at the original candidate location. Recovery
uses the two locations captured durably before the effect and verifies their
manifests and server hashes. It performs no speculative moves or deletions.
Finalization records locations and retains the displaced tree.

Legacy adoption records the observed binary without promoting it or claiming
immutable source provenance. Promotion requires the actual start receipt,
manifest, binary hash, new invocation, selected model, exact context and slots,
and the workflow's inference result. Long health checks keep the operation
lease alive. Failure restoration restores prior lineage and the exact saved
known-good configuration, including a prior absence of either identity.

The regression fixtures exercise real Git, CMake and filesystem effects with
the production adapter and durable engine. Service/health replies remain
fixtures in those tests. Their results are developer evidence, not physical
model, desktop, participant, independent security or release acceptance.

## Qualification boundary

Full candidate qualification and any later application/runtime deployment
must be recorded against their exact commits and artifacts. The current
runtime's source commit is
`000547513f1530346ecd163db8b3e13962949961`; rebuilding that exact source avoids
downgrading it merely to obtain an identity. An observed legacy tree is not
automatically a promoted rollback target.

Hardware profiles remain ESTIMATED until real measurements pass the existing
identity, restoration and fit gates. TIGHT trials still require explicit
acceptance. CachyOS, desktop/human, phone, reboot, interruption, thermal/soak,
accessibility, independent security and signed release gates remain pending.
Release remains BLOCKED.
