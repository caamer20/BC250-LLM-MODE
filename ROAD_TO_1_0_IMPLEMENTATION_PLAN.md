# BC250 LLM MODE — Road to 1.0 Implementation Plan

> **Status: historical.** Superseded by `POST_R2_PRODUCTION_IMPLEMENTATION_PLAN.md`
> as sequencing authority after the closed R1/R2 gate.

**Purpose:** Provide one bounded execution plan from the current SQLite cutover to a production-ready 1.0 release, then stop feature development at a deliberate, useful “sweet spot.”

**Current baseline:** `main` at `c3e076b`, package version `0.9.0.dev0`, 252 tests passing, pre-SQLite checkpoint tagged locally as `v0.8.0-pre-sqlite` at `2126d61`. The working tree was clean before this document was added. The branch is 28 commits ahead of `origin/main`; publication remains a user/repository-owner decision.

**Authority:** This plan selects and orders work from `MASTER_IMPLEMENTATION_PLAN.md` and `END_USER_EXPERIENCE_IMPLEMENTATION_PLAN.md`. The master plan remains authoritative for safety/security requirements. This plan is authoritative for scope, delivery order, and the 1.0 stopping point.

---

## 1. The 1.0 sweet spot

BC250 LLM MODE 1.0 will be a focused, single-machine, single-user local-inference appliance for the AMD BC-250 on supported Bazzite releases.

It will do these things exceptionally well:

1. Validate the supported hardware and keep the next boot safe.
2. Install and maintain one known-good llama.cpp Vulkan runtime.
3. Discover, download, validate, install, compare, and safely activate fitting GGUF models.
4. Offer conservative named workload profiles rather than requiring low-level tuning.
5. Provide a clear Home screen, persistent activity/progress, and a native basic chat experience.
6. Support optional Open WebUI without making it required.
7. Support optional authenticated tailnet access without exposing the raw llama.cpp backend.
8. Preserve state across crashes and recover or roll back interrupted critical operations.
9. Enforce thermal safety independently of the GUI.
10. Provide storage visibility, verified backup/restore, repair mode, diagnostics, and redacted support bundles.
11. Ship reproducible, signed application/runtime metadata and immutable production dependencies.
12. Pass Linux integration and real BC-250 qualification.

### 1.0 supported operating model

- One supported BC-250 appliance.
- One application installation profile per desktop user.
- One systemd-owned llama.cpp server.
- One active model at a time.
- One or more bounded request slots only when fit checking permits.
- Loopback-only backend.
- Optional authenticated Tailscale/tailnet access through a gateway.
- Optional Open WebUI connected through the authenticated service topology.
- Local conversations and diagnostics by default.
- User-triggered updates; no unattended automatic mutation.

### Explicitly deferred until after 1.0

The following are outside the 1.0 scope even if they sound attractive:

- Generic AMD/NVIDIA/Intel hardware support.
- Public-internet hosting or Tailscale Funnel support.
- Multi-node inference, sharding, or clustered scheduling.
- Multiple simultaneously loaded models.
- Agent/tool execution, shell access, browser automation, or plugin execution.
- Multimodal/vision/projector/MTP/fused model support.
- Vector database and full RAG stack.
- Cloud synchronization or hosted telemetry.
- Multi-user roles beyond separate gateway credentials.
- Automatic BIOS changes or broader overclocking.
- Background unattended application/runtime/model updates.
- A full marketplace or remotely mutable model catalog.
- System tray integration.
- Mobile applications.

These can be reconsidered only after 1.0 has shipped and real usage identifies a concrete need.

---

## 2. Current state and remaining risk

### Landed foundation

- JSON schema v5 freeze and migration fixtures.
- SQLite schema migration infrastructure.
- Typed repository classes over migration 001.
- One-time JSON import with field classification and repair gate.
- SQLite compatibility facade and application composition cutover.
- State-carried optimistic revision checking.
- Atomic schema migrations.
- Durable atomic file publication and restrictive path permissions.
- Shared-connection serialization for the transitional facade.
- Runtime handoff renderer and configuration service.
- Whole-state mutation guard counts.
- Launcher v2 using rendered named JSON configuration.
- Catalog, chat, benchmarking, thermal logic, autotune, runtime lifecycle, GUI split, CI/build smoke, and packaging tests.

### Immediate technical debt

| Area | Current state | 1.0 requirement |
| --- | --- | --- |
| Durable mutations | Frontends and workflows still call whole-state `save()` | Narrow repository/domain commands only |
| Compatibility facade | Required by existing callers | Removed before R2 exit |
| Paths | Several modules still consume paths from state or home discovery | All paths injected from `AppPaths`/validated config |
| Operations | Long work uses ad hoc GUI threads and synchronous calls | Persistent operation engine with recovery/cancellation |
| Domain boundary | GUI/CLI call modules directly | Shared typed services and adapters |
| Privilege | 44 generic `elevated()` sites | Typed allowlisted privileged helper |
| Thermal safety | Process-level watchdog logic | Independent managed safety service |
| API exposure | Tailscale can publish raw unauthenticated backend | Authenticated gateway only |
| Open WebUI | Host networking, mutable tag, placeholder key | Private network, digest, real credential, backup/rollback |
| Runtime build | Mutable base image/tag/source identity | Immutable digest/commit and provenance |
| Models | Path identity and partial staging model | Content hash, staged validation, quarantine, provenance |
| Chat | Terminal-oriented, unbounded HTTP waits | Bounded streaming, cancellation, minimal native GUI chat |
| Recovery | Import repair gate exists | Operations recovery, backup/restore, maintenance repair mode |
| CI/release | Unit/build smoke | Lint/types/security/SBOM/VM/HIL/signed artifacts |

### Current whole-state save inventory

The frozen counts are a migration checklist, not a permanent allowance:

| Module | Saves | Transactions | Migration owner |
| --- | ---: | ---: | --- |
| `__main__.py` | 10 | — | CLI application service commands |
| `gui/steps.py` | 10 | — | Setup workflow service |
| `gui/dashboard.py` | 7 | — | Runtime/model/maintenance services |
| `chat.py` | 5 | 1 | Conversation/history/runtime services |
| `model_manager.py` | 3 | — | Model activation service |
| `bootstrap.py` | 3 | — | Setup workflow service |
| `tune.py` | 3 | — | Profile/autotune service |
| `thermals.py` | 2 | — | Safety state service |
| `gui/app.py` | 2 | — | Remove generic GUI persistence |
| `gui/forms.py` | 1 | — | Profile/settings service |

The guard should be changed from “no growth” to “exact expected count” after every reduction. Zero is the only R2 exit value.

---

## 3. Critical path

```text
Phase A — Finish persistence and paths
  -> Phase B — Persistent operation engine
    -> Phase C — Domain services and typed adapters
      -> Phase D — Privileged helper and safety supervisor
        -> Phase E — Authenticated gateway and Open WebUI hardening
          -> Phase F — Trusted updates and model artifacts
            -> Phase G — Polished appliance UX
              -> Phase H — Recovery, diagnostics, and storage
                -> Phase I — CI, Linux, BC-250 qualification
                  -> 1.0 RC -> 1.0
```

No phase may be skipped. Some pure UI/view-model work can proceed against fakes, but production wiring follows the dependency order.

---

# Phase A — Finish persistence and path boundaries

## A1. Convert safety-authoritative state first

**Files:** `thermals.py`, repositories, service module, thermal tests.

### Implementation

1. Add a `ThermalStateService` with commands:
   - read current latch/baseline;
   - record throttle baseline;
   - update nominal/throttled/degraded/stopped state;
   - latch stop;
   - reset latch only after a safe sensor probe;
   - restore/clear baseline after verified host restoration.
2. Keep hysteresis decisions pure; the service coordinates persistence and host effects.
3. Persist the latch before or immediately with the service-stop intent so a crash cannot forget the stop.
4. Preserve the prior GPU profile until restoration is verified.
5. Remove both whole-state thermal saves.

### Tests

- Latch survives new service/repository instances.
- Reset denied above resume threshold.
- Missing sensor cannot clear a latch.
- Failed profile restoration retains recovery evidence.
- Concurrent status probe cannot overwrite a latched stop.
- Save guard decreases by exactly two.

### Exit

Thermal persistence is narrow, authoritative, and independent of stale state dictionaries.

## A2. Convert append-only histories

**Files:** `chat.py`, `tune.py`, history repositories/services.

### Implementation

- Use `BenchHistoryRepository.append()` with capped retention.
- Add `AutotuneHistoryRepository.append()` rather than replacing all history.
- Record benchmark/autotune method, model ID, runtime fingerprint, profile, context, slots, timestamp, result, and failure category.
- Do not store prompts or generated content in benchmark history.
- Make retention enforcement transactional.

### Tests

- Exact caps under concurrent append.
- Stable ordering.
- Malformed legacy entries remain readable or quarantined.
- No broad save after benchmark/autotune completion.

## A3. Convert setup and acknowledgement state

**Files:** `bootstrap.py`, `gui/steps.py`, `disclaimer.py`, CLI setup/repair paths.

### Implementation

Create a `SetupService` owning:

- disclaimer acknowledgement and timestamp;
- workflow stage/status;
- setup-complete status;
- boot-safety intent;
- repair-required state;
- preflight observations.

Do not preserve `setup_phase` as an unvalidated arbitrary integer. Map it to named stages while retaining a compatibility projection until the facade is removed.

Every stage is marked complete only after its postcondition probe succeeds.

### Tests

- Resume at first incomplete/unverified stage.
- Stale GUI draft rejected.
- Disclaimer is never silently reset.
- Repair retry does not skip preflight.
- Closing GUI cannot mark the active stage complete.

## A4. Convert model activation atomically

**Files:** `model_manager.py`, runtime/model repositories, server adapter, tests.

### Implementation

Create a `ModelActivationService` that owns:

1. Candidate lookup and validation status.
2. Catalog and artifact metadata.
3. Fit check for model/context/slots/profile.
4. Prior known-good runtime snapshot.
5. Desired runtime configuration update.
6. Runtime handoff publication.
7. Service restart.
8. Layered health and minimal inference probe.
9. Known-good promotion on success.
10. Full rollback and verification on failure.

During Phase A this can remain synchronous, but its command and step boundaries must be designed for direct conversion to an R3 operation.

### Tests

- No-fit candidate causes no mutation.
- Missing/unverified artifact rejected according to policy.
- Handoff failure leaves DB commit status explicit and start blocked until regeneration.
- Failed service health restores the prior known-good model/configuration.
- Failed rollback enters recovery-required state.
- Concurrent model/context/slot changes conflict rather than interleave.

## A5. Convert runtime/profile settings

**Files:** `__main__.py`, `gui/forms.py`, `gui/dashboard.py`, `tune.py`, `optimize.py`.

### Implementation

- Add typed runtime/profile records.
- Add `RuntimeConfigurationService.preview()` and `.apply()`.
- Centralize numeric ranges and fit checks.
- Store desired state separately from observed service health.
- Render handoff only after committed runtime changes.
- Keep host tuning receipts separate from desired runtime flags.
- Convert context, slots, optimization forms, and autotune winner persistence.

### Tests

- Profile preview and apply use identical resolved values.
- Context × slots is included in fit.
- Invalid threads, batch, clocks, or thermal thresholds fail before mutation.
- Handoff revision equals committed runtime revision.
- Failed runtime health rolls back profile and handoff.

## A6. Convert sharing and optional-service state

**Files:** CLI branches, GUI dashboard, `sharing.py`, `tailscale.py`, `openwebui.py`.

Separate:

- desired sharing mode;
- observed Tailscale/gateway status;
- desired Open WebUI installation/running state;
- observed container status.

Do not let status probes overwrite user configuration. Persist observations with timestamps and stale markers.

## A7. Remove frontend persistence

**Files:** GUI package, terminal chat, CLI composition.

- GUI widgets maintain local draft objects only.
- GUI commands call services and then refresh immutable view models.
- Chat receives the composed application/services; no fallback `StateStore()`.
- CLI commands receive the same services.
- Remove all generic GUI `save_state()` helpers.
- Add stale-revision conflict UX rather than retrying with an old whole-state dictionary.

## A8. Finish path integration

**Files:** download, prepare, environment, Open WebUI, chat, bootstrap, local model discovery, uninstall.

- Pass `AppPaths` or typed paths explicitly.
- Remove `Path.home()` outside path composition.
- Stop reading `app_dir`, `logs_dir`, or state database paths from persisted settings.
- Validate custom model/search roots for existence, type, containment policy, symlink policy, and ownership.
- Use `fsops` for app-owned durable files.
- Extend the path guard to prohibit new fallback construction.

## A9. Remove the facade

### Preconditions

- Whole-state save/transaction guard is zero.
- Direct `StateStore()` remains only in importer canonicalization/legacy tests.
- Frontends use services/view models.
- Runtime handoff is produced by runtime service only.

### Removal

1. Delete `compat_state.py`.
2. Change `Application` to expose repositories/services, not `.store`.
3. Make `--state` import-only; normal operation always uses the profile database.
4. Remove JSON-shaped compatibility projections from production flow.
5. Keep JSON migration fixtures and importer isolated.
6. Add tests that fail if production imports the legacy state module.

## A10. R2/R1 exit gate

- [ ] All durable mutations use repositories/services.
- [ ] Whole-state save guard equals zero.
- [ ] Compatibility facade deleted.
- [ ] JSON remains byte-identical after cutover.
- [ ] Database corruption/newer schema enters repair mode.
- [ ] All supported migration fixtures pass.
- [ ] Concurrent changes do not lose updates.
- [ ] Runtime handoff is reconstructable and revision-linked.
- [ ] All production paths are injected/validated.
- [ ] Filesystem primitives cover every durable app-owned publication.
- [ ] Source, editable, wheel, and clean-install tests pass.

### Phase A recommended commits

```text
refactor(safety): persist thermal latch through ThermalStateService
refactor(history): append benchmark and autotune records narrowly
refactor(setup): replace setup whole-state saves with SetupService
refactor(models): introduce transactional ModelActivationService
refactor(runtime): apply runtime and profile changes through typed service
refactor(optional): separate desired and observed sharing/webui state
refactor(frontends): remove GUI CLI and chat persistence access
refactor(paths): finish AppPaths consumer sweep
chore(state): delete compatibility facade and close R2 gate
docs(plan): record R1/R2 completion evidence
```

---

# Phase B — Persistent operation engine

The operation engine should be intentionally small. It is not a generic workflow framework.

## B1. Migration 002: operations and events

Minimum schema:

```text
operations(
  id, type, status, requested_by, resource_key,
  created_at, started_at, finished_at,
  current_step, progress, cancel_requested,
  error_code, error_json, recovery_policy,
  input_json, result_json
)

operation_steps(
  id, operation_id, sequence, name, status,
  started_at, finished_at,
  checkpoint_json, rollback_json, error_json
)

resource_locks(resource_key, operation_id, acquired_at)

events(
  id, occurred_at, severity, category,
  operation_id, code, payload_json
)
```

Use foreign keys, checked statuses, unique step sequence per operation, and bounded serialized payloads.

## B2. State machine

```text
QUEUED
  -> PREPARING
  -> RUNNING
  -> VERIFYING
  -> COMMITTING
  -> SUCCEEDED

RUNNING/VERIFYING
  -> CANCELLING -> CANCELLED
  -> ROLLING_BACK -> FAILED_ROLLED_BACK
  -> RECOVERY_REQUIRED
```

Rules:

- Persist intent before external effects.
- Never hold a SQLite transaction across an external process or network request.
- Cancellation occurs only at declared safe points.
- `COMMITTING` is non-cancellable and short.
- Every step has precondition, effect, verification, checkpoint, timeout, and compensation.
- Resource locks are durable and reconciled after process death.
- Retry only explicitly idempotent steps.

## B3. Engine API

```text
start(command) -> operation_id
get(operation_id) -> OperationView
list(filter) -> list[OperationView]
request_cancel(operation_id)
resume_or_recover(operation_id)
subscribe(after_event_id)
```

The GUI and CLI use the same API. CLI supports `--wait`; GUI subscribes/polls event revisions without owning the worker.

## B4. Recovery classification

At startup classify unfinished operations as:

- already completed after probe;
- resumable from checkpoint;
- safely revertible;
- automatically rolling back;
- user decision required;
- unrecoverable without repair.

Record the decision as an event and block conflicting work until resolved.

## B5. Convert critical operations in this order

1. **Model activation** — tests verification and rollback.
2. **Model download/import/validation** — tests progress, resume, cancellation, staging.
3. **Runtime install/update/rollback** — tests staged component swaps.
4. **Profile apply/autotune** — tests multi-candidate work and winner commit.
5. **Open WebUI install/update/backup/restore.**
6. **Sharing enable/disable/credential rotation.**
7. **Backup/restore/support bundle/cleanup.**

Simple status queries and immediate idempotent start/stop commands need not become elaborate workflows unless recovery requires it.

## B6. Crash-injection matrix

For model activation, download, runtime update, and restore, inject failure:

- before external effect;
- after effect before checkpoint;
- after checkpoint before verification;
- during verification;
- during commit;
- during rollback.

Assert one valid terminal state and preservation of the known-good configuration.

## B7. R3 exit gate

- [ ] Critical operations are durable and queryable after frontend restart.
- [ ] Resource conflicts are deterministic.
- [ ] Cancellation works only at safe points.
- [ ] Startup recovery classifies every non-terminal operation.
- [ ] Crash-injection passes for four critical workflows.
- [ ] Operation/event retention is bounded.
- [ ] No frontend starts background host work directly.

---

# Phase C — Domain services and typed adapters

## C1. Application composition

`Application.compose()` should construct:

- paths;
- database connection factory;
- repositories;
- event sink;
- operation engine;
- runtime/model/profile/setup/sharing/update/backup/diagnostic services;
- command runner and typed host adapters;
- immutable query/view builders.

Tests replace adapters, not domain logic.

## C2. Required services

```text
SetupService
RuntimeService
RuntimeConfigurationService
ModelLibraryService
ModelActivationService
ProfileService
SafetyService
SharingService
OpenWebUIService
UpdateService
StorageService
BackupService
DiagnosticService
ConversationService
```

Each command returns an immediate typed result or an operation ID. Stable errors are values at service boundaries, not parsed exception strings.

## C3. Desired versus observed state

Keep separate records for:

- runtime desired configuration versus service/model health;
- sharing desired exposure versus Tailscale/gateway reality;
- Open WebUI desired install/run state versus container health;
- host tuning intent versus applied/verified host state.

Observations have timestamps and become stale. A probe never silently changes user intent.

## C4. Typed adapters

Minimum adapters:

- systemd;
- Podman;
- Tailscale;
- sensors;
- Vulkan/hardware;
- filesystem;
- HTTP/backend health;
- process execution;
- clock.

Expose methods such as `systemd.start(unit)` rather than accepting caller-provided flags or shell strings.

## C5. Timeouts and errors

Define bounded timeouts for every command and HTTP request. Replace chat `timeout=None` with connect, write, read-idle, and total policies plus cancellation.

Initial stable errors:

- invalid input;
- unsupported host;
- dependency missing;
- insufficient space/memory;
- fit rejected;
- authorization denied;
- timeout;
- network unavailable;
- integrity/provenance failure;
- health failure;
- safety stop;
- stale revision/conflict;
- operation conflict;
- recovery required;
- internal error.

## C6. R4 exit gate

- [ ] GUI and CLI use services, not repositories/commands directly.
- [ ] Desired and observed states are separate.
- [ ] All external calls are typed and bounded.
- [ ] Errors have stable codes and remediation.
- [ ] Runtime argv/handoff remains deterministic and behavior-tested.

---

# Phase D — Privileged helper and independent safety

## D1. Typed privileged protocol

The helper accepts versioned requests only for:

- install/remove approved generated units;
- daemon reload;
- start/stop/restart allowlisted units;
- apply/revert bounded CPU governor settings;
- apply/revert bounded BC-250 GPU clock/thermal settings;
- install/remove approved desktop/session configuration;
- perform validated runtime directory swaps;
- narrowly scoped host probes requiring privilege.

It never accepts a raw command, shell string, arbitrary path, arbitrary unit name, or caller-provided executable.

## D2. Helper validation

- Validate caller and authorization.
- Validate request schema and protocol version.
- Resolve and contain every path.
- Reject symlinks and unexpected ownership/types.
- Enforce exact numeric safety ranges.
- Reject unknown fields.
- Emit redacted structured events.
- Use fixed argv or direct APIs only.

## D3. Policy and packaging

- Narrow polkit policy suitable for supported Bazzite.
- Interactive authorization for installation, host policy, destructive reset, and update.
- Correct helper/unit ownership and modes.
- Disposable Linux install/uninstall tests.
- Guard generic `elevated()` call sites down from 44 to zero.

## D4. Independent safety supervisor

The supervisor runs without GUI/chat and owns:

- sensor discovery and validation;
- warning/throttle/stop hysteresis;
- stale/missing/implausible sensor handling;
- critical server stop;
- persistent latch;
- explicit safe reset;
- structured events;
- host tuning restoration evidence.

The GUI and CLI only query/control it through services.

## D5. Unit hardening

Generate and verify units for:

- llama server;
- safety supervisor;
- gateway;
- optional Open WebUI integration where required;
- boot cleanup/revert if still necessary.

Apply compatible systemd sandboxing and verify units with `systemd-analyze verify` in Linux tests.

## D6. R5 exit gate

- [ ] Generic elevation interface removed.
- [ ] Helper rejects arbitrary commands/paths/units.
- [ ] Safety enforcement survives GUI and supervisor restart.
- [ ] Latch persists across reboot.
- [ ] Missing sensors trigger documented fail-safe behavior.
- [ ] Next boot returns to desktop.
- [ ] Host tuning restores after stop/crash/reboot.

---

# Phase E — Authenticated access and Open WebUI

## E1. Authentication decision

Test the exact pinned llama.cpp runtime for safe native API-key support and secret delivery. If sufficient, use it behind the external endpoint policy. Otherwise select a small pinned, reviewed gateway/proxy. Do not invent a custom security proxy without dedicated review.

## E2. Required topology

```text
llama.cpp backend: loopback/private only
             ^
             |
authenticated gateway
     ^                 ^
local clients       Tailscale Serve
     ^
Open WebUI service credential
```

Tailscale must never publish `127.0.0.1:8080` directly.

## E3. Credential lifecycle

- CSPRNG-generated secret.
- One credential per named purpose/client.
- Protected storage outside ordinary settings/events.
- Secret never in argv, logs, support bundles, or generated snippets on disk.
- One-time reveal.
- Rotation, revocation, and optional bounded overlap.
- Emergency disable independent of backend health.

## E4. Gateway controls

- Method/path allowlist.
- Request/header/body limits.
- Connection/concurrency limits.
- Stream idle and total timeouts.
- Local-only health/admin endpoints.
- Sanitized request IDs/access events.
- Unauthorized request tests.

## E5. Open WebUI hardening

- Pin image by digest.
- Replace host networking with a private Podman network.
- Use gateway service credential, replacing `sk-no-key-needed`.
- Bind UI loopback-only by default.
- Use non-root/read-only root filesystem where compatible.
- Explicit writable volumes and resource limits.
- SELinux-compatible ownership/labels.
- Health checks.
- Backup before update and rollback on failure.
- Secure first-run admin flow.

## E6. Supported remote UX

Show local-only/tailnet-only status, URL, credential age, connected client metadata where available, rotate/revoke, and emergency disable. Public Funnel is unsupported and actively disabled.

## E7. R6/R7 exit gate

- [ ] Raw backend is not remotely reachable.
- [ ] Unauthenticated and revoked credentials fail.
- [ ] Secrets absent from argv/logs/events/support bundles.
- [ ] Open WebUI uses private networking and pinned digest.
- [ ] Open WebUI backup/update/rollback passes.
- [ ] Emergency remote disable works with backend stopped.

---

# Phase F — Trusted runtime, application, and model artifacts

## F1. Immutable runtime build

- Replace Fedora `latest` with reviewed digest.
- Pin llama.cpp to exact commit or signed release identity.
- Record source digest, build image digest, compiler/runtime versions, build flags, binary digest, and compatibility result.
- Keep versioned active and rollback trees.
- Convert update/rollback to persistent operations.

## F2. Signed application updates

The 1.0 scope is **user-triggered signed updates**, not background auto-update.

Manifest includes:

- channel/version;
- supported source/target versions;
- artifact digests/URLs;
- platform/Python requirements;
- database migration range;
- minimum space;
- runtime/image identities where bundled;
- signing key ID/signature.

Update flow stages, verifies, backs up, migrates, activates, health-checks, and rolls back. No downloaded script is executed without trusted artifact verification.

## F3. Model artifact trust

Required for 1.0:

- SHA-256 content identity.
- Operation-specific staging.
- Resume/checkpoint where supported.
- GGUF metadata and artifact-policy validation.
- Provenance, source revision, size, quantization, license metadata.
- Quarantine/removal of invalid or partial artifacts.
- Full verification before first activation.
- Re-verification command.

### Deliberate scope cut

A full content-addressed deduplicating object store is deferred unless implementation is straightforward during artifact hashing. For 1.0, managed validated paths plus immutable digest records are sufficient. Do not delay release solely to build cross-alias deduplication.

## F4. Catalog governance

- Supported/preview/blocked tiers.
- Immutable source revision/digest where available.
- License and expected-size metadata.
- Tested profile/context/slot combinations.
- BC-250 evidence required for supported status.
- Catalog changes shipped with signed application releases for 1.0.

## F5. SBOM and provenance

- SPDX or CycloneDX SBOM.
- Python dependencies, runtime source/build, and container digests.
- License inventory.
- Vulnerability scan and documented exceptions.
- Signed checksums and release provenance.

## F6. R8/R9 exit gate

- [ ] No mutable production image/tag references.
- [ ] Runtime binary has reproducible identity and rollback.
- [ ] Application update requires signed metadata.
- [ ] Active models have digest/validation/provenance.
- [ ] Partial/corrupt models cannot become installed/active.
- [ ] Supported catalog entries have BC-250 evidence.

---

# Phase G — The polished 1.0 user experience

This phase intentionally implements a limited, complete interface rather than every item in the broader UX plan.

## G1. Home and Quick Start — required

Top-level states:

- Setup required.
- Ready.
- Stopped.
- Working.
- Needs attention.
- Recovering.
- Safety stopped.

The Home screen shows:

- one primary next action;
- current model/profile/context;
- runtime health;
- temperature/safety status;
- local/tailnet exposure;
- disk headroom;
- active/recent operation;
- links to Models, Chat, Activity, Remote Access, and Maintenance.

Quick Start goals:

- Safe/cool.
- Balanced.
- Fast chat.
- Best validated quality.
- Long context.
- Shared clients.

It previews the exact model/profile/fit/host changes and starts an operation.

## G2. Activity/Operations Center — required

- Active, needs-attention, recent, and history views.
- Phase/progress/elapsed time/heartbeat.
- Cancellation when safe.
- Conflict explanation and queue option where supported.
- Recovery and rollback timeline.
- Stable error/remediation and copyable operation ID.
- Bounded retention.

## G3. Unified Model Library — required

Combine catalog, installed, local, downloading, missing, invalid, and quarantined states.

Required features:

- Search and filters.
- Fit/support/validation/provenance/storage badges.
- Model detail and fit breakdown.
- Install/import/verify/activate/benchmark/remove.
- Download progress and resume.
- Compare up to three models.
- Safe removal preview.

Defer remotely updated catalog and elaborate visual quality scoring.

## G4. Named profiles — required

Ship:

- Safe.
- Balanced.
- Fast.
- Long Context.
- Shared.
- Custom/Advanced.

Preview memory calculation, expected interruption, host changes, and tested/estimated status. Profile apply is an operation with rollback.

## G5. Native basic chat — required

The 1.0 native chat scope is deliberately bounded:

- conversation list;
- new/rename/delete;
- transcript;
- multiline composer;
- send and stop;
- bounded streaming timeouts;
- model/profile indicator;
- approximate context meter;
- regenerate last response;
- copy/export Markdown;
- persistent or no-history mode;
- atomic private storage;
- no prompt logging.

### Deferred chat features

- Branch visualization.
- Full-text global search.
- Attachments.
- RAG.
- Tool calls.
- Rich Markdown rendering beyond safe plain/basic formatting.
- Prompt marketplace.

Open WebUI remains available for users needing a richer interface.

## G6. Guided onboarding — required

Stages:

1. Hardware/safety preflight.
2. Storage/runtime readiness.
3. Intended use.
4. Recommended model/profile.
5. Review exact changes.
6. Persistent installation operation.
7. Verified first prompt.
8. Optional Open WebUI/remote access offered after local success.

## G7. Remote Access center — required if sharing is supported

- Local-only/tailnet-only state.
- Gateway and Tailscale health.
- Named client credentials.
- URL/config copy.
- Authorized/unauthorized self-test.
- Rotate/revoke/emergency disable.

## G8. Accessibility baseline — required

- Keyboard navigation and visible focus.
- Text plus icon/status, never color alone.
- Usable scaling and scrollable layout.
- Screen-reader labels where tkinter permits.
- No disabled control without an explanation.
- No log stream as the only progress indication.

## G9. CLI parity — required

Minimum stable command families:

```text
status
quick-start --dry-run
models list|search|info|install|verify|activate|remove
profiles list|preview|apply|autotune
operations list|show|wait|cancel|recover
chat --no-history
remote status|enable|disable|clients|rotate|revoke
storage status|cleanup --dry-run
backup create|list|verify|restore --dry-run
update check|apply|rollback
doctor
support-bundle create --preview
```

JSON output is versioned and stdout-clean. State-changing commands return operation IDs.

## G10. UX exit gate

- [ ] First-time user reaches verified chat without terminal use.
- [ ] Returning user starts last known-good setup in at most two actions.
- [ ] GUI restart preserves active operation visibility.
- [ ] Model install/switch explains fit, progress, and rollback.
- [ ] Native chat stops a stalled stream promptly.
- [ ] Advanced settings are available but not required.
- [ ] Remote access is clearly authenticated and disableable.
- [ ] Keyboard/accessibility review passes.

---

# Phase H — Maintenance, recovery, and diagnostics

## H1. Storage inventory and preflight

Report:

- installed model files;
- partial/quarantined model files;
- runtime active/rollback/build trees;
- application/runtime backups;
- Open WebUI data/backups;
- conversations;
- logs/events/metrics;
- reclaimable bytes;
- reserved safety headroom.

Every download/update/backup checks final, staging, rollback, and reserve space before starting.

## H2. Safe cleanup

- Dry-run first.
- Exact paths and bytes.
- Never select active model/runtime, last-known-good state, in-progress staging, or newest verified backup.
- Recheck references before deletion.
- Record result as an event.

## H3. Backup and restore

Required scopes:

- settings/metadata;
- settings plus conversations;
- Open WebUI data;
- optional models with size warning.

Backup uses the SQLite backup API or safe quiesced snapshot and verifies archive hashes.

Restore stages, validates paths/hashes/schema/space, backs up current state, activates atomically, verifies, and rolls back on failure.

## H4. Repair mode

Available without starting llama.cpp:

- database diagnosis;
- retry legacy import;
- restore verified backup;
- reconcile operations;
- regenerate runtime handoff/services;
- verify model paths/digests;
- disable sharing;
- revert host tuning;
- return to desktop mode;
- create support bundle.

## H5. Doctor and health model

Checks:

- platform/hardware;
- paths/permissions;
- database/schema/integrity;
- runtime/component provenance;
- systemd desired/observed state;
- Vulkan access;
- sensors/safety latch;
- model artifact and fit;
- gateway exposure/authentication;
- Open WebUI;
- disk/memory headroom;
- update/backup recoverability.

Each result contains status, evidence, impact, remediation, and timestamp.

## H6. Structured events and support bundle

Events include timestamp, severity, category, code, operation ID, component, and redacted payload.

Support bundle includes version/provenance, redacted config, integrity summary, recent events/logs, hardware/Vulkan/sensor summaries, model IDs/digests, unit/config fingerprints, and doctor report.

It excludes credentials, prompts, conversations, authorization headers, model contents, and full environment dumps. Secret-canary tests are mandatory.

## H7. Scoped reset

Offer independent reset scopes:

- settings/database;
- generated services/host tuning;
- sharing credentials;
- Open WebUI data;
- conversations/logs;
- runtime builds;
- models.

Preserve models by default. Preview targets and recoverability.

## H8. Maintenance exit gate

- [ ] Low disk cannot destroy rollback state.
- [ ] Backup verifies before success.
- [ ] Failed restore returns to prior working state.
- [ ] Repair mode works without runtime/backend.
- [ ] Support bundle passes secret/content canaries.
- [ ] Cleanup/reset preview exact targets.

---

# Phase I — Quality, qualification, and release

## I1. CI quality gates

- Pin actions to reviewed commit SHAs.
- Confirm supported Bazzite Python versions; test minimum and target deliberately.
- Ruff format/lint.
- Targeted mypy/pyright for repositories, operations, services, helper protocol, and security boundaries.
- Critical-module coverage policy.
- Source/editable/sdist/wheel/clean-install tests.
- Xvfb tkinter integration.
- Secret, dependency, container, license, and static security scans.
- Mutable-production-reference guard.
- Migration fixture matrix.
- SBOM and artifact provenance.

## I2. Linux/Bazzite VM tests

- Install/uninstall.
- systemd unit verification.
- helper/polkit authorization.
- service lifecycle.
- Podman private network/hardening.
- database backup/WAL behavior.
- migration/restore/repair/reset.
- Xvfb GUI journeys.
- ownership/permission validation.

## I3. BC-250 HIL qualification

- Fresh install and upgrade.
- Vulkan/runtime compatibility.
- Every supported catalog model/profile.
- Prompt/stream/cancel.
- Thermal soak and safety latch/reset.
- Sensor failure behavior.
- Runtime/server/GUI crash recovery.
- Low disk and interrupted operation.
- Authenticated tailnet access and unauthorized rejection.
- Open WebUI install/update/rollback.
- Backup/restore.
- Next-boot desktop safety.
- Uninstall and host-setting restoration.

Record Bazzite image, kernel, firmware/BIOS, Mesa/Vulkan, runtime/image/model digests, ambient conditions, and results.

## I4. Performance qualification

For each supported model/profile:

- load time;
- time to first token;
- prompt/generation rate;
- host/UMA peak;
- temperature curve;
- throttling;
- sustained stability;
- slot behavior;
- cancellation recovery.

Use results for recommendation badges and regression thresholds, not marketing guarantees.

## I5. Release artifacts

- Wheel/sdist or selected platform package.
- Signed checksums and update manifest.
- SBOM and provenance.
- Changelog and migration notes.
- Supported platform/model matrix.
- Backup/update/rollback instructions.
- Known issues.
- Support/diagnostic instructions.

## I6. RC soak

Run a multi-day supported-hardware soak covering idle, repeated model switching, chat, Open WebUI, authenticated remote use, reboots, storage pressure, and thermal load. Any P0/P1 restarts the relevant gate after correction.

## I7. Final 1.0 no-go list

Release is blocked if any is false:

- [ ] No open P0/P1 defect.
- [ ] Whole-state persistence/facade/legacy writes removed.
- [ ] Critical operations recover after interruption.
- [ ] Generic elevation removed.
- [ ] Independent thermal supervisor validated on BC-250.
- [ ] Next boot always returns to desktop in supported scenarios.
- [ ] Raw backend cannot be remotely exposed by supported configuration.
- [ ] Credentials absent from argv/logs/events/support bundles.
- [ ] Runtime/images/application metadata are immutable and verifiable.
- [ ] Active models have digest/validation/provenance.
- [ ] Update/rollback and backup/restore pass.
- [ ] Low-space behavior preserves known-good state.
- [ ] GUI journeys and accessibility baseline pass.
- [ ] Signed artifacts, SBOM, provenance, and docs are published.

---

## 4. Release milestones

### `0.9.0` — Transactional core

Includes Phases A and B:

- no whole-state saves;
- no compatibility facade;
- complete path injection;
- persistent operation engine;
- model activation/download/runtime update operations;
- migration/recovery evidence.

Do not release `0.9.0` with the facade still present.

### `0.10.0` — Safe and secure appliance core

Includes Phases C, D, E, and F foundation:

- domain services/adapters;
- privileged helper;
- independent safety supervisor;
- authenticated gateway;
- hardened Open WebUI;
- immutable runtime/image/model provenance.

### `0.11.0` — Complete user experience

Includes Phases G and H:

- Home/Quick Start;
- Activity;
- Model Library;
- profiles;
- native basic chat;
- onboarding;
- remote center;
- storage/backup/restore/repair/doctor/support bundle.

### `1.0.0-rc.1`

Feature freeze. Only blockers, qualification fixes, documentation, and packaging changes.

### `1.0.0`

All Phase I gates pass and the supported BC-250 contract is published.

---

## 5. Exact next six sessions

### Session 1 — Safety/history save sweep

1. Implement `ThermalStateService`.
2. Remove thermal whole-state saves.
3. Add narrow benchmark/autotune append methods.
4. Remove history whole-state saves.
5. Update exact guard counts.
6. Run full source/editable tests and update handoff.

### Session 2 — Setup/runtime/model save sweep

1. Implement named setup stages and `SetupService`.
2. Implement runtime/profile records and preview/apply commands.
3. Implement `ModelActivationService` around existing rollback behavior.
4. Remove bootstrap/model-manager/tune saves.
5. Add stale/conflict/rollback tests.

### Session 3 — Frontend save sweep and path closure

1. Convert CLI mutation branches to services.
2. Convert GUI forms/steps/dashboard to commands and immutable refreshes.
3. Inject application/services into chat; remove fallback stores.
4. Finish download/prepare/env/Open WebUI/chat path injection.
5. Drive all guards to zero.

### Session 4 — Facade removal and R2 gate

1. Delete compatibility facade.
2. Make normal composition repository/service-native.
3. Restrict legacy JSON to importer/fixtures.
4. Make `--state` import-only.
5. Run migration/concurrency/crash/package matrices.
6. Update all plans/docs and mark R1/R2 complete.

### Session 5 — Operation engine foundation

1. Add migration 002.
2. Implement state machine, repository, locks, events.
3. Add worker/executor and recovery classification.
4. Add cancellation/progress contracts.
5. Convert model activation.
6. Crash-inject every activation step.

### Session 6 — Download/runtime operations and minimal Activity

1. Convert model download/import/validation.
2. Convert runtime update/rollback.
3. Add resume/cancel/progress.
4. Add operation list/show/wait/cancel CLI.
5. Add a minimal GUI Activity view.
6. Pass R3 exit gate or record exact remaining failures.

---

## 6. Parallel work that is safe

While the critical path proceeds, separate contributors may work on:

- Pure Home/Activity/Model Library view models against fake services.
- Error catalog and remediation text.
- Accessibility helpers and keyboard navigation tests.
- CI action pinning, Ruff, typing baseline, and Xvfb setup.
- Documentation restructuring and screenshot harness.
- BC-250 HIL runbook design.
- Authentication capability spike using pinned runtime documentation/tests.
- Image/runtime digest research and license/SBOM tooling.

They must not add new persistence, command execution, privilege, or remote exposure paths.

---

## 7. Feature decisions for 1.0

| Feature | Decision | Reason |
| --- | --- | --- |
| Home/Quick Start | Include | Highest reduction in user confusion |
| Persistent Activity | Include | Required for reliable long operations |
| Unified Model Library | Include | Core appliance workflow |
| Named profiles | Include | Hides unsafe low-level complexity |
| Native basic chat | Include | Product should work without terminal/Open WebUI |
| Open WebUI | Include, optional | Existing richer interface |
| Authenticated tailnet | Include, optional | Valuable and bounded remote use |
| Storage manager | Include | Prevents disk-related failures |
| Backup/restore/repair | Include | Production recovery requirement |
| Signed user-triggered updates | Include | Maintainability without unattended mutation |
| Support bundle | Include | Practical supportability |
| Local metrics | Include, bounded | Health/performance evidence |
| Idle model unload | Include if low-risk | Useful power/thermal improvement |
| Model comparison | Include, simple | Helps selection using existing metadata |
| Text attachments | Defer | Expands parsing/context/privacy scope |
| RAG/vector store | Defer | High complexity and host-memory cost |
| Agent/tools | Defer | Major security boundary expansion |
| Public sharing | Exclude | Conflicts with appliance threat model |
| Generic hardware | Defer | Invalidates current safety/fit assumptions |
| Multiple loaded models | Defer | Conflicts with 12 GiB UMA budget |
| System tray | Defer | Packaging complexity, low core value |

---

## 8. Definition of done for every implementation slice

- One named plan task and user/system outcome.
- Tests added before or with behavior.
- Success, validation failure, timeout, conflict, and rollback/recovery covered.
- No broad state save, hidden path fallback, raw SQL, generic elevation, or shell interpolation introduced.
- External work has timeout and cancellation semantics.
- Durable intent/checkpoint precedes external effects where required.
- Error code and user remediation defined.
- Events/logs redacted; prompts/secrets excluded.
- CLI and GUI use the same service behavior.
- README/changelog/plan/handoff updated.
- Source and editable tests pass.
- Package/wheel checks run when public API/resources/schema change.
- Guard counts decrease or remain exactly justified.
- Commit remains narrow and reviewable.

---

## 9. Stop rule

Development reaches the intended sweet spot when:

1. The final 1.0 no-go checklist passes.
2. All first-time, returning-user, model-install, failure-repair, secure-remote, and update journeys pass on a real BC-250.
3. The user can operate, update, diagnose, back up, and recover without undocumented shell steps.
4. No 1.0-deferred feature is required to satisfy the supported product promise.
5. A release candidate completes the soak without a P0/P1 issue.

At that point, stop adding features. Ship 1.0, collect real-world feedback, and limit the first maintenance releases to defects, compatibility updates, security fixes, catalog evidence, and small usability improvements. Begin a 1.1 roadmap only from observed demand rather than the pre-1.0 idea backlog.

---

## 10. Immediate next task

Start with **A1 — ThermalStateService**.

The safest first test is:

> Given a latched thermal stop, a concurrent stale status/configuration write cannot clear or downgrade the latch, and a reset request above the resume threshold is rejected without changing durable state.

Then remove the two thermal whole-state saves, reduce the guard exactly, run all tests, and hand off to A2 history appends.
