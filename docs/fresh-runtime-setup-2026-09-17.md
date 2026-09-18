# Fresh runtime setup correction

This work is on `codex/fresh-setup-dev11`. Its exact source/tests and wheel
passed isolated BC250 qualification after explicit owner approval. It has
not been activated. The tested dev10 candidate remains frozen on its separate
branch; the device's verified active application is dev9.

## Reproduced dependency

Native Setup builds its first runtime before `MODEL_SELECTED`. The runtime
update previously always attempted service start and inference, although
production model validation correctly refuses an empty model selection. A
fixture that always answers healthy had hidden this circular dependency.

The correction retains the canonical setup stages and one runtime lifecycle.
For an observed fresh state, `RUNTIME_UPDATE v1` installs the smoke-tested
immutable files and returns `RUNTIME_INSTALLED`, with model verification
pending. It keeps the service stopped, preserves the handoff, and writes no
promoted or known-good identity. No request field can bypass verification.
The durable boundary records the no-model decision and configuration
fingerprint; old records default to requiring full verification.

Reopening Setup reuses an exactly observed completed installation. Its source,
recipe, all three executable hashes, owned location and successful operation
record must match. A changed prepared binary is refused before reuse. When the
chosen model has been activated through `MODEL_ACTIVATE v1`, Setup invokes the
same runtime update with the prepared source commit and expected build ID.
This uses the existing binaries, publishes handoff v2, proves a new invocation,
verifies model/context/slots and inference, and then promotes the runtime.
Setup advances only after this verification succeeds.

Failure restores the prior handoff, service state and known-good record while
retaining the unpromoted installation. Runtime promotion stays owned by the
runtime lifecycle. No database migration, alternate installer or automatic
model selection was added. CLI/System status distinguishes installed files
from a verified runtime.

An additional filesystem test exposed helper refresh trying to rewrite its
own mode-0500 file. Staging now writes a new owned temporary file, flushes it,
atomically replaces the destination and checks the resulting file digest.
Repeating staging works without write permission on the previous file, and a
destination symlink cannot redirect the write into another file.

An extra failure check on the first built dev11 wheel found that the existing
model activation restoration path treated an explicit absent model as an
omitted setting and inherited the failed candidate. That left a recovery
barrier after first inference failure. Empty-model restoration now uses a
validated, revision-fenced content transaction that preserves the absence,
model inventory, prior optimization values and thermal latch. The activation
adapter still owns handoff removal and stopping the failed service. A failed
first activation can be retried using the retained model and prepared runtime.
The earlier `daabbf8` wheel and its passing preliminary reports are preserved
as diagnostics; they are superseded and must not be deployed.

## Verification boundary

Local end-to-end tests use the production runtime adapter, durable engine,
model activation adapter, SQLite services and handoff renderer. The model
service is a fixture that calls production model-selection validation, so an
empty model cannot appear healthy. They exercise initial installation,
re-entry without rebuilding, first model activation and promotion, failed
verification/restoration, interruption after publication, tamper rejection,
and the no-model pending state.

On macOS, compilation uses the system C compiler and the atomic-operation
fixture uses Darwin `renamex_np`. Those results do **not** qualify the Linux
helper or CMake environment. Separate tests exercise the actual Linux/CMake
path. Automatic approval review initially rejected the draft source/test copy.
The owner subsequently approved the exact qualified source/tests and wheel for
isolated Linux tests using temporary directories and fake services. Those tests
completed successfully on September 18 UTC, as recorded below. Application
activation and production-service changes were outside that approval.

## Local qualification

The corrected package at `67202e2ec6f140ad6745735d751e27ac5065e8df` passed
the full macOS/Python 3.14.7 default and slow suites: **1,875 selected,
1,856 passed and 19 expected Linux-only skips**. All 52 slow gates passed.
Default and slow inventories are complete and disjoint. Both suites ran on
this exact code commit.

A clean source archive was built through an sdist into one wheel. A fresh
hash-locked environment verified all 195 Python modules / 196 package files
against the source and wheel, with no broken dependencies. Outside the
checkout, the installed wheel passed 42 feature checks and 31 runtime/recovery
checks, including failure of first model inference, empty-state restoration
and successful retry without rebuilding. Native Tk passed 28 route/scale
checks and the chat/source/portable-backup journeys at 100% and 200% scale.
These additional checks overlap the suite and are not added to its inventory.

Wheel SHA-256:
`2e83b108c95239ed5b24ee1957d0e1085491c51216c0b3f1cd8ab55669679da3`.
The [qualification record](fresh-runtime-qualification-2026-09-17.json)
binds artifact and raw-report hashes. Local artifacts and logs are retained
under `dist/dev11-fresh-setup-20260917`; superseded `daabbf8` diagnostics remain
under `dist/dev11-daabbf8-diagnostic`. The artifact verifier passes; the release
evaluator refuses final eligibility because external and attestation evidence
is still missing. These unsigned reports do not satisfy signed release gates.

## Isolated Bazzite qualification, September 18 UTC

The approved transfer contains the exact `67202e2` source/test archive and the
same wheel above. Both fresh hash-locked Linux environments verify all 195
Python modules / 196 package files against that source and wheel. The Bazzite
host's default and slow inventories select **1,875** distinct tests:
**1,860 passed and 15 skipped**, including **52/52 slow gates**.

Eight skips need CMake, which the host does not have. All eight pass in the
existing compiler-equipped guest's **53/53 installed-runtime checks**. Those
checks include real Git/CMake builds, the shipped Linux atomic helper,
initial installation, first-model inference failure, empty-state restoration,
retry, promotion, tamper rejection and interruption recovery using temporary
data and fixture model services. The other seven skips are explicit paths for
other platforms. Installed chat/source/backup/profile feature checks pass
**42/42**. These additional checks overlap the main inventory.

The source archive does not contain Git history. The slow release regression
requires a Git HEAD, so its isolated source directory has a synthetic snapshot
commit recorded in `linux/source-fixture-git.json`. That commit is test fixture
metadata only. The transfer archive hash and package-byte comparisons bind the
original source commit; no source provenance or publication is inferred from
the synthetic commit.

The before/after record confirms the active application remains dev9, with the
same application link, model/gateway service invocations and PIDs, unit bytes,
graphical boot target and production checkout. Model and SearXNG return HTTP
200. No production runtime was rebuilt, no real model was loaded by these
tests and no service was restarted. The isolated directory remains at
`/var/tmp/bc250-dev11-67202e2-tests`; raw reports are retained locally under
`dist/dev11-fresh-setup-20260917/linux` and bound by the qualification JSON.

These developer fixtures do not prove physical fresh installation, CachyOS
support, human acceptance,
profile measurements, phone/reboot/soak, accessibility, independent security
or signed release acceptance. Release remains BLOCKED.
