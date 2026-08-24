# BC250 LLM MODE — Session 5C Durable Model Activation Implementation Plan

**Status:** Ready for implementation after Session 5B

**Plan IDs:** R3.3 / POST-R2 Session 5C

**Baseline:** `f27afec`, version `0.9.0.dev0`, 448 tests collected and passing

**Predecessor:** Session 5B (`89da016` → `f27afec`)

**Sequencing authority:** `POST_R2_PRODUCTION_IMPLEMENTATION_PLAN.md`

**Requirements authority:** `MASTER_IMPLEMENTATION_PLAN.md` and
`R2_EXIT_AND_OPERATION_ENGINE_PLAN.md`

**Durable contract:** `docs/adr/002-durable-operations.md`

This document turns Session 5C into an implementation-ready slice. It is a
conversion plan, not a parallel feature path: the existing synchronous model
activation implementation is removed as each behavior moves behind the
durable operation engine. The end state has one production activation path,
one owner of the llama-server service, and crash-test evidence for every
externally visible boundary.

---

## 1. Objective and hard stop

Session 5C converts model, context, and request-slot activation into the first
real `MODEL_ACTIVATE` durable workflow. It connects the Session 5B executor to
typed production adapters for committed runtime configuration, runtime
handoff publication, service restart, HTTP health, bounded inference, and
known-good promotion/restoration.

The session is complete when an activation can:

1. durably preserve a versioned request and immutable artifact identity;
2. reject thermal-latched, stale-revision, missing, quarantined, fused/MAX, or
   NO-FIT candidates before an active-runtime mutation;
3. capture a complete prior-runtime restoration target;
4. commit candidate intent, publish the exact handoff, and restart only through
   `server.py`;
5. prove actual model identity, context, slots, health, and bounded inference;
6. promote the exact verified candidate as known-good;
7. recover after process death without duplicating a restart or promotion;
8. restore prior desired state, handoff, service state, health, and inference
   exactly once when the candidate cannot be proven;
9. persist `FAILED_SAFE`, `FAILED_ROLLED_BACK`, or `RECOVERY_REQUIRED` honestly;
10. serve every existing production model/context/slot caller through this one
    implementation.

### Hard stop

Stop after durable model activation and its existing-surface cutover are green.
Do **not** add:

- an `operations` CLI command, detach flag, cancellation command, or operation
  history command (Session 6C);
- an Activity GUI, operation notification center, or new progress UX
  (Session 6C);
- a production background worker, daemon, privileged helper, or composition
  auto-start side effect;
- model acquisition/download/import conversion (Session 6A);
- llama.cpp update/rollback conversion (Session 6B);
- Open WebUI, sharing, Tailscale, host-mode, optimization, setup, or uninstall
  durable workflows;
- parent/child operations or workflow fan-out;
- migration 004 unless the entry corrections below prove a schema change is
  unavoidable. The planned implementation needs no new table or column.

Existing CLI, chat, and dashboard actions remain available. They enqueue and
drive one activation synchronously in their current process, then render the
durable terminal result. Session 5C does not claim execution continues after
that process exits; the interrupted operation remains recoverable on the next
explicit activation/recovery entry.

---

## 2. Non-negotiable invariants

- **One service owner:** only `server.py` constructs commands that touch
  `bc250-llm.service`. A production activation adapter may call typed
  `server.py` functions; it may not invoke `systemctl` itself.
- **One activation owner:** after cutover, `ModelActivationService`,
  `_apply_legacy_or_raise`, `restart_with_rollback`, and the production
  model-manager fallback are gone or unreachable by architecture test.
- **Fit gate:** the candidate passes `calculate_fit` using its installed
  record, quant, context, KV settings, and slots. `NO-FIT` never commits,
  publishes, or restarts.
- **Thermal authority:** activation reads the SQLite thermal latch and refuses
  `STOPPED`; it never clears or weakens the latch.
- **Single durable truth:** runtime configuration, known-good evidence, and
  operation state live in SQLite. `runtime-handoff.json` is a rendered artifact,
  never a second authority.
- **Exact artifact:** candidate identity is resolved from the installed-model
  repository and verified against the file before any restart. No arbitrary
  caller path is accepted.
- **Mmap-safe inspection:** any GGUF/content inspection is streaming or mmap
  bounded; no multi-GiB file is loaded into host RAM.
- **No fused/MAX:** activation rechecks the standard-layout invariant even if
  an old or manually imported record bypassed catalog filtering.
- **Known-good honesty:** `SUCCEEDED` requires exact handoff + health + model
  identity + requested context/slots + bounded inference before promotion.
- **Rollback honesty:** `FAILED_ROLLED_BACK` requires the prior target to be
  restored and verified; otherwise the operation is `RECOVERY_REQUIRED` and
  retains `runtime-active` as a barrier.
- **Desktop next boot:** activation may start/restart the service now, but it
  never enables it or changes the desktop-on-next-boot policy.
- **Secrets:** no token, raw request headers, raw exception text, prompt body,
  generated text, command output, or credential-bearing argv reaches operation
  rows, events, results, logs, or handoff evidence.
- **Injected ownership:** production adapters receive `AppPaths`, UoWs,
  runner factories, clocks, and server ports through composition. No
  `Path.home()` or global writable store reappears.
- **Fenced mutation:** every executor-owned state/checkpoint/compensation write
  proves the current `runtime-active` lease generation in the same transaction.
- **No frontend writes:** model/context/slot frontend actions do not call
  `commit_settings_changes` or save a draft after activation.

---

## 3. Entry audit: correct the generic engine before real effects

Session 5B proved the protocol with fake effects, but production activation
exposes six generic gaps. Fix them with red tests in a narrow first code
boundary. Do not hide them in activation-specific callbacks.

### 3.1 Critical-section operation state

ADR 002 says cancellation is never accepted during publication, restart,
verification, or compensation. The current engine sets `COMMITTING` only after
all steps have verified, so a cancellation can currently transition an
operation while a `critical=True` step is executing.

Record a narrow ADR 002 correction and update the pure transition table:

```text
COMMITTING -> VERIFYING
COMMITTING -> ROLLING_BACK
```

Required engine behavior:

1. immediately before a critical step effect, CAS the operation from
   `RUNNING` or `VERIFYING` to `COMMITTING`;
2. keep it in `COMMITTING` through effect, checkpoint, and postcondition
   verification;
3. only after step verification commits, transition to `VERIFYING`;
4. if the critical effect or verification fails after mutation is possible,
   transition `COMMITTING -> ROLLING_BACK`;
5. if a cancellation CAS wins before entry to `COMMITTING`, run no effect and
   honor it; if entry to `COMMITTING` wins, cancellation is refused until the
   critical step resolves.

This is a state-machine correction, not migration 004: the database stores
state strings already supported by migration 003. Add transition/model,
repository cancellation-race, and engine barrier tests before changing code.

### 3.2 Durable compensation recovery

The current forward loop cannot resume a row left in `COMPENSATING`, and the
current crash test only proves that a compensation death does not fabricate
success. Real activation requires convergence.

Extend the generic step contract with a restoration probe:

```text
probe_restoration(ctx) -> ProbeResult
```

On takeover while the operation is `ROLLING_BACK`:

- reconstruct the reverse compensation set from durable step rows, never the
  prior worker's in-memory `effected` list;
- for `COMPENSATING`, probe the restoration postcondition before repeating;
- `COMPLETE` checkpoints `COMPENSATED` without a second restoration effect;
- `ABSENT`/`REVERTIBLE` runs the idempotent compensation with the same external
  effect ID, then verifies restoration;
- `UNCERTAIN_MANUAL` enters `RECOVERY_REQUIRED` and retains leases;
- continue remaining compensations in reverse sequence and publish exactly one
  terminal result.

Add death points before restoration probe, after restoration effect, before
compensation checkpoint, after compensation checkpoint, and before rollback
terminal publication. Every point must converge under a new executor.

### 3.3 Durable reconstruction of all visible effects

After takeover, a later-step failure currently sees only effects accumulated
by the new process. Change failure and cancellation paths to derive the full
compensation set from durable `operation_steps` states plus immutable workflow
definitions. A verified earlier candidate commit or handoff can never disappear
from rollback merely because the process changed.

### 3.4 Intent-transaction correctness

Red-test and remove the duplicate derive/ID/read block in
`ExecutionEngine._intent_transaction`. Fix the reclaim diagnostic's undefined
`step_key`, remove the empty placeholder output comprehension, and prove:

- `derive_input` runs once per new attempt;
- a reclaim reuses the stored external-effect ID and stored input;
- input used by the effect is the canonical input durably recorded at intent,
  not a newly derived value after the transaction;
- a stale worker cannot pass intent, checkpoint, or verification.

### 3.5 Per-step implementation versions

`EnqueueService` currently writes the first step's implementation version to
every row. Change the repository/API to insert each declared step version.
Reject zero/negative versions and prove mixed-version workflows round-trip
correctly. No schema change is needed.

### 3.6 Bounded evidence and progress wiring

- Store checkpoint output as a sanitized JSON object in event detail, not a
  JSON string nested inside JSON.
- Persist stable codes and bounded scalar evidence only.
- Wire `EffectContext.pulse` to fenced heartbeat/progress for bounded calls
  that expose progress; activation's health and inference calls remain bounded
  and may heartbeat between poll attempts.
- Never persist completion text from the inference probe. Evidence is limited
  to success boolean, elapsed bucket, model identity, and token count.

### Entry-correction gate

- Each issue has a failing test before its correction.
- ADR changes are confined to critical-state cycling and compensation recovery.
- Migration 003 DDL remains unchanged.
- The fake 5B crash matrix remains green and gains true compensation recovery.
- No production activation adapter lands in this boundary.

---

## 4. Target architecture and ownership

Use this shape; exact private helper names may vary, ownership may not:

```text
bc250_llm_mode/
  operations/
    activation.py       request v1, evidence types, workflow definition
    engine.py           generic corrected protocol only
    workflow.py         generic step/probe/restoration contracts
    repositories.py     generic durable operation rows only
  activation_adapter.py production implementation of typed activation port
  activation_command.py enqueue + foreground execute + terminal mapping
  runtime_handoff.py    sole handoff writer + strict observation
  services.py           narrow runtime-config primitives; no orchestrator
  server.py             sole service command/health/inference owner
  app.py                one registry, command, adapter, engine factory
```

### Import direction

```text
frontends/model_manager
          |
          v
ActivationCommandService
          |
          +--> EnqueueService / ExecutionEngine
          |
          v
operations.activation (workflow + typed port)
          |
          v
ActivationHostAdapter
     |         |          |
     v         v          v
repositories  runtime_   server.py
/services     handoff
```

Rules:

- `operations.activation` imports no `server`, tkinter, GUI, chat,
  `CommandRunner`, subprocess, urllib, systemd, or concrete filesystem writer.
- `activation_adapter.py` is the one production adapter and may call narrow
  services; it never constructs operation SQL.
- `server.py` retains all service commands and loopback HTTP calls.
- `runtime_handoff.py` remains the only handoff writer.
- frontends know operation IDs and typed terminal results, not workflow steps.
- `Application.compose()` constructs objects only; it starts no worker/thread,
  performs no activation, and makes no host call.

---

## 5. Versioned activation request and evidence types

### 5.1 `ModelActivateRequestV1`

Define a frozen dataclass with a closed decoder. Persist only fields needed to
reproduce intent:

```text
model_alias: str
context_per_slot: int | None
parallel_slots: int | None
profile_id: str | None
expected_runtime_revision: int
requested_by: cli | chat | gui | setup | repair
```

The request does not accept a path, service name, port, command, URL, token, or
free-form optimization dictionary. Unknown fields fail. Context remains within
the product's 512..262144 range and slots within 1..8, but fit is authoritative.
`allow_preview` is removed: preview is a separate read operation, never a flag
that weakens activation.

### 5.2 Candidate artifact identity

Step 1 resolves a durable `CandidateRuntimeV1` from the installed-model row and
the actual file. It records:

- model alias, canonical managed path, quant, display alias;
- repository validation status and provenance class;
- byte size and stable file identity available on the host;
- a streaming SHA-256 content digest, unless an already verified digest of the
  same byte size/file identity is present;
- GGUF architecture/layout verdict sufficient to reject fused/MAX and missing
  standard tensor layouts;
- requested context, slots, normalized runtime settings, fit verdict/detail;
- expected and candidate runtime revision;
- calculated runtime fingerprint and runtime component identity.

Hashing reads bounded chunks. The digest is step evidence for this operation;
Session 6A later normalizes trust metadata into the installation schema. Before
restart, re-stat and revalidate the identity so a file replaced in place cannot
run under stale evidence.

### 5.3 Prior runtime snapshot

Persist a closed `PriorRuntimeSnapshotV1` in step output containing:

- desired runtime config and its exact revision;
- normalized optimizations/profile;
- full validated handoff payload fingerprint and config revision, or explicit
  `ABSENT`;
- known-good row, including fingerprint/component identity, or explicit
  `NONE`;
- service state: `ACTIVE_VERIFIED` or `STOPPED`;
- if active: observed model alias, context, slots, health, and bounded inference
  success evidence;
- launcher/runtime component identity.

An active prior server that cannot pass health and inference is not a valid
rollback target. Fail safely before candidate mutation and tell the caller to
repair. A stopped prior target is valid: rollback restores config/handoff and
proves the service is stopped; it does not start an old server just to claim
restoration.

### 5.4 Evidence vocabulary

Use stable, bounded codes, for example:

```text
CANDIDATE_RESOLVED
THERMAL_LATCH_STOPPED
RUNTIME_REVISION_CONFLICT
ARTIFACT_IDENTITY_CHANGED
MODEL_LAYOUT_REJECTED
FIT_NO_FIT
PRIOR_ACTIVE_VERIFIED
PRIOR_STOPPED_CAPTURED
CANDIDATE_CONFIG_COMMITTED
CANDIDATE_HANDOFF_PUBLISHED
CANDIDATE_RUNTIME_VERIFIED
CANDIDATE_INFERENCE_VERIFIED
KNOWN_GOOD_PROMOTED
PRIOR_RUNTIME_RESTORED
RESTORATION_UNCERTAIN
```

Do not persist raw exceptions, server bodies, completion content, logs, model
prompts, or command output. User-facing diagnostics may read the existing
bounded server-log tail at the frontend boundary without copying it into the
operation database.

---

## 6. Typed activation port

`operations.activation` declares a protocol consumed by step callbacks. The
production implementation lives outside `operations/` and exposes typed,
idempotent methods grouped by postcondition:

```text
resolve_candidate(request) -> CandidateRuntimeV1
capture_prior(candidate) -> PriorRuntimeSnapshotV1
observe_candidate(candidate, prior) -> ActivationObservationV1
commit_candidate(candidate, effect_id) -> ConfigEvidenceV1
publish_candidate(candidate, effect_id) -> HandoffEvidenceV1
restart_candidate(candidate, effect_id) -> RestartEvidenceV1
check_health(candidate) -> HealthEvidenceV1
check_inference(candidate) -> InferenceEvidenceV1
promote_known_good(candidate, verified, effect_id) -> KnownGoodEvidenceV1
restore_prior(prior, candidate, restoration_id) -> RestorationEvidenceV1
observe_restoration(prior, candidate) -> ProbeResult
```

Every mutator has a read-only probe that can classify reality without first
mutating it. `external_effect_id` is an idempotency correlation key and may be
stored in bounded local evidence; it is never treated as proof by itself.

The adapter must compare complete postconditions:

- candidate config: exact revision + model/context/slots/settings;
- handoff: schema, config revision, runtime fingerprint, model ID/path, total
  context, slots, port, and llama.cpp path;
- active runtime: systemd active plus `/health`, `/v1/models`, `/props`, exact
  model alias, requested context/slots, and component identity when observable;
- inference: bounded one-token request with no persisted content;
- known-good: exact candidate fingerprint, component identity, model/context,
  slots, runtime settings, and verification timestamp.

An observation that is merely “HTTP 200” is insufficient.

---

## 7. Exact workflow definition

Register exactly one production definition:

```text
(MODEL_ACTIVATE, request_version=1, recovery_policy_version=1)
resource: runtime-active
```

All steps use implementation version 1 initially.

### Step 1 — `resolve_candidate`

- Phase: `prepare`; no externally visible mutation.
- Validate closed request, installed row, file identity/layout, runtime settings,
  fit, thermal latch, and expected revision.
- Output `CandidateRuntimeV1`.
- Probe: recompute/compare identity; `COMPLETE` only for exact evidence.
- Failure: `FAILED_SAFE`; release lease.

### Step 2 — `capture_prior`

- Phase: `prepare`; read-only.
- Capture desired config, handoff, known-good, component, and service state.
- If active, require exact health and bounded inference before accepting it as
  restoration evidence.
- Output `PriorRuntimeSnapshotV1`.
- Failure before candidate mutation: `FAILED_SAFE`.

### Step 3 — `commit_candidate_config`

- Phase: `commit-config`; externally visible; critical.
- One UoW CAS against `expected_runtime_revision` writes settings and
  `runtime_config`, then increments revision exactly once.
- It **does not publish the handoff**.
- Probe exact candidate config/revision:
  - exact candidate -> `COMPLETE`;
  - exact prior -> `ABSENT`;
  - any third revision/config -> `UNCERTAIN_MANUAL`.
- Compensation delegates to idempotent prior restoration.

### Step 4 — `publish_candidate_handoff`

- Phase: `publish`; externally visible; critical.
- Render from the committed candidate revision and publish atomically through
  `RuntimeHandoffRenderer`.
- Probe validates the full payload, not only the 16-character fingerprint.
- On interrupted `RUNNING`, perform the composite recovery decision in §8.
- Compensation delegates to the same restoration target.

### Step 5 — `restart_candidate`

- Phase: `restart`; externally visible; critical.
- Re-stat artifact identity immediately before restart.
- Call only `server.restart_service`; never enable the unit.
- Probe first: if exact candidate is already healthy and can infer, return
  `COMPLETE` and never restart twice.
- If exact prior remains active after an interrupted restart intent, classify
  `REVERTIBLE`; do not guess whether systemd consumed the candidate handoff.
- Compensation delegates to prior restoration.

### Step 6 — `verify_candidate_health`

- Phase: `verify-health`; critical but non-mutating.
- Use bounded server health polling and require model/context/slots identity.
- Progress can report bounded poll count/phase, never response bodies.
- Probe/execute may repeat safely; output sanitized health evidence.

### Step 7 — `verify_candidate_inference`

- Phase: `verify-inference`; critical but non-mutating.
- Run one bounded token-generation probe.
- Output only boolean/token-count/timing bucket identity evidence.
- A timeout or malformed response begins restoration; no completion text is
  persisted.

### Step 8 — `promote_known_good`

- Phase: `promote`; externally visible; critical.
- In one UoW, re-read candidate config revision and write the exact verified
  fingerprint/component/model/context/slots/settings to `known_good_runtime`.
- Do not call `current()` through a second connection inside the write UoW.
- Probe exact row:
  - exact candidate -> `COMPLETE`;
  - exact captured prior -> `ABSENT` and safe to execute;
  - third value -> `UNCERTAIN_MANUAL`.
- Compensation restores the captured prior known-good row as part of the
  aggregate restoration.

### Completion

Only after all eight rows are `VERIFIED` may the engine enter its final
`COMMITTING` transition, persist `SUCCEEDED/ALL_STEPS_VERIFIED`, and release
`runtime-active`. Result detail includes operation ID, model alias, candidate
revision, context, slots, fingerprint, and stable verification codes.

---

## 8. Mandatory first red test and takeover policy

The first production workflow test reuses the Session 5B crash harness and
must land before the happy path.

### Scenario

1. Seed prior model A as desired, handed off, active, healthy, inference-capable,
   and known-good.
2. Enqueue activation of model B.
3. Execute candidate config commit.
4. Publish model B's handoff.
5. Inject `SimulatedProcessDeath` after the atomic handoff replacement but
   before the step checkpoint.
6. Leave the row `RUNNING`, the lease owned by worker A, candidate revision in
   SQLite, and candidate handoff on disk. Fabricate no terminal/event cleanup.
7. Advance the injected clock past TTL and start worker B with a new lease
   generation.

### Required takeover inspection

Before another external effect, worker B reads:

- stored step intent and external-effect ID;
- candidate desired revision/config;
- full candidate handoff payload/fingerprint;
- captured prior snapshot/known-good fingerprint;
- actual systemd state;
- `/health`, `/v1/models`, `/props` identity;
- bounded inference result when a runtime answers.

### Closed outcomes

- **Candidate already complete:** all candidate config/handoff/runtime/health/
  inference evidence agrees. Checkpoint the interrupted publication from probe
  evidence, let later restart/verify probes checkpoint without effects, promote
  known-good once, and finish `SUCCEEDED`. Restart count remains exactly one
  overall; publication count remains one.
- **Prior still exact and healthy, or active reality is not the complete
  candidate:** classify the interrupted publication as `REVERTIBLE`, enter
  rollback, and invoke the aggregate prior restoration exactly once. Finish
  `FAILED_ROLLED_BACK` only after config, handoff, service state, health, and
  inference all match A. The user may retry B as a new operation.
- **Neither candidate nor prior can be proven:** persist bounded mismatch
  evidence, enter `RECOVERY_REQUIRED`, retain `runtime-active`, and perform no
  speculative restart.

Stale worker A must be fenced from every subsequent checkpoint, restart,
promotion, restoration, event, terminal write, and lease release.

---

## 9. Aggregate restoration protocol

Independent reverse callbacks cannot naively restart first and restore the
handoff later. Every candidate-visible step therefore delegates compensation
to one idempotent restoration protocol keyed by operation/restoration ID.

### Restoration order

1. Fence `runtime-active` and read durable prior/candidate evidence.
2. If restoration is already complete, checkpoint it without effects.
3. Restore prior desired config with exact revision handling.
4. Restore the prior known-good row exactly, including explicit absence.
5. Publish the prior handoff exactly, or remove only the operation-owned
   candidate handoff when prior evidence says `ABSENT`.
6. If prior state was `ACTIVE_VERIFIED`, restart through `server.py`, require
   exact health/model/context/slots, then bounded inference.
7. If prior state was `STOPPED`, stop through `server.py` and verify inactive;
   never enable/disable the service.
8. Persist sanitized restoration evidence and complete compensation.

Later reverse callbacks call `observe_restoration`; once the aggregate target
is proven they are no-ops. Thus the engine may honor generic reverse sequence
while the host performs the coherent restoration effect exactly once.

### Revision rules

- Candidate commit moves prior revision `R` to `R+1`.
- Restoration never rewinds the revision number. It writes prior content as a
  new committed revision (`R+2`) and records both `restored_from_revision` and
  `restored_content_of_revision`.
- Probes compare content + lineage; they do not assume numeric rollback.
- Any unrelated revision after candidate commit is an ownership conflict and
  becomes `RECOVERY_REQUIRED`, not a blind overwrite.

### Restoration failure

Persist only stable stage codes and exception class names. Keep
`runtime-active` leased as the durable barrier. The existing server-log tail
can be shown by the frontend, but raw log text is not copied into operation
events or error detail.

---

## 10. Production adapter changes

### 10.1 Runtime configuration primitives

Refactor `RuntimeConfigurationService` into narrow operations usable by the
adapter:

- pure `preview/resolve`;
- exact-revision `commit_candidate` with no handoff side effect;
- exact-lineage `restore_content`;
- one-UoW `promote_known_good(candidate_evidence)`;
- one-UoW `restore_known_good(prior_row_or_none)`;
- exact `observe_config`/`observe_known_good` reads.

Remove `ApplyResult.handoff_published/handoff_error` from the activation path.
Handoff publication is its own durable step. Fix nested reads in
`promote_known_good`; all source data for a promotion is read and written on
the same connection.

### 10.2 Runtime handoff observation

Add a strict, read-only observation method that validates:

- JSON object/schema version;
- complete required key set and bounded types;
- config revision and runtime fingerprint;
- model ID, canonical path, context total, slots, port, and llama.cpp path.

Malformed or partial content is `UNCERTAIN_MANUAL` unless the adapter can
prove it is the operation's own replaceable candidate. Preserve atomic write
and directory fsync behavior supplied by the filesystem helper.

### 10.3 Server observations

Keep command construction in `server.py`. Add/adjust typed functions so the
adapter can:

- observe service active/inactive without mutation;
- restart/stop without changing enablement;
- poll health with an injected monotonic clock/deadline and bounded interval;
- compare `/v1/models` and `/props` to expected model/context/slots;
- run bounded one-token inference without returning completion content to the
  operation layer;
- retrieve a sanitized failure class/code separately from the optional UI log
  tail.

Tests use a fake adapter/world and never invoke systemd, Podman, Vulkan, or a
real HTTP server. A separate adapter construction test proves all production
calls route through `server.py`.

---

## 11. Composition and foreground execution host

`Application._wire_services` constructs:

1. one `ActivationHostAdapter`;
2. one frozen `WorkflowRegistry` containing production `MODEL_ACTIVATE v1`;
3. one `EnqueueService`;
4. an `ExecutionEngine` factory that creates a fresh worker identity per
   foreground execution attempt;
5. one `ActivationCommandService` published as `application.activation`.

`ActivationCommandService.activate(request)`:

- refuses/recovers an existing active `runtime-active` activation according to
  durable state before creating a conflicting request;
- enqueues atomically;
- calls `execute_one(operation_id)` in the current process;
- reads the durable terminal row/result;
- returns a typed result containing operation ID and stable outcome;
- refreshes no caller-owned draft and performs no second restart.

No `Worker.run_until_idle()` starts during composition. No thread outlives the
frontend process. Recovery of an interrupted activation is explicit: the next
activation entry first resumes the older operation by ID; it never abandons an
expired nonterminal row and jumps to a new candidate.

`RECOVERY_REQUIRED` is returned as a barrier with its operation ID. Session 6C
adds the human recovery command and Activity surface; Session 5C does not
silently release it.

---

## 12. Existing frontend cutover

### 12.1 `model_manager.py`

Keep public function signatures needed by callers, but make production
behavior a thin typed adapter:

- `switch_model` -> activation command;
- `change_context` -> activation command with current model;
- `change_parallel_slots` -> activation command with current model/context;
- `register_and_switch_local` may register through the existing install
  service, then activates through the same command.

Delete production `_apply_legacy_or_raise`, `restart_with_rollback`, and the
“service or legacy fallback” branch. If small unit tests require pure fit
calculation, test that as a separate pure helper; do not retain a second
restart implementation in test-shaped production code.

The `wait_for_health=False` bypass must not permit unverified activation. Remove
it from production behavior or reject it explicitly; operation success always
requires health and inference.

### 12.2 CLI/chat/dashboard behavior

Preserve current commands and labels. They receive a terminal result from the
activation command and refresh their disposable read model once afterward.

- No caller commits `current_model`, `current_ctx`, or `optimizations`.
- No caller calls `restart_service` for an activation.
- Chat slash commands use the same operation as CLI/dashboard.
- GUI work stays in its existing action/task mechanism; no Activity page is
  introduced.
- A rolled-back failure says the prior model remains active.
- Recovery-required output includes the operation ID and advises repair; it
  does not claim the candidate is running.

Remove model/context/slot keys from `FRONTEND_COMMIT_KEYS` once all their
callers are operation-backed. Add AST/import guards preventing those generic
commits from returning.

### 12.3 Setup path

If setup starts a model through the same existing model-manager entry, it uses
the durable activation command. Do not redesign the wizard or create a child
acquisition operation. First-run stopped-state rollback follows §5.3/§9.

---

## 13. Recovery classification table

| Interrupted step | Complete | Absent/safe retry | Revertible | Uncertain |
| --- | --- | --- | --- | --- |
| Resolve candidate | exact identity output | no output | artifact changed before mutation -> fail safe | path escapes root / ambiguous layout |
| Capture prior | complete validated snapshot | no snapshot, no candidate mutation | n/a | active prior cannot be identified |
| Commit config | exact candidate config/revision | exact prior content/revision | candidate plus incompatible active reality | unrelated revision |
| Publish handoff | full exact candidate + complete candidate runtime | exact prior handoff/config | candidate handoff with prior/incomplete runtime | malformed/third-party payload |
| Restart candidate | exact candidate health + identity + inference | restart provably not begun and prior exact | prior or partially started runtime | service/model identity ambiguous |
| Verify health | exact candidate health/identity | bounded retry allowed | prior answers or candidate fails | conflicting endpoint identity |
| Verify inference | exact bounded success evidence | bounded retry allowed | candidate cannot infer | response identity cannot be trusted |
| Promote known-good | exact candidate row | exact captured prior row | candidate row with later restoration needed | third-party row |
| Restore prior | exact prior target | restoration not begun | partial operation-owned restoration | third-party revision/handoff/runtime |

Automatic recovery never converts `UNCERTAIN_MANUAL` to a retry merely because
the lease expired.

---

## 14. Test plan

### 14.1 Generic engine corrections

1. critical-state entry wins cancellation race;
2. cancellation wins before critical entry and no effect runs;
3. critical verification remains `COMMITTING` and refuses cancellation;
4. critical failure enters rollback legally;
5. mixed per-step implementation versions persist;
6. derive input runs once and reclaim uses stored input/effect ID;
7. checkpoint event output is an object and stays bounded;
8. prior verified effects are reconstructed after process death;
9. `COMPENSATING` takeover probes before repeating;
10. restoration effect death converges without a duplicate.

### 14.2 Request/preflight

- valid model/context/slot requests round-trip canonically;
- missing/unknown/extra/wrong-type fields fail;
- secret-like fields fail before persistence;
- stale runtime revision is `FAILED_SAFE`/conflict before mutation;
- thermal `STOPPED` blocks every entry surface;
- missing/quarantined/fused/MAX/invalid-layout artifacts fail;
- canonical path outside injected model roots fails;
- file replacement after resolution fails before restart;
- context and slot bounds fail;
- NO-FIT fails and TIGHT remains a warning;
- streaming digest inspection has a bounded read-size assertion.

### 14.3 Happy path

- model A -> B: one config commit, one publication, one restart, one health,
  one inference, one promotion;
- context-only activation uses the same workflow;
- slot-only activation uses the same workflow;
- exact candidate already active checkpoints from probes without restart;
- stopped prior -> successful initial activation;
- known-good row contains exact fingerprint/component/context/slots;
- terminal success is after inference and promotion events;
- lease releases only with `SUCCEEDED`.

### 14.4 Candidate-failure and rollback

- restart failure restores A;
- health timeout restores A;
- wrong `/v1/models` identity restores A;
- wrong `/props` context or slots restores A;
- inference timeout/malformed result restores A;
- promotion failure restores prior known-good/runtime;
- stopped prior returns to stopped;
- restoration config/handoff/restart/health/inference failure enters
  `RECOVERY_REQUIRED` and retains the lease barrier;
- rolled-back terminal detail contains no raw exception or server content.

### 14.5 Full crash matrix

Inject before and after each protocol boundary for Steps 3–8:

```text
before critical-state entry
after critical-state entry
after step intent
after external effect
before checkpoint
after checkpoint
before verification
after verification
before critical-state exit
```

Also inject for aggregate restoration:

```text
before restoration probe
after config restore
after known-good restore
after handoff restore
after service restore effect
after health restore
after inference restore
before compensation checkpoint
after compensation checkpoint
before rollback terminal
```

After every death, instantiate a new engine/adapter against the same SQLite DB
and fake world, advance only the injected clock, take over the expired lease,
and drive to a closed outcome. Assert:

- config content and revision lineage;
- full handoff payload/fingerprint;
- actual active model/context/slots or stopped state;
- health and inference evidence;
- known-good row;
- operation/step states and attempts;
- lease generation and stale-owner fencing;
- effect counts (publication/restart/promotion/restoration at most once per
  intended postcondition);
- no sleeps and no fabricated terminal after `BaseException`.

### 14.6 Concurrency

- two activation enqueues contend on `runtime-active`; only one effects;
- a stale expected runtime revision loses before commit;
- a second frontend first resumes an expired interrupted activation rather
  than leapfrogging it;
- active lease returns busy without mutation;
- `RECOVERY_REQUIRED` blocks after TTL;
- unrelated fake resources still execute independently;
- stale worker cannot restore or promote after takeover.

### 14.7 Frontend and architecture

- CLI `switch`, chat `/model`, chat `/ctx`, slot command, and dashboard action
  all reach the same activation command;
- exactly one restart occurs from each surface;
- no frontend generic commit follows success;
- no legacy fallback/restart helper remains callable in production;
- only `server.py` contains the service-control command strings;
- `operations/` has no host/frontend imports;
- composition starts no worker/thread/host effect;
- source and editable installs compose the same registry.

### 14.8 Privacy and bounds

- secret canaries absent from SQLite byte dump, events, results, logs, handoff,
  and constructed argv;
- inference prompt/response content absent from durable evidence;
- raw `URLError`, subprocess stderr, and server log lines absent;
- operation request/result/detail limits enforced;
- malicious model alias/path cannot escape roots or become shell text.

---

## 15. Commit boundaries

Keep each boundary independently green and reviewable.

### Commit 1 — freeze the Session 5C plan

```text
docs(R3.3): define durable model activation conversion
```

This document, sequencing pointers, and AGENTS handoff only. No code.

### Commit 2 — correct executor production gaps

```text
fix(R3.3): make critical and compensation recovery durable
```

ADR correction, red tests, critical-state cycle, durable compensation resume,
effect reconstruction, intent cleanup, per-step versions, bounded evidence.
No production adapter.

### Commit 3 — define activation v1 workflow and fake adapter

```text
feat(R3.3): define versioned model activation workflow
```

Typed request/evidence/port, exact steps, registry tests, fake activation world,
mandatory handoff-death red test. No production server calls.

### Commit 4 — add production activation adapter

```text
feat(R3.3): adapt runtime activation effects
```

Runtime config primitives, strict handoff observation, server typed seams,
production adapter, composition tests. Still no frontend cutover if the old
path remains reachable.

### Commit 5 — remove synchronous activation and cut over callers

```text
refactor(R3.3): route model activation through operations
```

Activation command service, model-manager/CLI/chat/dashboard/setup cutover,
legacy synchronous path deletion, frontend commit-key removal, architecture
guards. This commit may not land with both implementations callable.

### Commit 6 — complete crash/security matrix and evidence

```text
test(R3.3): complete activation crash and rollback matrix
docs(R3.3): record Session 5C evidence and 6A handoff
```

Repeated stress, privacy/path tests, docs/AGENTS truth pass, exact exit evidence.
No acquisition, update, Activity, or operation CLI implementation.

Do not squash migration history, amend migration 003, or combine Session 6
work into these commits.

---

## 16. Verification cadence

At every commit:

```bash
PYTHONPATH=. .venv/bin/pytest -q <focused tests>
python3 -m compileall -q bc250_llm_mode tests
git diff --check
```

At Commit 2 and every later boundary, run the complete 5B operation suite so a
real workflow never weakens generic recovery.

At the final gate:

```bash
PYTHONPATH=. .venv/bin/pytest -q
.venv/bin/pytest tests --collect-only -q
python3 -m compileall -q bc250_llm_mode tests
git diff --check
```

Also run:

- focused activation/crash/concurrency suite at least 20 times, no sleeps;
- source/editable-install collection parity;
- clean wheel build/install and console-entry smoke;
- launcher behavioral test;
- architecture/import guards;
- SQLite foreign-key/query-only contract tests;
- tracked-tree status preserving all user-owned `scripts_audit/` files.

The collection hook is authoritative; never infer counts from dots or carry
448 forward after tests are added.

---

## 17. Session 5C exit gate

### Architecture

- One registered production `MODEL_ACTIVATE v1` workflow.
- One production adapter and one activation command service.
- One service owner (`server.py`).
- No synchronous activation orchestrator or production legacy fallback.
- No frontend generic model/context/slot commit.
- No background worker/host effect during composition.
- No host/frontend imports under `operations/`.
- Migration 003 remains valid and JSON remains read-only backup.

### Correctness

- Mandatory post-handoff/pre-checkpoint death test passes both candidate-complete
  and prior-restoration branches.
- Candidate artifact, thermal latch, fit, and revision gates precede mutation.
- Exact config/handoff/health/model/context/slots/inference evidence precedes
  known-good promotion and success.
- Context-only and slots-only changes use the identical durable path.
- Stopped prior state restores to stopped.
- Candidate failure restores prior exactly once or enters recovery required.
- Every crash point converges under takeover; stale workers remain fenced.
- Cancellation is never accepted during critical activation/restoration.
- `RECOVERY_REQUIRED` retains the conflict barrier after TTL.

### Security and boundedness

- No secrets, prompts, generated text, raw exception text, server bodies, or
  logs are durable.
- File hashing/inspection is bounded-memory and paths remain inside injected
  roots.
- No shell interpolation or caller-provided service/URL/path command input.
- All probes and health/inference calls are time-bounded.

### Product behavior

- Existing CLI, chat, GUI, and setup model/context/slot actions still work.
- They report success only after inference and known-good promotion.
- A rollback says the prior model is active; uncertain state says repair is
  required and includes the operation ID.
- Desktop next boot and service enablement policy are unchanged.
- No Activity UI, operation command, detach, or auto-start was added.

### Evidence

- Full authoritative suite green.
- 20/20 focused crash/concurrency iterations green with no sleeps.
- Compile and diff checks clean.
- Source/editable parity and clean wheel install pass.
- Tree clean except untouched user-owned `scripts_audit/` files.

---

## 18. Session 6A handoff

After this gate, stop. Session 6A converts model acquisition/import/validation
to durable operations. Its first red test should crash after final artifact
publication but before checkpoint and prove the next executor recognizes the
exact content digest without downloading, copying, or publishing twice.

Session 6A may then normalize the temporary activation-time artifact identity
evidence into installation trust columns/records: content digest, byte size,
source revision, GGUF metadata, validator version, staging/quarantine/final
ownership, and deduplication. It must not weaken Session 5C's activation gate.

---

## 19. Exact executor checklist

1. Confirm HEAD includes Session 5B through `f27afec`; preserve
   `scripts_audit/` untouched.
2. Run full/collection/compile/diff baseline and record authoritative counts.
3. Read ADR 002, the post-R2 plan, Session 5B plan, and this plan completely.
4. Land red tests for critical-state cancellation and compensation takeover.
5. Record the narrow ADR correction; fix only generic engine gaps in §3.
6. Prove migration 003 unchanged and the full 5B suite green.
7. Define frozen activation v1 request/evidence/port types.
8. Build the fake activation world and exact eight-step workflow.
9. Land the mandatory handoff-death test before the happy path.
10. Complete candidate-complete, prior-restored, and uncertain takeover cases.
11. Refactor runtime config into commit/observe/restore/promote primitives.
12. Add strict handoff observation and typed server observation seams.
13. Implement the one production activation adapter and composition wiring.
14. Add foreground enqueue/execute/terminal mapping; do not auto-start worker.
15. Delete the synchronous orchestrator/fallback while cutting over all callers.
16. Remove generic frontend commit rights for model/context/slots.
17. Run rollback, full crash, concurrency, privacy, and architecture matrices.
18. Run full/source/editable/wheel/compile/diff verification and 20x stress.
19. Update AGENTS/docs with exact commits/counts/evidence.
20. Stop at 5C and hand off Session 6A; add no Activity/operation CLI/update.

### Session report format

- HEAD and tracked/untracked status;
- commits in order with R3.3 plan IDs;
- authoritative collected/passed/skipped counts and invocation;
- ADR corrections made (or explicitly none beyond §3.1);
- exact workflow request/recovery/step versions;
- adapter ownership map;
- mandatory handoff-death evidence and effect counts;
- rollback/restoration and recovery-required evidence;
- crash points and repeated-run count;
- frontend cutover and deleted legacy paths;
- privacy/path/timeout evidence;
- source/editable/wheel/compile/diff results;
- deliberately deferred Session 6/Activity/operation-command work;
- exact Session 6A first red test and stop condition.
