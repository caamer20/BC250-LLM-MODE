# BC250 LLM MODE — End-User Experience Implementation Plan

**Purpose:** Define the next implementation sequence and the product features that will make BC250 LLM MODE understandable, dependable, and pleasant for a non-developer to operate.

**Baseline:** Originally written at `main` at `2126d61` (`v0.8.0-pre-sqlite`, 225 tests). **Update:** the repository/facade work and the single composition cutover are DONE (SQLite is the source of truth behind `compat_state.CompatStateStore`; launcher v2 consumes the rendered runtime handoff), followed by the R2 hardening pass (repair gate, state-carried revision validation, atomic migrations, durable fsynced publication, transaction return semantics, connection serialization, dedicated handoff renderer/service, whole-state-save guards) — 252 tests passing at `82832a3`. Remaining before any visible feature: migrate whole-state saves to narrow repository/service calls until the guards reach zero, remove the compatibility facade, and pass the R2 exit gate.

**Relationship to the master plan:** This is an additive product-experience plan. `MASTER_IMPLEMENTATION_PLAN.md` remains authoritative for security, safety, persistence, recovery, and release gates. A feature in this document cannot bypass a dependency in the master plan.

---

## 1. Direct answer: where development continues

~~The next implementation task is still the **R2.2 call-site cutover**, not a new visible feature.~~ **Update:** the cutover and the R2 hardening pass are complete. The next implementation task is the **whole-state save sweep** (Session 2 of the master plan): small domain commands over repositories, one guarded save removed per commit, until the compatibility facade can be deleted at the R2 exit gate.

Execute the next work in this order:

1. Finish the R1.1 path-consumer sweep.
2. Add typed SQLite repositories and a temporary optimistic compatibility facade.
3. Switch the application composition root from JSON to SQLite exactly once.
4. Preserve `state.json` as a read-only migration backup and prove no code writes it.
5. Replace all whole-state production saves with narrow repository/service calls.
6. Pass the complete R2 durability and migration exit gate.
7. Build the R3 persistent operation engine.
8. Introduce R4 domain services and immutable frontend view models.
9. Implement the first user-visible product slice: **Quick Start + Operations Center + Model Library**.
10. Complete the R5 privileged helper/safety supervisor and R6 authenticated gateway before exposing remote-access workflows as supported.

The first visible feature work may begin in parallel only when it is pure presentation or view-model work with fake services. It must not add another state mutation path.

---

## 2. Product objective

The finished application should feel like a focused local-AI appliance rather than a collection of setup scripts.

A first-time user should be able to:

1. Open the application and understand whether the machine is ready.
2. Accept the safety policy in plain language.
3. Choose a recommended usage goal instead of tuning low-level flags.
4. Select or install a model that is guaranteed to fit the selected profile.
5. Start chatting with one clear action.
6. See download, build, model-load, and update progress without watching logs.
7. Close and reopen the GUI without losing the operation.
8. Understand thermal, storage, and health warnings and know what to do next.
9. Enable secure tailnet access through a guided workflow.
10. Back up, update, repair, or roll back without using a shell.

An experienced user should also be able to inspect exact settings, provenance, fit calculations, logs, and benchmark evidence without those details overwhelming the default interface.

---

## 3. Experience principles

### 3.1 Safe default, explicit advanced mode

- Default to the validated BC-250 profile, one request slot, conservative thermal policy, and loopback-only access.
- Present goals such as “best quality,” “fast responses,” and “long documents” before raw context, slot, clock, and cache controls.
- Keep expert controls available but visually separated and bounded.
- Explain the consequence of every host-level change before authorization.

### 3.2 One source of truth

- GUI, CLI, chat, Open WebUI integration, and future remote clients must show the same desired state, observed state, operation status, and error information.
- No frontend owns authoritative state or long-running work.
- A refresh must never overwrite an in-progress edit or operation.

### 3.3 Progress instead of uncertainty

- Long actions show phase, progress, elapsed time, and whether cancellation is safe.
- Indeterminate work emits a heartbeat and current subtask.
- Closing the GUI detaches from the operation rather than silently killing it.
- After restart, the application explains whether work resumed, rolled back, or needs a decision.

### 3.4 Plain language first

- Prefer “This model needs more memory than the current profile allows” to internal fit codes.
- Prefer “Server started but did not answer a test prompt; the previous model was restored” to a raw service error.
- Keep stable error codes and technical detail behind a Details action for support.

### 3.5 Local and private by default

- Conversations remain local unless the user exports them.
- Remote access is off by default and authenticated when enabled.
- Support bundles exclude prompts, conversations, secrets, and model contents.
- Any future usage analytics must be local-only by default and separately opt-in if transmission is ever introduced.

### 3.6 Reversible actions

- Model changes, profiles, updates, Open WebUI changes, and host tuning preserve a known-good rollback target.
- Destructive cleanup displays exact targets and recoverability.
- “Factory reset” is decomposed into understandable scopes instead of one dangerous button.

---

## 4. Primary user journeys

These journeys define the product-level acceptance tests.

### Journey A — First successful chat

1. Launch application.
2. Hardware and safety preflight runs.
3. Application recommends a profile and two or three fitting models.
4. User chooses a model.
5. Download/import progress is visible and resumable.
6. Artifact validation and fit checks pass.
7. Server starts and completes a minimal inference probe.
8. Native chat opens with an example prompt.

**Success criterion:** A user unfamiliar with llama.cpp never needs to choose a quantization, find a port, open a terminal, or read a service log.

### Journey B — Returning user

1. Launch application.
2. Home screen reports ready, stopped, attention needed, or recovering.
3. “Start last setup” restores the last known-good model/profile.
4. User chats or switches to another installed model.

**Success criterion:** Normal operation is one or two actions and never repeats setup.

### Journey C — Install another model

1. Open Model Library.
2. Filter by goal, fit, tier, size, or family.
3. Compare expected quality, speed, context, and storage.
4. Preview download size and remaining disk space.
5. Start installation.
6. Operation can be paused/cancelled where safe and survives GUI restart.
7. User can activate immediately or keep the current model running.

### Journey D — Resolve a problem

1. Home screen presents “Needs attention.”
2. User opens a health item with evidence and remediation.
3. Safe automatic repair is offered where available.
4. Risky repair previews changes and requests authorization.
5. If unresolved, a redacted support bundle is generated.

### Journey E — Secure remote access

1. User opens Remote Access.
2. Application checks Tailscale and gateway readiness.
3. User chooses local-only or tailnet access.
4. Application creates a client-specific credential.
5. User receives the correct URL and a copyable credential once.
6. A built-in authorized probe succeeds and an unauthorized probe fails.
7. User can rotate or disable access immediately.

### Journey F — Update safely

1. Application reports an available signed update.
2. User sees version, channel, size, compatibility, and release notes.
3. Preflight checks space and creates a verified backup.
4. Update progress survives application restart.
5. Health checks pass or the prior version is restored.

---

## 5. Current experience audit

### What already works well

- Native resumable setup wizard and management dashboard.
- Hardware validation and memory-profile reporting.
- 24-model searchable catalog with fit-aware recommendation.
- Local GGUF discovery and catalog aliases.
- Safe model activation rollback.
- Context and slot controls with fit checks.
- Terminal chat with conversation commands and benchmarks.
- Optional Open WebUI and Tailscale management.
- Thermal status, tuning, and autotune controls.
- llama.cpp status/update/rollback actions.
- Repair, desktop return, logs, and doctor commands.

### Main user-facing weaknesses

| Problem | User impact | Architectural cause |
| --- | --- | --- |
| Dashboard exposes many peer-level buttons | New users cannot tell what to do first | No task-oriented home view model |
| “Start terminal chat” is the primary native chat path | GUI users are sent into a terminal | No native chat workspace |
| Long work is tied to GUI threads/log text | Closing the window creates uncertainty | R3 operation engine absent |
| Model catalog and installed models are separate tables | Hard to understand ownership, status, and storage | No model-library domain view |
| Low-level tuning is prominent | Users can choose settings without understanding tradeoffs | No named workload profiles |
| Errors are primarily log/exception shaped | Remediation is unclear | No error catalog or health model |
| Remote sharing exposes development-era assumptions | Unsafe and confusing credential story | Authenticated gateway absent |
| Update/rollback is component-specific | No unified maintenance experience | No update/backup service |
| State may be overwritten by whole-state saves | UI can display or persist stale data | SQLite cutover incomplete |
| Chat can wait indefinitely | User may be unable to recover cleanly | Unbounded HTTP timeout |
| Conversation management is terminal-oriented | Difficult to browse or organize history | No conversation repository/UI |
| Storage use is not explained before large actions | Disk exhaustion can surprise users | No storage inventory/preflight service |

---

## 6. Dependency gates for feature work

| Gate | Required foundation | Features unlocked |
| --- | --- | --- |
| G0 — Clean persistence | R1.1 complete, SQLite sole source, compatibility saves zero | Reliable settings, profiles, model library metadata |
| G1 — Recoverable operations | R3 engine, progress, cancellation, crash recovery | Downloads, installs, updates, backups, autotune UX |
| G2 — Domain services | R4 services/adapters and immutable view models | Shared GUI/CLI behavior, task-oriented home screen |
| G3 — Host safety | R5 helper and independent supervisor | Guided tuning, service repair, trustworthy safety controls |
| G4 — Authenticated access | R6 gateway and credentials | Supported remote API/WebUI workflows |
| G5 — Trusted artifacts | R7–R9 provenance/update/model pipeline | Automatic updates, model integrity, signed catalog updates |
| G6 — Recovery | R10–R12 storage/backup/repair | Maintenance center and production factory reset |
| G7 — Qualification | R14–R16 CI/VM/HIL gates | Stable 1.0 release claims |

Features can be designed and tested against fakes before their gate, but cannot be presented as supported production behavior until the gate passes.

---

# Part I — Foundation continuation

## UXF-1. Finish the R1.1 path-consumer sweep — P0

**Outcome:** After composition, no application module discovers installation paths independently.

**Implementation:**

1. Pass `AppPaths` or specific path values into download, prepare, Open WebUI, environment, bootstrap, chat, local model discovery, hardware detection, and uninstall workflows.
2. Remove production fallbacks such as `StateStore()` inside GUI/chat code.
3. Remove `Path.home()` discovery from local model scanning; pass explicit default search roots from composition.
4. Stop reading `app_dir` and `logs_dir` from mutable state dictionaries.
5. Preserve customized external model roots as validated user configuration.
6. Add a guard test that permits home resolution only in `paths.py` and the composition entry point.
7. Test every state-changing path with an unwritable sentinel home.

**Exit:** R1.1 is DONE and path-derived database fields cannot drift from the active profile.

## UXF-2. Add typed repositories — P0

**Suggested files:**

```text
bc250_llm_mode/repositories/
  settings.py
  runtime.py
  models.py
  thermal.py
  history.py
  observations.py
  legacy.py
```

**Repository requirements:**

- Typed input/output records.
- Explicit transaction boundaries.
- Stable not-found/conflict/integrity errors.
- Optimistic revisions for user-editable settings.
- No raw connection exposure to frontends.
- No JSON-shaped catch-all repository in the final architecture.
- Repository tests against temporary real SQLite databases.

## UXF-3. Implement the temporary compatibility facade — P0

The facade exists only to make one safe application cutover possible while direct callers are migrated.

It must:

- project SQLite records into the current state-dictionary shape;
- add derived paths from `AppPaths`;
- attach an aggregate revision;
- reject stale whole-state saves;
- translate only known fields;
- never write JSON;
- emit a development warning when a compatibility save occurs;
- have a guard count that can only decrease;
- be impossible to instantiate after its removal milestone.

It must not become a permanent generic persistence API.

## UXF-4. Perform the single composition cutover — P0

Startup matrix:

| Database | Legacy JSON | Behavior |
| --- | --- | --- |
| Missing | Missing | Create fresh SQLite database |
| Missing | Valid | Import once, publish, retain JSON read-only |
| Missing | Invalid | Enter migration/repair mode; publish nothing |
| Present/valid | Present | Open database; do not import or write JSON |
| Present/invalid | Present | Enter repair mode; do not silently fall back and diverge |
| Present/newer | Any | Refuse normal startup with upgrade-required message |

After cutover, application composition provides repositories and the compatibility facade. `StateStore` remains only for importer canonicalization and legacy tests.

## UXF-5. Drive compatibility saves to zero — P0

Migrate by vertical slice:

1. Safety latch and thermal baseline.
2. Model selection/activation and rollback.
3. Runtime configuration and status observations.
4. Setup progress and acknowledgement.
5. Optimization/profile settings.
6. Benchmark/autotune history.
7. Sharing/Open WebUI settings and observations.
8. CLI mutation branches.
9. GUI forms/dashboard saves.
10. Chat conversation/benchmark settings.

For each slice, introduce one domain command, repository transaction, stale-update test, and rollback test.

**Exit:** Production `rg` finds no `store.save`, `store.update`, or `StateStore()` outside the importer/legacy boundary.

## UXF-6. Pass the R2 exit gate — P0

- JSON is never written after successful import.
- v1–v5 legacy migration fixtures are supported or explicitly documented.
- Database integrity failure enters repair mode.
- Newer schemas are refused.
- Stale edits are detected.
- Concurrent writers do not lose updates.
- Source, editable, wheel, and clean-install tests pass.
- Database and backup permissions are verified.
- The migration receipt contains no secrets.

## UXF-7. Build the persistent operation engine — P0

Implement R3 before wiring additional state-changing GUI actions.

Initial operation types needed by the experience plan:

- model download/import/validation/activation;
- runtime start/stop/ensure/update/rollback;
- profile apply/autotune;
- Open WebUI install/update/backup/restore;
- sharing enable/disable/rotate;
- backup/restore;
- support bundle;
- cleanup.

Expose operation ID, phase, progress, cancellation state, resource locks, warnings, error code, and recovery action.

## UXF-8. Introduce frontend-safe domain services — P0

Required query view models:

- `HomeView`;
- `HealthSummary`;
- `RuntimeView`;
- `ModelLibraryView`;
- `ModelDetailView`;
- `ProfilePreview`;
- `OperationView`;
- `StorageView`;
- `RemoteAccessView`;
- `MaintenanceView`;
- `ConversationView`.

Required commands return an operation ID or an immediate typed result. GUI code must not parse logs or raw database rows.

---

# Part II — User-facing feature epics

## UX-1. Task-oriented Home and Quick Start — P0 for 1.0

**Dependency:** G2; safety indicators require G3.

### User outcome

The first screen answers four questions:

1. Is the system safe and healthy?
2. Is a model ready?
3. What should I do next?
4. Is anything currently running?

### Proposed home layout

```text
BC250 LLM MODE                         [Help] [Advanced]

Status: Ready / Stopped / Working / Needs attention / Recovering
[primary action: Start chatting / Resume operation / Fix issue]

Current setup
  Model: Qwen ...       Profile: Balanced
  Context: 8K           Access: Local only
  Temperature: 72 C     Storage free: 84 GiB

Recent or active operation
  Downloading model ... 61%      [View] [Cancel]

Quick actions
  Model Library | Conversations | Remote Access | Maintenance
```

### Implementation tasks

#### UX-1.1 Define status vocabulary

Use exactly these top-level states:

- **Ready:** Known-good runtime can start or is healthy.
- **Stopped:** Safe and configured, model service intentionally stopped.
- **Working:** A durable operation is active.
- **Needs attention:** User action is required but the machine remains safe.
- **Recovering:** Automatic verification or rollback is underway.
- **Safety stopped:** Thermal or host safety latch prevents start.
- **Setup required:** No usable configuration exists.

Avoid showing contradictory peer statuses without one summarized priority.

#### UX-1.2 Build next-best-action policy

Examples:

- No setup → “Set up this machine.”
- No model → “Choose a model.”
- Active safe model stopped → “Start chatting.”
- Operation active → “View progress.”
- Recovery required → “Review recovery.”
- Thermal latch → “View safety status.”
- Gateway credential expired → “Rotate remote access.”

The policy is pure and exhaustively unit-tested.

#### UX-1.3 Add Quick Start

Quick Start accepts a user goal:

- Fast everyday chat.
- Best answer quality.
- Long documents.
- Multiple simultaneous clients.
- Lowest heat/power.

It chooses a fitting installed model/profile or recommends downloads. The user previews the resolved model, quantization, context, slots, expected memory, and host changes before applying.

#### UX-1.4 Progressive disclosure

- Default dashboard shows user goals and health.
- Advanced mode reveals ports, service names, exact fit math, runtime flags, clocks, and raw component versions.
- Remember advanced-mode preference per local user.
- Safety warnings and destructive confirmations are never hidden by mode.

### Tests and acceptance

- Every state combination maps to one top-level status and action.
- Home can render with optional dependencies absent.
- Active operation remains visible after GUI restart.
- User reaches a first prompt without visiting advanced settings.
- No home action performs host work directly.

---

## UX-2. Guided onboarding and preflight — P0 for 1.0

**Dependency:** G2; privileged setup requires G3.

### UX-2.1 Replace component-oriented steps with goal-oriented stages

Recommended stages:

1. Welcome and supported-hardware check.
2. Safety and reboot policy.
3. Storage and runtime readiness.
4. Intended use.
5. Recommended model/profile.
6. Optional local model import.
7. Optional Open WebUI and remote access, disabled by default.
8. Review exact changes.
9. Installation progress.
10. Verified first chat.

### UX-2.2 Preflight report

Show pass/warning/blocker for:

- BC-250 identity;
- Bazzite version/support tier;
- GPU/Vulkan availability;
- host memory and UMA profile;
- required disk space including rollback reserve;
- sensor availability;
- required commands;
- database/application permissions;
- network availability if downloads are requested;
- existing installation/import opportunity.

Every blocker includes remediation and whether automatic repair is available.

### UX-2.3 Installation review

Before authorization, show:

- packages/components to install;
- model and download size;
- estimated staging/backup space;
- generated service units;
- reversible host settings;
- services temporarily stopped;
- remote exposure remains off unless selected;
- next-boot desktop guarantee.

### UX-2.4 Resume and recovery

- Wizard progress is an operation/workflow record, not just an integer.
- Resume at the first unverified stage.
- Re-probe host reality instead of trusting stale completion flags.
- Explain recovered, skipped, and rolled-back steps.

### Acceptance

- Fresh install can be completed without terminal commands.
- A failed or closed setup resumes safely.
- Setup never describes a step as complete until postconditions pass.
- User can exit without leaving boot policy or tuning partially applied.

---

## UX-3. Unified Model Library — P0 for 1.0

**Dependency:** G1, G2; trusted model status requires G5.

### User outcome

Catalog entries, installed models, local discoveries, downloads, validation state, and storage ownership appear in one place.

### UX-3.1 Library record model

Each visible model row should include:

- display name and family;
- catalog/support tier;
- installed/not installed/downloading/invalid/missing;
- quantization;
- artifact size;
- expected fit for current profile;
- recommended use;
- context guidance;
- license/provenance status;
- active/last-used/favorite state;
- validation and digest status;
- available update or alternate quantization.

### UX-3.2 Search and filtering

Filters:

- installed;
- fits safely;
- supported/preview;
- use case;
- model family;
- parameter/size range;
- context capability;
- license;
- favorites;
- needs verification;
- reclaimable storage.

Search should tolerate aliases, punctuation, and common family names.

### UX-3.3 Model detail page

Show:

- “Good for” and “Not ideal for” summaries;
- quality/speed/context relative indicators backed by catalog evidence;
- fit breakdown for each profile;
- tested versus estimated badge;
- source, revision, digest, license;
- installed artifact path in advanced details;
- benchmark history;
- chat template and sampling defaults;
- activate, verify, benchmark, remove, and reveal-file actions.

### UX-3.4 Download manager

- Queue multiple requested models but limit concurrent transfers for memory/disk stability.
- Resume supported downloads using verified ranges.
- Show downloaded/total bytes, transfer rate, and estimated remaining time.
- Distinguish downloading, hashing, GGUF validation, and installation.
- Cancellation removes or quarantines partial data by policy.
- Retry network failures without repeating verified bytes.
- Explain authentication requirements without exposing tokens.

### UX-3.5 Compare models

Allow two or three models to be compared on:

- current-profile fit;
- storage;
- expected speed;
- quality tier;
- context;
- support status;
- local benchmark history;
- license.

Do not present speculative numeric quality scores as objective fact.

### UX-3.6 Import local GGUF

- File/folder picker with preview.
- Detect duplicates by content digest.
- Validate before copying or registering.
- Offer managed copy or explicit external read-only reference.
- Warn that removable/external paths may disappear.
- Never modify an external source file during import.

### UX-3.7 Safe removal

- Preview aliases and content object affected.
- Prevent active/rollback/in-progress artifact deletion.
- Explain when removal only deletes an alias versus reclaiming bytes.
- Offer “Remove from library” and “Delete file” as distinct actions.

### Acceptance

- A model is never simultaneously shown as installed and missing without a clear degraded status.
- Fit is recalculated for profile/context/slots before activation.
- Download/validation survives GUI restart.
- Partial files never appear activatable.
- Removing one alias does not delete an artifact referenced elsewhere.

---

## UX-4. Named profiles and Performance Coach — P1

**Dependency:** G0, G1, G2; host tuning requires G3.

### UX-4.1 Named workload profiles

Ship:

- **Safe:** coolest, most conservative, one slot.
- **Balanced:** normal interactive default.
- **Fast:** lower context or more aggressive validated runtime settings.
- **Long Context:** one slot and fit-bounded context.
- **Shared:** validated multi-slot configuration.
- **Custom:** expert settings within hard safety bounds.

### UX-4.2 Profile preview

Before apply, show:

- selected model and quantization;
- context per slot and total slots;
- predicted weight/KV/overhead memory;
- remaining safety headroom;
- expected restart/interruption;
- host settings changed;
- thermal target;
- whether values are BC-250 tested or estimated.

### UX-4.3 Performance Coach

Use local benchmark and health evidence to make conservative suggestions:

- “Long context is unused; Balanced may start faster.”
- “Four slots leave little memory headroom; Shared-2 is safer.”
- “This model has repeatedly failed to load at 16K; use 8K.”
- “Temperature reached the warning threshold during the last benchmark.”

Suggestions are never auto-applied. Each includes evidence and an undo path.

### UX-4.4 Autotune UX

- Explain candidate count and maximum duration.
- Check fit and thermal readiness before starting.
- Show candidate-by-candidate progress.
- Permit cancellation between candidates.
- Preserve the previous profile until the winner passes health checks.
- Show speed, latency, peak temperature, and headroom—not only one winning score.

### UX-4.5 Idle behavior

Optional policies:

- keep model loaded;
- stop after an inactivity period;
- stop when returning to desktop mode;
- never auto-start after reboot.

Idle policy must not conflict with the no-autostart/reboot-safety invariant.

---

## UX-5. Persistent Operations Center — P0 for 1.0

**Dependency:** G1 and G2.

### UX-5.1 Operation list

Views:

- active;
- needs attention;
- recently completed;
- failed/rolled back;
- all history.

Each operation displays phase, progress, resource, requester, elapsed time, cancellation availability, and result.

### UX-5.2 Operation detail

Display:

- plain-language goal;
- step timeline;
- current heartbeat;
- warnings acknowledged;
- exact target model/component/profile;
- prior known-good state;
- rollback/recovery state;
- stable error and remediation;
- copyable operation ID;
- redacted technical events.

### UX-5.3 Notifications

Provide in-app notifications for:

- model ready;
- operation failed or needs attention;
- thermal warning/stop;
- update available;
- low storage;
- remote credential expiration/rotation reminder;
- backup verification failure.

Optional desktop notifications must contain no prompt or sensitive model/path information and must be user-configurable.

### UX-5.4 Conflict handling

When an action conflicts with a running operation, explain:

- which operation holds the resource;
- whether the new request can be queued;
- whether safe cancellation is available;
- what can still be done concurrently.

Do not merely disable controls without explanation.

### Acceptance

- GUI restart does not lose operation visibility.
- Cancellation state is accurate and deterministic.
- A failed rollback is unmistakably classified as recovery required.
- Operation history retention is bounded.

---

## UX-6. Native Chat and Conversation Workspace — P1

**Dependency:** G2; generation cancellation should use G1; production remote/local auth path requires G4.

### UX-6.1 Native chat surface

Add a tkinter-native chat workspace for users who do not want Open WebUI:

- conversation list;
- message transcript;
- multiline composer;
- send and stop;
- model/profile indicator;
- token/context budget indicator;
- generation status and rate;
- retry/regenerate;
- copy and export;
- clear error and reconnect action.

Keep Open WebUI as the richer optional interface, not a prerequisite for basic chat.

### UX-6.2 Conversation organization

- New, rename, pin, archive, delete.
- Search titles and optionally local message text.
- Sort by recent/model/favorite.
- Automatic title suggestion performed locally and editable.
- Show model/profile used for each message or conversation.
- Warn when continuing with a different model/template.

### UX-6.3 Message controls

- Edit a prior user message and branch from it.
- Regenerate the last assistant response.
- Continue a truncated response.
- Copy as plain text or Markdown.
- Export conversation as Markdown or JSON.
- Optional visible reasoning indicator without persisting hidden reasoning text.

### UX-6.4 Prompt presets

Ship editable local presets such as:

- General assistant.
- Coding assistant.
- Summarize text.
- Brainstorm.
- Rewrite/edit.
- Structured JSON response.

Presets declare system prompt and sampling defaults but cannot override fit/safety settings.

### UX-6.5 Context management

- Use model-aware tokenization where available.
- Show approximate remaining context before send.
- Offer trim strategies: oldest turns, summarize locally, or start new conversation.
- Preview when messages will be excluded.
- Include system prompt and template overhead in the budget.

### UX-6.6 Streaming reliability

- Replace `timeout=None` with connect/write/read-idle/total policies.
- Handle fragmented SSE, keepalives, unknown fields, malformed events, and terminal markers.
- Stop generation cooperatively and restore the UI immediately.
- Never retry automatically after output may have begun without user confirmation.
- Report server restart/model unload distinctly from network failure.

### UX-6.7 Conversation privacy

- Atomic `0600` storage.
- Persistence setting explained on first use.
- Optional no-history conversation.
- Retention and bulk-delete controls.
- Conversations excluded from support bundles and local metrics by default.
- Search index deleted consistently with the conversation.

### Acceptance

- First prompt can be sent from Home without opening a terminal.
- Stop responds promptly even if the server stream stalls.
- Conversation writes survive process interruption without corruption.
- Context trimming behavior is visible and deterministic.
- No message content enters logs or support bundles.

---

## UX-7. Secure Remote Access and Client Setup — P0 for remote claims

**Dependency:** G3 and G4.

### UX-7.1 Remote Access center

Show separately:

- backend: local-only and never directly shared;
- gateway: healthy/authenticated;
- Open WebUI: installed/running/shared state;
- Tailscale: installed, signed in, tailnet identity;
- active client credentials and age;
- externally reachable URLs.

### UX-7.2 Guided enablement

1. Verify gateway health.
2. Verify Tailscale identity.
3. Select API, WebUI, or both.
4. Create purpose-specific credentials.
5. Apply Tailscale Serve to gateway/UI endpoints.
6. Run authorized and unauthorized probes.
7. Present URL and credential setup instructions.

### UX-7.3 Client cards and snippets

Provide copyable configuration examples for:

- OpenAI-compatible base URL;
- curl;
- Python OpenAI-compatible client;
- common local clients documented as tested.

Never insert a real credential into a persistent snippet file. If shown in the UI, reveal once and clear from memory when the view closes where practical.

### UX-7.4 Credential management

- Named clients, e.g. “Laptop” or “Open WebUI.”
- Created/last-used/rotated/revoked metadata.
- Rotate with explicit overlap deadline.
- Revoke immediately.
- Emergency Disable Remote Access action independent of model-server state.

### UX-7.5 Exposure explanations

Use explicit modes:

- Local only.
- Tailnet only.
- Unsupported external exposure detected.

Do not label Tailscale Serve and Funnel interchangeably. Production defaults must not enable public Funnel exposure.

### Acceptance

- Unauthenticated requests always fail.
- Raw llama endpoint cannot be selected as the shared target.
- Secrets never appear in process arguments, logs, events, or support bundles.
- Emergency disable works when the server is unhealthy.

---

## UX-8. Maintenance Center — P0 for 1.0

**Dependency:** G1, G2, G5, and G6.

### UX-8.1 Unified component inventory

Show application, llama.cpp, Open WebUI, gateway, helper, service templates, database schema, and catalog versions with:

- installed version/digest;
- update availability;
- support status;
- last successful verification;
- rollback availability.

### UX-8.2 Update experience

- Stable/preview channel selection with clear risk explanation.
- Signed release notes and compatibility summary.
- Space and backup preflight.
- Persistent update operation progress.
- Post-update health report.
- One-click rollback when valid.
- No silent component update behind the user’s back.

### UX-8.3 Backup and restore

Backup presets:

- Settings only.
- Settings and conversations.
- Open WebUI data.
- Full portable metadata backup.
- Include models, with a prominent size warning.

Restore provides dry-run results, conflicts, required migrations, missing model artifacts, excluded host changes, and rollback plan.

### UX-8.4 Storage manager

Display active models, inactive models, duplicates, partial downloads, quarantine, runtime builds, backups, conversations, logs, and reclaimable space.

Cleanup suggestions:

- abandoned staging data;
- expired rollback versions beyond retention;
- unreferenced duplicate model objects;
- old logs/metrics;
- old verified backups beyond policy.

Never select active, last-known-good, or newest verified backup artifacts by default.

### UX-8.5 Repair mode

Provide a safe reduced interface when normal startup cannot continue:

- database diagnosis;
- restore last verified backup;
- reconcile interrupted operations;
- regenerate services;
- verify models;
- disable sharing;
- revert host tuning;
- create support bundle;
- enter desktop mode.

### UX-8.6 Factory reset

Separate choices for settings, generated services, sharing credentials, Open WebUI, conversations/logs, models, and runtime builds. Preserve models by default.

### Acceptance

- No update begins without verified rollback space.
- Restore is staged and verified before activation.
- Low disk never causes automatic deletion of the known-good copy.
- Repair mode works without starting llama.cpp.

---

## UX-9. Diagnostics, help, privacy, and accessibility — P0/P1

### UX-9.1 Health center

Organize checks by:

- Safety;
- Runtime;
- Models;
- Storage;
- Network and remote access;
- Optional services;
- Updates and backups.

Each check includes status, evidence, impact, remediation, and last checked time.

### UX-9.2 Error catalog

Create stable user-facing errors with:

- code;
- title;
- plain-language cause;
- whether prior configuration is still working;
- safe retry policy;
- remediation actions;
- technical details;
- support-bundle relevance.

High-priority errors include fit rejection, missing model, invalid GGUF, disk full, migration failure, authorization denial, runtime health failure, thermal stop, sensor loss, gateway auth failure, and rollback failure.

### UX-9.3 Contextual help

- “Why this recommendation?” on model/profile choices.
- “What changes on my machine?” before privilege prompts.
- “Why is start disabled?” for blocked controls.
- Links from error codes to local bundled help.
- Searchable command/reference documentation available offline.

### UX-9.4 Support bundle

Preview included files, estimated size, and redactions. Add a self-check proving secret canaries and conversation content are absent.

### UX-9.5 Privacy center

Show:

- conversation persistence and retention;
- local logs and metrics retention;
- remote access status;
- credential inventory;
- Open WebUI data location;
- support bundle policy;
- actions to clear each data class.

### UX-9.6 Accessibility

- Complete keyboard navigation.
- Visible focus.
- Non-color status symbols/text.
- Minimum contrast review.
- Text scaling without clipped controls.
- Screen-reader-friendly labels where tkinter supports them.
- No essential information only in a tooltip.
- Avoid rapidly changing log text as the primary progress signal.

### UX-9.7 Localization readiness

Do not commit to translations before 1.0, but centralize user-facing strings, avoid sentence assembly that cannot be translated, and format dates/numbers through a locale-aware boundary.

---

## UX-10. Optional advanced features after the production core

These features can materially improve the product but should not interrupt R2–R9 safety work.

### UX-10.1 Local text workspace — P2

Allow users to attach bounded plain-text, Markdown, source-code, or extracted-text files to a conversation.

Requirements:

- local-only ingestion;
- explicit supported file types and size limits;
- no executable content;
- token-budget preview;
- source labels in the prompt;
- removal from context without deleting the source;
- no vision/projector assumptions;
- no silent persistence in support data.

Start with direct bounded context insertion. Do not build a vector database until real user need and memory/storage cost are measured.

### UX-10.2 Local document collections/RAG — P3

Only after the text workspace is stable:

- separate indexing operation;
- pluggable local embedding model with BC-250 resource validation;
- collection-level storage accounting;
- source citations to local documents;
- index rebuild/versioning;
- privacy and deletion controls;
- benchmark impact on the 4 GiB host-memory budget.

### UX-10.3 Prompt and profile library — P2

- User-created presets.
- Export/import as validated JSON.
- Versioned built-in presets.
- Model compatibility warnings.
- No arbitrary shell/tool execution.

### UX-10.4 API integration center — P2

- Show base URL, supported endpoints, model alias, context, slots, and auth setup.
- Generate redacted examples.
- Connectivity test per named client.
- Request/concurrency dashboard without prompt logging.

### UX-10.5 Offline installation/update bundle — P2

Create and consume signed bundles containing approved application/runtime/container metadata and optionally selected model artifacts. Verify every digest and estimate required space before import.

### UX-10.6 Catalog update channel — P2

Allow signed catalog metadata updates independently of application code, with rollback and hardware-validation tiers. Never promote a model to supported solely through a remote metadata change.

### UX-10.7 System tray/status indicator — P3

Potential controls:

- current health/model;
- open chat;
- start/stop runtime;
- active operation progress;
- emergency disable sharing;
- thermal stop notification.

Defer until desktop integration can be packaged consistently on Bazzite and does not create a second service owner.

---

## 7. Schema additions anticipated by the experience plan

Do not add all tables immediately. Add them through ordered migrations when the owning feature is implemented.

```text
operations
operation_steps
operation_resource_locks
events
notifications

profiles
profile_revisions
known_good_runtime_configs

model_artifacts
model_aliases
model_sources
model_validations
model_benchmarks
download_checkpoints

conversations
conversation_messages
conversation_branches
prompt_presets

credentials                 # metadata only
gateway_clients             # secret_ref, never secret value

storage_snapshots
backups
installed_components
update_history

health_checks
metrics
support_bundles
```

### Schema design rules

- Use immutable IDs rather than display names as references.
- Store timestamps in UTC with explicit format.
- Store model content identity separately from aliases and paths.
- Store credential references, never secret values.
- Bound history tables through explicit retention jobs.
- Keep prompt/message content out of event and metric tables.
- Use foreign keys and uniqueness constraints.
- Write a forward and failure-recovery test for every migration.
- Do not mutate old migration SQL after release; add a new migration.

---

## 8. Frontend architecture

### 8.1 Navigation model

Recommended top-level destinations:

1. Home
2. Chat
3. Models
4. Activity
5. Remote Access
6. Maintenance
7. Settings

Setup and Repair are modes entered when required, not permanent peer tabs.

### 8.2 UI state rules

- Render immutable view models.
- Keep form draft state separate from persisted settings.
- Refresh by revision/event, not periodic whole-state overwrite.
- Every command returns an immediate result or operation ID.
- Disable controls only with a visible reason.
- Handle stale revisions with compare/reload/reapply choices.
- Never call system commands, repositories, or HTTP directly from widgets.
- Marshal all rendering back to the tkinter main thread.

### 8.3 Reusable components

- status banner;
- health card;
- primary next-action card;
- operation progress card;
- warning/acknowledgement panel;
- fit badge and breakdown;
- support-tier badge;
- storage meter;
- empty-state panel;
- error/remediation panel;
- confirmation preview;
- advanced-details disclosure;
- searchable table with keyboard navigation.

### 8.4 Responsive behavior

The tkinter UI should remain usable at common Bazzite resolutions and scaling settings. Prefer scrollable content and adaptive wrapping over fixed pixel widths. Persist window geometry only after validating it remains on-screen.

---

## 9. CLI experience parity

Every major GUI workflow should have a scriptable CLI counterpart using the same services.

Recommended command evolution:

```text
bc250-llm status [--json]
bc250-llm quick-start --goal balanced [--dry-run]
bc250-llm models list|search|info|install|verify|activate|remove
bc250-llm profiles list|preview|apply|autotune
bc250-llm operations list|show|wait|cancel|recover
bc250-llm chat [--conversation ID] [--no-history]
bc250-llm remote status|enable|disable|clients|rotate|revoke
bc250-llm storage status|cleanup --dry-run
bc250-llm backup create|list|verify|restore --dry-run
bc250-llm update check|apply|rollback
bc250-llm doctor [--json]
bc250-llm support-bundle create --preview
```

CLI rules:

- Query commands are non-privileged.
- State-changing commands return operation IDs.
- `--wait` optionally follows progress.
- `--json` emits only versioned machine-readable output on stdout.
- Human diagnostics go to stderr in JSON mode.
- `--dry-run` is required for destructive/large maintenance previews.
- Exit codes distinguish validation, conflict, authorization, operation failure, and recovery required.
- CLI and GUI use identical error codes and policy decisions.

---

## 10. Test strategy for the experience plan

### 10.1 Pure policy tests

- next-best action for every home state;
- profile recommendation and fit mapping;
- control enable/disable reasons;
- model filter/search/compare;
- cleanup eligibility;
- notification priority and deduplication;
- error-code remediation mapping;
- context-budget decisions.

### 10.2 Service component tests

- Quick Start from empty, installed, degraded, and safety-stopped states;
- model install/activate/rollback;
- operation resume and cancellation;
- profile apply/failed health rollback;
- remote enable/authorized and unauthorized probes;
- backup/update/restore;
- conversation create/branch/delete;
- support-bundle redaction.

### 10.3 GUI tests with fake services

- Home status/action rendering;
- setup preflight blockers;
- model-library filtering and detail;
- download progress after UI restart;
- operation conflict explanations;
- chat send/stop/error;
- stale form revision;
- remote credential one-time reveal;
- repair mode;
- keyboard traversal and focus.

### 10.4 Linux/Xvfb integration

- real tkinter startup and navigation;
- window close during active operation;
- clipboard/copy behavior where supported;
- file/folder dialogs through adapter seams;
- desktop notifications disabled/enabled;
- text scaling and constrained resolution;
- Open WebUI launch URL behavior.

### 10.5 Hardware journey tests

Automate Journeys A–F on the BC-250 runner. Record screenshots or structured checkpoints, component/model digests, temperatures, and operation IDs. A release candidate fails if a journey requires undocumented shell intervention.

### 10.6 Usability acceptance sessions

Before 1.0, conduct at least:

- one clean-install session with a Linux user unfamiliar with llama.cpp;
- one model-switch/storage cleanup session;
- one induced failure and repair session;
- one remote access setup session;
- one backup/update/rollback session.

Record where users hesitate, misunderstand status, or seek logs. Fix recurring confusion before adding more advanced controls.

---

## 11. Delivery roadmap

### Experience Milestone E0 — Persistence cutover

**Includes:** UXF-1 through UXF-6.

**Exit:** SQLite is sole truth, JSON is read-only backup, compatibility saves are zero, all tests and migration matrices pass.

### Experience Milestone E1 — Recoverable application shell

**Includes:** UXF-7, UXF-8, UX-1, UX-5 foundation.

**Exit:** Home and Activity render durable operation truth; model start/download/update work can survive GUI restart.

### Experience Milestone E2 — Guided model appliance

**Includes:** UX-2, UX-3, UX-4.

**Exit:** A first-time user can select a goal, install a fitting model, apply a safe profile, and complete a verified first prompt without using a terminal.

### Experience Milestone E3 — Native local assistant

**Includes:** UX-6 and conversation privacy controls.

**Exit:** Native chat has bounded streaming, stop, context visibility, atomic history, organization, and no-content logging guarantees.

### Experience Milestone E4 — Secure connected appliance

**Includes:** R5/R6 foundation and UX-7.

**Exit:** Tailnet access is authenticated, guided, testable, rotatable, and immediately disableable.

### Experience Milestone E5 — Maintainable appliance

**Includes:** UX-8, UX-9, signed updates, storage, backup/restore, support bundle.

**Exit:** A user can diagnose, update, roll back, back up, restore, clean storage, and repair from the GUI.

### Experience Milestone E6 — 1.0 qualification

**Includes:** accessibility review, complete journeys, VM and BC-250 HIL evidence, release artifacts, documentation.

**Exit:** Master-plan go/no-go gate passes with no open P0/P1 issue.

### Post-1.0

Evaluate UX-10 features using measured demand and BC-250 resource evidence.

---

## 12. Exact next three implementation sessions

### Session 1 — SQLite repositories and composition cutover

1. Complete UXF-1 path sweep.
2. Add typed records and repository protocols.
3. Implement settings/runtime/model/thermal/history repositories.
4. Add compatibility view with revision conflict detection.
5. Add startup matrix tests.
6. Switch `Application.compose()` to initialize/open/import SQLite.
7. Prove JSON remains byte-for-byte unchanged.

**Do not yet remove all legacy save callers in this session if doing so would make the cutover unreviewably large.**

### Session 2 — Compatibility-save elimination and R2 gate

1. Migrate thermal and model-manager writes first.
2. Migrate setup/CLI mutations.
3. Migrate GUI, tune, and chat history writes.
4. Replace state observations with probe/repository writes.
5. Drive the guard count to zero.
6. Remove production compatibility save support.
7. Run migration interruption, concurrency, source/editable/wheel, and clean-install tests.
8. Update master plan and handoff.

### Session 3 — Operation engine skeleton

1. Define operation/step/resource-lock schema migration.
2. Implement legal transitions and repository.
3. Implement operation runner with fake steps.
4. Add cancellation-safe points and progress events.
5. Add startup recovery classification.
6. Convert one contained vertical slice: model download/import.
7. Add a minimal Activity view backed by fake/real operation queries.

Only after this skeleton is proven should more operations or visible feature epics be wired.

---

## 13. Suggested commit sequence

```text
refactor(paths): finish composed path consumers (R1.1)
feat(repo): add typed SQLite repositories (R2.3)
test(state): define SQLite composition startup matrix (R2.4)
feat(state): cut application composition to SQLite (R2.4)
refactor(safety): migrate thermal persistence to repositories (R2.5)
refactor(models): migrate activation persistence to repositories (R2.5)
refactor(app): migrate setup CLI and GUI persistence (R2.5)
refactor(chat): migrate benchmark and conversation settings (R2.5)
chore(state): remove compatibility writes and close R2 gate
feat(operations): add durable state machine and resource locks (R3)
feat(operations): migrate model acquisition vertical slice (R3)
feat(gui): add Home and Activity view models (UX-1/UX-5)
```

Keep schema migration, data import, composition cutover, and call-site cleanup separately reviewable.

---

## 14. Feature priority table

| Feature | User value | Risk | Dependency | Target |
| --- | --- | --- | --- | --- |
| Home/Quick Start | Very high | Medium | G2 | 0.9–0.10 |
| Operations Center | Very high | Medium | G1/G2 | 0.9–0.10 |
| Unified Model Library | Very high | Medium | G1/G2 | 0.10 |
| Guided onboarding | Very high | Medium | G2/G3 | 0.10 |
| Named profiles | High | Medium | G0/G2 | 0.10 |
| Native chat | High | Medium | G2/G4 | 0.11 |
| Conversation manager | High | Medium | Native chat | 0.11 |
| Secure Remote Access center | High | High | G3/G4 | 0.10–0.11 |
| Maintenance Center | Very high | High | G5/G6 | 0.11 |
| Storage manager | High | Medium | G1/G6 | 0.11 |
| Backup/restore UX | Very high | High | G1/G6 | 0.11 |
| Health/support bundle | High | Medium | G2/G6 | 0.11 |
| Performance Coach | Medium | Medium | Profiles/metrics | 0.11 |
| Text attachments | Medium | Medium | Native chat | Post-1.0 |
| Local RAG | Medium | High | Text workspace/resource evidence | Post-1.0 |
| Offline bundle | Medium | High | Signed artifacts | Post-1.0 |
| System tray | Low/medium | Medium | Desktop packaging | Post-1.0 |

---

## 15. Definition of done for a user-facing feature

A feature is complete only when:

- It has a named user outcome and documented dependencies.
- Business policy resides in a service or pure policy module, not a widget.
- All persistence uses repositories.
- Long-running work uses the operation engine.
- Privileged work uses the allowlisted helper.
- Remote work uses the authenticated gateway.
- It has bounded timeout, cancellation, and restart behavior.
- It has plain-language errors and remediation.
- It does not expose secrets or conversation content.
- It handles low disk, missing dependency, stale state, and concurrent operation cases.
- CLI parity exists where useful.
- Unit, service, GUI, and applicable hardware tests pass.
- Accessibility and keyboard behavior are verified.
- Documentation and screenshots match the shipped behavior.
- The master plan and handoff status are updated.

---

## 16. Product no-go conditions

Do not ship a feature as supported if it:

- writes legacy JSON after SQLite cutover;
- performs a whole-state save;
- runs long work only in a GUI daemon thread;
- directly executes an elevated command from GUI/CLI input;
- exposes the raw llama.cpp endpoint remotely;
- passes a secret in argv or logs it;
- bypasses fit calculation;
- can delete active/rollback artifacts without a preview;
- reports success before health verification;
- loses operation status after frontend restart;
- has an unbounded network wait without cancellation;
- stores prompts in logs/events/support bundles;
- requires undocumented terminal recovery for a normal failure;
- makes a generic-hardware support claim without updated validation and evidence.

---

## 17. Final recommendation

The best product strategy is not to add the largest number of features. It is to make the existing capabilities feel coherent and trustworthy.

The next visible release should center on three promises:

1. **Start easily:** Home and Quick Start choose a safe model/profile and lead directly to chat.
2. **Know what is happening:** Activity persists downloads, setup, model switches, updates, and recovery across application restarts.
3. **Recover confidently:** Health, storage, backup, rollback, and repair explain problems without requiring shell expertise.

Complete the SQLite cutover and operation engine first. Then build Home, Activity, and the unified Model Library as one vertical product slice. That combination delivers more end-user value than adding another tuning control or model-family integration, and it establishes the frontend patterns every later feature can reuse.
