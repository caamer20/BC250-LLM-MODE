# Current implementation state

Source version `0.9.0.dev10` at `ff26fb3` is developer-qualified and staged on
`codex/runtime-identity-dev10`. Its 1,844-test inventory passes on macOS
(1,831 passed / 13 skips) and Bazzite (1,841 passed / three skips); all 52 slow
gates pass on both. The exact installed wheel, real Tk and staged live
model/SearXNG checks also pass. See [the corrective work and reverted activation
attempt](runtime-recovery-corrections-2026-09-17.md) and
[qualification record](runtime-qualification-2026-09-17.json).

The active development version is **dev9**. The first dev10 application
activation was reverted after its audit incorrectly compared a generated
heartbeat as persistent data. Dev9 is restored and healthy; 13 persistent
private files, service units and boot configuration are preserved. The
corrected activation retry and real runtime rebuild await explicit approval
after automatic approval review rejected the retry. No runtime promotion,
profile measurements or GitHub push occurred. The dev9 artifact identity and
historical results below remain unchanged.

The [September experience improvements](experience-improvements-2026-09.md)
build on
[the full application review plan](../FULL_APP_REVIEW_IMPLEMENTATION_PLAN.md)
and the GUI, appliance-experience and end-user-friendliness implementations.
The exact dev9 wheel is installed on the existing Bazzite BC250. See
[deployment details](experience-deployment-2026-09-17.md) and the
[candidate-bound diagnostic record](experience-deployment-2026-09-17.json).
Human acceptance, independent security review and final release remain pending.

Physical Bazzite access became available on September 17. Initial dev6 testing
found that Fedora Python's virtual-memory baseline exceeded the HTTP worker's
available import headroom. The dev7 correction keeps a bounded 384 MiB ceiling;
its Linux regression and real model-counting probe pass. Linux Tk journeys
then exposed cyclic Tk dialog objects being finalized by Python on a worker.
Dev8 routes cyclic collection through the UI's existing refresh coordinator,
including safe retention when a worker outlives the short close budget. The
corrected real Tk journeys pass on Linux and macOS. The live model inventory
also exposed a digest-format mismatch: acquisition stores `sha256:<hex>`,
while profile identities use bare hex. Dev9 normalizes that verified input
without changing existing profile fingerprints or trusting unverified models.
Calibration still requires a real promoted runtime/known-good identity; the
legacy runtime does not yet provide one. After dev9 deployment, the supported
local-import operation verified a separate managed copy of the small model as
`lfm25-26b-verified`; the active alias and original source were preserved.
The runtime adapter corrections must qualify before establishing a promoted
runtime. Profile measurements were not invented or run past those safeguards.

The installed code commit is `d3fcf3bf9cd3fbb39b8c6f6749330973990a076c`, on
`codex/chat-experience-dev6`. Its exact wheel SHA-256 is
`b9059ec6715d7e357b82359daabd6e0ad316b87459984eec85193b951b1a8554`.
The combined default/slow inventory selects 1,823 tests: Bazzite/Python 3.14.6
passes 1,822 with one expected skip; macOS/Python 3.14.7 passes 1,819 with four
expected Linux-only skips. All 52 slow gates pass on both. The fresh hash-locked
Bazzite environment matches all 194 Python modules/195 package files against
the source and wheel. Fifty additional installed-feature checks, 28 real Tk
route/scale checks, and the native experience journeys pass; these overlap the
suite and are not added to its inventory.

Native Chat successfully used the actual model and a live, local SearXNG
provider, with model-counted context and an exact source URL in the answer.
The measured window used about 75 MiB after chat and 77 MiB after 100 route
changes. These are short observations with temporary profiles and an isolated
Xvfb display on the BC250, not desktop participant or soak qualification.
The provider's failure/restart path also passed. SearXNG is bound only to
loopback, has a 384 MiB limit and no boot service, and is saved in Chat settings.

The active model, 128,000 context, one slot, service units, boot states and all
14 snapshotted private files were preserved. The previous dev5 environment and
private backup remain on the device; an isolated backup restore passed.
Authenticated gateway models/SSE pass and unauthenticated access returns 401.
The device's source checkout was fast-forwarded; no GitHub push occurred.

The previous locally qualified dev6 implementation is
`376f3f4ae220c7fd1adf1aec933605a0819101a0` on
`codex/chat-experience-dev6`. Developer qualification selected 1,815 tests:
1,812 passed and three expected Linux-only skips on macOS/Python 3.14.7.
The clean installed 194-module wheel passed 47 additional feature checks,
28 real Tk route/scale checks and the new native experience journeys.
These extra checks overlap the suite and are not added to its test inventory.
See [the exact artifact and evidence record](experience-qualification-2026-09-17.json).
The dev6 record is historical and does not qualify dev9. The dev9 artifact
verifier also passes; its evaluator remains blocked by the external/attestation
gates. The branch and package have not been published.

The [September 4 implementation status](review-implementation-status.md)
records the earlier corrective changes and installed dev5 result. Its counts
and candidate identity remain historical; they do not qualify this checkout.

Developer validation, physical Bazzite/CachyOS qualification, independent
security review, non-developer acceptance and publication are separate gates.
Physical and external gates remain pending; release remains blocked.
