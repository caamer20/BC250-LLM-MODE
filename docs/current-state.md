# Current implementation state

The active development work is
[September experience improvements](experience-improvements-2026-09.md),
source version `0.9.0.dev8`. It builds on
[the full application review plan](../FULL_APP_REVIEW_IMPLEMENTATION_PLAN.md)
and the GUI, appliance-experience and end-user-friendliness implementations.
The changed package is a new candidate. No dev6 device deployment, human
acceptance, independent security review, or final release is claimed.

Physical Bazzite access became available on September 17. Initial dev6 testing
found that Fedora Python's virtual-memory baseline exceeded the HTTP worker's
available import headroom. The dev7 correction keeps a bounded 384 MiB ceiling;
its Linux regression and real model-counting probe pass. Linux Tk journeys
then exposed cyclic Tk dialog objects being finalized by Python on a worker.
Dev8 routes cyclic collection through the UI's existing refresh coordinator,
including safe retention when a worker outlives the short close budget. The
corrected real Tk journeys pass on Linux and macOS. Full candidate
qualification and deployment are in progress; the active installation remains
dev5 until the verified cutover is recorded.

The previous locally qualified dev6 implementation is
`376f3f4ae220c7fd1adf1aec933605a0819101a0` on
`codex/chat-experience-dev6`. Developer qualification selected 1,815 tests:
1,812 passed and three expected Linux-only skips on macOS/Python 3.14.7.
The clean installed 194-module wheel passed 47 additional feature checks,
28 real Tk route/scale checks and the new native experience journeys.
These extra checks overlap the suite and are not added to its test inventory.
See [the exact artifact and evidence record](experience-qualification-2026-09-17.json).
The artifact verifier passes; the release evaluator remains blocked by the
external/attestation gates. The branch and package have not been published.

The [September 4 implementation status](review-implementation-status.md)
records the earlier corrective changes and installed dev5 result. Its counts
and candidate identity remain historical; they do not qualify this checkout.

Developer validation, physical Bazzite/CachyOS qualification, independent
security review, non-developer acceptance and publication are separate gates.
Physical and external gates remain pending; release remains blocked.
