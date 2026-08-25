# ADR 004 — Immutable Runtime Lifecycle (llama.cpp update/rollback)

**Status:** Accepted (Session 6B / U1.2)
**Complements:** ADR 002 (durable operation contract), ADR 003 (managed
model artifacts)
**Scope:** how llama.cpp runtimes are built, identified, exchanged,
verified, promoted, rolled back, and recovered; the `RUNTIME_UPDATE v1`
and `RUNTIME_ROLLBACK v1` durable operations; migration 005 persistence;
phase-scoped resource leasing in the shared executor.

---

## 0. Problem

Before U1.2, llama.cpp updates were synchronous functions that mutated the
active checkout with interpolated shell scripts and best-effort in-memory
rollback. The active tree could disappear between two moves; process death
left no durable intent; mutable refs (`fedora:latest`, the llama.cpp
default branch, display tags) served as identities; "healthy" was accepted
as proof that the intended binary was running; rollback restored "whatever
happens to be in `-backup`"; and uncertain recovery deleted fixed staging
and backup names — potentially destroying the only useful evidence.

## 1. D1 — a build ID is content-derived, not a tag

A runtime build identity is:

```text
llamacpp:sha256:<sha256(canonical build manifest)>
```

The canonical build manifest is encoded with sorted compact JSON and is
immutable. It includes at least:

- schema/version and component name (`llamacpp`);
- fixed upstream repository identity;
- requested ref for display only;
- resolved full source commit;
- source checkout verification result;
- build recipe version and recipe digest;
- exact CMake generator/options/targets and parallelism policy;
- observed container image ID and digest when available;
- bounded toolchain identities (`cmake`, `ninja`, compiler, linker, libc);
- target architecture/platform;
- binary entries for `llama-server`, `llama-cli`, and `llama-quantize`
  (relative path, size, executable mode, full SHA-256, bounded
  version-output digest);
- smoke-test contract version.

Requested tags, timestamps, paths, operation IDs, progress, and mutable
status never affect the build ID. Migration 005 stores manifests as
canonical bytes; re-inserting an identical build ID must compare exact
bytes and be idempotent; differing bytes under one ID are corruption.

## 2. D2 — immutable resolution precedes fetch/build mutation

The production upstream URL is a fixed reviewed constant. Requests may
carry a bounded tag/ref or exact commit — never a URL, path, shell text,
build option, container image, or command. Source resolution:

1. validates the ref as data;
2. resolves it to one full commit (peeling annotated tags);
3. persists source-resolution evidence durably;
4. on recovery refuses if the same mutable ref now resolves differently
   (`SOURCE_REF_MOVED`) — the recorded commit stays the authority;
5. fetches/checks out the recorded commit, never "whatever the ref means
   now".

The shipped known-good pin remains the ordinary safe default. An exact
commit is accepted when policy permits. A tag remains display metadata
after resolution; no mutable ref is ever recorded as the active runtime
identity.

## 3. D3 — build environment identity is observed and frozen

The actual build container's image ID/digest and the toolchain identities
are observed and frozen into the manifest before source/build mutation.
When a stable image identity cannot be observed the build fails before any
mutation with `BUILD_ENVIRONMENT_UNPROVEN`. A mutable provision source
(literally `fedora:latest`) may remain a user-facing provisioning input
until the later container-supply-chain phase, but it can never be the sole
recorded identity of a promoted build. This distinction is honest and
deliberate.

## 4. D4 — active-tree mutation is one atomic exchange

Active cutover uses exactly one no-gap atomic exchange primitive — Linux
`renameat2(..., RENAME_EXCHANGE)` (or an equivalently reviewed single
syscall with identical semantics) — between the validated active runtime
root and a validated candidate/rollback tree on the same filesystem.

Requirements:

- preflight proves both paths are real directories, not symlinks;
- both are under one approved container runtime root;
- both are on one filesystem (same `st_dev`);
- active and candidate manifests match the expected identities;
- the helper refuses `/`, empty paths, `.`, `..`, traversal, aliases,
  newline/NUL, prefix confusion, sibling escapes, and nested overlap;
- parent directories are fsynced after exchange;
- missing exchange support fails safely BEFORE mutation
  (`ATOMIC_EXCHANGE_UNSUPPORTED`);
- there is NO fallback to `mv active backup && mv candidate active`.

The exchange helper is a fixed package resource with a recorded source
digest. When transferred into the container it is copied to an
operation-owned path, digest-verified remotely, invoked with typed argv,
and removed only when safe.

## 5. D5 — exact observation is a chain of independent evidence

"The intended runtime is running" is TRUE only when all of the following
agree:

1. the active tree manifest says build ID B;
2. the full SHA-256 of the active `llama-server` equals B's manifest entry;
3. handoff schema v2 expects B and the same binary digest;
4. a launcher/start receipt records B, the binary digest, the operation ID,
   and the invocation/start nonce immediately before `exec`;
5. systemd observation proves a new successful invocation after the swap;
6. service observation proves the expected model alias, context, and slots;
7. bounded minimal inference succeeds.

A start receipt alone is not proof the process stayed alive; health and
inference are required. Desired database/handoff values are never
substituted for observed service values.

## 6. D6 — promoted state means verified known-good, not "files moved"

The authoritative runtime component row changes only after §5 is
satisfied. Promotion happens in ONE database unit of work that:

- advances the runtime component generation;
- records the new promoted build and its tree;
- records the exact prior build as the rollback target and its retained
  tree;
- updates the known-good runtime component identity while preserving its
  model configuration;

and nothing else. "Candidate active" is never written to authoritative
promoted state before live verification; durable step rows and evidence
represent the in-progress external reality.

## 7. D7 — rollback is symmetric and toggle-safe

`RUNTIME_ROLLBACK v1` names the current promoted build and the exact
retained target at enqueue time (selected by the command service from the
repository, never free-form frontend paths). At cutover both are
revalidated against observed identities. On success the previously active
build becomes the next rollback target, so an accidental rollback can be
undone without rebuilding.

## 8. D8 — recovery prefers evidence, never optimism

For every interrupted tree-exchange probe:

| Observed arrangement | Classification | Action |
| --- | --- | --- |
| target identity active AND prior identity in its retained path | COMPLETE | checkpoint; never a second exchange |
| prior identity active AND target identity still staged | ABSENT | execute the original exchange exactly once |
| identities prove the reverse exchange already completed | RESTORED | continue forward |
| neither arrangement provable / duplicate identities / missing tree / containment or symlink failure | UNCERTAIN | enter `RECOVERY_REQUIRED`; delete and exchange NOTHING |

Classification uses exact content identities, never filenames, desired
state, or mutable tags.

## 9. D9 — cancellation is honored only before the activation boundary

Resolution, fetch, configure, and build declare bounded safe pulses.
Process cancellation terminates the process group, waits a bounded grace
period, then kills if necessary. Before the swap, cancellation cleans or
retains only operation-owned staging and must prove the active tree
unchanged. Once tree-exchange intent begins, cancellation is deferred until
the operation either succeeds or restores the prior runtime. Cancellation
never leaves "new files but old DB" mislabeled as success.

## 10. D10 — one service owner remains absolute

Runtime workflow modules and adapters never invoke `systemctl` or write the
unit file. They call a typed runtime-server port implemented through
`server.py`, which remains the sole owner of the `bc250-llm.service`
literal and systemd mutation. Architecture tests enforce this.

## 11. D11 — no dual runtime lifecycle route

After cutover there is exactly one production route:

```text
CLI / wizard / dashboard / setup
        -> RuntimeLifecycleCommandService
        -> shared EnqueueService
        -> shared ExecutionEngine factory
        -> RUNTIME_UPDATE v1 | RUNTIME_ROLLBACK v1
        -> one RuntimeLifecycleHostAdapter
```

`env.update_llamacpp`, `env.rollback_llamacpp`,
`ComponentLifecycleService.update_llamacpp/.rollback_llamacpp`, the
mutable `llamacpp_history` lifecycle writes, and the direct setup
clone/build path are deleted in the same boundary that wires the last
caller. Both routes are never callable at once. Fresh installations obtain
their first runtime through the same durable pinned update workflow.

## 12. Phase-scoped resource leasing (ADR 002 addendum summary)

The executor gains a generic mechanism so workflows acquire each resource
when their steps first need it instead of holding everything from claim:
acquisition of the full remaining set happens atomically (one transaction,
sorted keys, all-or-none, every held lease asserted), conflicts before a
critical effect resolve to deterministic pause/busy with no effect, and no
gap may exist between acquiring `runtime-active`, capturing the prior
snapshot, and the atomic exchange. Existing MODEL_ACTIVATE /
MODEL_ACQUIRE / MODEL_IMPORT workflows keep their present
all-at-claim behavior via an explicit compatibility declaration.

## 13. Consequences

- Update/rollback survive crashes at any point with exactly-once critical
  effects and closed terminal meanings (SUCCEEDED, FAILED_SAFE,
  FAILED_ROLLED_BACK, CANCELLED, RECOVERY_REQUIRED).
- Uncertainty is a product outcome: `RECOVERY_REQUIRED` retains every
  potentially useful tree and forbids destructive guessing.
- Foreground-only execution remains until U1.3; closing a frontend leaves
  the operation durably paused/interrupted for explicit resume. This
  limitation is reported honestly everywhere.
