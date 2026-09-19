# Runtime recovery corrections, September 17

Source `0.9.0.dev10` at `ff26fb306b5a13e928d13ee8a292046fb613b0d1` on
`codex/runtime-identity-dev10` passed developer qualification and is staged.
The existing Bazzite installation runs dev9 after a reverted application
activation attempt. Retrying activation and rebuilding the existing runtime
await explicit approval. No runtime cutover, GitHub push, tag, package
publication, signing or trust-root change occurred. See the
[exact qualification record](runtime-qualification-2026-09-17.json).

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

## Qualification and application activation

Combined default/slow inventories select **1,844** tests on each host:
Bazzite/Python 3.14.6 passes **1,841**, with three skips;
macOS/Python 3.14.7 passes **1,831**, with 13 expected Linux-only skips.
All 52 slow gates pass on both. Two Bazzite host skips require a compiler;
the same installed wheel passes all 33 runtime fixture checks in the existing
inference guest, including those compiler cases. These use fixture service and
inference replies and do not qualify a production runtime exchange.

The clean source → sdist → wheel build has wheel SHA-256
`f52a66068c3d532f0286e599918f2c6dd05c613ebdd50e81034ed8f97db8a4fb`.
Fresh hash-locked environments on both hosts verify all 195 Python modules /
196 package files against source and wheel. Each passes 42 installed feature
checks, 28 actual Tk route/scale checks and native experience journeys at
100% and 200% scale. Additional checks overlap the suite inventory.

Staged dev10 native Chat used the actual Bazzite model and local SearXNG:
the answer completed in 5.565 seconds, cited the exact source URL and used
model-counted context. Save/reopen retained the answer and source. RSS was
76,944 KiB after chat and 78,612 KiB after 100 route changes; a 15-second idle
sample used 0.45% of one CPU core. These are short isolated-Xvfb observations,
not desktop, participant or soak acceptance. The owned temporary display
container was removed after testing.

The first application activation reached a healthy dev10 model service, then
its audit comparison raised an assertion because `runtime-policy.json` changes
every five seconds as a generated supervisor heartbeat. The deployment script
restored the dev9 environment and prior source checkout and restarted the
previously active services. All 13 persistent private files remained
byte-identical. A subsequent read-only check confirmed dev9 and model HTTP 200.
Service units, boot configuration, model alias, context 128,000 and one slot
remain unchanged. No runtime update or profile calibration was performed.

The corrected retry excludes generated runtime status from the persistent
user-data comparison and takes a fresh backup. It has **not run**: automatic
approval review rejected the retry as an unapproved dev10 deployment beyond
the checkpoint, citing service disruption and data-integrity risk. Explicit
approval for activation and the real runtime rebuild is pending. Source
publication also remains unapproved after an earlier automatic review rejected
the GitHub push; nothing was pushed.

Retain the first-attempt backup under
`/root/.bc250-deployments/20260917-ff26fb3/rollback`. After the reverse exchange,
its `app-venv` link points to the **inactive dev10 environment**, not dev9;
never blindly restore that link. The live application link points to the
original dev9 environment. Preserve the older dev5 backup and never replace
newer user data with an old snapshot. Conversation schema 2 still cannot be
read by the actual dev5 application; database schema remains 14.

Checksums, the wheel-bound SBOM and the unsigned BLOCKED manifest verify in
the local `dist/dev10-runtime-20260917` packet. The release evaluator remains
ineligible. This record is developer evidence, not signed release evidence.

## Remaining boundary

Fresh installation is not covered by the successful existing-runtime tests.
Inspection found that Setup requests the durable runtime update before
`MODEL_SELECTED`, while the update's promotion path requires live model
inference. The real payload renderer produces an empty model path for fresh
state, and the production model selector refuses it with
`No current installed model is selected`. This is a remaining setup dependency
to resolve and test before claiming a fresh-install PASS. Stable absence of a
prior runtime is accepted by preflight; the missing model is the unresolved
dependency. Neither the fixture first-install test nor primitive initial
publication proves that the actual no-model setup completes. A correction must
preserve durable recovery and defer promoted/known-good claims until real
inference succeeds; it must not manufacture a model or bypass verification.

Any later application/runtime deployment must be recorded against its exact
commit and artifacts. The current
runtime's source commit is
`000547513f1530346ecd163db8b3e13962949961`; rebuilding that exact source avoids
downgrading it merely to obtain an identity. An observed legacy tree is not
automatically a promoted rollback target.

Hardware profiles remain ESTIMATED until real measurements pass the existing
identity, restoration and fit gates. TIGHT trials still require explicit
acceptance. CachyOS, desktop/human, phone, reboot, interruption, thermal/soak,
accessibility, independent security and signed release gates remain pending.
Release remains BLOCKED.
