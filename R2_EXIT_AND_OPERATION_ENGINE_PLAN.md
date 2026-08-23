# BC250 LLM MODE — R2 Exit and Durable Operations Plan

**Purpose:** Continue from the completed Session 3 frontend migration, close the
SQLite/path cutover without leaving a compatibility layer behind, and then add
the smallest complete durable-operation system that materially improves the
end-user experience.

**Starting baseline:** `main` at `b079952`, package version `0.9.0.dev0`, 296
tests passing, and a clean working tree. Sessions 1–3 of
`ROAD_TO_1_0_IMPLEMENTATION_PLAN.md` are complete: frontends no longer perform
whole-state saves, status refresh is read-only, GUI/CLI/chat share the composed
services, and path ownership is centralized in `AppPaths`.

**Important correction to the earlier road map:** schema migration 002 already
belongs to `known_good_runtime`. The operations schema begins at **migration
003**. Migration numbers are permanent once released or used by a committed
test fixture; they must never be reused.

**Authority:** The safety, reversibility, privilege, fit, and release rules in
`MASTER_IMPLEMENTATION_PLAN.md` remain authoritative. This document is the
execution plan for Session 4 and the R3 operation-engine work that follows it.

---

## 1. Target state and stopping point

This plan has two independently reviewable outcomes.

### Outcome 1 — R1/R2 are genuinely complete

- `compat_state.py` is deleted.
- `Application` has no generic `store`, `load`, `save`, `transaction`, or
  whole-state-diff escape hatch.
- SQLite repositories, units of work, query services, and domain services are
  the only runtime persistence API.
- Legacy JSON is accepted only as an immutable import source.
- `--state` can no longer create a parallel JSON-backed runtime mode.
- Production modules cannot construct `StateStore` or `CompatStateStore`.
- No frontend, status probe, or composed service discovers paths from HOME or
  from persisted derived path strings.
- The source, editable install, and built wheel pass the same test battery.

### Outcome 2 — R3 reaches a useful product boundary

The following long-running workflows are durable operations:

1. Model activation, including health verification and known-good rollback.
2. Model acquisition/import/validation, including resumable staging and atomic
   publication.
3. llama.cpp update/rollback, including staged build, smoke test, activation,
   and restoration of the prior tree.

Users can inspect progress, see actionable failures, cancel at safe points,
restart the application, and recover or resume interrupted work. The CLI has
complete operation controls and the GUI has a minimal Activity view. The
operation core is process-agnostic and ready to move behind a dedicated worker
or privileged helper later without changing its durable model.

### Deliberate stop after this plan

Do **not** fold the following into this batch:

- the allowlisted privileged helper;
- an independent thermal supervisor;
- authenticated remote gateway or Tailscale topology changes;
- Open WebUI container hardening;
- full GUI redesign or native streaming chat overhaul;
- automatic updates;
- generic hardware support;
- release signing, SBOM publication, or BC-250 hardware qualification.

Those remain required on the road to 1.0, but mixing them into the persistence
and operation-engine changes would make failure analysis and rollback much
harder. The sweet spot for this plan is a clean storage architecture plus a
durable, testable workflow foundation.

---

## 2. Non-negotiable invariants

Every task and review must preserve these rules.

1. **One server owner:** only the typed server/systemd adapter may touch
   `bc250-llm.service`.
2. **Fit before mutation:** model, context, slot, and profile changes must pass
   the canonical fit calculation before publishing desired runtime state.
3. **Known-good promotion is last:** an unverified candidate never replaces the
   last known-good runtime.
4. **Thermal authority is independent:** generic settings writes and operation
   recovery cannot clear or downgrade a thermal latch.
5. **Safe boot:** the next boot remains the desktop and nothing newly
   auto-starts unless the user explicitly selected that behavior.
6. **No dual writes:** SQLite is the sole runtime source of truth. Legacy JSON
   and runtime handoff files are import/rendered artifacts, not second state
   stores.
7. **Atomic publication:** a final model, database, handoff, receipt, or runtime
   tree is never visible in a partial state.
8. **Reversibility:** host/runtime mutation records enough prior state to
   restore or to enter an explicit `RECOVERY_REQUIRED` state.
9. **No secrets in observability:** command argv, logs, operation events,
   support data, and exception strings are redacted before persistence.
10. **Composition owns paths:** derived paths come from one validated
    `AppPaths`; customized `models_dir` remains supported as persisted user
    configuration.
11. **No invisible background success:** every long-running mutation has a
    durable operation ID, terminal result, and human-readable outcome.
12. **No fake cancellation:** cancellation is honored only at declared safe
    points; otherwise the UI reports that rollback or verification is in
    progress.

---

## 3. Delivery map

| Session | Primary result | Required stop gate |
| --- | --- | --- |
| 4A | Freeze behavior and extract legacy import-only schema code | No behavior change; 296+ tests green |
| 4B | Native composition and facade/test migration | No runtime `CompatStateStore`; repair behavior intact |
| 4C | Delete facade, constrain `--state`, close R1/R2 | R1/R2 exit matrix green; clean tree |
| 5A | Migration 003, operation state machine, repository | Invalid transitions impossible; migration rollback tested |
| 5B | Executor, leases, events, cancellation, recovery | Crash-injection foundation green |
| 5C | Convert model activation | Every step crash-tested; prior known-good recovered |
| 6A | Convert model acquisition/import | Resume, validation, quarantine, atomic publish green |
| 6B | Convert runtime update/rollback | Active tree survives every staged failure |
| 6C | CLI controls, minimal Activity, R3 gate | All three workflows observable/recoverable |

Each row is a separate review boundary. Session 4 must be committed and green
before migration 003 is introduced.

---

# Part I — Session 4: finish R1/R2

## 3.1 P0 discovered during planning — enforce migration order

`db.py` currently declares migration 002 before migration 001, and
`initialize()` iterates declaration order. The two current migrations happen
not to fail in that order, but this violates the ordered-migration contract and
would make migration 003 unsafe to reason about.

Fix this before any facade work:

1. Declare migrations in ascending order **and** validate the registry at
   startup/test collection.
2. Reject duplicate versions, gaps in the supported sequence, non-positive
   versions, and a `SCHEMA_VERSION` that does not equal the highest declared
   version.
3. Execute unapplied migrations in numeric order even if a future declaration
   is accidentally reordered.
4. Test a migration whose statement depends on the preceding migration, so
   ordering is behavioral rather than a list-shape assertion.
5. Preserve atomic rollback and newer-schema refusal.

Recommended commit:

`fix(R2.2): enforce contiguous ordered schema migrations`

## 4. Session 4A — Freeze behavior before deletion

The facade is still carrying two different responsibilities: assembling a
whole-state compatibility snapshot and providing test conveniences. Remove
those responsibilities only after equivalent native contracts are explicit.

### 4.1 Capture the current composition contract

Add or refine tests that describe only supported post-facade behavior:

- a fresh profile creates and initializes `state.db` at the composed path;
- a valid v5 legacy file imports once and remains byte-identical;
- a valid v4 fixture canonicalizes through v5 and imports with the established
  field-mapping rules;
- customized `models_dir` survives import and composition;
- derived installation paths are absent from persisted settings and are
  reconstructed by the query layer;
- a corrupt legacy source publishes no database and enters repair mode;
- a fixed source can be retried and becomes operational;
- a newer database schema is refused without reset or overwrite;
- a normal composed application exposes query/services, not a store;
- status, health, GUI refresh, and chat startup do not bump configuration
  revision.

The safest first test is:

> Compose a profile containing the frozen v5 fixture, query its snapshot, and
> assert the imported domain values, customized model path, authoritative
> thermal latch, stale observations, quarantined unknown keys, and secret
> refusal without reading through `CompatStateStore`.

### 4.2 Inventory facade-only behavior

Classify every test in `tests/test_cutover.py` and every production reference
to `CompatStateStore`, `StateStore`, `.save(`, and `.transaction(` as one of:

- legacy canonicalization/import behavior;
- repository behavior;
- query assembly behavior;
- domain service behavior;
- obsolete compatibility behavior.

Move the assertion to the owning layer instead of mechanically copying facade
tests. Delete tests for unsupported whole-state semantics such as replacement
dictionary transactions, caller-dictionary revision mutation, or broad
snapshot saves. Those behaviors were migration scaffolding, not product API.

### 4.3 Add final architecture guards before refactoring

The guards should eventually assert:

- `compat_state.py` does not exist;
- no production import mentions `compat_state` or `CompatStateStore`;
- `StateStore(` is allowed only in import/canonicalization internals until its
  class is removed;
- `.save(` and generic `.transaction(` do not occur in frontends or domain
  workflows;
- raw SQL remains restricted to migrations and repository implementations;
- `Application` does not contain a generic persistence object;
- frontends receive an `Application` and cannot construct fallback services;
- `Path.home()` remains restricted to `paths.py`;
- JSON writes to `state.json` do not occur anywhere in production;
- status/query methods open read-only units and do not change revisions.

Initially mark guards against the intended final shape as expected failures or
land them in the same commit as the relevant deletion. Do not weaken a guard
to preserve obsolete code.

### 4.4 4A acceptance

- Existing source and editable test runs pass.
- No product behavior changes.
- Every compatibility test has a documented destination or deletion reason.
- The planned post-facade public contract is test-visible.

Recommended commit:

`test(R2.2): freeze native composition and facade removal contract`

---

## 5. Session 4B — make composition repository/service-native

### 5.1 Remove generic storage from `Application`

Replace the transitional `Application` shape with explicit dependencies. At a
minimum it should expose:

- `paths`;
- `logger` and command-runner factory/host adapters;
- `UnitOfWorkFactory`;
- `ApplicationQueryService`;
- setup, safety, runtime configuration, activation, host mode, component,
  Open WebUI, sharing, model installation, and maintenance services;
- repair status/reason as an explicit composition result.

Delete or replace:

- `store`;
- `Application.wrap(store)`;
- `persist_state_changes()` if it is still a generic diff escape hatch;
- `apply_to_state()` if the query layer already owns path projection;
- duplicated runner methods;
- `state_supplier=lambda: application.store.load()`.

If model activation still needs a complete read model, inject a typed query or
purpose-built runtime view provider. A service must not reconstruct the old
store behind a new name.

### 5.2 Make composition outcomes explicit

Prefer one of these two forms:

1. `Application.compose()` returns an operational `Application` or raises a
   typed `RepairRequired`; the CLI catches it and composes a `RepairService`.
2. `Application.compose()` returns a discriminated result such as
   `OperationalApplication | RepairApplication`.

Do not preserve `store=None` as the signal. Whichever form is selected must
make it impossible for GUI/chat code to dereference half-wired services.

Recommended minimal choice for 0.9: retain one `Application` dataclass with
`operational: bool`, `repair_reason`, and optional services only at the
composition boundary, plus `require_operational()` returning a fully typed
service bundle. Avoid a broad class hierarchy unless typing proves materially
clearer.

### 5.3 Initialize SQLite without the facade

Normal composition flow:

1. Construct and validate `AppPaths` once.
2. Enforce app-owned directory permissions.
3. Configure logging under the composed logs directory.
4. If the database is absent and the legacy source exists, invoke the locked,
   staged importer.
5. Open a connection and run ordered migrations.
6. Refuse a newer schema or failed integrity check.
7. Build one `UnitOfWorkFactory`.
8. Wire repositories/query/services/controllers.
9. Regenerate a missing or stale runtime handoff from committed state only.
10. Return the operational application.

The importer must still leave no database on pre-publication failure. Normal
database creation without a legacy source must use the same permission and
integrity contract.

### 5.4 Remove remaining legacy fallback execution

The current transaction fallbacks in bootstrap, chat, thermals, and tune are
test-era compatibility paths. Replace them as follows:

- bootstrap receives `SetupService` or the composed application;
- chat receives query/conversation/runtime services;
- thermals receives `ThermalStateService` and a typed sensor/host adapter;
- tune receives `RuntimeConfigurationService` and history repositories.

Tests should use fake adapters with real temporary SQLite units. They should
not use JSON stores to avoid composing an application.

### 5.5 Preserve query/read model fidelity

`ApplicationQueryService.snapshot()` remains the single assembled read model
for legacy-shaped GUI data until view models replace it later. Verify that it:

- reads one consistent database snapshot;
- projects `app_dir` and `logs_dir` from `AppPaths`;
- respects customized `models_dir`;
- includes authoritative thermal and known-good runtime records;
- labels transient runtime observations as stale where appropriate;
- never writes defaults merely because fields are absent;
- does not expose quarantined unknown data or secret-like legacy values to
  frontends.

### 5.6 4B acceptance

- `Application.compose()` does not instantiate `CompatStateStore`.
- No frontend or domain service needs generic load/save/transaction behavior.
- Repair mode, auto-import, handoff regeneration, and custom paths retain their
  tests.
- All tests pass before deleting `compat_state.py`.

Recommended commits:

1. `refactor(R2.2): compose native units queries and services`
2. `refactor(R2.2): remove legacy workflow transaction fallbacks`

---

## 6. Session 4C — remove legacy runtime mode and close the gates

### 6.1 Split schema canonicalization from JSON persistence

`legacy_import.py` currently uses `StateStore` as a convenient way to apply
legacy migrations. Replace this with a pure import API, for example:

```python
canonicalize_legacy_state(raw: Mapping[str, object]) -> LegacyStateV5
```

The function must:

- accept only a decoded JSON mapping;
- apply v1–v5 migrations deterministically;
- validate types/ranges used by the importer;
- distinguish absent from explicit false/zero values;
- preserve customized model directory intent;
- quarantine supported unknown non-secret keys;
- refuse secret-like keys;
- mark transient observations stale;
- preserve the thermal latch as authoritative;
- perform no file I/O and no HOME/path discovery.

The importer owns reading, source digesting, locking, staging, SQLite writing,
integrity verification, publication, permissions, and receipt creation. The
canonicalizer owns only data interpretation.

Once tests use the pure canonicalizer, delete `StateStore` from production if
no supported runtime path needs it. It is acceptable to retain frozen JSON
fixtures; it is not acceptable to retain a writable JSON store.

### 6.2 Define `--state` transition behavior precisely

The current `--state PATH` branch creates a second JSON-backed application.
That must end.

For the 0.9 compatibility window:

- normal commands used with `--state PATH` fail fast with usage exit code 64;
- the error explains that JSON state is import-only and identifies the
  supported import/repair command;
- `--state PATH repair-retry` treats PATH as the immutable import source;
- import target identity is explicit and deterministic—prefer an `--app-dir`
  option, otherwise use the normal composed profile;
- the source is never modified, renamed, deleted, or used for later reads;
- a database that already contains durable state is never silently replaced;
- repeated import reports “already imported” based on receipt/source digest;
- a conflicting source digest requires an explicit future restore workflow,
  not implicit re-import.

If parser clarity permits, add `import-state PATH` and make `--state` a
deprecated alias accepted only for `repair-retry`. Document removal of the
alias for 1.0. Do not carry `--state` as a general path/profile selector.

Required parser tests:

- `--state x status` rejected with no files changed;
- `--state x setup` rejected with no GUI start;
- valid import publishes one database and leaves source byte-identical;
- corrupt import leaves target absent and source byte-identical;
- second identical import is idempotent;
- existing target database is not overwritten;
- custom target app directory does not touch sentinel HOME;
- help text describes import-only behavior.

### 6.3 Delete facade and obsolete tests

Delete `bc250_llm_mode/compat_state.py`. Replace `tests/test_cutover.py` with
focused test modules where useful:

- `test_legacy_import.py` — canonicalization, mapping, receipt, publication;
- `test_composition.py` — operational/repair outcomes and path isolation;
- `test_repositories.py` — revision and repository concurrency;
- `test_queries.py` — assembled read model and read-only behavior;
- service-specific tests — stale command, rollback, and invariants.

Do not preserve tests for facade-only `load/save/transaction` semantics.

### 6.4 Final R1/R2 architecture guards

Make the final guard exact and simple:

- compatibility facade file/import/name count is zero;
- runtime `StateStore` construction count is zero;
- frontend `.save(` count is zero;
- frontend generic `.transaction(` count is zero;
- no fallback application/store construction;
- no persisted `app_dir` or `logs_dir` settings;
- no `Path.home()` outside `paths.py`;
- no raw SQL outside `db.py`, `repositories.py`, and explicitly approved
  migration helpers;
- no JSON write targeting legacy `state.json`;
- no duplicate CLI command dispatch branch;
- all status/query entry points are revision-pure.

Prefer an AST-based guard for constructor/import/call rules where simple text
counts would produce false positives in comments or documentation.

### 6.5 R1/R2 verification matrix

Run all of the following before marking the gate complete:

#### Persistence and migration

- fresh database creation;
- migration 001 then 002 in order;
- rollback after failure in the middle of each migration;
- newer-schema refusal;
- foreign keys, WAL, busy timeout, and FULL synchronous policy;
- database 0600 and app-owned directories 0700;
- v4 and v5 frozen fixture import;
- corrupt/truncated/non-mapping/secret-bearing JSON refusal;
- no database on failed staged import;
- source unchanged after successful and failed import;
- receipt digest and idempotency;
- concurrent unit-of-work conflict behavior;
- integrity check cursor consumption and no blocked checkpoints.

#### Runtime behavior

- setup, status, chat startup, GUI construction, repair status/retry;
- model activation success and rollback;
- runtime profile preview/apply;
- thermal latch persistence/reset rejection;
- benchmark/autotune append retention;
- llama.cpp handoff regeneration;
- custom model directory path;
- sentinel-HOME isolation.

#### Packaging

```bash
git diff --check
PYTHONPATH=. .venv/bin/pytest -q
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q bc250_llm_mode tests
.venv/bin/python -m build
```

Install the built wheel into a clean temporary virtual environment and verify:

- package import path points into the wheel environment;
- `bc250-llm-mode --help`;
- `bc250-llm-mode --version`;
- fresh profile composition;
- `status` JSON/stdout contract;
- no import from the source checkout.

### 6.6 Documentation and Session 4 stop condition

Update:

- `AGENTS.md` with the actual commit, version, test count, and immediate task;
- `ROAD_TO_1_0_IMPLEMENTATION_PLAN.md` Sessions 1–4 as done and migration 003
  as next;
- `MASTER_IMPLEMENTATION_PLAN.md` R1/R2 evidence and remaining R3 work;
- ADR 001 with the facade removal record and final `--state` behavior;
- state schema and architecture documents;
- CLI help, README migration/repair instructions, and changelog.

**Stop after Session 4.** Required report:

- commit range and exact test count;
- zero-count architecture evidence;
- source/editable/wheel results;
- explicit confirmation that legacy JSON is import-only;
- exact next test: migration 003 atomic rollback plus invalid operation-state
  transition.

Recommended final commits:

1. `refactor(R2.2): replace writable legacy state with pure importer schema`
2. `feat(R2.2): make state option import-only`
3. `refactor(R2.2): remove compatibility state facade`
4. `docs(R1,R2): close persistence and path exit gates`

---

# Part II — Session 5: operation engine foundation

## 7. Architecture decisions before implementation

Write ADR 002, “Durable operation execution,” before migration 003. It must
decide the following rather than leaving them implicit in code.

### 7.1 Execution-host decision

Recommended 0.9 design:

- the operation engine is process-agnostic;
- the current CLI/GUI process may host an executor;
- operation state and checkpoints are durable;
- after process death, the next application start classifies and resumes or
  rolls back unfinished work;
- closing a frontend never falsely marks an operation successful;
- stages that cannot safely continue without a host pause at a checkpoint;
- a persistent unprivileged operator service and the privileged helper remain
  later deployment changes, not assumptions embedded in workflow code.

This provides honest restart recovery without claiming that every operation
continues running after the GUI process exits. The Activity view must say
“paused/interrupted; reopen to resume” where that is the actual behavior.

### 7.2 One operation, one durable state machine

Use a closed status enum. Recommended states:

- `QUEUED`
- `PREPARING`
- `RUNNING`
- `VERIFYING`
- `COMMITTING`
- `CANCEL_REQUESTED`
- `ROLLING_BACK`
- `PAUSED`
- `SUCCEEDED`
- `CANCELLED`
- `FAILED_ROLLED_BACK`
- `RECOVERY_REQUIRED`

Terminal states are `SUCCEEDED`, `CANCELLED`, `FAILED_ROLLED_BACK`, and
`RECOVERY_REQUIRED`. Do not use a generic `FAILED` when the product must know
whether rollback succeeded.

Define an explicit transition table in code. Repositories accept a compare-and-
transition request containing operation ID, expected current state, target
state, and expected operation revision. Invalid or stale transitions change
nothing.

### 7.3 Separate desired state, effects, and observations

- **Desired state** is validated configuration the operation intends to make
  active.
- **Effects** are external mutations such as download, file publication,
  service restart, or runtime-tree swap.
- **Observations** are health probes, progress counters, temperatures, and
  verification results.

Do not persist a probe result by broadly replacing desired settings. Do not
mark desired state committed merely because an external command returned zero.

### 7.4 Idempotent steps and compensations

Each step has:

- stable step ID and version;
- declared resources;
- input derived from the immutable operation request or prior output;
- precondition probe;
- execution method;
- postcondition/verification probe;
- durable sanitized output;
- optional compensation;
- recovery classifier for “started but completion unknown.”

Retrying a completed step must be a no-op after verifying its postcondition.
Retrying an interrupted step must inspect the world before repeating a side
effect.

### 7.5 Resource serialization

Initial resource keys:

- `runtime-active` — model/config activation and service restart;
- `runtime-installation` — llama.cpp build/swap/rollback;
- `model:<artifact-id>` — one download/import/publish per artifact;
- `model-library` — destructive cleanup/index reconciliation;
- `host-performance` — host tuning changes;
- `thermal-safety` — never acquired to bypass the safety service.

Use database leases with owner ID, acquisition timestamp, heartbeat, and
expiry. A lease permits recovery classification; it does not itself prove that
an old external process is dead. File locks may supplement database leases for
cross-version protection.

### 7.6 Cancellation semantics

- cancellation is a durable request, not thread interruption;
- every step declares whether it is cancellable;
- download/build loops poll between bounded chunks/subprocess phases;
- publish/swap/restart/rollback critical sections defer cancellation;
- cancellation after external mutation enters rollback where required;
- final `CANCELLED` means the pre-operation safe state is restored or no
  externally visible mutation occurred;
- inability to restore yields `RECOVERY_REQUIRED`.

### 7.7 Security and privacy

- operation requests persist references to credentials, never credentials;
- event messages are structured and redacted before database insertion;
- no raw stdout/stderr is persisted without redaction and size limits;
- paths shown to users are normalized but never shell-interpolated;
- adapters receive argv arrays or typed requests;
- event payload schema rejects unknown secret-like keys.

---

## 8. Session 5A — migration 003 and repositories

### 8.1 Migration 003 schema

Add migration 003 atomically. Exact names may follow repository conventions,
but the logical schema should include:

#### `operations`

- immutable operation ID (UUID text);
- operation type and schema version;
- state and state revision;
- sanitized request JSON;
- progress current/total/unit and display summary;
- cancellation requested timestamp;
- created, started, updated, and finished timestamps;
- initiating surface (`cli`, `gui`, `recovery`, future `service`);
- terminal result/error code and sanitized detail;
- recovery policy/version;
- parent operation ID where a composed workflow needs it.

#### `operation_steps`

- operation ID and stable ordered step key;
- step implementation version;
- state (`PENDING`, `STARTED`, `VERIFIED`, `COMPENSATING`, `COMPENSATED`,
  `FAILED`);
- attempt count;
- sanitized input/output JSON;
- started, checkpointed, and finished timestamps;
- external identity/checksum needed to inspect interrupted effects;
- failure code and sanitized detail;
- unique `(operation_id, step_key)`.

#### `operation_events`

- monotonic event ID;
- operation ID;
- timestamp, level, event code, user-facing summary;
- sanitized structured detail;
- optional progress snapshot;
- bounded retention policy.

#### `operation_leases`

- resource key primary key;
- operation ID and worker owner ID;
- acquired/heartbeat/expires timestamps;
- lease revision.

Use foreign keys with explicit delete behavior. Operation history should not be
silently deleted when another domain record is removed.

### 8.2 Migration tests

- migration 001 → 002 → 003 ordering;
- fresh creation reaches schema 003;
- injected failure after each table creation rolls back every migration-003
  table and its schema row;
- fixed retry succeeds;
- a schema-004 fixture is refused;
- foreign keys enforce operation/step/event relationships;
- indexes support active operation, type/state, event cursor, and lease expiry
  queries;
- existing 002 data is byte/row equivalent after migration.

### 8.3 Repository API

Add typed repositories under the existing raw-SQL boundary:

- `OperationRepository.create/get/list_active/list_recent`;
- `compare_and_transition`;
- `update_progress` with monotonic validation;
- `request_cancel`;
- `record_terminal_result`;
- `OperationStepRepository.start/checkpoint/verify/fail/compensate`;
- `OperationEventRepository.append/list_after`;
- `OperationLeaseRepository.acquire/heartbeat/release/list_expired`.

Every mutating method receives a unit-of-work connection. Repository methods
must not commit independently. Multi-row operation changes—state transition,
step checkpoint, and event—commit in one unit.

### 8.4 Repository tests

- duplicate operation ID rejected;
- invalid transition leaves revision/events unchanged;
- stale revision loses deterministically;
- progress cannot move backward unless starting a named new phase;
- cancel request is idempotent;
- terminal result cannot transition again;
- event cursors are stable under concurrent append;
- only one contender acquires a resource lease;
- expired lease takeover increments lease revision;
- an old owner cannot heartbeat or release a replaced lease;
- event/request secret canaries never persist.

Recommended commits:

1. `docs(R3): decide durable operation execution contract`
2. `feat(R3.1): add atomic operation schema migration 003`
3. `feat(R3.1): add typed operation repositories and transitions`

---

## 9. Session 5B — engine, worker, cancellation, and recovery

### 9.1 Module boundaries

Recommended package shape:

```text
bc250_llm_mode/operations/
  __init__.py       public commands and query types
  model.py          enums, requests, results, transition table
  engine.py         enqueue, execute, cancel, recover orchestration
  workflow.py       step/workflow protocols and registry
  recovery.py       interrupted-step classification
  worker.py         bounded executor loop and heartbeat
  redaction.py      operation payload/event sanitization
```

Raw SQL remains in the approved repository layer. Workflow modules use typed
repositories/services/adapters and never import sqlite directly.

### 9.2 Workflow registry and request versioning

Map `(operation_type, request_version)` to a workflow definition. Unknown types
or newer request versions enter repair/reporting behavior; they are never
guessed or discarded. Validate the complete request before inserting `QUEUED`.

The request captures stable identities, not mutable display state. Examples:

- model artifact ID/content digest rather than only an alias;
- expected runtime configuration revision;
- expected known-good fingerprint;
- runtime source commit and build recipe identity;
- composed path profile identity, not derived path strings.

### 9.3 Executor loop

For each operation:

1. Atomically claim the operation and required resource leases.
2. Validate current state and thermal gate.
3. Resume at the first unverified step.
4. Persist `STARTED` before executing an external effect.
5. Run the effect through a typed adapter.
6. Probe the postcondition.
7. Persist output, verification, state transition, and event atomically.
8. Heartbeat during bounded long phases.
9. Observe durable cancellation at safe points.
10. On error, classify and compensate in reverse verified/effected order.
11. Record an exact terminal result and release leases.

Only one operation executes a given resource at a time. Unrelated model
downloads may proceed concurrently if disk/network policy permits, while model
activation and runtime update must serialize around the active server.

### 9.4 Recovery classification

At startup inspect non-terminal operations and expired/missing leases.

- `QUEUED`: safe to claim.
- `PREPARING`: revalidate request and resources.
- `RUNNING`: inspect the current step's external postcondition.
- `VERIFYING`: repeat the read-only verification.
- `COMMITTING`: inspect database/domain state and rendered artifacts before
  retrying publication.
- `CANCEL_REQUESTED`: resume cancellation/rollback.
- `ROLLING_BACK`: continue compensation from its checkpoints.
- `PAUSED`: resume only by policy/user command.

For each interrupted step, classify:

- effect absent → execute;
- effect complete and verified → checkpoint as verified;
- effect complete but unverified → verify;
- partial effect safely resumable → resume;
- partial effect must be discarded → clean staging and retry;
- active state uncertain but restorable → rollback;
- active state unsafe/unknown and not automatically restorable →
  `RECOVERY_REQUIRED` with exact remediation.

### 9.5 Progress and events

Progress must be useful but write-bounded:

- percent only when total work is known;
- otherwise phase plus bytes/items completed;
- throttle persistence by time and meaningful delta;
- always persist phase boundaries;
- keep the latest progress on the operation row;
- append durable events for decisions, warnings, transitions, and results—not
  every output line;
- expose an event cursor for CLI waiting and GUI polling.

### 9.6 Worker lifecycle

The first executor may be a bounded thread owned by the composed application,
but it must:

- have a unique worker ID;
- use fresh units of work, never share a connection across steps;
- stop claiming new work during shutdown;
- finish or checkpoint the current non-interruptible section;
- mark a safe pause where possible;
- never mark success from thread completion alone;
- tolerate frontend disappearance and recover at next composition.

Do not invoke Tk APIs from the worker. GUI updates consume query/event data on
the Tk thread.

### 9.7 Engine foundation tests

- complete no-op workflow;
- failure before first effect;
- failure after effect before checkpoint;
- failure during verification;
- cancellation before start;
- cancellation during cancellable work;
- cancellation deferred during atomic publication;
- rollback success and rollback failure;
- process-death simulation by abandoning the worker and reopening the DB;
- stale lease takeover;
- two worker contention;
- unrelated resource concurrency;
- shutdown checkpoint/recovery;
- redaction canaries in request, event, exception, argv, and logs;
- event retention without deleting terminal result;
- clock-independent tests using injected clock/IDs.

Recommended commits:

1. `feat(R3.2): add operation workflow and executor core`
2. `feat(R3.2): add durable cancellation leases and recovery`
3. `test(R3.2): add operation crash injection harness`

---

## 10. Session 5C — convert model activation first

Model activation is the safest first real operation because its synchronous
service already has validation, handoff, restart, health, inference, known-good
promotion, and rollback semantics.

### 10.1 Refactor without duplicating policy

Extract reusable commands/ports from `ModelActivationService`; do not retain a
second synchronous activation implementation. Frontends enqueue an operation
and may wait for it, but the operation workflow owns side-effect ordering.

The existing typed `RuntimeController` remains the only server-control port.
The workflow never imports `server.py` or `CommandRunner` directly.

### 10.2 Activation request

Persist only validated, non-secret identifiers:

- candidate model installation/artifact ID;
- candidate content digest and validation status;
- requested context, slots, and profile;
- expected configuration revision;
- expected known-good fingerprint;
- initiation reason/surface;
- request schema version.

Do not persist an arbitrary whole-state snapshot.

### 10.3 Activation step sequence

1. **Resolve candidate:** ensure artifact identity still matches the request.
2. **Thermal gate:** refuse while latched or sensor policy is unsafe.
3. **Fit validation:** calculate fit from current authoritative inputs.
4. **Acquire `runtime-active`:** serialize activation/restart.
5. **Capture recovery record:** verify current known-good config and runtime
   component identity.
6. **Publish candidate desired config:** one unit of work, expected revision.
7. **Render handoff:** from the committed candidate revision.
8. **Restart server:** through the typed controller.
9. **Health verification:** process/service and HTTP readiness.
10. **Minimal inference probe:** bounded prompt, timeout, and response sanity.
11. **Promote known-good:** only after all verification succeeds.
12. **Record success and release lease.**

Failure after candidate publication triggers compensation:

1. restore prior runtime config;
2. regenerate prior handoff;
3. restart prior runtime;
4. verify health and minimal inference;
5. retain prior known-good record;
6. terminal state `FAILED_ROLLED_BACK`.

Any failed compensation records `RECOVERY_REQUIRED`, preserves evidence, and
blocks further activation until repair.

### 10.4 Activation crash matrix

Inject termination before and after every durable checkpoint, especially:

- before candidate commit;
- after candidate commit but before handoff;
- after handoff but before restart;
- after restart but before health result;
- after health but before inference result;
- after inference but before known-good promotion;
- during each rollback step;
- after known-good promotion but before operation terminal event.

On recovery, assert the actual runtime, handoff fingerprint, desired config,
known-good row, operation status, and service health agree. No test may accept
“operation failed” without proving which configuration is active.

### 10.5 Frontend transition

- CLI activation enqueues and waits by default for backward-compatible
  scripting; add an explicit detach/no-wait option if useful.
- GUI activation immediately shows an operation ID and opens/focuses Activity.
- Chat model switch uses the same command and cannot bypass fit/thermal gates.
- Existing synchronous API becomes a thin enqueue-and-wait compatibility call
  only if needed temporarily; remove it by the end of Session 5C.

### 10.6 Session 5 stop gate

- model activation has one implementation: the workflow;
- all activation steps and compensations are crash-tested;
- no facade or whole-state persistence returns;
- synchronous CLI behavior still provides a correct exit code/result;
- failed rollback visibly blocks future activation;
- source/editable tests and compile pass;
- docs identify model acquisition as the next workflow.

Recommended commits:

1. `refactor(R3.3): express model activation as durable steps`
2. `feat(R3.3): route model activation through operation engine`
3. `test(R3.3): crash-inject activation and rollback checkpoints`

---

# Part III — Session 6: acquisition, runtime update, and Activity

## 11. Session 6A — model acquisition/import/validation operation

### 11.1 Artifact identity and staging

Before conversion, define the final model artifact contract:

- stable installation/artifact ID;
- SHA-256 or stronger content digest;
- exact byte size;
- source URL/repository/revision/file identity;
- local final path derived from the configured models directory;
- validation status and validator version;
- GGUF metadata summary;
- catalog association/alias;
- acquisition timestamp and provenance;
- quarantine reason where validation fails.

Never identify a model solely by mutable filename or catalog display name.

Use an operation-specific staging directory under composed paths. Final paths
become visible only through atomic rename after content and GGUF validation.

### 11.2 Acquisition step sequence

1. Validate source policy and request.
2. Resolve expected identity/size/digest where available.
3. Check free space for download, temporary validation overhead, and reserve.
4. Acquire `model:<artifact-id>` lease.
5. Inspect existing staging metadata and choose resume/restart.
6. Download or copy in bounded chunks with timeouts and progress.
7. Verify size and content digest.
8. Parse and validate GGUF metadata safely with bounded reads.
9. Enforce supported architecture/quantization/tensor policy.
10. Calculate representative fit verdicts without activating the model.
11. Atomically publish the artifact.
12. Register installation/provenance in the same logical commit boundary.
13. Clean staging and record success.

For local import, use the same validation/publication path after the source-copy
step. Never run a model directly from an arbitrary user-selected location.

### 11.3 Resume and failure policy

- resumable HTTP requires matching URL identity, ETag/Last-Modified policy, and
  local partial length;
- if identity changed, quarantine/delete only the operation-owned partial and
  restart;
- digest mismatch quarantines with a user-readable reason;
- malformed GGUF never enters the model library;
- cancellation removes or retains partial data according to explicit “resume
  later” policy;
- failure never overwrites an existing verified artifact;
- duplicate digest reuses the existing artifact and may add a catalog alias
  without copying bytes.

### 11.4 Acquisition tests

- clean download and local import;
- timeout and network interruption at multiple chunk boundaries;
- resume with valid range/identity;
- server ignores range request;
- ETag/source changes;
- disk fills before and during download;
- expected size mismatch and digest mismatch;
- malformed/truncated/unsupported GGUF;
- duplicate content under different names;
- cancellation before/during/after validation;
- crash before/after atomic publication;
- database commit failure after file publication is reconciled safely;
- two processes request the same artifact;
- malicious filename/path traversal rejected;
- token/source URL redaction;
- custom models directory and sentinel-HOME isolation.

---

## 12. Session 6B — llama.cpp update and rollback operation

### 12.1 Preserve the proven staging contract

The existing update path already leaves the active checkout untouched until a
staged build passes smoke checks. Convert it into steps without weakening that
rule.

Persist immutable provenance:

- source repository identity;
- requested and resolved commit SHA;
- submodule state if used;
- build configuration/toolchain identity;
- produced binary digest/version output;
- prior active tree identity;
- handoff/runtime compatibility result.

Mutable branch names or image tags may be user input, but the resolved commit
is the durable operation identity.

### 12.2 Update step sequence

1. Validate request and supported source policy.
2. Acquire `runtime-installation` and coordinate with `runtime-active`.
3. Check disk space and toolchain preconditions.
4. Create operation-owned staging tree.
5. Fetch/resolve exact commit.
6. Configure and build with bounded command execution and progress phases.
7. Run binary version and Vulkan/backend smoke checks.
8. Verify compatibility with current desired/known-good runtime.
9. Capture prior active tree and component provenance.
10. Stop/restart only at the declared activation boundary.
11. Atomically swap staged and active trees.
12. Regenerate handoff if component identity participates in fingerprinting.
13. Start and run health plus minimal inference verification.
14. Commit new component provenance.
15. Retain bounded rollback tree and clean obsolete staging.

### 12.3 Rollback policy

If activation or verification fails:

- stop the failed runtime where necessary;
- atomically restore the prior active tree;
- restore prior provenance and compatible handoff;
- restart and verify known-good inference;
- record `FAILED_ROLLED_BACK` if restoration succeeds;
- record `RECOVERY_REQUIRED` and block further updates if it does not.

An explicit user rollback is itself an operation and uses the same verification
path. It is not a raw directory swap command.

### 12.4 Runtime update tests

- staged fetch/build/smoke success;
- failure at every pre-swap step leaves active tree byte-identical;
- crash immediately before and after swap;
- health/inference failure restores prior tree;
- rollback restart failure enters recovery required;
- cancellation during fetch/build and deferral during swap;
- source branch moves after request but resolved SHA remains fixed;
- command timeouts and bounded stderr capture;
- symlink/path traversal defense in staging/publication;
- insufficient disk and cleanup;
- concurrent activation/update serialization;
- provenance and binary digest accuracy;
- no secret or arbitrary path shell interpolation.

---

## 13. Session 6C — operation CLI and minimal Activity UI

### 13.1 CLI contract

Add commands with stable machine-readable behavior:

```text
operations list [--active|--all] [--json]
operations show OPERATION_ID [--events] [--json]
operations wait OPERATION_ID [--timeout SECONDS] [--json]
operations cancel OPERATION_ID [--json]
operations recover OPERATION_ID [--json]
```

Rules:

- JSON mode writes one valid JSON document to stdout and diagnostics to
  stderr;
- waiting handles Ctrl-C with exit 130 without corrupting the operation;
- timeout is bounded and returns a documented nonzero code while operation
  continues;
- unknown ID, invalid transition, conflict, cancellation accepted, success,
  rolled-back failure, and recovery-required have distinct documented error
  codes/fields;
- `recover` never bypasses a thermal latch, integrity failure, or unknown
  external state;
- command help explains whether the invoking process must stay open for the
  current executor deployment.

### 13.2 Minimal GUI Activity view

This is a functional view, not a full redesign. It should show:

- active operations first, then bounded recent history;
- operation type, human-readable target, current phase, progress, elapsed
  time, and terminal result;
- expandable structured event timeline;
- cancel button only when cancellation is meaningful;
- retry/recover action only when the engine reports it safe;
- recovery-required banner with exact next action;
- “paused/interrupted” wording when no executor currently owns the operation;
- links back to Model Library or runtime maintenance context.

Implementation constraints:

- poll/query from the Tk event loop; no cross-thread widget mutation;
- cursor-based event fetching to avoid rereading all history;
- no generic state save;
- no raw exception trace shown by default;
- accessibility: keyboard reachable actions, textual progress, status not
  communicated by color alone;
- closing the window does not cancel an operation.

### 13.3 Notifications and user guidance

At operation completion:

- GUI displays a durable success/failure summary when next opened;
- CLI waiting prints the final verified outcome;
- cancellation explains whether partial download was retained;
- rolled-back failure clearly says the prior runtime was restored;
- recovery-required never recommends repeating the operation blindly;
- errors use stable codes mapped to remediation text.

Avoid desktop notifications until they can be implemented and permissioned
consistently; the in-app Activity record is the 0.9 source of truth.

---

## 14. R3 exit gate

R3 is complete only when all of the following are true.

### Architecture

- operations schema is migration 003 and migrates atomically;
- one transition table governs every operation type;
- no workflow writes raw SQL or constructs its own paths;
- all external effects pass through typed adapters;
- resource leases prevent incompatible concurrent operations;
- no workflow regresses to whole-state saves or compatibility storage;
- operation events and requests pass redaction/size validation.

### Recovery and correctness

- model activation, model acquisition/import, and runtime update/rollback are
  durable workflows;
- each effecting step has precondition, postcondition, and recovery behavior;
- crash injection exists before/after every critical checkpoint;
- cancellation is tested at every declared safe point;
- rollback success and failure are distinguishable;
- `RECOVERY_REQUIRED` blocks unsafe follow-on mutation;
- startup recovery is deterministic and idempotent;
- thermal latch and fit gate are checked using authoritative current state.

### User experience

- long commands return/show an operation ID;
- CLI can list, show, wait, cancel, and recover;
- GUI Activity survives application restart;
- progress does not falsely reach 100% before verification;
- terminal outcomes explain success, cancellation, restored rollback, or manual
  recovery;
- frontend closure never silently cancels or marks success.

### Verification

- full source and editable tests pass;
- compile and wheel tests pass;
- operation tests run repeatedly to expose races;
- test suite has no unbounded network or subprocess wait;
- architecture guards pass;
- `git diff --check` and working tree checks pass;
- docs/ADR/schema/CLI help/changelog/AGENTS are current.

### Suggested repeated stress battery

Run focused concurrency/recovery tests at least 10 times:

```bash
.venv/bin/python -m pytest -q tests/test_operations.py --count=10
.venv/bin/python -m pytest -q tests/test_model_activation_operation.py --count=10
.venv/bin/python -m pytest -q tests/test_model_acquisition_operation.py --count=10
.venv/bin/python -m pytest -q tests/test_runtime_update_operation.py --count=10
```

If `pytest-repeat` is not a project dependency, use the repository's existing
repeat harness or a small shell loop; do not add a runtime dependency solely
for this command.

---

## 15. Test strategy and fakes

### 15.1 Keep tests deterministic

Inject:

- monotonic/wall clock;
- UUID/worker ID source;
- filesystem fault points;
- downloader/source adapter;
- command/runtime controller;
- health and inference probes;
- disk-space probe;
- thermal service/query;
- event sink where needed.

Do not use sleeps to establish ordering. Use barriers/events in concurrency
tests and explicit fault injection around checkpoints.

### 15.2 Layer the test suite

1. Pure transition/request/redaction tests.
2. Repository tests with temporary SQLite.
3. Engine tests with fake workflows/adapters.
4. Workflow tests with real repositories and fake effects.
5. Filesystem integration tests using temporary directories.
6. CLI parser/output/exit-code tests.
7. Headless GUI Activity contract tests.
8. Packaging and clean-wheel smoke tests.
9. Later—not in this plan—Linux VM and BC-250 hardware tests.

### 15.3 Failure injection vocabulary

Use stable named fault points such as:

```text
before_step_start
after_step_start
after_external_effect
before_verification
after_verification
before_step_checkpoint
after_step_checkpoint
before_compensation
after_compensation_effect
before_terminal_transition
```

Each workflow may add domain-specific points, but the common vocabulary makes
the crash matrix auditable.

### 15.4 Assertions after every crash test

Check all relevant layers, not only operation state:

- database operation/step/event/lease rows;
- domain configuration and known-good records;
- handoff fingerprint/revision;
- active and staged filesystem trees;
- model artifact digest/registration;
- server health and minimal inference result;
- thermal latch;
- next recovery decision;
- no secrets in logs/events;
- no writes outside the injected profile.

---

## 16. Review and commit discipline

### Commit rules

- One architectural or behavioral unit per commit.
- Cite `R2.2`, `R1`, or `R3.x` in each subject.
- Tests land with or before behavior.
- No opportunistic security/UI refactor mixed into operation commits.
- Update exact guard counts in the same commit that changes them.
- Never amend migration 003 after it has been used by a committed fixture;
  follow-up schema changes use migration 004.
- Keep the working tree clean at every session handoff.
- Pushing, tagging, or publishing remains the repository owner's decision.

### Review checklist for every effecting step

- Is the input typed, validated, and immutable?
- Is the resource lease sufficient and narrowly scoped?
- Is intent/checkpoint persisted before the external effect?
- Can retry duplicate the effect?
- How is completion probed after a crash?
- Is compensation safe and idempotent?
- What happens if compensation crashes?
- Can cancellation occur here?
- Are timeout and output bounds explicit?
- Can a secret enter argv, logs, events, or the database?
- Does the step obey thermal, fit, boot, and known-good invariants?
- What exact user-visible result appears?

---

## 17. Immediate execution checklist

The next model should begin here, in this exact order:

1. Confirm `b079952`, clean tree, and 296-test baseline.
2. Read ADR 001, current state schema, this plan, and the completed Session 3
   commits.
3. Repair and test numeric migration ordering per §3.1.
4. Add the native-composition v5 import/query test from §4.1.
5. Classify and split `tests/test_cutover.py` by owning layer.
6. Remove `Application.store`, `wrap()`, generic diff persistence, and the
   activation store supplier while keeping all tests green.
7. Replace transaction fallbacks in bootstrap/chat/thermals/tune.
8. Add the pure legacy canonicalizer and remove importer dependence on
   `StateStore`.
9. Make `--state` import-only with parser/no-write tests.
10. Delete `compat_state.py` and obsolete compatibility tests.
11. Run the full R1/R2 matrix, update docs, commit, and stop.
12. In the following session, write ADR 002 and add atomic migration 003.
13. Implement and test the operation transition/repository foundation before
    writing any real workflow.
14. Add executor/recovery/cancellation with fake workflows.
15. Convert and crash-test model activation.
16. Stop and review before model acquisition/runtime update.
17. Convert acquisition, then runtime update, then add CLI/Activity surfaces.
18. Pass the R3 exit gate and stop feature expansion at this plan's boundary.

### Session 4 first red test

Compose from a frozen legacy fixture and assert the complete native query
snapshot without importing or mentioning `CompatStateStore`.

### Session 5 first red tests

1. A migration-003 failure after creating the first operation table leaves no
   operation tables and no version row; fixed retry succeeds.
2. An invalid or stale operation-state transition changes no row, revision, or
   event.

### Final handoff report format

Every session report should include:

- HEAD and clean/dirty status;
- commits and plan IDs;
- exact source/editable/wheel test results as applicable;
- architecture guard results;
- completed acceptance criteria;
- bugs found by behavioral/crash tests;
- deliberately deferred scope;
- exact next red test and stop condition.

This prevents the next session from reconstructing intent from commit history
and keeps the road to 1.0 bounded, evidence-driven, and recoverable.
