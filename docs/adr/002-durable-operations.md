# ADR 002 — Durable Operation Contract

**Status:** Accepted (Session 5A)
**Supersedes:** the provisional state list in
`R2_EXIT_AND_OPERATION_ENGINE_PLAN.md` Part II (one correction: `FAILED_SAFE`
is added and distinguished from `FAILED_ROLLED_BACK`).
**Scope:** the durable model only — states, transitions, persistence,
leases, events. Executors, workers, host adapters, CLI commands, and UI are
Session 5B+ and MUST NOT exist until this contract's gates pass.

---

## 1. Operation and step states

### 1.1 Operation states

```text
QUEUED, PREPARING, RUNNING, VERIFYING, COMMITTING,
CANCEL_REQUESTED, ROLLING_BACK, PAUSED,
SUCCEEDED, CANCELLED, FAILED_SAFE, FAILED_ROLLED_BACK, RECOVERY_REQUIRED
```

`FAILED_SAFE` is the correction to the earlier plan: a failure that occurs
before any externally visible mutation — or where cleanup *proved* no
mutation remains — must not be reported as `FAILED_ROLLED_BACK`, which
asserts a verified restoration of a prior state.

### 1.2 Step states

```text
PENDING, RUNNING, CHECKPOINTED, VERIFIED, COMPENSATING, COMPENSATED, FAILED
```

- `RUNNING` — step intent persisted; the executor claims it.
- `CHECKPOINTED` — the step's external effect completed AND its checkpoint
  was durably written (crash between the two is the 5B crash-test target).
- `VERIFIED` — the step postcondition was probed after checkpointing.
- `COMPENSATING` / `COMPENSATED` — reverse-effect bookkeeping.
- `FAILED` — terminal for the attempt; recovery policy decides retry.

## 2. Allowed transitions

Frozen table (anything not listed is disallowed):

| From | Allowed to |
| --- | --- |
| `QUEUED` | `PREPARING`, `CANCEL_REQUESTED`, `CANCELLED`, `PAUSED`, `FAILED_SAFE` |
| `PREPARING` | `RUNNING`, `ROLLING_BACK`, `CANCEL_REQUESTED`, `PAUSED`, `FAILED_SAFE` |
| `RUNNING` | `VERIFYING`, `COMMITTING`, `ROLLING_BACK`, `CANCEL_REQUESTED`, `PAUSED`, `FAILED_SAFE`, `FAILED_ROLLED_BACK`, `RECOVERY_REQUIRED` |
| `VERIFYING` | `COMMITTING`, `ROLLING_BACK`, `CANCEL_REQUESTED`, `PAUSED`, `FAILED_SAFE`, `FAILED_ROLLED_BACK`, `RECOVERY_REQUIRED` |
| `COMMITTING` | `VERIFYING`, `ROLLING_BACK`, `SUCCEEDED`, `FAILED_SAFE`, `FAILED_ROLLED_BACK`, `RECOVERY_REQUIRED` (Session 5C correction: the section cycles to `VERIFYING` after a verified critical step and to `ROLLING_BACK` on mutation-possible failure) |
| `CANCEL_REQUESTED` | `ROLLING_BACK`, `CANCELLED` |
| `ROLLING_BACK` | `CANCELLED`, `FAILED_ROLLED_BACK`, `RECOVERY_REQUIRED` |
| `PAUSED` | `PREPARING`, `RUNNING`, `VERIFYING`, `CANCEL_REQUESTED`, `CANCELLED`, `FAILED_SAFE` |

Notes:

- `COMMITTING` is the critical section: publication/swap/commit/restart.
  Cancellation is never honored inside it (`COMMITTING → SUCCEEDED` or a
  failure terminal only).
- `CANCEL_REQUESTED` is entered only when cancellation will be honored at
  the next safe point; deferral means the transition has not happened yet.
  `ROLLING_BACK → CANCELLED` covers "cancel honored, but compensation had to
  run first" — cancellation requires either no visible change or a verified
  restoration before it can be terminal.
- `RUNNING/VERIFYING → ROLLING_BACK` exists because a failure discovered
  mid-phase may already have visible effects requiring compensation;
  whether the outcome is `FAILED_SAFE` or `FAILED_ROLLED_BACK` is decided by
  what the compensations prove.

### Step transitions

| From | Allowed to |
| --- | --- |
| `PENDING` | `RUNNING` |
| `RUNNING` | `CHECKPOINTED`, `FAILED`, `COMPENSATING`, `RUNNING` (reclaim after process death) |
| `CHECKPOINTED` | `VERIFIED`, `FAILED`, `COMPENSATING` |
| `VERIFIED` | `COMPENSATING`, `FAILED` |
| `COMPENSATING` | `COMPENSATED`, `FAILED` |

A recovering worker re-entering `RUNNING` from `RUNNING` increments
`attempts`; this is the only same-state transition in the system.

## 3. Terminal-state meanings

Exactly five terminals; none has outgoing transitions:

| Terminal | Meaning | Evidence required |
| --- | --- | --- |
| `SUCCEEDED` | The requested end state is active and was verified. | Final verification event (health/inference/postcondition) recorded. |
| `CANCELLED` | No externally visible mutation occurred, OR every mutation was compensated and the prior state verified restored. | Compensation records proving restoration. |
| `FAILED_SAFE` | The operation failed but no visible mutation occurred, or cleanup proved none remains. | Pre-mutation failure evidence, or probe results proving absence of effect. |
| `FAILED_ROLLED_BACK` | A mutation occurred and the prior state was captured, restored, and verified. | Prior snapshot + restoration + verification records. |
| `RECOVERY_REQUIRED` | Safe state cannot be proven. Blocks conflicting operations until an explicit human recovery completes. | Exact failure and rollback-failure evidence persisted. |

`PAUSED` is deliberately NOT terminal: it covers interruption (process death
recovery pending), authorization waits (future privileged helper), and
explicit user review.

## 4. Schema/request versioning

- The operations schema begins at migration **003**; versions are permanent
  and never reused (ADR 001 rule).
- Every operation row stores `request_version` and
  `recovery_policy_version`.
- Request versions are per-type integers starting at 1:
  `MODEL_ACTIVATE=1`, `MODEL_ACQUIRE=1`, `RUNTIME_UPDATE=1`,
  `RUNTIME_ROLLBACK=1`. Repositories refuse unknown versions — an executor
  written against a request shape must be able to trust it forever.
- Changing a request's semantics bumps its version and adds a new known
  version; old rows are never rewritten.
- `recovery_policy_version` identifies the interruption-classification rules
  (absent / complete / partially resumable / discardable / revertible /
  uncertain-manual). Recovery behavior changes bump it; stored operations
  keep the policy they were created under unless explicitly migrated.

## 5. Identity and time

- Operation IDs are UUID4 strings generated by an injected factory
  (`uuid.uuid4` in production); tests inject deterministic providers.
- Worker/owner identity is an opaque token generated per worker start (also
  UUID4 via injection).
- All timestamps are UTC ISO-8601 compact form `%Y-%m-%dT%H:%M:%SZ`
  (matching `legacy_import.utcnow`). Clock access is injected
  (`clock: Callable[[], str]`); tests never depend on wall-clock ordering.
- Ordering guarantees come from monotonic database columns (state revision,
  event cursor), never from timestamps.

## 6. Resource keys and lock ordering

Resource keys are namespaced strings:

```text
runtime-active          # the single server/runtime configuration
model:<artifact-id>     # one installed/staged model artifact
runtime-installation    # llama.cpp staged builds/swaps
```

A 0.9 operation acquires at most two leases. Global acquisition order is
lexicographic by resource key, acquired one at a time immediately before
first use and released as soon as no longer needed:

```text
MODEL_ACTIVATE   : runtime-active
MODEL_ACQUIRE    : model:<artifact-id>
RUNTIME_UPDATE   : runtime-installation, then runtime-active (only at activation boundary)
RUNTIME_ROLLBACK : runtime-active, then runtime-installation
```

Because acquisition within an operation follows lexicographic key order,
and SQLite `BEGIN IMMEDIATE` serializes lease writes, deadlock is
impossible.

> **Erratum (Session 5B):** the `RUNTIME_UPDATE` example above lists
> `runtime-installation` before `runtime-active`, which is not Python/string
> lexicographic order. The binding rule is: acquire all resources for one
> operation in **sorted (lexicographic) key order**, one at a time. The
> example's prose intent ("installation before activation boundary") is
> preserved by naming, not by that listing: `runtime-active` sorts before
> `runtime-installation`, so an update acquires `runtime-active` first.
> An operation may release a higher-sorted resource only after releasing
> every lower-sorted resource it still holds; it may re-acquire a
> lexicographically lower resource while retaining a higher one.

## 7. Leases

- One row per resource key (`PRIMARY KEY`), referencing its owning
  operation (`ON DELETE CASCADE`).
- Fields: owner token, monotonically increasing `lease_revision`,
  `acquired_at`, `heartbeat_at`, `expires_at`.
- Default TTL is 60 seconds; heartbeats renew `heartbeat_at`/`expires_at`
  and MUST carry the expected owner and `lease_revision`.
- Acquisition: if the key is free or expired, the contender wins and
  `lease_revision` increments (an expired takeover begins a new lease
  generation, which is how stale owners are detected). If actively held,
  exactly one contender wins and the others receive `OperationConflict`.
  Writers serialize through `BEGIN IMMEDIATE`, making winner selection
  deterministic.
- A stale owner (wrong owner token or old revision) cannot heartbeat,
  release, or take over — its calls fail with `OperationConflict`.
- Release deletes the row only for the current owner+revision.

## 8. Cancellation-safe points

Cancellation is requested durably (`request_cancel`) and honored only at
declared safe points:

- safe: between steps, between bounded download/build chunks, while paused;
- deferred (never interrupted): publication, atomic swap, database commit,
  service restart, health verification, and every compensation.

If a mutation already happened, honoring cancellation means running
compensations and then terminating `CANCELLED` (verified restoration).
Otherwise it terminates directly as `CANCELLED` with no effects.
`CANCEL_REQUESTED` cannot be entered from `COMMITTING` — the transition
table forbids it there.

## 9. Recovery policy

Every step's external effect must classify, on recovery, as exactly one of:
absent, complete, partially resumable, discardable, revertible,
uncertain/manual. Classification rules are versioned (§4). Interrupted
operations land in `PAUSED` with an explicit reason when the class is
uncertain/manual; automatically resumable classes proceed without user
review once a worker reopens them.

## 10. Events

- Append-only; cursor is `INTEGER PRIMARY KEY AUTOINCREMENT` (monotonic).
- Required fields: timestamp, level (`debug|info|warn|error`), code,
  summary ≤ 512 characters.
- Optional sanitized detail JSON ≤ 16 KiB encoded; optional progress
  snapshot.
- Raw exceptions and subprocess output are NEVER stored; failures record a
  stable `code` plus bounded human text.
- Retention: decision/result events are never dropped by retention;
  debug-level events may be trimmed after 30 days (enforcement is a later
  maintenance operation — the schema carries the level needed for it).

## 11. Redaction and payload bounds

Before anything reaches SQLite (enforced in `operations/validation.py`):

- Keys matching secret markers (`token`, `secret`, `password`, `api_key`,
  `authorization`, `cookie`, `credential`, `private_key`, …) are rejected —
  not stripped — so a redaction bug fails loudly instead of leaking.
- Nesting depth ≤ 8, keys per object ≤ 64, strings ≤ 4 KiB (longer strings
  are truncated with a marker), encoded size bounded per use.
- Bounds: request ≤ 64 KiB, event detail ≤ 16 KiB, summary ≤ 512 chars,
  error/remediation detail bounded separately (16 KiB).
- JSON is encoded canonically (`sort_keys=True`, compact separators) so
  fingerprints and byte-comparisons stay stable.

## 12. Execution-host behavior (0.9)

For 0.9, a frontend-owned executor checkpoints and resumes after restart.
It MUST NOT claim work continues after the frontend exits: surfaces report
interrupted operations as "interrupted; ready to resume." Before 1.0, long
unsafe operations move behind an independently supervised worker or pause at
safe checkpoints. The durable model does not change when that move happens —
the schema already separates ownership (leases) from state.

## 13. Future privileged-helper integration

The allowlisted helper (post-R2 plan R5) integrates WITHOUT schema changes:

- Helper-executed steps remain rows in `operation_steps` with their own
  external effect identities; the helper authenticates by presenting an
  operation ID + step key whose row authorizes the effect.
- Authorization wait is modeled as `PAUSED` (not a new state), with an
  event recording the authorization request.
- Lease ownership may move to the helper's worker token via the existing
  takeover path (revision bump proves the handoff).

## 14. Parent/child operations

- `operations.parent_operation_id` self-references (`ON DELETE SET NULL`);
  children are independent operations with their own leases and events.
- 0.9 defines no fan-out: at most single-child delegation (e.g., a future
  acquisition operation invoking activation). Cancellation of a parent
  requests cancellation of running children first; a parent cannot reach a
  success terminal while a child is active.

## 15. Session 5C corrections (narrow)

Two corrections, both red-tested before adoption; no schema change.

1. **Critical-state cycling.** §2 originally gave `COMMITTING` only
   resolution terminals, so a cancellation could still transition an
   operation while a critical step executed (the engine entered the
   section only at final completion). Corrected behavior:

   - immediately before a critical step's effect, the executor CASes
     `RUNNING|VERIFYING -> COMMITTING`;
   - the operation stays in `COMMITTING` through effect, checkpoint, and
     postcondition verification;
   - after the step's verification commits, it cycles
     `COMMITTING -> VERIFYING`;
   - if the critical effect or its verification fails after mutation was
     possible, `COMMITTING -> ROLLING_BACK` runs compensation;
   - if a cancellation CAS wins before entry, no effect runs; once inside,
     `request_cancel` is refused until the critical step resolves.

2. **Durable compensation resume.** §9 originally had no convergence rule
   for an executor death between compensation effects/checkpoints, and the
   forward loop could not resume a `ROLLING_BACK` row. The generic step
   contract gains an optional restoration probe (`probe_restoration`);
   takeover reconstructs the reverse compensation set from durable step
   rows, probes interrupted compensations before repeating them
   (`COMPLETE` checkpoints without a second effect), continues the
   remaining compensations in reverse order, and publishes exactly one
   terminal result (`FAILED_ROLLED_BACK`, `CANCELLED`, or
   `RECOVERY_REQUIRED`).
