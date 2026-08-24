# BC250 LLM MODE — Post-R2 Production Finish Plan

**Purpose:** Continue from the closed R1/R2 gate, implement the durable
operation layer, and carry the application to a focused, supportable 1.0
without turning it into a generic inference platform.

**Repository baseline audited:** HEAD `7672e7d`, version `0.9.0.dev0`. The
tracked tree is clean; three user-owned scratch files remain untracked under
`scripts_audit/` and are outside this plan. The current checkout passes its
full test suite and compile check. A fresh collection on 2026-08-23 reports
**301 tests**, not the 313 recorded in the handoff, so the count must be
reconciled before the next handoff rather than copied forward.

**Predecessor:** `R2_EXIT_AND_OPERATION_ENGINE_PLAN.md` remains the detailed
contract for operation state, steps, recovery, model acquisition, runtime
updates, and Activity. This plan adds the code-audit findings, production
sequence after R3, feature decisions, and final stopping point.

**Product target:** one supported AMD BC-250/Bazzite appliance profile, one
desktop user/profile, one systemd-owned llama.cpp server, one active model,
safe local operation, optional authenticated tailnet access, reversible host
tuning, and user-triggered updates.

---

## 1. Executive recommendation

The project has an unusually good foundation for its stage. The strongest
parts are not cosmetic: path isolation, SQLite cutover, atomic migrations,
thermal latch protection, fit gating, known-good rollback, durable handoff
publication, typed service composition, and architecture guards are exactly
the foundations that prevent difficult production failures.

The next development order should be:

```text
P0 production-wiring corrections
  -> R3 durable operation engine
    -> R4 typed adapters, errors, timeouts, and removal of transitional diffs
      -> R5 privileged helper + independent thermal supervisor
        -> R6/R7 authenticated access + Open WebUI isolation
          -> R8 trusted runtime/model/application artifacts
            -> appliance UX + maintenance/recovery
              -> Linux/BC-250 qualification
                -> 1.0 release candidate
```

Do not begin a broad GUI redesign before R3. Activity, cancellation, recovery,
storage, and stable error objects are the data contracts that the final GUI
needs. Building those views first would preserve synchronous behavior behind a
new skin.

---

## 2. Current codebase assessment

### 2.1 What is already excellent

| Area | Evidence | Why it matters |
| --- | --- | --- |
| Composition and paths | `Application.compose(AppPaths)` and architecture guards | Prevents root/user HOME drift and test pollution |
| Persistence | SQLite, repositories, short-lived units, revision checking | Gives a recoverable concurrency boundary |
| Migration safety | Contiguous ordered registry, atomic DDL, newer-schema refusal | Avoids partial or destructive upgrades |
| Legacy transition | Immutable source, atomic import, repair gate, receipts | Existing users can migrate without losing evidence |
| Runtime handoff | Mode-0600 rendered config with revision/fingerprint | Decouples launcher execution from broad state |
| Thermal safety | Authoritative latch and baseline service | Stale UI data cannot clear a safety stop |
| Model safety | Canonical fit gate and forbidden-artifact checks | NO-FIT models cannot be casually activated |
| Activation recovery | Candidate, health, inference probe, known-good record | Establishes the right rollback model for R3 |
| Runtime updates | Staged build and active-tree restoration | Failed builds do not destroy the working runtime |
| Frontend architecture | No frontend stores/saves/raw SQL/host subprocess imports | CLI, GUI, and chat can converge on shared behavior |
| Testing | Fast suite, fakes, fixture migrations, headless GUI contract | Enables aggressive refactoring with feedback |

This is a strong core. The plan should preserve it, not replace it with a new
framework.

### 2.2 P0 findings to correct before migration 003

These were found by inspecting the current production wiring, not by relying
only on the green test suite.

#### F0.1 — production host-mode imports are broken

`HostModeService` imports `.desktop_mode` and `.llm_mode`, but the actual
modules are `desktop.py` and `llmmode.py`. Those methods will raise
`ModuleNotFoundError` on a real call. Existing GUI surface tests do not execute
the production imports.

#### F0.2 — composed activation refresh uses a deleted API

`model_manager._service_activation()` calls `store.load()` after success.
Production passes the composed `Application`, whose generic `load()` was
correctly deleted in Session 4. The successful path can therefore fail after
the server has already changed. Local-model switching also risks passing
through both the typed service path and the legacy restart path.

The fix is not to add `Application.load()`. Route model/context/slot changes
directly through the already-composed activation service, then refresh through
`Application.read_model()` exactly once.

#### F0.3 — unit-of-work connections do not apply the database contract

`UnitOfWorkFactory._connect()` currently sets only `busy_timeout`. It does not
enable foreign keys and does not share the authoritative connection policy in
`db.connect()`. SQLite foreign-key enforcement is connection-local, so schema
constraints can be silently skipped by normal service writes.

Create one connection factory/policy used by initialization, units of work,
import staging variants, and tests. Read units should additionally use
`PRAGMA query_only=ON` after connection setup where compatible.

#### F0.4 — composition does not close its initialization connection

`initialize_file()` returns an open connection and `Application.compose()`
ignores it. Make ownership explicit: either provide an initialize-and-close
function or use a context manager. No connection should depend on garbage
collection for closure.

#### F0.5 — launcher retains a stale JSON fallback

`generate_launcher()` still embeds a legacy `state.json` fallback. After the
R2 gate, a missing handoff should be regenerated or block start; it should not
silently launch from an immutable, potentially stale migration source.

Remove the fallback and test that a missing/invalid handoff fails closed until
`RuntimeHandoffService` regenerates it from SQLite.

#### F0.6 — reported test count is stale

The handoff reports 313 tests, while this checkout collects 301. Determine
whether 12 tests were dropped, renamed, or counted differently. The gate is
green, but handoff evidence must be reproducible.

### 2.3 P1 structural debt

| Debt | Current evidence | Required disposition |
| --- | --- | --- |
| Transitional broad diffs | `FRONTEND_COMMIT_KEYS`, `commit_settings_changes`, and 18 `persist_state_diff` call sites | Replace with typed commands; delete before 1.0 |
| Writable JSON implementation remains | `StateStore` and importer staging JSON canonicalization | Extract pure legacy canonicalizer; isolate/remove writable store from production |
| Incomplete production wiring tests | Fakes bypass imports and composed adapters | Add contract tests that resolve and invoke every production adapter seam safely |
| Unbounded command execution | `CommandRunner.run()` has no timeout/cancel/process-group policy | Add typed bounded execution and error taxonomy |
| Unbounded chat HTTP | streaming and benchmark use `timeout=None` | Add connect/write/read-idle/total policy and cancellation |
| Shell staging | 14 `bash -lc` sites, including interpolated paths | Replace user/path-sensitive sites with argv or validated scripts |
| Generic elevation | 44 audited `elevated()` call sites | Replace with allowlisted helper operations |
| Network exposure | tailnet sharing publishes raw unauthenticated API | Put an authenticated gateway in front; backend remains loopback-only |
| Mutable dependencies | Fedora `latest`, Open WebUI version tag, broad Python ranges | Resolve immutable digests/locks and record provenance |
| Large modules | `server.py` 1058 lines, `services.py` 996, CLI/chat ~700 | Split by ownership while adding typed adapters; avoid a standalone rewrite |
| Documentation drift | architecture still calls JSON the state store; old plans point to A1 | Consolidate current-status authority and add freshness checks |
| Quality gates | no formatter/linter/type/security configuration; Actions use version tags | Add staged quality gates and pin release workflow dependencies |

### 2.4 Honest readiness score

- **Core data/safety design:** strong.
- **Unit/component testability:** strong.
- **Production host-effect reliability:** promising but incomplete.
- **Crash recovery for long work:** not yet implemented.
- **Privilege/network security boundary:** not production-ready.
- **Release reproducibility and hardware evidence:** not production-ready.
- **End-user polish:** feature-rich, but failure recovery and Activity are not
  yet appliance-grade.

The codebase already looks good internally. It will look “awesome” when the
same rigor applied to SQLite and thermal safety also governs every command,
download, update, privilege boundary, and user-visible failure.

---

# Phase 0 — Stabilize the post-R2 production wiring

## 3. Session 4.1 — correctness before operations

This is a small mandatory slice. Do not introduce operation tables until it is
green.

### 3.1 Reconcile the baseline

1. Run collection with project `addopts` disabled and record the exact count.
2. Compare test paths at `7672e7d` with the prior Session 4 report.
3. Confirm source and editable invocation collect the same tests.
4. Update `AGENTS.md` and plan evidence to the reproducible number.
5. Add a tiny CI summary artifact or command that prints the collected count
   so future reports do not infer it from progress dots.

Do not create empty tests merely to restore “313.” The correct number is the
number of meaningful collected cases.

### 3.2 Centralize SQLite connection policy

Implement a single API such as:

```python
open_database(path, *, mode: Literal["read", "write", "migration"])
```

Required behavior:

- busy timeout on every connection;
- foreign keys on every connection;
- WAL and FULL synchronous policy for published runtime databases;
- migration staging may explicitly select DELETE journal mode before writes;
- read units set query-only and never begin a write transaction;
- row factory set consistently;
- deterministic close;
- database-too-new/integrity errors retain typed classification;
- tests consume PRAGMA results so they do not retain cursors/checkpoints.

Tests:

- repository foreign-key violation fails through a normal unit of work;
- read unit rejects INSERT/UPDATE/DDL;
- write unit commits once and rolls back once;
- busy timeout remains effective across processes;
- staging import remains self-contained and atomically publishable;
- composition leaves no owned connection open after wiring.

### 3.3 Repair production service wiring

- Correct host-mode module ownership and import names.
- Add production-wiring contract tests for every composed service method.
- Use fakes at the adapter boundary, not by monkeypatching away the service's
  own imports.
- Replace the deleted `store.load()` activation refresh with
  `application.read_model()`.
- Ensure local-model activation has one restart/health/probe sequence.
- Ensure model/context/slot commands use `application.activation` rather than
  constructing another service graph.
- Verify rollback calls both health and minimal inference for the restored
  known-good runtime.
- Ensure an error after an external activation cannot be mistaken for a
  pre-mutation failure.

Required end-to-end fake-host tests:

- enter LLM mode;
- enforce desktop next boot;
- return to desktop;
- model activation through CLI, chat, and GUI service entry points;
- local model registration plus activation;
- context and slot changes;
- candidate failure and verified rollback;
- rollback inference failure entering recovery required.

### 3.4 Close the final legacy launcher seam

- Remove `state.json` from the generated launcher.
- Make runtime handoff validation strict: schema version, config revision,
  fingerprint, model identity, bounded numeric ranges, approved flags.
- A missing or invalid handoff blocks service start with a stable diagnostic.
- Controlled start/restart first regenerates from current SQLite state.
- The service unit points only to the typed launcher/handoff path.
- Update comments and tests that still describe “handoff-first, legacy
  fallback.”

### 3.5 Extract pure legacy canonicalization

Move v1–v5 interpretation into a pure function/module used by import tests and
`LegacyImporter`. It must not create a staging JSON file or call a writable
`StateStore`.

Afterward:

- production import source is read exactly once;
- source remains byte-identical;
- canonicalization has no file I/O;
- `StateStore` is moved to test support or deleted if no package API promises
  it;
- `state.py` may retain immutable defaults/read-model helpers under a clearer
  name, but not a runtime JSON persistence abstraction;
- `fsops.py` no longer claims durable runtime state JSON is written.

### 3.6 Remove duplicate post-service commits

Audit CLI/chat/GUI call chains. If a typed service committed a domain change,
the caller must only refresh its disposable snapshot. It must not call
`commit_settings_changes()` afterward.

Freeze and drive down:

- `FRONTEND_COMMIT_KEYS`;
- `commit_settings_changes()` call sites;
- `persist_state_diff()` call sites.

Do not force all 18 diff sites to zero in this session if that would mix in
R4. Record exact owners and zero the activation/host-mode/component paths that
R3 will consume.

### 3.7 Documentation truth pass

Update current descriptions in:

- `AGENTS.md`;
- `ARCHITECTURE.md`;
- `ROAD_TO_1_0_IMPLEMENTATION_PLAN.md`;
- `MASTER_IMPLEMENTATION_PLAN.md`;
- `docs/STATE_SCHEMA.md` and ADR 001;
- README migration/launcher language.

Mark earlier plans historical rather than maintaining several contradictory
“immediate next task” sections. This file becomes sequencing authority after
R2; the master plan remains requirements authority.

The `scripts_audit/` files are user-owned. Do not delete them merely to make
the tree visually clean. Ask or leave them explicitly out of scope.

### 3.8 Session 4.1 exit gate

- Exact test count reconciled and documented.
- Full suite, compile, diff check, editable install, wheel build/install pass.
- UoW foreign-key and read-only contracts are test-proven.
- Composition closes initialization resources.
- All production service imports resolve.
- Activation never calls deleted persistence APIs and never double-restarts.
- Launcher has no JSON fallback.
- Legacy canonicalization is pure.
- No current documentation calls JSON the runtime source of truth.

Suggested commits:

1. `fix(R2): centralize SQLite connection policy and deterministic close`
2. `fix(R4): repair composed host and activation wiring`
3. `refactor(R2): remove launcher and canonicalizer JSON runtime seams`
4. `docs(R1,R2): reconcile baseline and current architecture`

**Stop and report before migration 003.**

---

# Phase 1 — R3 durable operation engine

## 4. Session 5A — ADR, migration 003, state machine, repositories

Follow Part II §§7–8 of `R2_EXIT_AND_OPERATION_ENGINE_PLAN.md`, with these
clarifications.

### 4.1 Write ADR 002 first

Decide and document:

- process-host model for 0.9 and final 1.0;
- state/step transition tables;
- terminal result meanings;
- operation request versioning;
- resource lock ordering;
- lease ownership/expiry/heartbeat;
- recovery policy versioning;
- cancellation-safe points;
- event redaction and retention;
- whether interrupted work automatically resumes or pauses for user review;
- how later privileged-helper authorization integrates without schema changes.

For 0.9, it is acceptable for a frontend-owned executor to checkpoint and
resume after restart. It is not acceptable to claim that work continues after
the frontend exits when no independent worker exists. The UI must say
“interrupted; ready to resume.” Before 1.0, safe long operations should run in
an independently supervised worker or explicitly pause at a safe checkpoint.

### 4.2 Migration 003

Add:

- `operations`;
- `operation_steps`;
- `operation_events`;
- `operation_leases`.

Include indexes for active operations, recent history, event cursors, and
expired leases. Foreign keys must be tested through the normal UoW connection,
not only the initialization connection.

Migration tests must cover fresh 003, 001→002→003, injected DDL failure,
retry, newer-schema refusal, and preservation of all v2 rows.

### 4.3 Closed state machine

Use the statuses already selected in the predecessor plan:

```text
QUEUED, PREPARING, RUNNING, VERIFYING, COMMITTING,
CANCEL_REQUESTED, ROLLING_BACK, PAUSED,
SUCCEEDED, CANCELLED, FAILED_ROLLED_BACK, RECOVERY_REQUIRED
```

Every transition is compare-and-swap against expected status and operation
revision. Transition, step checkpoint, and event commit in one unit.

### 4.4 Repository contract

Implement typed request/result records and repositories for operations, steps,
events, and leases. No arbitrary JSON dictionary should cross the public
service boundary without schema/version validation.

Safest first red tests:

1. migration 003 failure after its first table leaves no v3 table or version
   row;
2. invalid/stale state transition changes no row, revision, step, or event;
3. UoW foreign-key enforcement rejects an orphan step/event;
4. secret-like request/event fields are rejected before persistence.

### 4.5 Session 5A exit

- Atomic migration 003.
- Exact transition table and terminal meanings.
- Typed repositories with concurrency tests.
- No worker or real host operation started prematurely.
- Schema/ADR/architecture docs current.

---

## 5. Session 5B — executor, leases, cancellation, progress, recovery

**Detailed implementation authority:**
`SESSION_5B_EXECUTOR_IMPLEMENTATION_PLAN.md`. That document expands this
milestone without changing ADR 002 or the Session 5B stop boundary. Where it
identifies executor-facing contract ambiguities, they must be resolved with
red tests and a narrow ADR clarification before engine code depends on them.

### 5.1 Engine boundaries

Create the `operations/` package defined in the predecessor plan. Keep SQL in
repositories and host effects behind ports. Inject clock, IDs, filesystem,
command execution, health, network, disk, sensor, and runtime controllers.

### 5.2 Worker behavior

The executor must:

1. claim one operation and its ordered resources atomically;
2. persist step intent before ambiguous effects;
3. probe before retrying interrupted effects;
4. checkpoint verified output and event atomically;
5. heartbeat during bounded long phases;
6. honor durable cancellation only at safe points;
7. compensate in reverse effect order;
8. distinguish restored failure from recovery required;
9. release leases only as their current owner;
10. stop claiming new work during shutdown.

### 5.3 Process death and recovery

Use a crash harness that abandons a worker without cleanup, then opens a new
application/engine against the same database and filesystem. Never rely only
on raised exceptions; simulate death after the external effect and before its
checkpoint.

Every interrupted step classifies the real world as absent, complete,
partially resumable, discardable, revertible, or uncertain/manual.

### 5.4 Cancellation and progress

- Progress is monotonic inside a phase.
- Phase changes may reset a phase-local counter but total presentation remains
  understandable.
- Unknown total work uses an indeterminate phase plus heartbeat.
- Downloads/builds poll cancellation between chunks/phases.
- Publication, swap, commit, restart, and rollback critical sections defer
  cancellation.
- `CANCELLED` means no externally visible change or verified restoration.
- Progress writes are throttled; decision/result events are never dropped.

### 5.5 Session 5B exit

- Fake workflows pass success, cancellation, rollback, rollback failure,
  process death, stale lease, and two-worker contention tests.
- No sleeps establish ordering in tests.
- Requests/events/exceptions are redacted and size-bounded.
- Engine is independent of tkinter and CLI formatting.

---

## 6. Session 5C — durable model activation

Convert the mature synchronous activation first, but remove rather than wrap
the old duplicate paths.

### 6.1 Steps

1. Resolve immutable installed artifact identity.
2. Read authoritative thermal latch.
3. Validate fit and runtime request against current revision.
4. Acquire `runtime-active`.
5. Capture prior known-good config, handoff fingerprint, and runtime component.
6. Commit candidate desired config.
7. Publish candidate handoff.
8. Restart through typed runtime controller.
9. Verify service/HTTP health.
10. Run bounded minimal inference.
11. Promote known-good.
12. Complete and release.

Rollback restores config, handoff, server health, and minimal inference. A
failed rollback persists exact evidence and blocks conflicting operations.

### 6.2 Frontend behavior

- CLI enqueues and waits by default; optional detach returns an operation ID.
- GUI opens/focuses Activity and remains responsive.
- Chat model/context/slot commands call the same operation command.
- Progress never reaches success before inference verification.
- A restored failure says clearly that the old model is active.

### 6.3 Crash matrix

Inject before and after every checkpoint from candidate commit through
known-good promotion and every compensation. Assert database, handoff, active
server, desired runtime, known-good record, latch, and terminal status agree.

### 6.4 Session 5C exit

- One activation implementation.
- No `model_manager` legacy restart fallback.
- No generic frontend commit for model/context/slot changes.
- Full source/editable/wheel tests.
- Repeated activation crash suite passes.

---

## 7. Session 6A — model acquisition/import/validation

Implement the artifact workflow from predecessor §11.

### User-visible capabilities

- Resume interrupted catalog downloads.
- Import a local model into managed storage.
- Show disk requirement before starting.
- Verify size/digest and bounded GGUF structure.
- Quarantine corrupt/unsupported artifacts with a reason.
- Deduplicate by content digest when practical.
- Cancel safely and explain whether a resumable partial was retained.
- Keep final library paths invisible until atomic publication.

### Required trust data

- content digest and byte size;
- source/repository/revision/file identity;
- validation status and validator version;
- GGUF architecture/quantization metadata;
- catalog association and provenance;
- staging/quarantine/final path ownership;
- representative fit verdicts.

### Exit

- Download, local import, resume, cancellation, disk exhaustion, digest
  mismatch, malformed GGUF, duplicate content, crash/publication, and path
  traversal tests pass.
- No model runs from an arbitrary source path.

---

## 8. Session 6B — llama.cpp update/rollback

Convert the staged update path from predecessor §12.

### Additional requirements from the audit

- Replace shell-interpolated path/tag scripts with a fixed validated script or
  typed argv wherever possible.
- Persist resolved commit SHA, build recipe/toolchain identity, binary digest,
  prior tree identity, and smoke result.
- Apply command/build/health timeouts.
- Serialize against model activation.
- Make explicit rollback a first-class operation using the same verification.
- Clean only operation-owned staging paths.

### Exit

- Every failure before swap leaves active tree byte-identical.
- Every failure after swap restores and verifies prior health/inference or
  enters recovery required.
- Source branch movement cannot change the operation's resolved identity.
- Cancellation is safe during fetch/build and deferred during swap.

---

## 9. Session 6C — operation CLI and Activity Center

### CLI

Deliver stable list/show/wait/cancel/recover commands with JSON mode, event
cursors, Ctrl-C exit 130, bounded waits, and documented terminal/error codes.

### GUI Activity Center

Show:

- active first, then recent history;
- target, phase, progress, elapsed time, and owner;
- structured event timeline;
- cancellation only when meaningful;
- resume/retry/recover only when policy allows;
- rolled-back and recovery-required states in plain language;
- retained partial/staging information;
- links to the affected Model Library, Runtime, or Repair page.

Closing the GUI never marks an operation cancelled or successful. If the 0.9
worker cannot continue independently, the durable state says paused and the
next launch offers resume.

### R3 gate

- Activation, model acquisition/import, and runtime update/rollback are
  durable, queryable, cancellable, and crash-recoverable.
- CLI and GUI use the same operation service.
- Architecture guards prohibit synchronous bypasses for converted workflows.
- Repeated concurrency/crash tests pass.

Tag/release candidate after review: **`0.9.0` transactional operations core**.

---

# Phase 2 — R4 domain and adapter convergence

## 10. Sessions 7A–7C — remove transitional service seams

### 10.1 Split by domain while changing behavior

Do not perform a large file-move-only rewrite. As each workflow becomes typed,
move it from the 996-line `services.py` into focused modules:

```text
services/setup.py
services/runtime.py
services/models.py
services/host_mode.py
services/components.py
services/sharing.py
services/maintenance.py
ports/systemd.py
ports/podman.py
ports/tailscale.py
ports/sensors.py
ports/network.py
ports/filesystem.py
```

Keep public composition stable during the split.

### 10.2 Typed commands/results

Replace dictionaries/`Any` at service boundaries with dataclasses or typed
records. Every command result includes:

- status and stable code;
- operation ID where applicable;
- verified observed result;
- warnings;
- remediation/action IDs;
- expected/current revision conflict information;
- no raw command output or secret.

### 10.3 Typed adapters

Frontends/services cannot supply arbitrary argv. Provide narrow methods for:

- systemd unit lifecycle and property probes;
- Podman image/container/network/volume lifecycle;
- Tailscale serve configuration;
- GPU sensors and governor controls;
- filesystem staging, swap, publish, ownership, and free-space probes;
- bounded HTTP health/inference/download operations.

Production adapter contract tests use fake executables or captured argv and
must exercise the real production adapter implementation.

### 10.4 Desired versus observed state

Persist user intent separately from probes:

- desired runtime: stopped/running, model, context, slots, profile;
- observed runtime: unit/process state, endpoint health, loaded model,
  fingerprint, timestamp/staleness;
- desired optional services and sharing policy;
- observed container/network/Tailscale topology.

Reconciliation is an operation. A probe cannot rewrite intent.

### 10.5 Error and timeout taxonomy

Define stable categories:

- invalid input;
- unsupported host/hardware;
- dependency missing;
- authorization denied/cancelled;
- conflict;
- insufficient disk/memory;
- fit rejected;
- command timeout;
- network unavailable/idle timeout;
- checksum/provenance failure;
- health/inference failure;
- thermal stop;
- recovery required;
- internal error.

Every command and HTTP request receives bounded policy. Downloads use connect,
read-idle, and overall/retry budgets rather than one infinite timeout.

### 10.6 Drive transitional APIs to zero

By R4 exit:

- delete `Application.commit_settings_changes()`;
- delete `FRONTEND_COMMIT_KEYS`;
- delete `persist_state_diff()`;
- no service accepts a mutable assembled frontend view for persistence;
- no module constructs its own service graph;
- CLI/GUI/chat pass typed requests and refresh query/view models afterward;
- production package has no writable JSON state abstraction.

### 10.7 Quality tooling

Add in non-blocking-to-blocking stages:

1. formatter and import ordering;
2. Ruff lint baseline;
3. typing for operations/repositories/services/ports first;
4. dependency vulnerability and secret scanning;
5. architecture import rules.

Do not enable hundreds of unrelated failures in one commit. Establish a clean
baseline, then require no regression.

### R4 exit

- All host effects use typed adapters with timeouts.
- Desired/observed state is explicit.
- No generic settings diff remains.
- GUI/CLI/chat share typed service results and errors.
- Production wiring tests cover every adapter.

---

# Phase 3 — R5 privilege and independent safety

## 11. Sessions 8A–8C — allowlisted privileged helper

### 11.1 Protocol

Define a versioned request protocol with a closed operation enum. Requests
carry typed bounded values, never arbitrary commands, paths, units, sysctls,
or shell fragments.

Initial operations:

- install/remove/verify the exact service unit;
- start/stop/restart/reset-failed the exact owned unit;
- stage desktop boot policy and per-boot LLM mode changes;
- apply/revert bounded GPU governor values;
- apply/revert the approved swappiness value;
- install/remove approved udev/logrotate/config files;
- manage approved Tailscale service/serve actions where root is required;
- perform exact uninstall restoration.

### 11.2 Validation and authorization

- canonicalize and allowlist every target;
- reject symlinks and path traversal;
- enforce numeric ranges and file ownership/modes;
- verify generated unit/config content before installation;
- reject unknown protocol versions/fields;
- use a narrow polkit policy with explicit user action;
- redact requests and results;
- return typed errors.

### 11.3 Migrate all 44 elevation sites

Drive the guard from 44 to zero in classified slices. Delete generic
`elevated()` only after every call has a typed replacement. Keep exact command
audit evidence and negative tests.

## 12. Independent thermal supervisor

Move thermal enforcement out of the GUI/chat lifecycle into a hardened managed
process/service.

Requirements:

- authoritative latch remains in SQLite or a helper-safe protocol;
- sensor identity is rediscovered and validated;
- missing/invalid sensor fails safe and cannot clear latch;
- throttle baseline is persisted before host mutation;
- stop intent is persisted before service stop;
- explicit safe-temperature reset only;
- bounded polling and restart policy;
- no network access;
- least privilege and systemd hardening;
- operation engine treats thermal stop as a higher-priority gate.

### R5 exit

- No generic privilege wrapper.
- Unauthorized/malformed requests cannot mutate host state.
- Thermal protection works with GUI closed and after reboot/process crash.
- Uninstall restores every owned host change.

Target: **`0.10.0` safe appliance core** after R5 plus security topology.

---

# Phase 4 — authenticated access and optional services

## 13. Sessions 9A–9C — secure gateway

### 13.1 Required topology

```text
llama.cpp backend: loopback only
        |
authenticated gateway: loopback/tailnet policy, limits, audit
        |
Tailscale Serve: tailnet HTTPS only, Funnel disabled
        |
approved remote clients / isolated Open WebUI
```

Never publish the raw unauthenticated llama.cpp API, even to the tailnet.

### 13.2 Gateway capabilities

- generated 256-bit credential or equivalent approved tailnet identity policy;
- constant-time validation;
- credential stored in a mode-0600 file/secret mechanism, never argv/database
  event/log;
- rotate/revoke/status flow;
- request body and concurrency limits;
- connect/read/stream idle timeouts;
- endpoint allowlist;
- safe CORS default;
- request IDs and redacted audit events;
- loopback and tailnet tests proving backend is not directly reachable.

Choose the gateway implementation in a short ADR and prototype. Avoid a large
custom HTTP server if a small, pinned, supportable component satisfies the
contract.

## 14. Open WebUI hardening

- pin image by digest, not only `v0.6.14`;
- remove host networking;
- use a private Podman network with access only to the gateway;
- generate a real backend credential;
- preserve no-new-privileges, dropped caps, memory/PID limits;
- add health check and startup dependency behavior;
- backup/restore its named volume;
- staged update with rollback to prior digest;
- remove placeholder `sk-no-key-needed` production behavior;
- clearly mark Open WebUI optional.

### Security exit

- Public Funnel rules are actively removed/refused.
- Raw backend is loopback only and never tailnet-published.
- Remote requests require authentication and limits.
- Credential rotation does not expose the secret.
- Open WebUI is isolated, reproducible, and recoverable.

---

# Phase 5 — trusted artifacts and updates

## 15. Sessions 10A–10C — provenance and immutable dependencies

### 15.1 Runtime environment

- replace Fedora `latest` with an immutable digest;
- lock/review build and Python dependency versions;
- record image digest, package manifest, compiler/CMake versions, source
  commit, build flags, and binary digest;
- refuse unknown/mismatched runtime identity during known-good promotion;
- make the environment reproducible from a release manifest.

### 15.2 Model trust

- require exact digest when catalog provides one;
- use manifest verification only when manifest authenticity/identity is
  established;
- retain source revision and license metadata;
- distinguish `catalog-verified`, `locally-verified`, `hardware-validated`,
  `preview`, and `quarantined`;
- never auto-activate preview/quarantined content;
- ship catalog updates with signed application releases for 1.0.

### 15.3 Application update center

1. Check signed release metadata on explicit user request.
2. Show version, channel, notes, compatibility, size, and restart impact.
3. Download to staging with progress/cancellation.
4. Verify signature and hashes before installation.
5. Back up database/config and current package identity.
6. Install through the approved packaging path.
7. Run migration/CLI smoke and health checks.
8. Roll back package/database where supported or enter guided recovery.

No unattended update mutation before or at 1.0.

### 15.4 SBOM and release provenance

- Python dependency lock and licenses;
- application SBOM;
- runtime/container package manifest;
- model catalog source/license/digest data;
- pinned CI actions by commit SHA;
- signed checksums/artifacts;
- reproducibility notes.

---

# Phase 6 — appliance-quality user experience

## 16. Must-have 1.0 features

### 16.1 Home and Quick Start

The default screen answers:

- Is the machine safe and ready?
- Which model/profile is active?
- Is the server healthy?
- What temperature/memory headroom remains?
- Is any operation running or recovery required?
- What is the single recommended next action?

Provide Start/Stop, Open Chat, Open WebUI, Change Model, and Resolve Issue
actions. Use progressive disclosure; raw logs and tuning fields are secondary.

### 16.2 Unified Model Library

Combine catalog, installed models, discovered local models, downloads,
validation, fit, and activation.

For each model show:

- installed/available/downloading/quarantined state;
- source and trust tier;
- quantization and size;
- fit at current context/slots/profile;
- expected speed/quality positioning from measured evidence;
- last benchmark and last activation result;
- Update/Resume/Validate/Activate/Delete actions as applicable.

Deletion is an operation with exact target, active-model protection, and
space-to-reclaim preview.

### 16.3 Named workload profiles

Expose understandable presets rather than low-level knobs:

- Cool & Quiet;
- Balanced;
- Maximum Throughput;
- Long Context;
- Multi-user;
- Custom/Advanced.

Preview fit, memory, restart, host-tuning, and tested/estimated status before
apply. Promote a profile as known-good only after verification.

### 16.4 Native basic chat

The 1.0 scope:

- streaming text;
- stop generation;
- new conversation;
- bounded autosave and named history;
- system prompt and basic sampling controls;
- token/context estimate and clear trim warning;
- throughput/timing display;
- model/profile switch through operations;
- bounded HTTP timeouts and reconnect/error guidance;
- atomic private conversation writes.

Keep branching, RAG, attachments, tools, multimodal input, and cloud sync out of
1.0.

### 16.5 Activity and notifications

Activity is already introduced by R3. Complete it with:

- operation filters and recent history;
- actionable remediation;
- safe retry/resume;
- exportable redacted detail;
- optional local completion notification where supported;
- no notification containing prompt/model path/secret data.

### 16.6 Accessibility and interaction quality

- keyboard navigation and visible focus;
- screen-reader labels where tkinter permits;
- status not conveyed by color alone;
- scalable fonts/layout;
- confirmations describing exact effect and reversibility;
- no modal dialog loops during background work;
- consistent stable terminology across CLI, GUI, and docs.

## 17. Additional high-value features selected from the audit

These fit the product and should be included if they do not delay the safety
critical path.

### 17.1 Storage Center — include for 1.0

- space by installed models, staging, backups, conversations, logs, Open WebUI;
- required-space preview before every operation;
- safe cleanup of operation-owned stale staging;
- model deletion with active/known-good protection;
- log/history retention controls;
- exact reclaim estimate and post-clean verification.

Large model workflows make storage visibility a core feature, not decoration.

### 17.2 Backup and Restore Center — include for 1.0

- SQLite online backup API;
- settings/catalog/model-index/conversation selection;
- optional Open WebUI volume backup;
- manifest with versions, hashes, sizes, and compatibility;
- restore preview and free-space check;
- staging validation before publication;
- automatic pre-restore backup;
- crash-safe rollback or recovery-required outcome;
- never embed credentials by default.

### 17.3 Readiness/Doctor Center — include for 1.0

Convert the current doctor output into a layered health model:

- hardware/UMA/Vulkan;
- paths/permissions/database integrity;
- runtime/container/provenance;
- service/handoff/loaded model;
- thermal/host policy;
- storage;
- gateway/Open WebUI;
- updates/backups.

Each finding has stable code, severity, evidence, remediation, and whether an
automatic fix is safe.

### 17.4 Performance and thermal history — include bounded version

Persist coarse, bounded samples and operation-linked benchmark evidence:

- temperature state changes, not high-frequency telemetry;
- model/context/slots/profile/runtime fingerprint;
- prompt-free throughput and latency summary;
- thermal throttling occurrence;
- comparison against previous known-good run.

This helps users choose profiles and diagnose regressions without becoming a
telemetry platform.

### 17.5 Offline import bundle — optional 1.0 stretch

For machines with limited connectivity, support a signed/hashed bundle that
can contain a model artifact, catalog metadata, and optionally a known runtime
manifest. Import uses the same staging, digest, and validation operation.

Do not implement export of third-party model weights without clear license
handling. If scope grows, defer to 1.1.

### 17.6 Benchmark comparison/recommendation — include modest version

Use local measured history to answer:

- fastest verified model/profile;
- best context that fits;
- effect of slot count;
- whether a result is measured or estimated;
- whether runtime identity changed since the benchmark.

Never compare prompt content or persist prompts.

## 18. Features explicitly deferred

- public internet hosting/Funnel;
- multi-node or multi-GPU orchestration;
- multiple simultaneously loaded models;
- RAG/vector database/document ingestion;
- agent/tool/shell/browser execution;
- multimodal models and projector files;
- cloud sync or hosted telemetry;
- automatic BIOS changes;
- unattended automatic updates;
- broad non-BC-250 hardware support;
- plugin/marketplace architecture;
- mobile apps and full web admin replacement;
- complex chat branching, search, attachments, or collaboration.

Defer these even if individual pieces seem easy. They change the threat model,
support matrix, or product identity.

Target after Phase 6: **`0.11.0` feature-complete appliance UX**.

---

# Phase 7 — maintenance, diagnostics, and recovery

## 19. Sessions 11A–11C

### 19.1 Safe cleanup

- inventory first;
- exact targets under validated owned roots;
- protect active/known-good models and rollback trees;
- no unresolved globs/environment-variable targets;
- recoverable trash/quarantine where practical;
- operation with progress and cancellation before deletion begins;
- report removed bytes and recovery options.

### 19.2 Backup/restore implementation

Use operation engine checkpoints. Test:

- backup during concurrent readers/writers;
- corrupt archive/manifest;
- insufficient space;
- version/schema incompatibility;
- crash before/after database publication;
- restoration of runtime handoff;
- custom models directory;
- no secret leakage;
- source backup remains usable after failed restore.

### 19.3 Repair mode

Repair mode must support:

- database integrity and migration status;
- operation recovery decisions;
- handoff regeneration;
- desired/observed reconciliation;
- model index/artifact validation;
- known-good runtime recovery;
- optional service/gateway reconciliation;
- backup restore;
- safe scoped reset.

It must not silently clear a thermal latch, acknowledgement, backups,
credentials, models, or recovery evidence.

### 19.4 Redacted support bundle

Include versions, schema/migrations, sanitized settings, operation/event
history, unit/container status, hardware/Vulkan summary, storage inventory,
runtime provenance, and recent bounded logs.

Exclude prompts, conversations by default, tokens, credentials, raw env,
arbitrary home paths where unnecessary, and model contents.

Run secret canaries through every included source.

### Maintenance exit

- Common failures are diagnosable without undocumented shell commands.
- Backup/restore and cleanup are crash-safe operations.
- Repair preserves safety-authoritative state and evidence.
- Support bundles are useful and redaction-tested.

---

# Phase 8 — release engineering and qualification

## 20. CI and static quality

Required blocking jobs:

- Python 3.11 supported baseline plus next-version smoke;
- compile, formatter check, Ruff, targeted strict typing;
- source and installed-wheel tests;
- migration matrix from every supported fixture/schema;
- architecture guards;
- dependency vulnerability/license checks;
- secret scan;
- SBOM/provenance generation;
- reproducible build comparison where feasible;
- pinned GitHub Actions by commit SHA;
- artifact signing and verification smoke.

Add GUI headless execution under Xvfb and command/adapters integration tests on
Linux. macOS remains developer smoke, not qualification evidence.

## 21. Bazzite/VM integration

Build a reproducible supported-host test fixture or closest documented VM
approximation. Cover:

- fresh install and setup;
- old JSON import and migration;
- package upgrade with existing database;
- Podman/Distrobox lifecycle;
- systemd unit generation/start/stop;
- polkit authorization denial/cancel/success;
- next-boot desktop invariant;
- Tailscale/gateway topology with fakes or isolated network namespace;
- uninstall/reinstall preserving selected user data.

## 22. Real BC-250 hardware qualification

Record evidence for every supported model/profile tier:

- cold boot and hardware detection;
- 12/4 memory-profile confirmation;
- Vulkan runtime identity;
- install/load/health/minimal inference;
- context/slot fit boundary;
- throughput and latency;
- 30–60 minute thermal soak;
- throttle/recover/stop/reset behavior;
- restart and rollback;
- model/runtime update interruption;
- desktop recovery and uninstall;
- power-loss/reboot recovery at selected operation checkpoints.

Catalog status cannot be promoted to hardware-validated without this evidence.

## 23. Release candidate and no-go gate

### Required artifacts

- signed wheel/sdist or supported package format;
- signed checksums;
- SBOM and runtime provenance manifest;
- migration and rollback notes;
- supported Bazzite/BC-250 contract;
- security/credential/remote access guide;
- backup/recovery/uninstall guide;
- known limitations and deferred features.

### No-go conditions

Do not release 1.0 with any of:

- open P0/P1 defect;
- raw unauthenticated remote backend;
- generic privilege execution path;
- thermal enforcement dependent on GUI lifetime;
- unbounded command or chat request on supported workflows;
- operation crash state with no deterministic recovery;
- migration/restore capable of overwriting the last good state silently;
- mutable production runtime/image identity;
- missing signed artifacts/SBOM;
- unverified BC-250 install, model, thermal, rollback, backup, and uninstall
  journeys;
- documentation requiring an undocumented shell rescue for normal failures.

### RC soak

Run the release candidate on real hardware for a defined soak period with
representative model changes, chat, updates, cancellation, thermal events,
remote access, backup, restore, reboot, and desktop return. Restart the soak
after any P0/P1 fix.

---

## 24. Exact session sequence

| Session | Scope | Stop condition |
| --- | --- | --- |
| 4.1 | DB policy, wiring bugs, launcher/canonicalizer cleanup, baseline truth | Full source/editable/wheel green; report and stop |
| 5A | ADR 002, migration 003, states/repos | Invalid transitions and migration failures atomic |
| 5B | Executor, leases, cancellation, recovery harness | Fake workflows survive process death |
| 5C | Durable activation | Every activation/rollback checkpoint crash-tested |
| 6A | Model acquisition/import | Resume/cancel/digest/quarantine/atomic publish green |
| 6B | Runtime update/rollback | Active tree preserved or verified restoration |
| 6C | Operation CLI + Activity | R3 exit gate; review `0.9.0` |
| 7A | Typed systemd/filesystem/runtime adapters | Converted paths have bounded errors/timeouts |
| 7B | Podman/network/sensor adapters + desired/observed | No direct integration calls from services/frontends |
| 7C | Delete generic diff APIs; typing/lint baseline | R4 exit gate |
| 8A | Privileged protocol/helper validation | Negative security suite green |
| 8B | Migrate 44 elevation sites | Generic elevation count zero |
| 8C | Independent thermal supervisor | Safety works with GUI closed |
| 9A | Authenticated gateway | Raw backend unreachable remotely |
| 9B | Open WebUI isolation/update/backup | Digest/private network/credential/rollback green |
| 9C | Remote UX and security gate | Review `0.10.0` |
| 10A | Immutable runtime/model provenance | No mutable production identities |
| 10B | Signed app update operation | Verify/install/rollback path green |
| 10C | SBOM/release provenance | Trust gate green |
| 11A | Home, Model Library, profiles | Core journeys usable without logs |
| 11B | Native chat, Activity completion, accessibility | UX gate green |
| 11C | Storage, backup/restore, doctor/repair/support | Review `0.11.0` |
| 12A | CI/static/Linux integration | Release pipeline green |
| 12B | BC-250 functional/performance/thermal HIL | Qualification evidence complete |
| 12C | RC docs, artifact signing, soak | `1.0.0-rc.1` no-go gate passes |

The sequence may split a session further; it must not merge later security or
release work into an earlier foundational commit.

---

## 25. Verification contract for every slice

Every implementation slice includes:

- named requirement/plan ID and user outcome;
- red test or explicit evidence before behavior;
- success, rejection, timeout, conflict, cancellation, and rollback/recovery
  cases as applicable;
- no path fallback, raw SQL leak, generic elevation, shell interpolation, or
  generic settings diff;
- bounded external work;
- durable intent/checkpoint before ambiguous effects;
- stable error/remediation;
- redacted logs/events;
- CLI and GUI parity through the same command;
- source and editable tests;
- compile and diff checks;
- wheel build/install when public API, schema, resources, or packaging changes;
- repeated concurrency/crash suite when synchronization changes;
- README/architecture/changelog/ADR/schema/AGENTS update;
- exact test count and clean tracked-tree report;
- narrow commit with no unrelated scratch-file mutation.

---

## 26. Immediate next checklist

The next implementation session starts here:

1. Confirm HEAD `7672e7d` and preserve the three untracked scratch files.
2. Reproduce and explain the 301-versus-313 test count.
3. Add failing UoW tests for foreign keys, read-only query mode, and close.
4. Centralize the SQLite connection policy.
5. Add production-wiring tests that expose the host-mode import failures.
6. Fix host-mode imports without changing the public service behavior.
7. Add composed activation tests that expose the deleted `Application.load()`
   call and local double-restart path.
8. Route activation through the single composed service and read model.
9. Add a launcher test proving no `state.json` fallback remains.
10. Extract pure legacy canonicalization and isolate/remove writable
    `StateStore` from production.
11. Remove duplicate caller commits on the corrected paths.
12. Run source, editable, compile, wheel, and architecture gates.
13. Reconcile docs and commit Session 4.1 in narrow changes.
14. Stop and hand off the first Session 5A red tests: atomic migration 003 and
    invalid operation transition.

### Recommended first red test

> A normal `UnitOfWorkFactory.begin()` write that violates a declared foreign
> key must fail and roll back, proving the same connection policy used by
> production repositories—not only by database initialization—enforces the
> schema.

### Session report format

- HEAD and tracked/untracked status;
- exact collected/passed/skipped count and invocation;
- commits with plan IDs;
- production defects fixed;
- architecture/guard deltas;
- source/editable/wheel verification;
- deliberately deferred scope;
- exact next red test and stop gate.

---

## 27. Final sweet spot

Development stops and 1.0 ships when a BC-250 owner can:

1. install and validate the appliance safely;
2. download/import, validate, compare, activate, and remove fitting models;
3. apply understandable profiles with preview and rollback;
4. chat locally with cancellation and bounded recovery;
5. inspect and control long operations after a frontend restart;
6. update and roll back llama.cpp and the application from verified artifacts;
7. use optional authenticated tailnet/Open WebUI access without exposing the
   raw backend;
8. survive thermal events, crashes, failed updates, and interrupted restores;
9. see storage, health, Activity, and actionable repair guidance;
10. back up, restore, diagnose, support, and uninstall without undocumented
    shell steps;
11. pass the supported Linux and real BC-250 qualification matrix;
12. receive signed artifacts, provenance, SBOM, and honest limitations.

At that point, stop adding features. Ship 1.0, collect real use data, and keep
1.0.x limited to defects, security/compatibility fixes, catalog evidence, and
small usability improvements. Begin 1.1 only from observed user demand.
