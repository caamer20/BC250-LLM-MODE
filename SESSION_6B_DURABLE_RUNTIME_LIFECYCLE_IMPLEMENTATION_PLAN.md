# Session 6B — Durable llama.cpp Runtime Lifecycle Implementation Plan

**Status:** DONE (all ten commit boundaries landed; U1.2 exit gate green — evidence in §24)  
**Checkpoint authority:** `85d6db3` and descendants of `e8d91c3` (`0.9.0.dev0`)  
**Verified starting baseline:** 552 default tests green; acquisition security/stress and clean-wheel slow gates green  
**Roadmap scope:** `ULTIMATE_BC250_APPLIANCE_IMPLEMENTATION_PLAN.md` §U1.2  
**Durable-operation contract:** `docs/adr/002-durable-operations.md`  
**Required new decision record:** ADR 004 — immutable runtime builds and atomic activation  
**Required schema migration:** 005 (004 is already model artifacts)  
**Target operation versions:** `RUNTIME_UPDATE v1`, `RUNTIME_ROLLBACK v1`  
**Session stop:** close U1.2 and stop before U1.3 worker lifecycle, U1.4 Activity UI, or broad R4 adapter convergence

---

## 1. Executive outcome

At the end of this plan, llama.cpp update and rollback must no longer be
synchronous functions that mutate `/root/llama.cpp` with interpolated shell
scripts and best-effort in-memory rollback. They must be two versioned durable
operations using the same registry, enqueue service, execution engine, lease
fencing, recovery protocol, and composition root already used by model
activation and acquisition.

The finished product must provide these user-visible guarantees:

1. An update names a human-friendly ref, but the application resolves and
   durably records an immutable source commit before it fetches or mutates
   anything.
2. A candidate is built away from the active runtime, under an operation-owned
   path, with bounded progress, timeout, cancellation, output, and resource
   usage policies.
3. The candidate is smoke-tested and assigned a content-derived build identity
   covering source, recipe, build environment, and binaries before activation.
4. Activation is one no-gap atomic filesystem exchange. A crash at any point is
   classified from exact identities rather than filenames, desired state, or a
   mutable tag.
5. A successful restart proves the new systemd invocation, active component
   identity, model identity, context/slot shape, health, and bounded inference.
6. The component identity and known-good runtime are promoted only after those
   observations agree.
7. Any failed update restores the exact prior tree, handoff, service state, and
   known-good identity and verifies them. If that cannot be proved, the
   operation enters `RECOVERY_REQUIRED` and retains every potentially useful
   tree.
8. Rollback selects an exact retained build from durable state and uses the
   same atomic, observed, crash-recoverable protocol. It is not “move whatever
   happens to be in `-backup`.”
9. CLI, wizard, and dashboard all call one composed runtime lifecycle command
   service. The old update and rollback implementations are deleted and guarded
   against reintroduction.
10. A fresh installation obtains its first llama.cpp runtime through the same
    immutable, durable update workflow. Setup cannot silently clone a mutable
    default branch into the active path.

This is the final transactional-operation workflow needed for the R3 operation
gate. Background continuation after a frontend closes remains explicitly
deferred to U1.3; Session 6B must report that limitation honestly.

---

## 2. Starting point and reconciled facts

The executor must begin by confirming, not assuming, all of the following:

- `git rev-parse HEAD` is `7d81ad5` or a reviewed descendant containing the
  complete U1.1 checkpoint.
- The default suite reports 552 passing tests at this checkpoint. The terminal
  summary from `tests/conftest.py` is authoritative; progress dots are not.
- The slow acquisition security/stress test and clean-wheel test pass when run
  explicitly.
- `SCHEMA_VERSION` is 4.
- Migration 004 owns model artifact tables; migration 005 is unused.
- `OperationType` already reserves `RUNTIME_UPDATE` and `RUNTIME_ROLLBACK`.
- ADR 002 already freezes both requests at version 1.
- The one operation registry currently composes activation, acquisition, and
  import workflows. Runtime workflows are not yet registered.
- `server.py` remains the sole owner of `bc250-llm.service`.
- There is no background operation worker or Activity UI. Do not imply either
  exists.
- The user-owned untracked plan/audit files must remain untouched.

Before implementation, run and record:

```bash
git status --short
git rev-parse --short HEAD
PYTHONPATH=. .venv/bin/pytest -q
.venv/bin/pytest -q -m slow tests/test_acquisition_security_stress.py
.venv/bin/pytest -q -m slow tests/test_packaging.py
python -m compileall -q bc250_llm_mode tests
git diff --check
```

If the baseline differs, update the evidence header and explain why before
changing production behavior. Never “restore” test counts by recreating tests
that were intentionally removed.

### 2.1 Documentation drift to correct at closeout

The current `AGENTS.md` begins with the correct 552-test U1.1 checkpoint but
still contains older narrative fragments describing a 505-test baseline,
`compat_state.py`, `load_state_with_paths`, and Session 6A as future work. These
are documentation defects, not architectural truth. Correct them in the final
documentation boundary only; do not let them drive implementation backward.

---

## 3. Audit verdict: what is unsafe or incomplete today

The legacy implementation is useful as a behavior inventory, but it is not a
production-safe implementation of U1.2.

### P0 — must be eliminated in this session

#### F6B.0.1 — the active tree can disappear during a crash

`env.update_llamacpp()` performs two separate moves:

1. active root to backup;
2. staging root to active.

A crash between those operations leaves the active path absent. The matching
rollback path has the same multi-move problem. Calling this sequence “atomic”
is inaccurate. Session 6B must use a single no-gap atomic exchange primitive
and refuse mutation when the filesystem cannot support it.

#### F6B.0.2 — no durable intent, effect identity, or takeover recovery

Update and rollback currently exist only on the frontend call stack. Process
death after a tree move, handoff publication, restart, or persistence update
cannot determine whether to continue, retry, or restore. The existing
operations engine must own every external effect and checkpoint.

#### F6B.0.3 — mutable source and build environment identities

Setup uses `registry.fedoraproject.org/fedora:latest` and clones the llama.cpp
default branch. Update accepts a tag and mutates/fetches through the active
checkout before a candidate exists. A mutable ref may be displayed, but it may
not be the production identity. The full source commit and the observed
container image ID/digest must be frozen before build.

#### F6B.0.4 — exact running component identity is not verified

`restart_and_wait` establishes health only. A healthy process can still be the
prior binary, a stale launcher, or the wrong model/config. Success must require
independent evidence tying a new systemd invocation to the intended active
binary manifest, plus model/config observation and minimal inference.

#### F6B.0.5 — rollback is one unnamed mutable directory

`llamacpp_history` records at most one mapping while the filesystem relies on
`/root/llama.cpp-backup`. The database does not prove which tree is there, and
the path can be missing, stale, or replaced. Rollback must select a build ID
whose retained tree identity is verified immediately before exchange.

#### F6B.0.6 — recovery can destroy the only useful evidence

The old code uses interpolated `rm -rf` commands on fixed staging/backup names.
An uncertain recovery must never delete either potential active/prior tree.
Cleanup may remove only paths proven to be owned by the current operation and
not referenced by active, rollback, known-good, quarantine, or another live
operation.

### P1 — required to close U1.2

#### F6B.1.1 — shell interpolation and unbounded commands

The lifecycle uses `bash -lc` for Git, build, tests, deletion, and swaps.
`CommandRunner` has no complete timeout/cancellation/output/process-group
contract. Runtime lifecycle commands must use typed argv or one fixed,
hash-checked helper. No caller-provided ref or path may become shell source.

#### F6B.1.2 — the executor takes every lease before preflight

`WorkflowDefinition.all_resources()` and `ExecutionEngine._claim()` acquire all
workflow resources at operation start. That would hold `runtime-active` during
a potentially hour-long fetch/build, blocking model changes and contradicting
ADR 002’s “activation boundary” requirement. Add a generic, atomically fenced
phase-resource mechanism before registering runtime workflows.

#### F6B.1.3 — setup can bypass the future durable path

Even if update/rollback are converted, `setup_environment()` currently builds
llama.cpp synchronously from a mutable branch. Split environment provisioning
from runtime acquisition. The first runtime build must be an ordinary
`RUNTIME_UPDATE v1` targeting the shipped pin.

#### F6B.1.4 — handoff identity ignores runtime content

The current handoff fingerprint includes `llama_cpp_path`, not the build
identity at that path. Swapping content at the same path does not necessarily
regenerate the artifact. Handoff schema v2 must carry the expected runtime
build and server binary identity and participate in the fingerprint.

#### F6B.1.5 — persistence is a mutable projection

`component_provenance` stores only the current describe/commit, while
`llamacpp_history` is a mutable settings value. Neither models immutable build
manifests, retained trees, promotion generation, or rollback lineage.
Migration 005 and typed repositories must become authoritative.

#### F6B.1.6 — frontend ownership is duplicated

CLI and GUI call `ComponentLifecycleService`, which calls `env.py` directly;
the dashboard may then perform caller-side persistence. All surfaces must call
one command service and must not commit runtime component state themselves.

### P2 — improve now where naturally adjacent

- Status should distinguish requested ref, resolved commit, build identity,
  active observed identity, promoted identity, rollback availability, update
  availability, operation progress, and recovery barriers.
- Errors should be stable codes with actionable remediation, not raw command
  output or “inspect directories.”
- Re-running the same target should return a verified no-op when it is already
  active, avoiding an unnecessary rebuild/restart.
- Disk preflight should account for source, build tree, active tree, retained
  prior tree, temporary output, and a fixed safety margin.
- Runtime build retention needs an explicit policy; unbounded backup trees are
  not acceptable.

---

## 4. Scope, non-goals, and hard stops

### 4.1 In scope

- ADR 004 and ADR 002 errata/addenda required by the implementation.
- Migration 005 and typed runtime-build repositories.
- Phase-scoped resource leasing in the generic operation executor.
- Pure `RUNTIME_UPDATE v1` and `RUNTIME_ROLLBACK v1` workflow definitions.
- One typed production runtime host adapter.
- One bounded/cancellable process execution port for this workflow.
- Immutable source resolution and build-manifest creation.
- Operation-owned runtime staging and retained-tree management.
- Exact tree observation and atomic exchange.
- Handoff schema v2 and runtime start receipt/identity observation.
- Restart, health, exact model/config observation, and bounded inference.
- Verified promotion and complete rollback/compensation.
- One composed command service and all current frontend cutovers.
- Initial setup cutover to the durable pinned-runtime workflow.
- Architecture, security, crash, stress, packaging, and documentation gates.

### 4.2 Explicitly out of scope

Do not start any of the following merely because the new abstractions make them
tempting:

- U1.3 independently supervised/background worker lifecycle.
- U1.4 full Activity Center, operation history browser, or detach UX.
- U2.3 conversion of every command in the application to the new process port.
- Catalog source conversion. Keep the current fail-safe refusal and caveat.
- Broad `services.py` package decomposition from R4.
- Generic privileged helper/supervisor from R5.
- Update signing/TUF, transparency log, SBOM publication, or release signing.
- Automatic internet update checks.
- Open WebUI authenticated gateway work.
- Real-hardware certification or version/tag/push/release actions.

### 4.3 Hard stop

Stop when the U1.2 exit gate in §18 is green, the evidence is committed, and
the next task is named U1.3. Do not opportunistically add a daemon, autostart
policy, tray resident worker, or new background promise.

---

## 5. Frozen design decisions (ADR 004)

Write and accept `docs/adr/004-immutable-runtime-lifecycle.md` before migration
005. It must freeze all decisions below.

### D1 — a build ID is content-derived, not a tag

Use a canonical manifest encoded with sorted compact JSON. The build ID is:

```text
llamacpp:sha256:<sha256(canonical build manifest)>
```

The immutable manifest includes at least:

- schema/version and component name;
- fixed upstream repository identity;
- requested ref for display only;
- resolved full source commit;
- source checkout verification result;
- build recipe version and recipe digest;
- exact CMake generator/options/targets and parallelism policy;
- observed container image ID and digest when available;
- bounded toolchain identities (`cmake`, `ninja`, compiler, linker, libc);
- target architecture/platform;
- binary entries for `llama-server`, `llama-cli`, and `llama-quantize`:
  relative path, size, executable mode, full SHA-256, and bounded version-output
  digest;
- smoke-test contract version.

Requested tag, timestamps, paths, operation IDs, progress, and mutable status
must not affect the build ID.

### D2 — immutable resolution precedes fetch/build mutation

The production source is a fixed reviewed upstream URL. The request may carry a
bounded tag/ref or exact commit but no URL, path, shell, build option, image,
container, or command. Source resolution:

1. validates the ref as data;
2. resolves it to one full commit (peeling annotated tags);
3. persists source-resolution evidence;
4. on recovery, refuses if the same mutable ref now resolves differently;
5. fetches/checks out the recorded commit, never “whatever the ref means now.”

The shipped default pin remains the ordinary safe path. An exact commit is
accepted when policy permits. A tag remains display metadata after resolution.
No mutable ref is ever called the active runtime identity.

### D3 — build environment identity is observed and frozen

The session does not need to redesign all container provisioning, but it must
record the immutable image ID/digest of the actual build container and the
toolchain identity. If a stable image identity cannot be observed, the build
fails before source/build mutation with `BUILD_ENVIRONMENT_UNPROVEN`.

The literal `fedora:latest` may remain a user-facing provisioning source only
until the later container-supply-chain phase; it cannot appear as the sole
identity of a promoted build. Document this distinction honestly.

### D4 — active-tree mutation is one atomic exchange

Use Linux `renameat2(..., RENAME_EXCHANGE)` (or an equivalently reviewed
single-syscall exchange with the same semantics) between:

- the validated active runtime root; and
- a validated candidate/rollback tree on the same filesystem.

Requirements:

- preflight proves both paths are directories, not symlinks;
- both are under one approved container runtime root;
- both are on the same filesystem;
- the active and candidate manifests match the expected identities;
- the helper refuses `/`, empty paths, parent traversal, aliases, and overlap;
- parent directories are fsynced after exchange;
- lack of exchange support fails safely before mutation;
- there is no fallback to `mv active backup && mv candidate active`.

The helper is a fixed package resource with a recorded source digest. If it is
copied into the container, it is copied to an operation-owned path, verified,
invoked with typed argv, and removed only when safe.

### D5 — exact observation is a chain of independent evidence

“The intended runtime is running” is true only when all required evidence
agrees:

1. active tree manifest says build ID B;
2. full digest of the active `llama-server` equals B’s manifest;
3. handoff schema v2 expects B and the same binary digest;
4. a launcher/start receipt records B, the binary digest, operation ID, and the
   systemd invocation/start boundary immediately before `exec`;
5. systemd observation proves a new successful invocation after the swap;
6. service observation proves the expected model alias, context, and slots;
7. bounded minimal inference succeeds.

The receipt alone is not proof that the process remained alive; health and
inference are required. Desired database/handoff values are never substituted
for observed service values.

### D6 — promoted state means verified known-good, not “files moved”

The authoritative runtime component row changes only after D5 is satisfied.
Promotion occurs in one database unit of work that:

- advances the runtime component generation;
- records the new promoted build;
- records the exact prior build as rollback target;
- records retained-tree locations/identities;
- updates the known-good runtime component identity while preserving its model
  configuration;
- records compatibility provenance if a projection is temporarily required.

Do not write “candidate active” to authoritative promoted state before live
verification. The operation row/evidence represents the in-progress external
reality.

### D7 — rollback is symmetric and toggle-safe

`RUNTIME_ROLLBACK v1` names the current promoted build and exact retained target
at enqueue time. At cutover it revalidates both. On success, the old active
build becomes the next rollback target, allowing a user to undo an accidental
rollback without rebuilding.

### D8 — recovery prefers evidence, never optimism

For every tree exchange probe:

- target identity active and prior identity in the expected retained path:
  classify COMPLETE and checkpoint without a second exchange;
- prior identity active and target identity still in the candidate path:
  classify ABSENT and execute the original exchange once;
- identities prove the reverse exchange completed: classify RESTORED;
- neither arrangement is exact, a tree is missing, duplicate identities are
  ambiguous, or a symlink/containment check fails: classify UNCERTAIN and enter
  `RECOVERY_REQUIRED` without deleting or exchanging anything.

### D9 — cancellation is honored only before the activation boundary

- resolution, fetch, configure, and build contain bounded safe pulses;
- process cancellation terminates the process group, waits a bounded grace
  period, then kills if necessary;
- before swap, cancellation cleans or retains only operation-owned staging and
  proves the active tree unchanged;
- once tree exchange intent begins, cancellation is deferred until the update
  either succeeds or restores the prior runtime;
- cancellation must never leave “new files but old DB” mislabeled as success.

### D10 — one service owner remains absolute

Runtime workflow modules and adapters never invoke `systemctl` or write the unit
directly. They call a typed runtime-server port implemented through `server.py`.
Architecture tests enforce that only `server.py` contains the service literal
or systemd mutation.

### D11 — no dual runtime lifecycle route

After cutover there is one production route:

```text
CLI / wizard / dashboard / setup
        -> RuntimeLifecycleCommandService
        -> shared EnqueueService
        -> shared ExecutionEngine factory
        -> RUNTIME_UPDATE v1 or RUNTIME_ROLLBACK v1
        -> one RuntimeLifecycleHostAdapter
```

Delete the old synchronous update/rollback functions and service methods in the
same boundary that wires the last caller. Never leave both routes callable.

---

## 6. Target module and dependency shape

Create or converge on this structure:

```text
bc250_llm_mode/
  operations/
    runtime_lifecycle.py       # requests, evidence, typed port, pure workflows
    engine.py                  # generic phase-resource correction only
    workflow.py                # phase-resource contract only
    repositories.py            # operation/lease repositories (generic)
  runtime_lifecycle_adapter.py # one production host adapter
  runtime_lifecycle_command.py # enqueue/execute/terminal mapping
  runtime_builds.py            # manifests, identities, typed repositories/query
  runtime_process.py           # bounded process port + production implementation
  runtime_exchange_helper.py   # fixed reviewed helper/resource, no app imports
  runtime_handoff.py           # schema v2 renderer/observer
  server.py                    # sole systemd service owner/observation seam
  app.py                       # one composed graph/registry/engine factory
```

Import direction:

```text
pure workflow -> Protocol/dataclasses only
adapter       -> repositories + runtime_process + runtime_builds + server port
command       -> enqueue + shared engine factory + query result mapping
frontends     -> Application.runtime_lifecycle only
```

Forbidden imports in `operations/runtime_lifecycle.py`:

- `subprocess`, `sqlite3`, `tkinter`, `urllib`, `httpx`, `pathlib.Path`;
- `env`, `server`, GUI, chat, repositories, or global state;
- shell source, service names, host paths, URLs, or credentials.

The adapter may import concrete infrastructure. Frontends may not.

---

## 7. Migration 005 and persistence contract

### 7.1 Tables

Migration 005 must be forward-only, atomic, and filesystem-free.

#### `runtime_builds`

Immutable build records:

| Column | Contract |
| --- | --- |
| `build_id` | PK; `llamacpp:sha256:<64 lowercase hex>` or deterministic legacy ID |
| `component` | CHECK exactly `llamacpp` for v1 |
| `manifest_version` | positive integer |
| `manifest_json` | canonical, bounded, secret-refusing JSON |
| `manifest_digest` | 64 lowercase hex; agrees with non-legacy build ID |
| `source_commit` | full immutable commit or NULL for legacy backfill |
| `requested_ref` | bounded display metadata; nullable |
| `recipe_version` | positive integer |
| `provenance_class` | CHECK: `LEGACY_UNVERIFIED` or `IMMUTABLE_SOURCE`; live verification belongs only in the append-only verification table |
| `created_by_operation_id` | nullable FK to operations |
| `created_at` | UTC timestamp |

Do not update an existing manifest row. Re-inserting the same build ID must
compare canonical bytes and be idempotent; differing bytes are corruption.

#### `runtime_build_verifications`

Append-only observed verification facts:

- monotonic ID;
- build ID FK;
- operation ID FK;
- verification kind (`SMOKE`, `ACTIVE_HEALTH`, `ACTIVE_INFERENCE`,
  `RESTORED_HEALTH`, `RESTORED_INFERENCE`);
- bounded evidence JSON;
- observed timestamp.

Never store inference prompt or generated text. Store counts, latency bucket,
and digest-free boolean/result metadata only.

#### `runtime_trees`

Tracks trees the application owns or has adopted:

- stable `tree_id`;
- build ID FK;
- container profile identity;
- locator relative to the approved runtime storage root;
- role CHECK (`ACTIVE_OBSERVED`, `CANDIDATE`, `ROLLBACK`, `RETAINED`,
  `QUARANTINED`);
- tree manifest digest and server binary digest;
- creating operation ID;
- ownership class CHECK (`OPERATION_OWNED`, `LEGACY_ADOPTED`);
- last observation timestamp.

Location/role may change only through repository methods paired with operation
evidence. A locator is never accepted from a request.

#### `runtime_component_state`

One row for `llamacpp`:

- `component` PK;
- `promoted_build_id` nullable FK;
- `rollback_build_id` nullable FK;
- `generation` positive monotonic integer;
- promoted/rollback tree IDs nullable FK;
- last successful operation ID;
- updated timestamp.

“Promoted” means live verification completed. This row must not be used as an
observation during an in-flight swap.

### 7.2 Legacy backfill

Migration code performs no Podman, filesystem, Git, or service calls.

- If `component_provenance` contains llama.cpp data, insert one deterministic
  `legacy:llamacpp` build row with bounded legacy metadata and
  `LEGACY_UNVERIFIED` provenance.
- Do not claim an active tree or rollback tree from `llamacpp_history`.
- Leave old settings bytes untouched for downgrade/forensic compatibility.
- The first runtime preflight may adopt a legacy active tree only after exact
  host observation creates a manifest and repository record.
- A missing or malformed legacy value cannot block schema migration.

### 7.3 Repository APIs

Add typed repositories with no raw SQL outside repository modules:

- `RuntimeBuildRepository.create_immutable()`, `require()`, `list_bounded()`;
- `RuntimeVerificationRepository.append()` and bounded queries;
- `RuntimeTreeRepository.record_candidate()`, `observe_location()`,
  `move_role()`, `protected_tree_ids()`;
- `RuntimeComponentRepository.current()`, `promote_verified()`,
  `record_restoration()`.

`promote_verified()` must use expected generation/build IDs and update
known-good component identity in the same unit of work. Stale promotion fails
without partial writes.

### 7.4 Migration tests

Cover at minimum:

- fresh schema at version 5;
- v4 to v5 with no component provenance;
- v4 to v5 with bounded legacy provenance;
- deterministic repeat from identical v4 fixtures;
- migration rollback after each statement group leaves no partial v5 objects;
- constraints reject malformed IDs, invalid roles, oversized/secret-like JSON,
  missing FKs, and nonpositive generations;
- immutable record reuse compares exact manifest bytes;
- newer-schema refusal remains intact;
- clean wheel can initialize migration 005 with no repository-root imports.

---

## 8. Generic executor correction: phase-scoped leases

This correction lands before runtime workflow code.

### 8.1 Contract

Extend `StepDefinition`/`WorkflowDefinition` so resources are acquired when a
phase first needs them rather than all at `_claim()`. Preserve existing
activation/acquisition behavior exactly.

Required semantics:

1. Preflight runs only while holding resources declared for preflight. Existing
   workflows may retain their present all-operation lease with a compatibility
   declaration; do not silently weaken them.
2. Before starting each step, compute the step’s required resources.
3. Acquire all missing resources in one database write unit, sorted
   lexicographically, all-or-none. The transaction also asserts every already
   held lease.
4. Persist the held lease revisions for the executor invocation and fence every
   intent/checkpoint/heartbeat/compensation as today.
5. Release a resource only after the last normal or compensation step that can
   require it. A workflow-defined recovery barrier can retain selected leases.
6. A conflict before a critical effect returns deterministic busy/paused
   behavior and performs no effect.
7. No lease gap may occur between acquiring `runtime-active`, capturing the
   authoritative prior snapshot, and atomic exchange.

### 8.2 Runtime resource phases

Use:

- build phase: `runtime-installation` only;
- activation/verification/promotion phase: `runtime-active` plus
  `runtime-installation`;
- rollback operation: both resources from its preflight/capture boundary.

Because `runtime-active` sorts before `runtime-installation`, the repository’s
atomic `acquire_many()` must acquire/refresh the full desired set inside one
`BEGIN IMMEDIATE` transaction. Do not create a lock inversion by taking the
lower key with an unfenced series of separate transactions.

### 8.3 Tests

- A long fake build holding `runtime-installation` does not block
  `MODEL_ACTIVATE` from acquiring `runtime-active`.
- At the activation boundary, runtime update waits/refuses cleanly when model
  activation owns `runtime-active`; it has not swapped anything.
- Two updates cannot both build under the single installation lease.
- Update and rollback contenders cannot deadlock; exactly one owns both at the
  boundary.
- Partial acquisition is rolled back in one transaction.
- Lease expiry/takeover increments revisions and fences the stale executor.
- `RECOVERY_REQUIRED` retains the conflict barrier.
- Existing activation and acquisition crash/stress matrices remain green.

Do not add sleeps. Use injected clocks, barriers, and deterministic hooks.

---

## 9. Versioned request and evidence contracts

Implement these contracts in `operations/runtime_lifecycle.py` with closed
field sets and bounded validation before persistence.

### 9.1 `RuntimeUpdateRequestV1`

Allowed fields only:

- `requested_ref`: optional bounded ref; default is the shipped known-good pin;
- `expected_active_build_id`: optional optimistic concurrency guard;
- `requested_by`: closed set (`cli`, `gui`, `setup`, `repair`).

Forbidden request data:

- repository URL;
- container/service name;
- active/staging/backup path;
- build flags, jobs, commands, environment variables;
- token, credential, cookie, proxy secret;
- arbitrary cleanup policy;
- “force”, “skip smoke”, “skip health”, or “ignore thermal” switches.

### 9.2 `RuntimeRollbackRequestV1`

Allowed fields only:

- `expected_active_build_id` (required);
- `target_build_id` (required, selected from the repository by the command
  service rather than free-form frontend input);
- `requested_by` closed as above.

No paths or commands.

### 9.3 Closed evidence dataclasses

At minimum define:

- `ResolvedRuntimeSourceV1`;
- `BuildPreflightEvidenceV1`;
- `FetchEvidenceV1`;
- `BuildEnvironmentEvidenceV1`;
- `CandidateBuildEvidenceV1`;
- `SmokeEvidenceV1`;
- `PriorRuntimeSnapshotV1`;
- `TreeLocationEvidenceV1`;
- `TreeExchangeEvidenceV1`;
- `HandoffComponentEvidenceV1`;
- `ServiceRestartEvidenceV1`;
- `RuntimeIdentityEvidenceV1`;
- `RuntimeInferenceEvidenceV1`;
- `RuntimePromotionEvidenceV1`;
- `RuntimeRestorationEvidenceV1`;
- `RuntimeCleanupEvidenceV1`.

Evidence contains stable codes, identities, counts, booleans, bounded numeric
measurements, and relative managed locators. It never contains:

- credentials or environment dumps;
- full subprocess stdout/stderr;
- inference prompt or generated text;
- arbitrary absolute host paths;
- Python exception repr/traceback;
- unbounded package lists.

### 9.4 Stable result/failure codes

Freeze a vocabulary including:

```text
RUNTIME_ALREADY_ACTIVE
SOURCE_REF_RESOLVED
SOURCE_REF_MOVED
SOURCE_COMMIT_UNAVAILABLE
BUILD_ENVIRONMENT_UNPROVEN
BUILD_DISK_INSUFFICIENT
ACTIVE_RUNTIME_UNPROVEN
KNOWN_GOOD_RUNTIME_MISSING
THERMAL_LATCH_STOPPED
FETCH_TIMEOUT
BUILD_TIMEOUT
BUILD_CANCELLED_SAFE
CANDIDATE_BUILD_FAILED
CANDIDATE_SMOKE_FAILED
CANDIDATE_IDENTITY_MISMATCH
ATOMIC_EXCHANGE_UNSUPPORTED
ACTIVE_TREE_CHANGED
TREE_EXCHANGE_COMPLETED
TREE_EXCHANGE_UNCERTAIN
HANDOFF_COMPONENT_PUBLISHED
SERVICE_RESTART_FAILED
SERVICE_INVOCATION_UNPROVEN
RUNTIME_COMPONENT_MISMATCH
RUNTIME_MODEL_MISMATCH
RUNTIME_INFERENCE_FAILED
RUNTIME_PROMOTED
RUNTIME_ROLLBACK_TARGET_MISSING
RUNTIME_RESTORED
RUNTIME_RESTORATION_UNCERTAIN
RUNTIME_CLEANUP_DEFERRED
```

Map process failures into these codes. Never persist raw output as the reason
code.

---

## 10. Pure workflow definitions

### 10.1 `RUNTIME_UPDATE v1` steps

Use explicit step versions and recovery policy version 1. The recommended
sequence is below; keep names stable once rows can be persisted.

| # | Step | Phase/resources | Effect | Cancellation | Recovery probe |
| ---: | --- | --- | --- | --- | --- |
| 1 | `resolve_source` | installation | Persist immutable ref→commit evidence | safe before/after | re-resolve; moved ref fails, recorded commit remains authority |
| 2 | `preflight_build` | installation | Observe image/toolchain/disk/atomic support policy | safe | repeat pure observation |
| 3 | `fetch_source` | installation | Create/update operation-owned exact-commit checkout | safe pulses | absent / resumable exact commit / foreign / uncertain |
| 4 | `configure_build` | installation | Generate candidate build files from fixed recipe | safe pulses | recipe receipt + output identity |
| 5 | `compile_candidate` | installation | Build fixed binary targets | safe pulses | binary-set receipt; resume or rebuild owned output |
| 6 | `smoke_candidate` | installation | Hash/execute candidate, write immutable manifest | safe after smoke | exact manifest/binary probes |
| 7 | `capture_activation_boundary` | active + installation | Capture prior active tree, promoted state, known-good, handoff, service invocation/state | last safe cancellation point | all evidence must still match before swap |
| 8 | `exchange_active_tree` | active + installation | One atomic exchange | never interrupt | mandatory exact two-tree classifier |
| 9 | `publish_component_handoff` | active + installation | Publish schema-v2 handoff/launcher for candidate | never interrupt | exact fingerprint/build ID observation |
| 10 | `restart_runtime` | active + installation | Restart/start via server owner | never interrupt | invocation identity + start receipt classifier |
| 11 | `verify_runtime` | active + installation | Prove component, model/config, health, inference | never interrupt | repeat bounded observations; no second restart if already complete |
| 12 | `promote_runtime` | active + installation | Atomic DB promotion + known-good component identity | never interrupt | repository generation/build IDs |
| 13 | `finalize_trees` | active + installation | Mark prior tree rollback-retained; cleanup owned nonprotected staging | no cancel needed | identities and protected-set check |

#### No-op branch

If source resolution and active observation prove the requested immutable build
is already promoted and live, skip fetch/build/swap/restart. Re-run bounded
identity/health/inference verification and terminate `SUCCEEDED` with
`RUNTIME_ALREADY_ACTIVE`. Do not create a fake rollback target.

#### Initial-install branch

If no active tree exists:

- preflight must prove setup reached the container/toolchain prerequisite;
- candidate publication to the active path is a no-replace atomic rename, not
  exchange;
- death-after-publication uses the same target/prior/uncertain classifier with
  prior explicitly `ABSENT`;
- verification and promotion remain mandatory;
- failure before successful service verification removes only the
  operation-owned candidate/active tree if exact ownership is proved;
- uncertain identity retains the tree and enters `RECOVERY_REQUIRED`.

Do not hide initial installation in `setup_environment()`.

### 10.2 `RUNTIME_ROLLBACK v1` steps

Recommended sequence:

| # | Step | Requirement |
| ---: | --- | --- |
| 1 | `resolve_rollback_target` | target equals durable current rollback target and exact retained tree exists |
| 2 | `preflight_rollback` | active/promoted/known-good/service/thermal and atomic support agree |
| 3 | `smoke_rollback_target` | target manifest and binaries still match and execute smoke test |
| 4 | `capture_rollback_boundary` | hold both leases; recapture exact active and target identities |
| 5 | `exchange_active_tree` | one atomic exchange active↔retained target |
| 6 | `publish_component_handoff` | schema-v2 handoff expects target build |
| 7 | `restart_runtime` | one restart/start through server port |
| 8 | `verify_runtime` | exact component + model/config + health + inference |
| 9 | `promote_rollback` | target becomes promoted; former active becomes rollback target; generation++ |
| 10 | `finalize_trees` | update roles and clean only owned, unprotected leftovers |

Rollback cannot target an arbitrary historic build by path. A future UI may
offer multi-version selection, but v1 uses exactly the repository’s current
rollback target.

### 10.3 Effect dispositions

- source resolution/preflight: `NONE`;
- fetch/configure/build: `HIDDEN_DURABLE` and operation-owned;
- candidate manifest/retained tree: `HIDDEN_DURABLE`;
- active exchange, handoff, restart, promotion: `REVERSIBLE`;
- no runtime lifecycle step is `FORWARD_ONLY`;
- cleanup never compensates a promoted or rollback-protected tree.

### 10.4 Terminal decisions

- `SUCCEEDED`: target promoted and live verification evidence complete.
- `FAILED_SAFE`: failure before active mutation with active/prior state proved
  unchanged and owned staging handled safely.
- `FAILED_ROLLED_BACK`: visible mutation occurred; exact prior tree, handoff,
  service state, model/config, health, inference, and promoted state are proved
  restored.
- `CANCELLED`: cancellation before mutation with active state proved unchanged,
  or after verified compensation as allowed by ADR 002.
- `RECOVERY_REQUIRED`: any unproven tree arrangement, identity conflict,
  missing prior evidence, failed reverse exchange, failed restored health or
  inference, or promotion/filesystem contradiction.

Never report `FAILED_ROLLED_BACK` after a mere successful move. Restoration
must include live inference when the prior service was active.

---

## 11. Mandatory crash and recovery semantics

### 11.1 First red test — implement before production code

The first new test is the checkpoint named in `AGENTS.md`:

> `RUNTIME_UPDATE v1` swaps a staged, smoke-checked target tree into the active
> path and process death occurs after the exchange effect but before the step
> checkpoint. On takeover:
>
> - target identity active and prior identity retained: checkpoint without a
>   second exchange;
> - prior identity active and target identity still staged: execute the
>   original exchange exactly once;
> - neither exact arrangement can be proved: enter `RECOVERY_REQUIRED` and
>   delete/exchange neither tree.

Assert exchange count, active/prior bytes, manifests, operation/step states,
events, leases, and absence of cleanup in all three branches.

### 11.2 Required crash points for update

Inject death deterministically after effect and before checkpoint, plus after
checkpoint and before verification, for:

- source resolution persistence;
- checkout creation/update;
- configure receipt;
- completed compile;
- candidate manifest publication;
- prior snapshot persistence;
- atomic tree exchange;
- handoff publication;
- launcher/start receipt publication;
- service restart effect;
- health/identity/inference observation;
- database promotion;
- prior-tree role finalization;
- cleanup.

For each point define expected effect counts. Exchange, restart, promotion, and
reverse exchange are the critical exactly-once probes.

### 11.3 Required crash points for rollback

Repeat the critical matrix for:

- rollback target capture;
- target smoke;
- atomic exchange;
- handoff publication;
- restart;
- verification;
- promotion/lineage toggle;
- cleanup.

### 11.4 Compensation ordering

After any post-exchange failure, compensation runs in this logical order:

1. fence both resources;
2. probe active and retained tree identities;
3. exchange exact prior tree back once if required;
4. restore prior handoff and launcher identity;
5. restore prior service running/stopped state through `server.py`;
6. if prior was active, prove new restoration invocation, exact prior component,
   expected model/context/slots, health, and minimal inference;
7. restore/confirm promoted database generation and known-good identity;
8. mark candidate retained/quarantined as policy dictates;
9. release leases only after proof.

If any step is uncertain, stop. Do not continue “best effort” cleanup.

### 11.5 Recovery-required UX contract

Persist bounded remediation data sufficient to show:

- operation ID;
- expected prior and target build IDs;
- which tree identities were observed at which managed locators;
- whether handoff/service/promotion disagree;
- safe next action (`retry recovery`, `inspect`, or `restore selected verified
  tree`—the last may remain a later repair command if not safely implementable
  here).

Never instruct a normal user to run an undocumented `mv`/`rm` sequence.

---

## 12. Bounded process and fixed-helper contract

Implement a runtime-scoped typed process port now; U2.3 may later generalize
it.

### 12.1 Command specification

Each command uses a frozen specification containing:

- command kind enum;
- argv tuple;
- derived working directory;
- explicit, minimal environment allowlist;
- timeout and termination grace;
- maximum stdout/stderr bytes retained;
- whether cancellation pulses are allowed;
- expected exit codes;
- redaction policy;
- operation/step identity for logs without secret request content.

Production execution:

- never uses `shell=True`, `bash -lc`, backticks, `$()`, or interpolated script;
- starts a process group/session;
- reads output without unbounded buffering or deadlock;
- emits throttled progress and unconditional lease heartbeats;
- on timeout/cancel sends TERM to the group, waits bounded grace, then KILLs;
- reaps every child;
- raises typed stable failures;
- never logs full environment, authorization headers, or raw canary data.

### 12.2 Initial timeout policy

Freeze reviewed defaults in one module (tests inject smaller values):

| Class | Initial production bound |
| --- | ---: |
| ref/image/tool observation | 30 seconds |
| disk/filesystem preflight | 15 seconds |
| source fetch | 20 minutes, with heartbeat/progress |
| configure | 10 minutes |
| compile | 90 minutes, with heartbeat/progress |
| binary smoke/version | 60 seconds |
| atomic helper command | 15 seconds |
| service restart/start | 120 seconds |
| health convergence | 120 seconds |
| minimal inference | 120 seconds |
| owned cleanup | 60 seconds |

These are bounded defaults, not magic guarantees. Expose no “infinite” CLI
override. Later policy may make bounds hardware-profile aware.

### 12.3 Progress

Progress writes obey `ProgressPolicy` and should represent stable phases:

- resolve 0–5%;
- preflight 5–10%;
- fetch 10–30%;
- configure/build 30–70%;
- smoke/manifest 70–78%;
- activation 78–85%;
- restart/verify 85–96%;
- promotion/finalization 96–100%.

Unknown compiler totals use phase progress and elapsed buckets, not fake
precision. Every process loop heartbeats even if progress does not advance.

### 12.4 Helper safety tests

- hostile path corpus: empty, `/`, `.`, `..`, traversal, newline, NUL, prefix
  confusion, sibling escape, symlink active/candidate, nested overlap;
- same-filesystem requirement;
- exchange support probe;
- fsync success/failure handling;
- death immediately before/after syscall;
- helper resource digest mismatch refuses execution;
- cleanup cannot touch active, rollback, known-good, or another operation path;
- argv and logs contain no injected shell execution.

---

## 13. Production host adapter

Create exactly one `RuntimeLifecycleHostAdapter` implementing the pure port.
It owns infrastructure orchestration but not workflow policy.

### 13.1 Responsibilities

- derive container/profile/runtime roots from composed application state;
- validate container and approved root identities;
- resolve upstream ref to exact commit;
- perform disk/image/toolchain/filesystem preflight;
- create operation-owned staging with restrictive permissions;
- fetch/check out exact commit;
- configure/build fixed targets;
- construct and persist canonical candidate manifest;
- smoke binaries and Vulkan linkage/capability as safely possible before swap;
- adopt/probe legacy active trees without trusting old state strings;
- perform and probe atomic exchange via fixed helper;
- call handoff service for schema-v2 publication;
- call typed server port for restart/start/stop and observations;
- perform minimal inference and return only bounded content-free evidence;
- promote/restore through typed repositories/UoWs;
- clean only proven operation-owned, unprotected paths.

### 13.2 It must not

- build SQL strings;
- decide operation transitions;
- fabricate observed identity from the request or database;
- call `systemctl` directly;
- use the legacy `env.update_llamacpp` or `rollback_llamacpp`;
- mutate caller snapshots;
- persist raw command output;
- delete an uncertain tree;
- accept caller paths/commands/build options.

### 13.3 Legacy active-tree adoption

Before the first managed update/rollback:

1. require the active path to be a contained nonsymlink directory;
2. obtain source commit when available without trusting `.git` alone;
3. hash all required binaries with bounded streaming;
4. collect bounded build/version metadata;
5. create a `LEGACY_UNVERIFIED` manifest/record;
6. if the service is active, prove health/model/inference and append an active
   verification;
7. only then allow it as a restorable prior tree.

If the active runtime cannot be identified and verified, fail before update
with `ACTIVE_RUNTIME_UNPROVEN`. Do not swap away the user’s only runtime.

### 13.4 Retention policy

At U1.2 close:

- retain promoted active tree;
- retain exactly one verified rollback tree;
- retain any tree referenced by an in-flight or `RECOVERY_REQUIRED` operation;
- retain a quarantined/failed candidate only when needed for diagnosis and label
  it explicitly;
- remove older operation-owned unreferenced trees only through containment,
  identity, and lease checks;
- never remove an adopted user tree automatically unless it has become an exact
  managed rollback tree and policy explicitly owns it.

Report reclaimable bytes in status. A later maintenance UI may expose reviewed
cleanup.

---

## 14. Handoff v2 and server observation

### 14.1 Handoff schema v2

Add required fields:

- `runtime_component_id`;
- `runtime_source_commit`;
- `runtime_server_sha256`;
- `runtime_manifest_digest`;
- `runtime_operation_id` for an in-flight activation, nullable/empty only for a
  stable regenerated handoff;
- existing config/model fields.

Include component identity in `runtime_fingerprint`. `observe()` must reject:

- schema v1 after v2 cutover when starting a managed runtime;
- missing/malformed digests or component ID;
- inconsistent component/manifest/binary fields;
- all existing invalid numeric/model/path cases.

Regeneration from a stable promoted state uses the repository’s promoted build,
not mutable settings provenance.

### 14.2 Start receipt

Immediately before `exec` of `llama-server`, the reviewed launcher path must:

1. parse/validate handoff v2;
2. inspect the active tree manifest;
3. compute/verify the server binary digest;
4. refuse startup on mismatch with a stable diagnostic;
5. atomically publish a 0600 start receipt containing only bounded identities,
   invocation/start nonce, and timestamp;
6. `exec` the verified binary argv.

The receipt is a rendered observation aid, not authoritative state. It may be
recreated. Its parent path comes from `AppPaths`; no HOME fallback.

### 14.3 Server port additions

Add typed methods in `server.py` or a protocol adapter around it:

- `capture_service_state()`;
- `restart_for_runtime_change()`;
- `restore_service_state()`;
- `observe_invocation()`;
- `observe_runtime_identity()`;
- existing strict model/config observation;
- bounded `minimal_inference_probe()`.

`observe_runtime_identity()` must distinguish filesystem build identity from
live invocation evidence. It returns uncertain when the chain cannot be tied
together.

### 14.4 Tests

- schema-v1 handoff migration/regeneration to v2;
- missing/stale v2 fails closed;
- same path + changed component identity triggers publication;
- candidate build + prior binary mismatch refuses launch;
- forged desired build ID cannot satisfy live observation;
- stale start receipt from a prior invocation is rejected;
- health from the wrong model or context/slots fails;
- healthy endpoint with failed inference fails;
- generated inference content never reaches DB/log/events/receipt.

---

## 15. Command service, composition, and frontend cutover

### 15.1 `RuntimeLifecycleCommandService`

Expose typed methods:

- `update(requested_ref=None, expected_active_build_id=None, requested_by=...)`;
- `rollback(requested_by=...)`;
- `resume(operation_id)` only through existing supported foreground semantics;
- `status()` as a pure query.

Each mutation:

1. constructs a closed request;
2. uses the one shared `EnqueueService`;
3. executes via the one shared engine factory in the foreground;
4. maps `TerminalDecision` to a typed result with operation ID, status, stable
   code, observed target/prior IDs, warnings, and remediation;
5. never starts a private registry, executor, or worker.

If the frontend exits, report “interrupted; ready to resume.” Do not say the
update continues in the background.

### 15.2 Composition

`Application.compose()` registers both runtime workflows before registry
freeze, creates one production adapter, and injects one command service. Tests
assert object identity for registry/enqueue/engine factory/adapter boundaries,
mirroring the U1.1 and 5C single-graph guards.

### 15.3 CLI

Preserve sensible existing UX while changing ownership:

- existing llama.cpp update command enqueues `RUNTIME_UPDATE v1`;
- existing rollback command enqueues `RUNTIME_ROLLBACK v1`;
- JSON mode writes one machine-readable result to stdout; progress/logs use the
  established non-stdout channel;
- exit codes distinguish success, safe failure, rollback-restored failure,
  recovery required, conflict/busy, cancellation, and Ctrl-C without exposing
  implementation exceptions;
- Ctrl-C requests durable cancellation and waits only through the current safe
  checkpoint; it does not kill during swap/restart/compensation;
- status is read-only.

Do not add `--force`, arbitrary repo URL, arbitrary build flags, unsafe path,
skip verification, or infinite timeout options.

### 15.4 Dashboard/wizard

- Call `application.runtime_lifecycle`, never `env.py` or repositories.
- Keep the UI responsive using the existing frontend-owned execution pattern;
  marshal Tk updates onto the UI thread.
- Show phase, bounded progress, requested ref, resolved short commit, operation
  ID, cancellation availability, and honest foreground limitation.
- Disable Update/Rollback while a conflicting operation or recovery barrier is
  present.
- Rollback button names the exact retained target and is hidden/disabled when
  none is verified.
- On recovery required, show concise remediation and preserve the operation ID.
- Remove caller-side `commit_narrow()` for service-owned runtime changes.

Do not build the full Activity Center in this session.

### 15.5 Initial setup

Split `setup_environment()` responsibilities:

- container/dependency/venv prerequisite provisioning may remain in the setup
  service for now, subject to existing caveats;
- remove mutable llama.cpp clone/build and direct active-root creation;
- after prerequisites, setup invokes the same composed runtime update command
  targeting the shipped pin;
- setup stage advances only after the durable runtime operation succeeds and
  live verification passes;
- failure leaves the setup stage resumable and reports the operation ID;
- repeated setup recognizes an already verified active build without rebuild.

This is necessary to prevent a fresh-install bypass.

### 15.6 Delete old routes atomically

After the last caller is cut over, delete:

- `env.update_llamacpp`;
- `env.rollback_llamacpp`;
- shell-based `_git_show`/runtime provenance path when no longer needed;
- `ComponentLifecycleService.update_llamacpp` and `.rollback_llamacpp`;
- mutable `llamacpp_history` writes and query ownership;
- fixed `-staging`, `-backup`, and `-rolled` lifecycle assumptions;
- any duplicate dashboard/CLI persistence.

Retain only environment setup/status helpers that still have a real owner, and
rename/refactor them so architecture guards do not mistake them for a bypass.

### 15.7 Architecture guards

Add AST/import guards asserting:

- no production definitions named `update_llamacpp` or `rollback_llamacpp`;
- no `bash -lc` or `shell=True` in runtime lifecycle modules;
- no fixed `llama.cpp-staging`, `llama.cpp-backup`, or `llama.cpp-rolled` paths;
- frontends do not import env, runtime adapter/process/build repositories,
  subprocess, sqlite, or service literals;
- one runtime adapter construction in composition;
- one shared registry/enqueue/engine factory;
- only `server.py` owns the service literal/systemd mutation;
- no frontend runtime component save/transaction/commit;
- no mutable Git ref used as a build identity;
- runtime setup cannot clone/build llama.cpp directly.

---

## 16. Test program

### 16.1 Pure workflow/fake-world tests

Build a deterministic fake runtime world with:

- source refs/commits;
- image/toolchain identity;
- disk/free-space controls;
- active/candidate/rollback trees and exact manifests;
- handoff and start receipts;
- service invocation/process/model observations;
- health/inference outcomes;
- effect counters;
- injected crash hooks;
- cancellation and lease clocks.

Test:

- happy update;
- already-active no-op;
- first install with no active tree;
- happy rollback and rollback toggle;
- tag moves after resolution;
- expected-active conflict;
- thermal latch stop;
- no known-good/prior runtime;
- insufficient disk;
- unsupported atomic exchange;
- fetch/configure/build/smoke failures;
- cancellation in every safe build pulse;
- cancellation deferred at every critical step;
- wrong candidate/prior identity;
- handoff failure;
- restart failure;
- stale invocation/start receipt;
- wrong component, model, context, or slot observation;
- health success/inference failure;
- promotion conflict;
- reverse exchange failure;
- restored health/inference failure;
- cleanup failure after success (success retained with warning if protected state
  is already safe; no destructive retry).

### 16.2 Production adapter tests with boundary fakes

No test invokes real systemd, Podman, GitHub, compiler, or GPU by default.
Use fake process/server/filesystem ports to verify:

- exact typed argv and fixed origin;
- no shell interpolation for hostile refs/paths;
- source commit checked after checkout;
- build recipe/manifest determinism;
- streaming full binary digest and bounded reads;
- image/toolchain identity requirements;
- disk accounting and safety margin;
- helper containment/exchange contracts;
- no cleanup of protected trees;
- UoW promotion atomicity;
- legacy adoption policy;
- handoff v2/start receipt behavior;
- no prompt/generated text persisted.

### 16.3 Security canaries

Use unique canaries in:

- environment variables;
- simulated authorization/proxy values;
- process stdout/stderr;
- malicious ref text;
- filesystem names;
- inference prompt/output;
- raw exception text.

After success, failure, cancellation, takeover, and recovery-required branches,
scan:

- SQLite tables, operation requests/events/steps/evidence;
- app logs;
- handoff, launcher, start receipts, build manifests, helper receipts;
- generated unit files;
- command audit capture;
- exception/result text exposed to frontends.

No canary may appear. The only durable path-like data must be approved relative
managed locators, never secrets or arbitrary external paths.

### 16.4 Concurrency tests

Deterministically prove:

- update build does not block model activation before its active boundary;
- activation cannot cross the update’s active boundary;
- two updates produce one winner, no duplicate build/swap/promotion;
- update versus rollback produces one winner with no deadlock;
- stale worker after lease takeover performs no write/effect;
- settings writes and model history appends cannot clobber runtime build state;
- status queries never bump revisions or publish handoff;
- cancellation races at the last safe point have one closed outcome;
- promotion and takeover cannot create split lineage.

### 16.5 Mandatory crash matrix

Run all named update and rollback crash points through:

1. fresh execution until injected death;
2. lease expiry with injected clock;
3. takeover by a new owner;
4. repeated recovery until terminal;
5. exact effect-count and final-state assertions.

No sleeps. Every branch must converge to one of the five terminal meanings or
an intentional `RECOVERY_REQUIRED` barrier.

### 16.6 Stress gate

Add a slow-marked deterministic stress test:

- 20/20 update exchange-death recoveries;
- 20/20 rollback exchange-death recoveries;
- 20/20 update/activation contention runs;
- 20/20 cancellation-at-build-boundary runs;
- zero duplicate swaps/restarts/promotions;
- zero lost trees, stale writes, leaked leases, or canaries.

Run explicitly; do not silently add minutes to the default suite.

### 16.7 Packaging gate

Extend the existing clean-wheel smoke to:

1. install the wheel with repository root off `sys.path`;
2. initialize migration 005;
3. register `RUNTIME_UPDATE v1` and `RUNTIME_ROLLBACK v1`;
4. execute a no-host/fake happy update and rollback through the shared engine;
5. verify the fixed exchange helper/package resource is present and digestable;
6. assert both operations reach `SUCCEEDED` with exact lineage.

---

## 17. Ordered implementation and commit boundaries

Keep every boundary reviewable and green. Commit messages should cite U1.2 and
the relevant plan section.

### Commit 1 — plan and decision freeze

Deliver:

- this plan committed as execution authority;
- ADR 004 accepted;
- ADR 002 phase-resource/cancellation clarifications;
- mandatory first red exchange-death test;
- red architecture tests for legacy bypass and shell interpolation.

Expected state: focused red tests only, with failures proving missing
production behavior rather than broken fixtures.

Suggested message:

```text
docs(U1.2): freeze immutable runtime lifecycle and recovery contract
```

### Commit 2 — migration 005 and repositories

Deliver:

- schema v5;
- runtime build/verification/tree/component tables;
- deterministic legacy backfill;
- typed repositories and constraints;
- migration/repository tests.

No host calls, workflow, frontend, or runtime mutation.

```text
feat(U1.2): add immutable runtime build and rollback lineage schema
```

### Commit 3 — phase-scoped lease correction

Deliver:

- atomic `acquire_many`/phase resource contract;
- executor integration;
- compatibility behavior for existing workflows;
- contention/deadlock/fencing tests;
- all 5B/5C/6A tests green.

```text
fix(U1.2): acquire runtime resources at fenced workflow boundaries
```

### Commit 4 — pure runtime workflows and fake world

Deliver:

- closed update/rollback requests;
- evidence/code vocabulary;
- typed runtime port;
- complete workflow definitions, terminal decisions, compensation rules;
- fake-world happy/failure/cancel/recovery tests;
- mandatory first red test turns green in the fake world.

No production host adapter yet.

```text
feat(U1.2): define durable runtime update and rollback workflows
```

### Commit 5 — bounded process and atomic helper

Deliver:

- process spec/runner with timeout, cancellation, output bounds, process-group
  cleanup, and redaction;
- fixed hash-checked runtime filesystem helper;
- immutable source resolution primitives;
- build manifest identity primitives;
- hostile-path, timeout, canary, and exchange tests.

Do not wire frontends.

```text
feat(U1.2): add bounded runtime process and atomic exchange ports
```

### Commit 6 — production build/staging adapter

Deliver:

- runtime adapter source resolve/preflight/fetch/configure/build/smoke;
- operation-owned directories and receipts;
- legacy active-tree adoption;
- manifest/repository integration;
- progress/cancellation;
- no active mutation yet unless adapter tests cover the full fake boundary.

```text
feat(U1.2): build immutable llama.cpp candidates in owned staging
```

### Commit 7 — activation, handoff v2, restart, and exact verification

Deliver:

- production atomic exchange/probes;
- handoff schema v2 and start receipt;
- server typed observation/restart seam;
- exact component/model/config/health/inference verification;
- promotion and restoration UoWs;
- full crash/compensation matrices;
- mandatory production-boundary exchange-death test green.

```text
feat(U1.2): verify and promote atomically exchanged runtime builds
```

### Commit 8 — one command graph and initial setup cutover

Deliver atomically:

- shared registry registration;
- composed adapter/command service;
- CLI, wizard, dashboard, and setup routing;
- initial pinned runtime through update v1;
- old synchronous update/rollback and direct setup build deleted;
- caller-side commits deleted;
- AST architecture guards green.

Do not leave a dual route between commits.

```text
refactor(U1.2): cut every runtime lifecycle caller to durable operations
```

### Commit 9 — security, concurrency, stress, and clean-wheel gates

Deliver:

- durable-surface canary scan;
- full deterministic crash matrix;
- concurrency tests;
- slow 80-iteration stress battery;
- clean-wheel runtime workflow extension;
- coverage for status/query purity and retention safety.

```text
test(U1.2): close runtime lifecycle crash security and packaging gates
```

### Commit 10 — docs truth and checkpoint evidence

Deliver:

- `AGENTS.md` current state, test counts, exact next task U1.3;
- `CHANGELOG.md` U1.2 behavior and caveats;
- `ARCHITECTURE.md` one durable runtime path and identity chain;
- README update/rollback/cancel/interruption/recovery UX;
- ADR 004 implementation record;
- ultimate/post-R2 plan statuses;
- this plan marked DONE with commit/evidence table;
- explicit remaining caveats: foreground executor, catalog conversion refusal,
  broader command port migration, no automatic update/signature system yet.

```text
docs(U1.2): close durable runtime lifecycle checkpoint
```

Stop after this commit and a clean verification battery.

---

## 18. U1.2 exit gate

U1.2 is complete only when every item is true.

### Architecture

- [ ] `RUNTIME_UPDATE v1` and `RUNTIME_ROLLBACK v1` are registered in the one
  frozen workflow registry.
- [ ] One composed command service and one production host adapter serve CLI,
  wizard, dashboard, and setup.
- [ ] Old synchronous update/rollback and direct mutable setup build paths are
  deleted with AST guards.
- [ ] Only `server.py` owns service mutation.
- [ ] Runtime workflow purity/import guards pass.

### Persistence and identity

- [ ] Migration 005 is atomic, filesystem-free, and clean-wheel verified.
- [ ] Runtime builds are immutable content-derived records.
- [ ] Active and rollback lineage is typed, generation-checked, and no longer
  owned by `llamacpp_history`.
- [ ] Mutable refs are resolved to full commits before mutation.
- [ ] Image/toolchain/recipe/binary identities are recorded and bounded.
- [ ] Handoff v2 binds configuration to exact runtime component identity.

### Safety and recovery

- [ ] Candidate build and smoke never touch active tree.
- [ ] Active cutover is one no-gap atomic exchange; unsupported filesystems fail
  before mutation.
- [ ] Mandatory post-exchange/pre-checkpoint three-branch test passes.
- [ ] Full update and rollback crash matrices converge deterministically.
- [ ] No second swap/restart/promotion occurs when probes prove completion.
- [ ] Prior runtime restoration includes exact tree, handoff, service state,
  component/model/config identity, health, and inference.
- [ ] Uncertain state becomes `RECOVERY_REQUIRED` and preserves both trees.
- [ ] Cleanup touches only operation-owned, unprotected paths.

### Process/security

- [ ] Every runtime command has timeout, cancellation, output bound, and
  process-group cleanup.
- [ ] Runtime lifecycle uses no shell interpolation.
- [ ] Hostile paths/refs cannot execute commands or escape managed roots.
- [ ] Secrets, command output canaries, and inference content are absent from
  durable/log/rendered surfaces.
- [ ] Progress is throttled; heartbeats remain unconditional.

### User experience

- [ ] Update/rollback return operation IDs and stable actionable outcomes.
- [ ] UI remains responsive and labels the foreground/interruption limitation.
- [ ] Cancellation is available only at honest safe points.
- [ ] Rollback names an exact verified target.
- [ ] Status is read-only and distinguishes promoted, observed, rollback,
  in-progress, and recovery-required state.
- [ ] Fresh setup uses the shipped immutable pin through update v1.

### Regression and packaging

- [ ] Default source and editable suites pass with identical collection count.
- [ ] Existing activation and acquisition crash/security/stress behavior remains
  green.
- [ ] New slow runtime stress gate passes all planned iterations.
- [ ] Clean-wheel gate initializes schema v5 and executes both runtime workflows.
- [ ] `compileall` and `git diff --check` are clean.
- [ ] Working tree is clean except pre-existing user-owned untracked files.
- [ ] No tag, push, release, or version bump occurred unless separately requested.

---

## 19. Verification battery

Run focused tests after each boundary. At final closeout run:

```bash
git diff --check
python -m compileall -q bc250_llm_mode tests

PYTHONPATH=. .venv/bin/pytest -q
.venv/bin/pytest -q
.venv/bin/pytest tests --collect-only -q

.venv/bin/pytest -q \
  tests/test_runtime_migration.py \
  tests/test_runtime_repositories.py \
  tests/test_runtime_workflow.py \
  tests/test_runtime_adapter.py \
  tests/test_runtime_handoff.py \
  tests/test_runtime_command.py \
  tests/test_runtime_architecture.py

.venv/bin/pytest -q -m slow tests/test_runtime_security_stress.py
.venv/bin/pytest -q -m slow tests/test_acquisition_security_stress.py
.venv/bin/pytest -q -m slow tests/test_packaging.py

git status --short
git log --oneline --decorate -12
```

Adapt filenames only if the implementation uses clearer names; preserve the
coverage categories. Record exact counts and elapsed slow-gate evidence in this
plan and `AGENTS.md`. Never infer counts from prior reports.

---

## 20. Stop conditions during execution

Stop the current boundary and fix the design before proceeding if any occurs:

- exact prior runtime identity cannot be captured before swap;
- target and prior trees cannot be distinguished after a crash;
- the filesystem lacks the approved atomic exchange primitive;
- a workflow needs shell interpolation or an unbounded process;
- runtime-active must be held for the entire compile because phase leases are
  incomplete;
- observed service identity is derived from desired state;
- a rollback succeeds without restored inference proof;
- cleanup would need to delete an uncertain or user-owned path;
- migration needs host/filesystem I/O;
- a second production route is required to keep tests passing;
- frontend closure is represented as background continuation;
- a test uses sleeps to “solve” a race;
- security canary reaches any durable/log/rendered surface.

`RECOVERY_REQUIRED` is a valid product outcome when evidence is genuinely
uncertain. Destructive guessing is not.

---

## 21. Handoff template for the next executor

At session end append a concise evidence section containing:

```text
Session 6B / U1.2 status: COMPLETE | PARTIAL
HEAD: <commit>
Version: <version>
Schema: <version>
Default tests: <authoritative count>
Focused runtime tests: <count>
Slow runtime stress: <iterations/result>
Acquisition slow regression: <result>
Clean-wheel: <result>
Compile/diff-check: <result>
Working tree: <tracked status and preserved user files>

Commit boundaries landed:
- <hash> decision freeze
- <hash> migration/repositories
- <hash> phase leases
- <hash> pure workflows
- <hash> process/helper
- <hash> build adapter
- <hash> activation/verification
- <hash> cutover
- <hash> gates
- <hash> docs/evidence

Mandatory exchange-death result:
- target active branch: <effect counts>
- prior active branch: <effect counts>
- uncertain branch: <preservation/barrier evidence>

Remaining caveats:
- foreground-only operation execution until U1.3
- catalog conversion still refused until U2.3 process-port integration
- <any newly discovered honest caveat>

Exact next task:
- U1.3 explicit worker lifecycle; first red test: enqueue a long operation,
  close the frontend after a safe checkpoint, and prove one profile-scoped
  supervised worker resumes it without duplicate effects or changing reboot
  policy.
```

If partial, name the exact next commit boundary and first failing test. Do not
claim the U1.2 exit gate.

---

## 22. Post-6B feature sequence (do not execute in this session)

The application’s strongest next upgrades after U1.2 are:

1. **U1.3 supervised foreground-independent worker:** operation continuation
   across GUI/terminal closure, one profile worker, idle exit, no boot autostart.
2. **U1.4 Activity Center:** operation history, progress, cancel/resume,
   recovery guidance, and links to affected model/runtime pages.
3. **U2.3 universal bounded process port:** migrate converter and remaining host
   commands, enabling catalog conversions without unbounded execution.
4. **R4 focused domain modules/typed adapters:** shrink `services.py`, remove
   dictionary boundaries, and standardize result/error types.
5. **Authenticated local gateway:** make contained Open WebUI backend access
   genuinely functional without exposing raw llama.cpp remotely.
6. **Backup/restore and repair UX:** encrypted/verified backups, dry-run restore,
   runtime tree diagnostics, and guided recovery barriers.
7. **Supply-chain/release work:** pinned base image policy, dependency lock,
   signed artifacts, SBOM, provenance bundle, migration rehearsal, and hardware
   certification.

That sequence preserves the sweet spot: first make every destructive/long
workflow transactional, then let it survive frontend closure, then make the
durable truth easy for users to understand, and only afterward widen adapters,
remote access, and release machinery.

---

## 23. Final instruction to the implementing agent

Treat the current tests and architecture as a safety system, not a barrier to
work around. Start with the mandatory red exchange-death test, freeze ADR 004,
and make each external effect independently observable. Preserve exact prior
state before mutation. Prefer a hard safe refusal over an optimistic fallback.
Never use filenames, mutable tags, successful command exit, or desired state as
proof of a live runtime. The only acceptable success is an immutable candidate
that was atomically activated, observed as the new process, served the expected
model/configuration, produced bounded inference, and was promoted in one
fenced durable commit.


---

## 24. Execution evidence (Session 6B closeout)

Session 6B / U1.2 status: **COMPLETE**
HEAD: `31b0871` (ten-boundary chain from `e8d91c3`)
Version: `0.9.0.dev0`
Schema: 5
Default tests: authoritative collection **644**; executed green via eight
alphabetical chunk runs on a ~20 s-CPU-capped sandbox (single-shot full runs
are killed here): 54+75+53+153+53+39+107+82 = **616 passed**, 1 Linux-gated
skip (`renameat2` swap test); counts reconcile to collection minus 49
slow-marked.
Focused runtime tests: exchange-death 3/3, workflow behaviors 15/15, phase
leases 8/8, migration+repositories 13/13, process/helper 12+1 skip,
adapter 10/10, handoff/receipts 14/14, crash matrix 23/23, architecture
guards 9/9 (hard).
Slow runtime stress: `-m slow tests/test_runtime_security_stress.py` =
**6 passed** (~27 s) — 20/20 update exchange-deaths, 20/20 rollback
exchange-deaths, 20/20 build-boundary cancellations, 20/20 update/update
contentions, canary scans clean, status purity proven.
Acquisition slow regression: `-m slow test_acquisition_security_stress.py`
= **41 passed**.
Clean-wheel: `-m slow tests/test_packaging.py` = **2 passed** — wheel
initializes schema v5 and executes RUNTIME_UPDATE v1 → RUNTIME_PROMOTED and
RUNTIME_ROLLBACK v1 → RUNTIME_RESTORED through the shared engine with the
digest-verified helper resource present.
Compile/diff-check: `compileall` clean; `git diff --check` clean.
Working tree: tracked files clean; pre-existing user-owned untracked plan/
audit files preserved untouched.

Commit boundaries landed:
- `e8d91c3` decision freeze (ADR 004 + ADR 002 §17 + red exchange-death +
  red guards)
- `95b0022` migration 005 / repositories
- `20723cf` phase leases
- `025cc20` pure workflows + fake world (mandatory test GREEN)
- `fd4d59c` process/helper ports
- `6750c95` production build adapter
- `e92a955` activation/handoff v2/receipts/crash matrix
- `85d6db3` cutover (composition/CLI/wizard/dashboard/setup; deletions)
- `31b0871` stress/canary/packaging gates

Mandatory exchange-death result:
- target active branch: exchanges exactly **1**; takeover checkpoints the
  RUNNING step without a second swap; operation SUCCEEDED/RUNTIME_PROMOTED.
- prior active branch: intent durable, swap not landed → takeover executes
  the ORIGINAL exchange exactly once across three interruptions;
  SUCCEEDED with one ledger entry.
- uncertain branch: destroyed manifests → probe UNCERTAIN →
  RECOVERY_REQUIRED with BOTH trees byte-preserved, both resource leases
  retained as barrier, remediation `{step, classification, probe}` in
  error_detail.

Remaining caveats:
- Foreground-only operation execution until U1.3 (closing a frontend pauses
  safely; `llamacpp resume --operation-id …` continues). Stated on every
  surface; nothing pretends to run in the background.
- Catalog conversion still refused until U2.3 process-port integration.
- The fixed `fedora:latest` provision source remains a user-facing input
  only; promoted builds record observed image ID/digest + toolchain hashes
  (D3) until the container supply-chain phase.
- Real-hardware certification, signing/TUF/SBOM, automatic update checks:
  explicitly out of scope (§4.2), unchanged.

Exact next task:
- U1.3 explicit worker lifecycle; first red test: enqueue a long operation,
  close the frontend after a safe checkpoint, and prove one profile-scoped
  supervised worker resumes it without duplicate effects or changing reboot
  policy.


### 24.1 Follow-through additions (same session, post-Commit-10 audit)

Closing three §15/§14 contract items found during self-audit:
- §15.3 Ctrl-C: `RuntimeLifecycleCommandService._drive` converts the FIRST
  Ctrl-C into durable `request_cancel` and keeps driving with the SAME
  worker identity (engine defers inside critical sections); second Ctrl-C
  re-raises and the CLI prints an honest PAUSED payload with resume
  instructions (exit 130).
- §15.4 gating: pure `llamacpp_button_states`/`llamacpp_card_text` helpers;
  Update AND Rollback disable during recovery barriers or any active
  operation; rollback stays gated on verified retained targets.
- §14.1 regeneration: query snapshots project the promoted lineage
  (component id / manifest digest / source commit / server digest) and
  `build_payload` auto-binds schema v2 with an EMPTY `runtime_operation_id`
  for stable regeneration; observation accepts empty op-id while still
  rejecting missing identity keys.
- §11.5 UX: RECOVERY_REQUIRED outcomes surface the persisted remediation
  `{step, classification, probe}` verbatim.
- §16.4 isolation regression: settings writes cannot clobber runtime
  lineage tables.
New tests: tests/test_runtime_command_followthrough.py (6) + handoff
regeneration case (collection 644 -> 651). All chunks green.
