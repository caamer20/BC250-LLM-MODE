# BC250 LLM MODE — Session 5B Executor Implementation Plan

**Status:** Ready for implementation after Session 5A  
**Plan IDs:** R3.2 / POST-R2 Session 5B  
**Baseline:** `63f5fab`, version `0.9.0.dev0`, 402 tests collected and passing  
**Predecessor:** Session 5A (`3aa22cc` → `63f5fab`)  
**Sequencing authority:** `POST_R2_PRODUCTION_IMPLEMENTATION_PLAN.md`  
**Requirements authority:** `MASTER_IMPLEMENTATION_PLAN.md` and
`R2_EXIT_AND_OPERATION_ENGINE_PLAN.md`  
**Durable contract:** `docs/adr/002-durable-operations.md`

This document turns the Session 5B milestone into an implementation-ready
slice. It does not replace ADR 002 or expand the product boundary. Where this
plan finds an executor-facing ambiguity, the correction must be recorded as a
narrow ADR 002 clarification before code depends on it.

---

## 1. Objective and stop boundary

Session 5B builds a deterministic, process-agnostic operation executor against
the real SQLite repositories and fake external effects. It proves that durable
intent, leases, recovery classification, cancellation, compensation, and
process-death recovery work before any real model, systemd, Podman, network,
filesystem-publication, or runtime-update adapter is converted.

The session is successful when a fake reversible workflow can:

1. enqueue atomically with immutable typed request data and ordered steps;
2. be claimed by exactly one worker;
3. persist step intent before an external effect;
4. survive simulated process death after that effect but before checkpoint;
5. be reclaimed after lease expiry by a new worker;
6. inspect the real postcondition and checkpoint without repeating the effect;
7. honor cancellation only at declared safe points;
8. compensate verified/effected steps in reverse order;
9. distinguish `FAILED_SAFE`, `FAILED_ROLLED_BACK`, and
   `RECOVERY_REQUIRED` using durable evidence;
10. remain independent of tkinter, CLI rendering, and production host effects.

### Hard stop

Stop after the fake-workflow engine, worker lifecycle, recovery harness, and
5B evidence are green. Do **not** implement or expose:

- model activation steps (Session 5C);
- a production systemd/llama.cpp/Podman/filesystem/network host adapter;
- an operation CLI command or chat slash command (Session 6C);
- an Activity GUI or notification surface (Session 6C);
- a background system service or privileged helper (R5);
- model acquisition or llama.cpp update workflows (Sessions 6A/6B);
- schema migration 004 unless a separately reviewed, unavoidable schema
  defect is proven. The plan below requires no schema change.
- parent/child execution or workflow fan-out; the 5B harness uses one
  standalone operation and leaves ADR 002's later single-child policy dormant.

The worker class may exist and be fully tested, but 5B does not silently start
a production thread from `Application.compose()`.

---

## 2. Invariants carried into 5B

Every implementation decision must preserve these project invariants:

- **One service owner:** only `server.py` may touch `bc250-llm.service`.
- **Fit gate:** no future activation can bypass `calculate_fit`; 5B fakes do
  not weaken or duplicate it.
- **Thermal authority:** an executor can read the latch but can never clear or
  downgrade it.
- **Desktop next boot:** no operation enables LLM auto-start.
- **Single SQLite truth:** no JSON write, compatibility facade, or dual write.
- **Injected paths:** effects receive `AppPaths` or typed fake paths; no
  `Path.home()` fallback.
- **Secrets:** no secret-like key, credential value, raw exception, raw
  stdout/stderr, or credential-bearing argv reaches SQLite, events, or logs.
- **No shell interpolation:** effect interfaces accept typed requests or argv
  arrays, never user-controlled shell text.
- **Known-good honesty:** success means a declared postcondition was verified;
  thread completion or command exit zero is never enough.
- **Lease ownership:** a stale owner cannot checkpoint, transition, heartbeat,
  compensate, terminate, or release after its lease generation is replaced.
- **No fake continuation:** a frontend-owned executor does not claim to keep
  working after its process exits.

---

## 3. Session 5B entry audit and contract clarifications

Session 5A correctly stopped before an executor existed. Four behaviors become
observable only when the executor is added. Resolve them with red tests before
the engine core.

### 3.1 Atomic lease-generation fencing

`StepRepository` and `OperationRepository` currently enforce state CAS, while
`LeaseRepository` independently enforces owner/revision CAS. State CAS alone
does not fence a worker whose expired lease was taken over: a stale worker can
still hold a valid old operation/step state in memory.

Add a repository-level ownership assertion used in the **same write unit of
work** as every owner-sensitive mutation:

```text
assert_owned(resource_key, operation_id, owner, lease_revision, now)
```

It proves all of the following from the current row:

- resource key matches;
- operation ID matches;
- owner token matches;
- lease revision matches;
- lease has not expired at the injected current time.

The executor must assert all leases required for the current step before:

- step start/reclaim/checkpoint/verify/fail;
- operation transition or terminal result;
- progress/event publication performed as owner output;
- compensation start/completion;
- lease release.

The assertion and mutation commit together. A lost fence raises a stable
`OperationConflict`/`LostLease` result and the stale worker performs no more
durable writes or external effects.

### 3.2 Durable cancellation timestamp

`request_cancel()` transitions state but must also set
`cancel_requested_at` in the same CAS update. Add tests proving:

- the first accepted request sets the injected timestamp;
- a repeated request preserves the original timestamp and revision;
- a stale revision changes neither state nor timestamp;
- cancellation from `COMMITTING` and `ROLLING_BACK` is refused with a stable
  “critical section” result because ADR 002 deliberately has no deferred
  cancellation state from those phases;
- no cancellation is described as accepted until `CANCEL_REQUESTED` is
  durably committed.

### 3.3 `RECOVERY_REQUIRED` is a durable resource barrier

ADR 002 requires `RECOVERY_REQUIRED` to block conflicting operations. A normal
TTL expiry must not let another worker take over that resource and mutate an
unproven external state.

No new schema is required. Keep the operation's lease rows as resource-fence
evidence and make acquisition refuse an otherwise expired lease whose owning
operation is `RECOVERY_REQUIRED`. Only a later explicit recovery/repair command
may resolve and release that barrier. Session 5B tests only the block; the user
recovery surface belongs to Session 6C.

Other terminal states release their currently owned leases atomically with the
terminal transition. A process death between external failure and terminal
publication remains non-terminal and follows ordinary expiry/takeover.

### 3.4 Resource ordering clarification

ADR 002 says simultaneous resource acquisition is lexicographic, but its
`RUNTIME_UPDATE` example lists `runtime-installation` before
`runtime-active`; Python/string lexicographic order is the reverse.

Record this narrow erratum in ADR 002 before implementing the generic lease
guard:

- any set of leases held simultaneously is acquired as
  `sorted(resource_keys)`;
- a runtime update may hold only `runtime-installation` during its isolated
  build phase;
- before the active-runtime boundary it must release/reacquire the complete
  simultaneously held set in lexicographic order, or acquire both atomically
  in that order;
- it may never acquire a lexicographically lower resource while retaining a
  higher resource.

The 5B fake workflow must declare two resources in reverse order and prove the
engine acquires them in sorted order. Real runtime behavior remains deferred
to 6B.

### 3.5 Complete the repository surface promised by 5A

Add the already-planned read needed by recovery:

```text
LeaseRepository.list_expired(now, limit)
```

It must be deterministic, bounded, typed, and never commit. Also remove the
duplicate `acquired_at = excluded.acquired_at` assignment in the lease upsert
while preserving behavior. These are narrow repository corrections, not an
executor hidden inside the persistence layer.

### Entry-correction stop gate

- Red tests demonstrate every issue before its correction.
- Migration 003 and the four tables remain byte-for-byte/DDL equivalent.
- Existing 402 tests remain green.
- No workflow/executor class lands in this boundary.

---

## 4. Architecture and module boundaries

Use the following package shape. Exact private helper names may vary; public
ownership may not.

```text
bc250_llm_mode/operations/
  __init__.py       deliberately small public exports
  model.py          existing durable enums/records/exceptions
  validation.py     existing persistence validation/bounds
  repositories.py   existing SQL boundary + entry corrections
  workflow.py       typed workflow/step protocols and registry
  engine.py         single-operation deterministic executor
  recovery.py       interruption classification and decisions
  progress.py       throttled progress policy/reporter
  worker.py         bounded claim/run/shutdown loop

tests/operations/
  __init__.py
  fakes.py           durable fake world, fake clock/IDs, barriers, faults
  helpers.py         database/engine harness and invariant assertions

tests/test_operation_workflow.py
tests/test_operation_engine.py
tests/test_operation_cancellation.py
tests/test_operation_recovery.py
tests/test_operation_worker.py
```

Do not create a second SQL layer. All persistence continues through the
existing repositories and `UnitOfWorkFactory`. No connection crosses an
external effect, wait, callback, or thread boundary.

### Dependency direction

```text
frontend (later)
    -> operation command/query service (later 5C/6C)
        -> worker / execution
            -> workflow protocols + registry
            -> repositories through fresh units of work
            -> typed effect ports

workflow/effect code MUST NOT import:
    tkinter, gui.*, chat, __main__, sqlite3, systemd helpers, or global state
```

The deterministic executor is the center. `worker.py` decides which operation
to run and when to stop; it does not contain step semantics.

---

## 5. Typed workflow contract

### 5.1 Workflow identity and registry

The registry key is exactly:

```text
(OperationType, request_version, recovery_policy_version)
```

Registration is immutable after construction. Duplicate keys fail during
composition/test setup. Lookup never falls back to “closest” or latest.

An unrecognized durable row is not deleted or guessed:

- `QUEUED`/`PREPARING` rows transition to `PAUSED` with stable code
  `WORKFLOW_VERSION_UNAVAILABLE` when allowed;
- an already effecting state is left untouched and reported as requiring a
  newer application/recovery policy; do not invent a transition outside ADR
  002;
- no request payload is decoded before its registry version is known.

### 5.2 Request decoding

Each workflow supplies a typed, immutable request decoder. The public enqueue
path performs:

1. registry lookup;
2. full type/version-specific validation;
3. stable identity normalization (IDs/digests/revisions, not display aliases);
4. generic secret/bounds validation;
5. one transaction creating operation, ordered steps, and queued event.

The repository remains defensive, but callers never enqueue an arbitrary
dictionary directly through the engine.

### 5.3 `WorkflowDefinition`

The protocol must declare at least:

- operation type, request version, recovery-policy version;
- typed request decoder;
- stable ordered `StepDefinition` tuple;
- operation target summary with no secret/path leakage;
- resources required by each step;
- operation-level preflight/thermal policy;
- terminal result mapping;
- whether automatic recovery is permitted for each recovery class.

Workflow definitions are stateless and reusable. Per-operation mutable data
lives in the durable rows or a short-lived `ExecutionContext`.

### 5.4 `StepDefinition`

Every step declares:

- stable `step_key`, sequence, and implementation version;
- phase name and progress unit;
- typed input derivation from request/prior durable outputs;
- resources required while it acts;
- whether it can produce an externally visible effect;
- whether cancellation is safe before the step;
- whether its effect section is critical/non-interruptible;
- `probe()` — read-only postcondition inspection;
- `execute()` — effect through an injected port;
- `verify()` — read-only proof of the promised postcondition;
- optional `compensate()` and restoration verification;
- recovery classification for an interrupted `RUNNING` step;
- bounded stable error mapping.

Retry never calls `execute()` merely because the step row is `RUNNING`.
`probe()` and recovery classification decide first.

### 5.5 Recovery vocabulary

Represent ADR 002 §9 as a closed enum, not strings:

```text
ABSENT
COMPLETE
PARTIALLY_RESUMABLE
DISCARDABLE
REVERTIBLE
UNCERTAIN_MANUAL
```

A recovery decision is typed and includes only sanitized evidence:

- classification;
- stable reason code;
- next action (`EXECUTE`, `VERIFY`, `RESUME`, `DISCARD_AND_RETRY`,
  `ROLL_BACK`, `PAUSE`, `REQUIRE_RECOVERY`);
- bounded detail safe for an event;
- optional recovered output that passes normal output validation.

---

## 6. Fake-workflow harness

The fake must be realistic enough to prove recovery without importing any
production host integration.

### 6.1 Durable fake world

Use a temporary-directory effect store shared by separately constructed
executor instances. Do not keep the only truth in a Python object that
survives the test.

Suggested artifacts:

```text
fake-world/
  desired.json        requested fake value/revision
  active.json         externally active value + effect application count
  prior.json          captured reversible value
  publication.json    atomic commit marker
```

Writes use the project's atomic filesystem helpers. The active marker stores
an application count so recovery tests can prove the effect ran exactly once.
Files contain no secrets and remain under the injected temporary profile.

### 6.2 Fake workflow steps

Use a small reversible workflow that exercises all engine classes:

1. `capture_prior` — read-only preparation and durable output;
2. `apply_effect` — writes `active.json`, idempotency key persisted before
   execution, reversible;
3. `verify_effect` — read-only verification;
4. `publish` — atomic critical section writing `publication.json`;
5. `verify_publication` — final read-only proof.

The workflow is registered under `MODEL_ACTIVATE` version 1 only inside the
test harness. Do not add a fake operation type to the production enum or
schema.

Declare two fake resource keys in unsorted order so acquisition ordering is
observable. The fake effect adapter records acquisition/effect order through
bounded structured test evidence, not production logs.

### 6.3 Deterministic providers

Fakes provide:

- a manually advanced UTC clock producing ADR-formatted timestamps;
- deterministic operation and worker IDs;
- deterministic external-effect IDs;
- barriers/events for two-worker tests;
- a persistent effect-call counter;
- a named crash/fault injector;
- progress pulses under direct test control;
- no `sleep()` and no wall-clock ordering assumptions.

---

## 7. Transaction and effect protocol

The central safety rule is: **commit intent, close the transaction, perform
the effect, inspect reality, then atomically checkpoint evidence.**

### 7.1 Enqueue transaction

One write unit:

1. decode and validate request;
2. create `QUEUED` operation;
3. add every ordered `PENDING` step;
4. append `OPERATION_QUEUED`;
5. commit once.

Failure leaves no operation, steps, or event.

### 7.2 Claim transaction

One `BEGIN IMMEDIATE` write unit:

1. reload operation and verify non-terminal claimable state;
2. resolve exact workflow version;
3. acquire the resources required at the current boundary in sorted order;
4. transition `QUEUED → PREPARING` (or the ADR-permitted recovery state);
5. append claim event with worker ID fingerprint, never raw environment data;
6. commit once.

If any lease acquisition or CAS loses, the entire transaction rolls back.
No partial lease set remains.

### 7.3 Step-intent transaction

Before any effect:

1. assert every current lease fence;
2. derive and validate bounded step input;
3. assign stable external-effect ID/idempotency key;
4. transition step `PENDING → RUNNING`, increment attempts, persist intent;
5. transition operation phase/state if needed;
6. append `STEP_STARTED`;
7. commit and close.

Only after this commit may `execute()` run.

### 7.4 Effect and probe

- No database transaction remains open.
- The effect receives typed input, external-effect ID, and a pulse callback.
- Pulse may heartbeat/progress/check cancellation via fresh short UoWs.
- A critical step reports cancellation as deferred/refused, not accepted.
- Before a publication/commit effect begins, its intent transaction places the
  operation in `COMMITTING`; the closed transition table then prevents a
  cancellation race from being recorded as accepted.
- After the adapter returns, probe the external postcondition independently.
- Command success alone is not a postcondition.

### 7.5 Checkpoint transaction

After the effect is observed complete:

1. assert current lease fences;
2. reload operation/step and reject stale state;
3. validate sanitized output/evidence;
4. transition `RUNNING → CHECKPOINTED` and store output;
5. append `STEP_CHECKPOINTED` with decision evidence;
6. update meaningful progress/phase;
7. commit once.

Verification then runs read-only outside the transaction. A second fenced
transaction performs `CHECKPOINTED → VERIFIED`, appends `STEP_VERIFIED`, and
updates the operation phase.

### 7.6 Terminal transaction

For `SUCCEEDED`, `CANCELLED`, `FAILED_SAFE`, or `FAILED_ROLLED_BACK`:

1. assert owned lease fences;
2. validate required terminal evidence;
3. transition operation and append its terminal event;
4. release all owned leases;
5. commit once.

For `RECOVERY_REQUIRED`, transition and append exact safe evidence but retain
the resource rows as durable barriers. The acquisition repository refuses
their takeover even after TTL expiry.

---

## 8. Executor algorithm

Implement one synchronous deterministic primitive first:

```text
execute_one(operation_id, worker_id) -> ExecutionOutcome
```

It owns orchestration, not threads. Required behavior:

1. resolve exact workflow and immutable request;
2. claim or recover the operation with fenced leases;
3. revalidate workflow preconditions;
4. select the first non-verified ordered step;
5. honor accepted durable cancellation at a safe point;
6. for a new step, persist intent then execute/probe/checkpoint/verify;
7. for interrupted `RUNNING`, reclaim attempts then probe/classify before
   deciding whether execution is permitted;
8. for `CHECKPOINTED`, repeat only verification;
9. after each verified step, advance using a fresh operation revision;
10. on ordinary failure, decide safe failure versus compensation;
11. compensate in reverse effect order without honoring cancellation inside
    compensation;
12. publish an evidence-backed terminal result;
13. return a typed outcome, never frontend-formatted text.

The executor must reload after every externally concurrent boundary. It never
assumes its cached operation revision survived progress, heartbeat,
cancellation, or another worker.

### Stable executor outcomes

At minimum:

- completed/terminal record;
- skipped because another live owner holds a lease;
- paused with reason;
- lost lease/fenced out;
- recovery required;
- shutdown checkpoint reached;
- workflow version unavailable.

These are domain results or typed exceptions with stable codes. They do not
contain raw exception strings.

---

## 9. The first mandatory crash test

This test is the first executor acceptance test and must land before broad
success-path coverage.

### Arrange

1. Create a real temporary SQLite database through normal migrations/UoW.
2. Create a durable fake-world directory.
3. Enqueue the fake workflow with deterministic operation ID `op-001`.
4. Start worker `worker-a` with a 60-second lease.
5. Configure crash injection at `after_external_effect` for `apply_effect`.

### Act — first process

1. Claim operation/resources.
2. Persist `apply_effect` as `RUNNING`, attempts 1, with external-effect ID.
3. Commit intent.
4. Apply the fake external effect; `active.json` records application count 1.
5. Inject simulated process death **before** step checkpoint.

The death primitive must bypass ordinary exception compensation and `finally`
lease release. Use a test-only `BaseException`-class sentinel or an equivalent
harness abort that models abrupt process loss; do not model death as a normal
handled `Exception`.

### Assert after death

- operation remains non-terminal (`RUNNING`/appropriate effecting phase);
- step is `RUNNING`, attempts 1, no checkpoint timestamp/output;
- effect exists and application count is 1;
- worker-a lease remains present and un-released;
- no success/failure/rollback event was fabricated;
- SQLite connection can be reopened cleanly.

### Act — second process

1. Discard the first engine/worker objects.
2. Advance injected UTC clock beyond lease expiry.
3. Construct a new registry, engine, and worker `worker-b` from the same DB
   and fake-world paths.
4. Take over leases (revision increments).
5. Reclaim `RUNNING → RUNNING` (attempts becomes 2).
6. Probe `active.json`; classify `COMPLETE`.
7. Checkpoint recovered output exactly once without calling execute.
8. Verify, complete remaining steps, and terminate `SUCCEEDED`.

### Final assertions

- application count remains exactly 1;
- step has one durable checkpoint and ends `VERIFIED`;
- attempts is 2, proving recovery ownership was recorded;
- takeover lease revision is greater than worker-a's revision;
- stale worker-a cannot heartbeat, checkpoint, transition, or release;
- terminal event exists once and only after final verification;
- all releasable leases are gone;
- operation/steps/events and fake-world state agree;
- reopening and running recovery again is a no-op;
- no secret canary or raw exception appears in DB, event detail, or logs.

---

## 10. Recovery behavior by durable state

| Operation state | Recovery action |
| --- | --- |
| `QUEUED` | Claim normally if resources are available. |
| `PREPARING` | Revalidate exact request/workflow/resources; continue or `FAILED_SAFE`/`PAUSED`. |
| `RUNNING` | Reclaim lease/step, probe reality, classify, then execute/verify/resume/compensate/pause. |
| `VERIFYING` | Repeat read-only verification; never repeat the effect first. |
| `COMMITTING` | Inspect durable domain/publication state; only complete or produce an evidence-backed failure terminal. |
| `CANCEL_REQUESTED` | At next safe point cancel directly if no effect, otherwise enter rollback. |
| `ROLLING_BACK` | Reclaim and continue compensations from their own step checkpoints. |
| `PAUSED` | Automatic resume only when stored reason/policy permits; otherwise require explicit later command. |
| terminal except `RECOVERY_REQUIRED` | Never claim or modify. |
| `RECOVERY_REQUIRED` | Preserve barrier/evidence; report conflict for overlapping resources. |

### Recovery classification rules

- `ABSENT` → execute if policy allows and cancellation/shutdown do not block.
- `COMPLETE` → recover output, checkpoint, and verify; never re-execute.
- `PARTIALLY_RESUMABLE` → call typed resume path with the same effect ID.
- `DISCARDABLE` → remove only operation-owned staging, prove absence, then
  restart the step.
- `REVERTIBLE` → enter rollback and compensate from captured prior evidence.
- `UNCERTAIN_MANUAL` → if the uncertain state cannot affect safety, `PAUSED`;
  otherwise `RECOVERY_REQUIRED` with resource barrier.

Recovery is idempotent: running it twice after any completed recovery decision
does not add duplicate effects, terminal events, or compensations.

---

## 11. Cancellation and compensation

### 11.1 Cancellation acceptance

- Before start: `QUEUED → CANCEL_REQUESTED → CANCELLED`, no effects.
- At a safe boundary in `PREPARING`/`RUNNING`/`VERIFYING`/`PAUSED`: persist
  `CANCEL_REQUESTED`; the worker observes it from a fresh read.
- During a cooperative long effect: pulse observes cancellation, the adapter
  exits at its next declared safe chunk, then normal cancellation policy runs.
- During `COMMITTING` publication/restart and `ROLLING_BACK` compensation:
  cancellation is not accepted by the state machine; the critical action
  resolves first.
- During an in-flight read-only probe while the operation is `VERIFYING`, a
  cancellation request may be durably accepted, but it never interrupts the
  probe. The executor reloads at the following safe boundary and honors it
  without starting another effect.
- A UI/API response must distinguish accepted, already requested, already
  terminal, and unavailable-during-critical-section.

### 11.2 Compensation order

Compensate effectful steps in reverse sequence. For each step:

1. assert lease fence;
2. probe whether restoration is needed/already complete;
3. transaction: state → `COMPENSATING`, append event;
4. close transaction and perform typed compensation;
5. verify prior state outside the transaction;
6. fenced transaction: → `COMPENSATED`, persist bounded evidence/event.

Never honor cancellation during compensation. A crash during compensation is
recovered by probing restoration before repeating it.

### 11.3 Terminal selection

| Situation | Terminal |
| --- | --- |
| Failure before any effect, or absence proved | `FAILED_SAFE` |
| Cancellation with no effect | `CANCELLED` |
| Cancellation after all effects verified restored | `CANCELLED` |
| Ordinary failure after all effects verified restored | `FAILED_ROLLED_BACK` |
| Success after final postcondition verification | `SUCCEEDED` |
| Effect/restoration cannot be proven safe | `RECOVERY_REQUIRED` |

Do not use `FAILED_ROLLED_BACK` when nothing changed. Do not use `CANCELLED`
until restoration evidence exists.

---

## 12. Leases, heartbeat, and contention

### 12.1 Lease guard

Represent each acquired lease as an immutable proof containing resource key,
operation ID, owner, and lease revision. The proof is refreshed after
heartbeat/takeover and is required by executor mutation helpers.

### 12.2 Heartbeat

- Default TTL remains 60 seconds.
- Heartbeat threshold is injected and deterministic in tests.
- Cooperative long effects receive a pulse callback; each due pulse opens a
  fresh UoW, asserts ownership, renews all held leases, then closes.
- A lost heartbeat fence stops further effects/checkpoints by that worker.
- Heartbeat never changes operation progress by itself.

### 12.3 Contention

Test with barriers, not sleeps:

- two workers claim one queued operation: exactly one wins;
- two operations request the same resource: one runs, one receives a typed
  busy/skip result without changing state incorrectly;
- unrelated resources can make progress independently;
- reversed declaration order still acquires sorted keys;
- partial multi-resource acquisition rolls back completely;
- expired takeover increments generation;
- stale owner loses every guarded action;
- `RECOVERY_REQUIRED` blocks takeover after nominal expiry.

### 12.4 Shutdown

`Worker.request_shutdown()` stops new claims immediately. At the current
operation:

- finish a non-interruptible section;
- at the next safe point checkpoint current evidence;
- transition to `PAUSED` only if ADR permits and the external state is proven
  quiescent;
- release leases atomically with safe pause;
- otherwise leave durable intent for expiry/recovery without claiming success.

Tests drive an injected wake/barrier primitive. No production test waits for a
real TTL or uses `time.sleep()` to establish order.

---

## 13. Progress, events, errors, and privacy

### 13.1 Progress policy

Progress is monotonic within a phase. A phase change may reset its local
counter. Persist when any of these occurs:

- phase boundary;
- completion;
- injected minimum interval elapsed and meaningful delta reached;
- warning/cancellation/recovery decision requires a snapshot.

Unknown total uses `total=None` with a phase/summary and heartbeat. Never emit
a fake percentage. Progress writes use operation CAS and tolerate a concurrent
cancellation by reloading rather than overwriting it.

### 13.2 Events

Decision/result events are durable and never throttled:

- operation claimed/paused/resumed;
- step started/checkpointed/verified;
- recovery classification and selected action;
- cancellation accepted/honored;
- compensation start/result;
- lease lost/taken over;
- terminal result.

Do not append every fake chunk or subprocess line. Event cursor ordering, not
timestamps, is authoritative.

### 13.3 Stable failure mapping

Define bounded engine/workflow failures with stable code, safe summary,
sanitized detail, and mutation-possibility classification. Unexpected Python
exceptions map to a generic engine code and exception **class name only**;
raw `str(exc)`, repr, tracebacks, stdout/stderr, request payloads, and argv are
not persisted.

Application logs may retain developer diagnostics only after the existing
redaction path and size bound. Tests place secret canaries in exception text,
fake argv, request-like objects, and effect output and assert absence from DB,
events, returned results, and captured logs.

---

## 14. Required test matrix

Every test uses real temporary SQLite repositories/UoWs and fake effects.

### 14.1 Workflow/registry

- exact version resolves;
- duplicate registration rejected;
- unknown request/recovery version never guessed;
- typed decoder rejects invalid/mutable identity before insert;
- enqueue atomically creates operation, steps, and event;
- enqueue failure leaves none;
- step keys/sequences/implementation versions are stable and unique.

### 14.2 Success and safe failure

- no-op workflow success requires verification evidence;
- full reversible fake workflow succeeds;
- preflight failure → `FAILED_SAFE`;
- effect exception with probe proving absence → `FAILED_SAFE`;
- verification failure after mutation starts rollback;
- thread/function return alone never marks success.

### 14.3 Cancellation

- before claim;
- between steps;
- during cooperative cancellable effect;
- request timestamp/idempotency/stale revision;
- refused during `COMMITTING`;
- cancellation after mutation compensates then `CANCELLED`;
- compensation failure → `RECOVERY_REQUIRED`;
- repeated cancellation produces no duplicate terminal/event.

### 14.4 Crash injection

Inject at every common point:

```text
before_step_start
after_step_start
after_external_effect
before_probe
after_probe
before_step_checkpoint
after_step_checkpoint
before_step_verification
after_step_verification
before_compensation
after_compensation_effect
before_compensation_checkpoint
before_terminal_transition
after_terminal_transition
```

For each effecting step and compensation, assert recovery decision, effect
count, DB rows, lease ownership, fake files, events, next rerun behavior, and
absence of secret/path escape.

### 14.5 Recovery classes

- absent → execute once;
- complete → checkpoint without execute;
- complete/checkpointed → verify only;
- partial resumable → same effect ID resumes;
- discardable → only owned staging deleted, then retry;
- revertible → reverse compensation;
- uncertain but quiescent → `PAUSED`;
- uncertain unsafe → `RECOVERY_REQUIRED` barrier;
- second recovery pass is no-op.

### 14.6 Lease/concurrency

- active contention winner deterministic;
- two workers, one operation;
- two operations, one resource;
- unrelated resources;
- atomic multi-resource rollback;
- heartbeat extends lease;
- lost lease fences all stale mutations/effects;
- expired takeover generation;
- stale release refused;
- recovery-required expiry remains blocked.

### 14.7 Worker lifecycle

- bounded `run_once`/`run_until_idle` behavior;
- no work returns without mutation;
- shutdown stops claims;
- shutdown at safe point pauses/checkpoints honestly;
- shutdown during critical section resolves that section first;
- reopening with same DB/fake world recovers;
- no connection shared across threads/effects;
- no tkinter/CLI import from operation package.

### 14.8 Security/bounds

- secret canary in request, event, exception, fake argv, fake output, and log;
- oversize output/progress/detail rejected before persistence;
- path traversal cannot escape fake profile;
- raw exception never becomes terminal detail;
- event/progress throttling cannot drop terminal evidence.

Run focused crash and contention groups repeatedly (minimum 20 iterations in
the session evidence) without sleeps or flaky ordering.

---

## 15. Implementation slices and commit boundaries

Each boundary must be reviewable, green, and stop before the next. Tests land
with or before behavior.

### Commit 1 — contract errata and repository fences

Suggested subject:

```text
fix(R3.2): fence executor writes and recovery resources
```

Scope:

- ADR 002 lock-order clarification;
- lease `assert_owned` and bounded `list_expired`;
- cancellation timestamp CAS;
- recovery-required takeover refusal;
- remove duplicate lease-upsert assignment;
- repository red tests only; no executor.

Gate: existing and new repository/ADR contract tests green; schema unchanged.

### Commit 2 — workflow protocols and registry

Suggested subject:

```text
feat(R3.2): define versioned operation workflows
```

Scope:

- typed workflow/step/recovery contracts;
- immutable exact-version registry;
- typed request decoding/enqueue transaction;
- durable fake-world foundation;
- architecture import guards.

Gate: registry/enqueue tests green; no effect executes yet.

### Commit 3 — fenced executor core and first crash recovery

Suggested subject:

```text
feat(R3.2): execute and recover checkpointed workflows
```

Scope:

- deterministic `execute_one`;
- lease guard;
- intent/effect/probe/checkpoint/verify transaction protocol;
- interrupted-step reclaim;
- the mandatory death-after-effect-before-checkpoint test.

Gate: first crash test proves effect count 1 and idempotent second recovery.

### Commit 4 — cancellation and compensation

Suggested subject:

```text
feat(R3.2): add durable cancellation and compensation
```

Scope:

- cancellation acceptance/observation;
- reverse compensation;
- terminal evidence mapping;
- recovery-required resource barriers;
- cancellation/rollback crash cases.

Gate: cancellation and all three failure terminals have evidence-backed tests.

### Commit 5 — heartbeat, contention, progress, worker lifecycle

Suggested subject:

```text
feat(R3.2): add bounded operation worker lifecycle
```

Scope:

- cooperative heartbeat/pulse;
- progress throttle;
- `run_once`/bounded loop/shutdown;
- two-worker barriers and unrelated-resource concurrency;
- no production auto-start/composition side effect.

Gate: worker/concurrency tests pass repeatedly with no sleeps.

### Commit 6 — complete crash/security matrix and handoff

Suggested subjects:

```text
test(R3.2): complete executor crash and privacy matrix
docs(R3.2): record Session 5B evidence and 5C handoff
```

Scope:

- every named crash point;
- security/bounds regression suite;
- architecture/docs/AGENTS updates;
- exact test and repeated-stress evidence;
- no 5C activation behavior.

Gate: full Session 5B exit below.

Do not squash migration history, amend migration 003, or mix 5C production
activation into these commits.

---

## 16. Session 5B exit gate

All items are mandatory:

### Architecture

- Exact-version workflow registry exists.
- Executor is synchronous/deterministic and independent of worker threading.
- Worker is bounded and not auto-started by production composition.
- SQL remains only in repositories/approved DB layer.
- No tkinter, CLI, chat, systemd, Podman, network, or real host adapter import
  exists under the engine/workflow modules.
- Migration 003 is unchanged; no JSON write/facade returns.

### Correctness and recovery

- Mandatory death-before-checkpoint test passes exactly as §9.
- Every external effect has persisted intent, probe, verification,
  compensation, and recovery classification.
- Stale lease owners are transactionally fenced from every mutation.
- Cancellation is durable and only honored at safe points.
- Success, safe failure, verified rollback, cancellation, and uncertain
  recovery have distinct correct terminal outcomes.
- `RECOVERY_REQUIRED` blocks conflicting resource acquisition after TTL.
- Re-running recovery is idempotent.
- No sleeps establish ordering.

### Security and boundedness

- Requests, outputs, progress, events, failures, and exceptions are bounded.
- Secret canaries are absent from SQLite, events, results, logs, and argv.
- Fake paths cannot escape injected roots.
- Progress is write-throttled; terminal/decision evidence is never dropped.

### Verification

Run and record:

```bash
PYTHONPATH=. .venv/bin/pytest -q
.venv/bin/pytest tests --collect-only -q
python3 -m compileall -q bc250_llm_mode tests
git diff --check
```

Also run:

- source and editable-install collection parity;
- focused executor/recovery/concurrency suite at least 20 times;
- clean wheel build/install and console-entry smoke if package exports or
  packaging changed;
- architecture guards;
- tracked-tree status preserving the three user-owned untracked
  `scripts_audit/` files.

The terminal collection hook is the authoritative test count; never infer it
from dots and never carry “402” forward after tests are added.

---

## 17. Session 5C handoff

After 5B passes, stop and prepare—not implement—the first real durable
workflow: model activation.

The 5C first red test should reuse the 5B crash harness:

> With an existing known-good model active, crash after publishing the
> candidate runtime handoff but before the step checkpoint. A new executor
> must inspect the handoff fingerprint, desired runtime revision, actual
> server health, and bounded inference result; it must either checkpoint the
> already-complete candidate without a second restart or restore and verify
> the prior known-good configuration exactly once.

Before that test is written, map each mature synchronous activation behavior
to a 5B `StepDefinition` and identify the one typed adapter owner for:

- runtime handoff publication;
- server restart;
- health check;
- minimal inference probe;
- known-good promotion/restoration.

Session 5C must remove the old duplicate activation path as it converts it. It
must not wrap the synchronous fallback behind an operation and leave both
callable.

---

## 18. Exact next-session checklist

1. Confirm HEAD `63f5fab` and preserve `scripts_audit/` untouched.
2. Re-run the authoritative collection/full/compile/diff baseline.
3. Read ADR 002 and this plan completely.
4. Add red lease-fence, cancel-timestamp, recovery-barrier, and lock-order
   tests from §3.
5. Apply only the repository/ADR corrections; prove schema 003 unchanged.
6. Commit boundary 1 and stop for review if any durable contract changes.
7. Add typed workflow/step/recovery protocols and exact registry.
8. Add atomic enqueue using real UoWs and test-only fake workflow.
9. Implement intent/effect/probe/checkpoint/verify executor transactions.
10. Land the mandatory process-death test before general success tests.
11. Add compensation/cancellation and their crash matrix.
12. Add heartbeat/progress/worker lifecycle and barrier-based contention.
13. Run security/bounds and repeated stress suites.
14. Update architecture docs/AGENTS with exact evidence.
15. Stop at the 5B gate and hand off the exact 5C red test from §17.

### Session report format

- HEAD and tracked/untracked status;
- commits in order with R3.2 plan IDs;
- authoritative collected/passed/skipped counts and invocation;
- contract clarifications made (or explicitly none);
- executor/lease/cancellation/recovery behavior completed;
- crash points covered and repeated-run count;
- secret/path/concurrency evidence;
- source/editable/wheel/compile/diff results;
- deliberately deferred production integrations/surfaces;
- exact 5C first red test and stop condition.
