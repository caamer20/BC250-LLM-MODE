# Fresh runtime setup correction

This work is isolated on `codex/fresh-setup-dev11`. It has not been copied to
the BC250 or activated. The tested dev10 candidate remains frozen on its
separate branch; the device's last verified active application is dev9.

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
helper or CMake environment. Separate tests retain the actual Linux/CMake
path. Linux execution is pending: automatic approval review rejected copying
the 420-file draft source/test archive to the BC250 because explicit payload
export authorization was not established. The copy did not run. Approval was
requested for isolated tests using temporary directories and fake services;
no application activation or production-service change is part of that request.

Full candidate qualification remains in progress. These developer fixtures
do not prove physical fresh installation, CachyOS support, human acceptance,
profile measurements, phone/reboot/soak, accessibility, independent security
or signed release acceptance. Release remains BLOCKED.
