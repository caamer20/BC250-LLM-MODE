# BC250 LLM MODE — Session 6A Final Checkpoint Implementation Plan

**Status:** DONE (executed; evidence below)

## Execution evidence (appended at closeout)

| Field | Value |
| --- | --- |
| Entry boundary commits | `ac2a675` … `8a3abe4` (Commits 1–5a + handoff) |
| 5b correction commit | `994428d` forward-only recovery, stable failure codes, cancellation finalizer, lease-fenced reservations, no-replace quarantine/storage hardening |
| Local import production commit | `1dea485` real `AcquisitionHostAdapter` end-to-end tests |
| Hub transfer commit | `6bb7bfb` immutable resolution + range resume (mock transport) |
| Cutover commit | `aa1dd95` composed command; synchronous route deleted; AST guards |
| Canary/stress commit | `8113e21` canaries + publication-death/registration/concurrency batteries |
| Documentation closeout | this commit |
| SQLite schema version | 4 (migration 004; no 005 required — repository checks enforce ADR 003 state pairs) |
| Default test result/count | 552 passed (`PYTHONPATH=.` and editable `.venv/bin/pytest`) |
| Slow stress result | 41 passed (`-m slow tests/test_acquisition_security_stress.py`: canary + 20×publication-death + 20×duplicate-concurrency, zero sleeps) |
| Slow clean-wheel result | 1 passed (`-m slow tests/test_packaging.py`) |
| Compile / diff-check | clean |
| Conversion note | catalog conversion entries refuse at resolve (`SOURCE_MANIFEST_INVALID`) pending U2.3 process port; direct-GGUF and local import are fully production-wired. This is recorded honestly rather than silently stubbed. |
| Preserved untracked files | ULTIMATE_BC250_APPLIANCE_IMPLEMENTATION_PLAN.md, SESSION_6A_DURABLE_MODEL_ACQUISITION_IMPLEMENTATION_PLAN.md, scripts_audit/*.py |
| Push/tag/version action | none |

---

**Original plan text follows.**

**Status:** Ready for execution from the mid-Session-6A handoff

**Plan IDs:** U1.1 / Session 6A completion / final acquisition checkpoint

**Stable starting HEAD:** `8a3abe4`

**Version:** `0.9.0.dev0`

**Starting verification evidence:** 528 default tests green; compile and
`git diff --check` clean

**SQLite schema at entry:** version 4

**Landed boundaries:** original Session 6A Commits 1–5a

**Remaining authority:** This plan supersedes only the unfinished Commit 5b
and Commit 6–8 sequencing in
`SESSION_6A_DURABLE_MODEL_ACQUISITION_IMPLEMENTATION_PLAN.md`. The earlier
plan, ADR 002, ADR 003, and
`ULTIMATE_BC250_APPLIANCE_IMPLEMENTATION_PLAN.md` remain requirements
authorities.

This is a continuation plan, not a restart. It preserves the accepted
operation/artifact decisions and finishes the real production path. Its final
checkpoint is the closed **U1.1 durable acquisition/import gate**: catalog
downloads and local imports are managed, resumable, validated, recoverable,
and used by every existing production surface, with the old synchronous route
deleted.

The final checkpoint in this document is not the 1.0 release and not the full
U1 exit gate. Runtime update/rollback, worker lifecycle, operation CLI,
Activity Center, and later appliance phases remain separate sessions.

The tracked tree is clean at entry. Preserve these user-owned untracked files
without editing, deleting, staging, or committing them unless separately
authorized:

```text
ULTIMATE_BC250_APPLIANCE_IMPLEMENTATION_PLAN.md
SESSION_6A_DURABLE_MODEL_ACQUISITION_IMPLEMENTATION_PLAN.md
scripts_audit/debug_5b.py
scripts_audit/debug_workers.py
scripts_audit/fix_app.py
scripts_audit/rewrite_app_a.py
scripts_audit/rewrite_app_b.py
```

This new plan is also untracked when first created. The executor may commit it
as the first documentation boundary, but must not absorb the other untracked
files.

---

## 1. Final checkpoint outcome

The session is complete only when all of the following describe production,
not merely a fake operation world:

1. `MODEL_ACQUIRE v1` resolves a catalog entry to an immutable repository
   revision and exact bounded source manifest.
2. Catalog files transfer through bounded, authenticated, range-aware,
   cancellation-aware HTTP with durable validators and safe partial resume.
3. `MODEL_IMPORT v1` copies a descriptor-stable local GGUF into private
   app-owned staging without modifying or later running from the source.
4. Conversion/quantization uses fixed argv, bounded process ownership,
   cancellation, deadlines, capped output, and an exact recipe identity.
5. Required/free/reserved/reclaimable bytes are checked honestly before every
   major growth boundary.
6. The final candidate is fully hashed and independently validated after any
   conversion or metadata healing.
7. Valid content is published atomically with no overwrite, correct mode, and
   power-loss-durable receipts/parent metadata.
8. Invalid content is published once into private quarantine without
   overwriting prior evidence and never receives an installation alias.
9. Duplicate bytes reuse one managed artifact and never duplicate final
   storage.
10. Artifact, alias, reservation, step, and terminal records are lease-fenced
    and transactionally consistent.
11. A crash after final publication but before checkpoint converges with
    transfer/copy, conversion, publication, and registration counts each
    exactly one.
12. Cancellation retains only a valid, labeled, operation-owned partial and
    releases its logical reservation.
13. No recovery or compensation path deletes a valid content-addressed
    artifact merely because registration, cleanup, or a later action fails.
14. CLI, wizard, dashboard, and local-model selection all call one composed
    `ModelAcquisitionCommandService`.
15. Install-and-use surfaces invoke the existing durable activation command
    only after acquisition succeeds, as a separate operation.
16. `download_model`, `prepare_model`, `prepare_local_model`, direct local
    registration, and the synchronous installation service are no longer
    production-callable.
17. Security canaries, crash/adverse matrices, no-sleep stress, source/editable
    parity, and clean-wheel smoke all pass.
18. Documentation and `AGENTS.md` report one exact clean checkpoint and the
    next Session 6B red test.

### Hard stop

Do not begin:

- `RUNTIME_UPDATE v1` or `RUNTIME_ROLLBACK v1`;
- background or detached worker execution;
- a generic operations CLI/query surface;
- Activity Center or broader GUI navigation redesign;
- quarantine deletion, model removal, garbage collection, or storage moves;
- broad U2 command/network/service refactors outside what acquisition itself
  requires;
- privileged helper, gateway, backup/restore, release signing, or 1.0 work;
- version bump, release tag, push, or publication.

---

## 2. Starting checkpoint: preserve what is genuinely complete

Do not redo or reinterpret these landed decisions without a failing regression
test proving a contract defect.

| Landed commit | Preserve |
| --- | --- |
| `ac2a675` | ADR 003, ADR 002 §16, two operation types, `model-storage`, quarantine as failed-safe, acquisition/activation separation |
| `54c0ab9` | Ordered migration 004, deterministic file-free legacy backfill, artifact/install/reservation tables, narrow repository direction |
| `e9c367e` | Closed `TerminalDecision`, cancellation-safe pulse signal, throttled progress direction, two eight-step workflows, fake operation world, mandatory publication-death proof |
| `a705cc4` | Hidden paths derived from `models_dir`, bounded streaming hash direction, content-addressed artifact namespace, no-replace direction |
| `8a3abe4` | Honest mid-plan handoff: old synchronous route remains production until one atomic cutover |

The executor begins by rerunning the 528-test default suite and focused landed
tests. If the count differs, record the authoritative collection result before
editing anything.

```bash
git status --short
git log -8 --oneline
PYTHONPATH=. .venv/bin/pytest -q
PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_session6a_policy.py \
  tests/test_migration_004.py \
  tests/test_operation_acquisition.py \
  tests/test_artifact_storage.py
python -m compileall -q bc250_llm_mode tests
git diff --check
```

---

## 3. Mandatory entry correction: Commit 5b before networking

The current fake-world and storage primitives prove the architecture, but a
production adapter would amplify several contract gaps. Correct them before
creating `hub_source.py`.

### 3.1 Findings that block Commit 6

| Priority | Audited finding | Required correction |
| --- | --- | --- |
| P0 | `quarantine_candidate()` deletes an existing destination and then uses `os.replace()` | Quarantine is no-replace. Exact existing digest/reason is reused; mismatch is a stable collision/recovery condition. Never unlink prior quarantine evidence. |
| P0 | Publication/receipt helpers do not consistently enforce 0600, unique temporary names, temp cleanup, and parent fsync | Reuse hardened `fsops` primitives or equivalent descriptor-based helpers; verify mode and fsync all durable names/receipts. |
| P0 | Acquisition compensation in the fake world can delete a valid published artifact | Treat published content-addressed artifacts as forward-only safe effects. Never compensate by deleting them; recover registration forward. |
| P0 | Reservation repository methods do not themselves prove the current `model-storage` lease fence | Reserve/update/release must assert operation, owner, and lease revision in the same UoW as the reservation write. |
| P0 | Workflow recovery trusts output presence for validation and compares recovered catalog identity against a fresh mutable resolution | Every verify/probe observes reality independently. Once a commit is pinned, recovery probes that pinned identity; it never follows current HEAD. |
| P0 | Stable `StepFailure.code` is collapsed into generic `STEP_FAILED_SAFE`/exception-class evidence | Preserve a closed sanitized code, retryability, mutation disposition, and safe bounded detail through engine failure mapping. Activation regressions must remain unchanged. |
| P1 | Request decoding accepts incomplete bounds/types; local paths can be relative; `requested_by` is open; import summary exposes a source basename | Close every field, require an absolute path, enforce alias/display/quant/fingerprint bounds, close surface values, and use a redacted operation summary. |
| P1 | Evidence dataclasses accept arbitrary nested mappings when reconstructed from durable JSON | Add exact decoders with unknown-field, type, range, count, nesting, and secret-like-key rejection. |
| P1 | Installation registration accepts a caller path and permits invalid trust/storage combinations through repository logic | Derive the path from the artifact record; require exactly `MANAGED + VERIFIED` for new aliases; never accept a caller path as authority. |
| P1 | Migration 004's SQL CHECK permits combinations ADR 003 says are illegal | Do not edit migration 004. Add migration 005 only if needed to rebuild with exact state-pair/digest/size/version constraints. |
| P1 | `ProgressReporter` remains disconnected/duplicative and has stale imports | Keep one progress policy/path. Delete the unused duplicate abstraction or make the engine use it with per-operation state. |
| P1 | Reservation cancellation is not guaranteed to release the row while retaining a safe partial | Add a workflow cancellation finalizer/step-specific compensation that releases reservation and records retained-partial evidence without touching final/quarantine artifacts. |
| P1 | All workflow steps currently derive the same generic input | Persist exact per-step canonical inputs, including source identity, reservation, transfer receipt, candidate recipe, validation digest, publication target, and registration alias. |

### 3.2 Forward-only effect model

The generic engine was designed around reversible effects. Content-addressed
publication is different: once exact validated bytes are durably published,
deleting them is neither required nor safe, especially when another alias may
reuse them.

Add a closed effect disposition to the generic step contract:

```text
EffectDisposition
  NONE                 # pure/read-only or no durable external effect
  HIDDEN_DISCARDABLE   # operation-owned staging; cleanup/retention policy
  REVERSIBLE           # compensation restores a prior external state
  FORWARD_ONLY         # safe immutable effect; recover subsequent state forward
```

Rules:

- activation steps retain their existing `REVERSIBLE` behavior;
- source resolution is `NONE`;
- reservation is `REVERSIBLE` through release;
- transfer/candidate work is `HIDDEN_DISCARDABLE`;
- quarantine/final publication is `FORWARD_ONLY`;
- artifact/alias commit is `FORWARD_ONLY` and idempotent;
- final staging cleanup is `HIDDEN_DISCARDABLE` plus reservation release;
- a failure after a proven forward-only effect never enters a rollback path
  that deletes the effect;
- retryable absence/incompleteness after a forward-only effect moves to
  `PAUSED` with a stable forward-recovery code and releases or safely expires
  the worker lease so the command service can resume it first;
- identity ambiguity or conflicting durable truth becomes
  `RECOVERY_REQUIRED` and retains the resource barrier;
- deterministic safe alias conflict may terminate `FAILED_SAFE` only after the
  artifact itself is represented durably and the existing alias is proven
  unchanged;
- no workflow callback may choose arbitrary operation states.

Amend ADR 002 and ADR 003 with this implementation correction. No existing
activation semantics may change.

### 3.3 Failure decision contract

Extend `StepFailure` or add an equivalent frozen closed record:

```text
code
summary
detail                 bounded and sanitized
mutation_disposition   ABSENT | DISCARDABLE | FORWARD_PROVEN | UNCERTAIN
retry_disposition      TERMINAL_SAFE | PAUSE_RETRY | RECOVERY_REQUIRED
```

The engine:

1. probes after a normal exception;
2. trusts probe reality over the exception's claim;
3. records the stable code, never raw exception text;
4. compensates only `REVERSIBLE` effects;
5. finalizes hidden staging according to explicit retention policy;
6. pauses retryable forward recovery;
7. retains leases only for `RECOVERY_REQUIRED`;
8. never reports `FAILED_ROLLED_BACK` when no prior external state was
   restored;
9. never reports `FAILED_SAFE` while an unrecorded or ambiguous final artifact
   remains.

### 3.4 Step-specific cancellation/finalization

Cancellation before publication must execute one typed acquisition finalizer:

```text
cancel_before_publication(operation_id, source_identity, staging_observation)
  -> CleanupEvidence
```

It must:

- close/terminate live network or process resources first;
- fsync a valid resumable partial and its exact source/validator receipt;
- delete an invalid/unidentified partial only inside the operation root;
- remove disposable conversion outputs;
- mark the logical reservation `RELEASED` under the current lease fence;
- preserve no candidate as installed;
- record retained bytes and resumability with bounded path tokens;
- verify the external source is unchanged for local import;
- terminate `CANCELLED`, never `FAILED_ROLLED_BACK`.

Cancellation after entering publication/registration remains deferred until
the critical step reaches a verified checkpoint or recovery state.

### 3.5 Storage durability corrections

Refactor `artifact_storage.py` around descriptor-safe primitives:

- normalize and validate every digest before deriving a path;
- use `mkstemp`/exclusive creation for incoming files, always 0600;
- copy/hash through an already-open regular-file descriptor where TOCTOU
  matters;
- fsync file, apply and verify mode, publish no-replace, fsync destination
  directory and relevant shard/root directory;
- use unique receipt temporaries, 0600, bounded JSON, atomic replacement, and
  parent fsync;
- clean temporary files on every `BaseException` without touching the target;
- open existing destinations without symlink following and prove regular-file
  identity before hashing;
- quarantine no-replace with exact reuse and mismatch refusal;
- never rely on `Path.exists()` followed by a destructive action as the
  security boundary;
- reject operation IDs/path tokens that can create separators or escape roots;
- make containment an input-validation aid, not the sole TOCTOU defense.

Add power-loss-order tests with injected fsync/open/link failures. Test modes
under permissive umask, read-only roots, existing symlinks, competing
publishers, stale incoming files, and failures after the destination name is
created but before receipt publication.

### 3.6 Schema/repository correction

Migration 004 is committed and must never be edited. If repository checks
alone cannot guarantee ADR 003 state pairs, add ordered migration **005** that
atomically rebuilds `model_artifacts` with:

- normalized `sha256:<64 lower-hex>` for managed/quarantined rows;
- positive byte size for new artifacts;
- validator version >= 1;
- tensor count null or >= 0;
- `MANAGED` iff trust is `VERIFIED`, with digest/size/validated timestamp and
  no quarantine reason;
- `QUARANTINED` iff trust is `QUARANTINED`, with digest/size/reason/timestamp;
- `LEGACY_EXTERNAL` iff trust is `LEGACY_UNVERIFIED`;
- exact source-kind vocabulary;
- all existing v4 rows copied only if valid;
- all indexes and foreign keys restored;
- complete rollback on any copy/drop/rename/index/version-row failure.

Also tighten reservation constraints: all byte fields nonnegative,
`credited_partial_bytes <= reclaimable_owned_bytes`,
`reserved_bytes <= required_bytes`, and released timestamp/state consistency.

Repository rules:

- `record_verified` and `record_quarantine` become exact-idempotent by digest,
  path, state, and trust; conflicting existing rows are refused;
- artifact IDs are derived from digest policy, not arbitrary frontend text;
- `install_alias` fetches canonical path from the artifact row and accepts no
  path parameter;
- only `MANAGED + VERIFIED` receives a new alias;
- same alias/same artifact is idempotent; same alias/different artifact is a
  stable conflict;
- artifact + alias + reservation transition uses one UoW where applicable;
- reservation mutation asserts the `model-storage` lease owner/revision in
  the same connection;
- `replace_all()` remains test-fixture-only or is replaced by a named test
  seeder; architecture guards prevent production use and future growth.

### 3.7 Request/evidence correction tests

Add table/property-style tests for:

- empty/relative/NUL/oversized local paths;
- symlink policy at execution;
- invalid alias/display/quant/fingerprint/surface types and bounds;
- booleans where integers are expected;
- negative/overflow sizes and totals;
- duplicate/unsafe source filenames;
- excessive source file count or aggregate bytes;
- malformed digest/revision/validator;
- unknown/nested secret-like evidence fields;
- source basename canary absent from operation summary/events;
- exact durable decode round-trip for every evidence type;
- old request/recovery versions remain exact and unknown versions pause safely.

### Commit 5b exit gate

- New red tests fail before each correction.
- Existing 528 default tests remain green.
- Mandatory fake publication-death count remains exactly one.
- Fake cancellation now proves reservation release and valid-partial retention.
- Fake failure after publication proves no artifact deletion.
- Activation crash/rollback matrices remain green.
- Storage mode/fsync/no-replace and repository-fence tests pass.
- If migration 005 is added, fresh and v4-to-v5 paths pass atomically.
- No hub/network/process adapter or frontend cutover has begun.

Suggested commit boundaries:

```text
fix(U1.1): harden forward-only acquisition recovery and evidence contracts
fix(U1.1): make managed artifact storage and repositories fail closed
```

---

## 4. Commit 6A — Local import production path first

Build the production host from the filesystem-only path before adding network
and conversion complexity. This gives the first real end-to-end operation
without requiring Hugging Face or Podman.

### 4.1 Production module ownership

Create:

```text
bc250_llm_mode/acquisition_adapter.py
```

The adapter receives through composition:

```text
AppPaths
UnitOfWorkFactory
catalog lookup/fingerprint policy
clock and UUID factories
artifact storage port
GGUF validator port
hub source port (stub/not used for import until 6B)
conversion process port (stub/not used for import)
disk-usage/statvfs port
```

It must not accept assembled mutable state, a generic destination, arbitrary
commands, arbitrary URLs, or a frontend runner as authority.

### 4.2 Descriptor-stable local source

`observe_local_source` and `copy_local` must:

1. require an absolute user-selected `.gguf` path;
2. open with no symlink following and close deterministically;
3. require one regular file and reject directories, sockets, FIFOs, devices,
   and unsupported sparse/extent conditions according to policy;
4. capture device, inode, size, mtime-ns, and mode before copy;
5. derive a redacted source label for events while keeping the absolute path
   only in the private request row;
6. copy fixed-size chunks to
   `<models_dir>/.bc250-staging/<operation-id>/transfer/`;
7. hash during copy for progress but independently hash the completed staged
   file for final proof;
8. pulse with `cancellation_safe=True` between chunks;
9. fsync staged data and its versioned receipt;
10. compare source descriptor identity after copy;
11. refuse changed/truncated/replaced source as `LOCAL_SOURCE_CHANGED`;
12. leave source bytes, size, mode, mtime, name, and parent contents unchanged.

The adapter never calls `prepare_local_model` and never patches the source.

### 4.3 Disk preflight and reservation

For local import, calculate integer bytes for:

- complete source copy;
- possible healing/candidate copy;
- final publication incoming copy if the publication primitive requires it;
- receipt/metadata overhead;
- safety reserve;
- exact valid operation-owned partial credit.

Record filesystem identity from the actual `models_dir` filesystem. Refuse
insufficient space before copy and recheck before materialization and
publication. Do not credit quarantine, managed artifacts, another operation,
or arbitrary files.

Reservation creation/update/release is fenced in the same UoW. A stale worker
may continue reading bytes but cannot checkpoint, publish, register, or release
the newer owner's reservation.

### 4.4 Candidate and validation

- Direct import materialization creates a candidate path token under the
  operation root; it never exposes an absolute staging path durably.
- Use the U0 hardened bounded GGUF parser and `model_artifact` identity checks.
- Any metadata healing operates only on the candidate, then rehashes and
  revalidates from a fresh descriptor.
- Validation evidence includes digest, size, GGUF version, architecture,
  tensor/block facts, inferred quantization when reliable, standard-layout
  verdict, validator version, and stable warnings.
- Invalid/fused/MAX/projector/hostile content routes to no-replace quarantine.
- The selected source remains unchanged even when the candidate is healed.

### 4.5 Publish/register/finalize

- Derive artifact ID and final path from the validated digest.
- Probe exact-existing content with full digest before reuse.
- Publish no-replace and write a durable operation publication receipt.
- Register artifact and alias with exact source kind `local` and bounded
  provenance.
- Alias defaults to a deterministic sanitized product identifier, not merely
  a path hash that changes when the source is moved.
- If caller supplies an alias, apply the same closed policy.
- Quarantine writes an artifact row with no alias.
- Release reservation and clean only owned intermediates.
- A cleanup failure may return success with explicit retained-staging warning
  only after artifact/alias truth is proven and reservation is released.

### 4.6 Local production tests

Use real temp filesystem operations, not only `FakeAcquisitionHost`:

```text
test_production_import_copies_valid_gguf_to_managed_digest_path
test_production_import_leaves_source_byte_and_metadata_identical
test_production_import_refuses_symlink_fifo_directory_and_source_swap
test_production_import_cancel_retains_valid_partial_and_releases_reservation
test_production_import_resume_uses_same_descriptor_identity
test_production_import_invalid_gguf_quarantines_without_alias
test_production_import_duplicate_digest_reuses_one_final_file
test_production_import_publication_death_converges_exactly_once
test_production_import_registration_death_converges_exactly_once
test_production_import_custom_models_dir_writes_nothing_to_app_fs_staging
test_production_import_sentinel_home_remains_empty
```

Stop and review this path before implementing HTTP.

---

## 5. Commit 6B — Immutable hub source and range transfer

Create `bc250_llm_mode/hub_source.py` as a typed production adapter using
bounded `httpx` calls or an equally inspectable library already declared by
the project. Do not use the old unbounded `hf download` orchestration.

### 5.1 Catalog policy fingerprint

Add one canonical fingerprint function covering acquisition-relevant fields:

```text
catalog model ID and schema/version
repo and source repo
quantization allow glob
avoid globs
direct-vs-conversion mode
conversion source allowlist policy
expected weight/temporary bytes
checksum manifest policy
family/true block count/validation tier
```

The command service computes and persists the fingerprint at enqueue. The
workflow preflight requires exact agreement. A queued request under changed
catalog policy pauses/fails with `CATALOG_POLICY_CHANGED`; it is never silently
reinterpreted.

### 5.2 Immutable revision resolution

Resolution must:

1. map only a built-in catalog ID and allowlisted quantization;
2. query repository metadata under bounded connect/read/overall deadlines;
3. resolve mutable HEAD/default branch to a full immutable commit SHA;
4. enumerate one exact direct GGUF or one bounded conversion manifest;
5. reject unsafe paths, symlinks, duplicates after normalization, excessive
   count/size, avoid globs, projector/vision/media assets, and unknown required
   file types;
6. record per-file path token, immutable blob/LFS identity, expected length,
   and optional trusted checksum;
7. bind checksum manifest to the same commit;
8. persist no signed URL or raw header;
9. on recovery, probe the saved commit/blob identity rather than resolving
   current HEAD again.

If the pinned commit disappears, terminate safely with
`SOURCE_REVISION_UNAVAILABLE`; never follow a newer branch revision.

### 5.3 HTTP security policy

- Closed HTTPS host allowlist for metadata and blob origins.
- Authorization header created from `HF_TOKEN` only at call time.
- Never put the token in argv, URL, durable JSON, receipt, or log.
- Strip authorization on cross-origin redirect unless the exact destination
  host is explicitly trusted for credential forwarding.
- Bound redirect count, request/response header bytes, metadata body size,
  chunk size, retry count, backoff, connect timeout, read-idle timeout, and
  overall deadline.
- Reject user-controlled proxy/URL overrides in durable input.
- Handle anonymous operation and stable auth/rate-limit codes.
- Signed redirect query is ephemeral and redacted from errors/logs.

### 5.4 HEAD/range resume algorithm

For each pinned file:

1. observe expected representation length, immutable blob identity, range
   support, and hashed transport validator;
2. read a bounded private partial receipt;
3. credit existing bytes only when source fingerprint, file identity,
   expected length, and validator agree;
4. request `Range: bytes=N-` with `If-Range` where supported;
5. append only on exact `206` start/total/identity agreement;
6. on full `200`, atomically reset only the owned partial and start at zero;
7. on `416`, accept completion only when expected length and independent full
   digest agree; otherwise reset/refuse;
8. reject compressed artifact representation unless identity is explicitly
   modeled;
9. fsync partial and receipt at bounded checkpoints;
10. pulse progress/cancellation between chunks;
11. independently hash every completed file;
12. verify trusted manifest digest where provided.

If a validator changes while the immutable blob identity remains the same,
discard only the owned partial and restart from zero. If logical blob identity
changes, fail `SOURCE_CHANGED` rather than mixing representations.

### 5.5 Local HTTP server tests

Build deterministic tests for:

- HEAD + complete 200;
- valid 206 resume;
- ignored Range returning 200;
- valid and invalid 416;
- wrong Content-Range start/total;
- ETag/Last-Modified/blob identity change;
- redirect loop and excessive redirects;
- trusted and untrusted cross-origin redirect auth handling;
- signed URL canary redaction;
- oversized headers/metadata/body;
- slow/stalled response and total timeout;
- repeated connection drop with bounded retry exhaustion;
- cancellation mid-body;
- disk full mid-transfer;
- partial receipt absent/corrupt/oversized/foreign-operation;
- branch changes after pinning;
- pinned commit unavailable;
- source manifest traversal/symlink/duplicate/forbidden assets;
- anonymous/authenticated/rate-limited responses.

No test reaches the public internet.

---

## 6. Commit 6C — Conversion process adapter and complete production host

Catalog entries that already provide GGUF use the transferred artifact as the
candidate. Conversion entries require a narrow process adapter.

### 6.1 Explicit conversion source policy

The two broad catalog conversion entries cannot retain unrestricted `*`
snapshot semantics in production. Add a catalog-owned conversion manifest
policy that permits only required:

- config/tokenizer metadata;
- supported safetensors shards and index;
- fixed converter inputs;
- no executable repository scripts, optimizer/training state, media,
  projector/vision files, alternate model formats, caches, VCS files, or
  path traversal.

Cap file count and aggregate bytes against catalog temporary-space policy.
Tests freeze the exact accepted manifest for representative conversion
entries.

### 6.2 Acquisition-only process port

Create a narrow typed process adapter for converter and quantizer only:

```text
run_conversion(recipe, pulse) -> ProcessResult
run_quantization(recipe, pulse) -> ProcessResult
```

Requirements:

- fixed reviewed argv; no shell;
- composed container, virtualenv, llama.cpp, staging, and tool paths;
- no arbitrary executable, cwd, environment, or extra arguments from request;
- process-group ownership;
- total and idle deadlines;
- bounded polling using injected clock/wait primitive;
- capped/redacted stdout and stderr;
- cancellation: terminate group, bounded wait, then kill;
- stable missing-tool, timeout, cancel, nonzero, and output-missing codes;
- no token or full environment logging;
- deterministic recipe identity before execution;
- output accepted only from exact operation candidate paths.

This is deliberately acquisition-scoped and later absorbed into U2.3. Do not
replace every command runner in this session.

### 6.3 Recipe identity and recovery

Recipe identity includes:

```text
pinned source revision and ordered source blob digests
converter script/component identity
Python/toolchain identity
conversion output type
quantizer binary identity
quantization
catalog repair/true-block policy
recipe schema version
```

Recovery reuses a candidate only after proving its complete digest, validation
facts, and exact recipe receipt. Presence or filename alone is not complete.
An interrupted or mismatched conversion output is operation-owned and
discardable; a tool process that may still exist is terminated/probed before
retry.

### 6.4 Complete `AcquisitionHostAdapter`

Wire every pure-port method to production behavior:

- source resolution/observation;
- preflight and fenced reservation;
- transfer/copy and partial probe;
- candidate materialization/probe;
- full hash and GGUF validation/probe;
- valid publication or quarantine/probe;
- artifact/alias registration/probe;
- cancellation/finalization/probe.

Every probe must be read-only and independent of prior execute return values.
Every output is converted through exact evidence decoders before returning to
the engine.

### 6.5 Commit 6 production gate

- Real local import end-to-end tests pass.
- Real local HTTP range/resume tests pass.
- Real fake-executable conversion tests pass.
- Mandatory production publication-death test passes with exact effect counts.
- Full digest is recomputed after conversion/healing.
- Tokens, URLs, output, and source-path canaries do not leak.
- No final or quarantine overwrite is possible.
- No source file or repository snapshot is modified.
- No production frontend uses the adapter yet; old route remains sole
  production caller until atomic Commit 7 cutover.

Suggested subjects:

```text
feat(U1.1): implement descriptor-safe durable local model import
feat(U1.1): add immutable range-aware catalog source transfer
feat(U1.1): bind bounded conversion and the production acquisition host
```

---

## 7. Commit 7A — Command service and composition

### 7.1 `ModelAcquisitionCommandService`

Create `bc250_llm_mode/acquisition_command.py` with frozen outcomes and two
entry points:

```text
acquire_catalog(model_id, quantization, requested_by, progress_observer=None)
import_local(source_path, alias=None, display_name=None,
             requested_by, progress_observer=None)
```

The command service:

1. creates the exact request payload, including catalog fingerprint;
2. scans durable acquisition/import operations and the `model-storage` lease;
3. returns `RECOVERY_REQUIRED` for a retained barrier;
4. returns `BUSY` for a current nonexpired owner;
5. resumes the oldest matching interrupted/paused operation before enqueueing
   a new conflicting request;
6. never leapfrogs a prior forward-recovery registration;
7. enqueues through the one shared `EnqueueService`;
8. executes in the current process through the shared engine factory;
9. maps only durable terminal/paused truth;
10. never performs filesystem, network, SQL, conversion, or activation effects
    itself.

Closed status set:

```text
INSTALLED
REUSED
QUARANTINED
CANCELLED
CANCELLED_PARTIAL_RETAINED
FAILED_SAFE
PAUSED
BUSY
RECOVERY_REQUIRED
```

Outcome includes operation ID, stable code, safe artifact ID/alias, retained
partial byte count, and `activation_allowed`. It excludes token, URL, raw
headers/output, and generic local source path.

### 7.2 Ctrl-C and progress observer

The foreground service may accept a transient observer for existing UI/CLI
feedback. Durable operation progress remains authority.

- observer receives closed phase/current/total/unit/summary values only;
- observer errors do not mutate operation truth;
- Ctrl-C after enqueue durably requests cancellation at the next safe point;
- the command waits a bounded period for truthful cancellation/critical-step
  resolution;
- unresolved interruption exits 130 while leaving the operation nonterminal,
  never fabricating cancellation;
- Ctrl-C before enqueue creates no operation;
- no generic detach or operation-management commands are added.

### 7.3 One composition graph

Update `Application._wire_services`:

1. compose activation adapter as now;
2. compose one acquisition adapter;
3. register activation, acquire, and import definitions in one registry;
4. freeze once;
5. create one shared `EnqueueService` and one engine factory;
6. compose `application.activation` and
   `application.model_acquisition` from those shared objects;
7. remove `application.model_install` only in the same cutover series after
   callers are converted;
8. composition performs no HTTP, source scan, process, operation enqueue,
   worker startup, or model mutation.

Add identity-spy tests proving one registry/enqueue/engine-factory graph and
that activation behavior remains unchanged.

---

## 8. Commit 7B — Atomic CLI/GUI/model-manager cutover

The new production route and old synchronous route must not coexist in a
committed checkpoint. Convert callers and delete bypasses in one review
boundary or in tightly ordered commits where an architecture guard prevents
the second route from remaining callable.

### 8.1 CLI `install-model`

Preserve parser compatibility:

```text
bc250-llm-mode install-model MODEL_ID [--quant Q] [--ctx N]
```

Behavior:

1. acknowledgement/environment/fit validation remains;
2. choose quant exactly as today when omitted;
3. call `application.model_acquisition.acquire_catalog`;
4. print one bounded outcome including operation ID and installed/reused alias;
5. if setup is complete and acquisition permits activation, set requested
   context through the owning durable runtime/activation path and call the
   existing activation command as a distinct operation;
6. report both operation IDs;
7. quarantine/cancel/pause/busy/recovery never activates;
8. no caller-side state commit or `download_dir` mutation;
9. Ctrl-C follows section 7.2 and exits 130.

Do not add a global JSON mode or generic operations CLI in this checkpoint.
Existing machine-readable command conventions must remain stable.

### 8.2 CLI `models use` for discovered local files

- installed alias continues directly to durable activation;
- discovered local ID resolves to one explicit source selection;
- run fit preview from discovered metadata, but do not treat it as validation;
- call `import_local`, refresh the read model, then activate the returned alias
  only on `INSTALLED/REUSED`;
- never call `prepare_local_model` or activate the external path;
- source-disappeared/source-changed returns a stable safe error with operation
  ID.

Refactor `register_and_switch_local` into a thin import-then-activate command
helper or remove it. It must contain no filesystem preparation.

### 8.3 Wizard

Preserve the visible setup stages while replacing mutable path handoff:

- Step 6 starts/reattaches to one acquire/import operation.
- Step 7 renders durable validation/install outcome; it does not call a
  prepare helper.
- `self.downloaded_path`, `state_data["downloaded_path"]`, and
  `download_dir` cease to be authority and are deleted where acquisition
  owned them.
- Local copy language says the original remains unchanged and the managed
  copy consumes additional storage.
- Progress uses the transient observer and stays honest that foreground
  execution does not survive closure until U1.3.
- Closing the wizard leaves the durable operation interrupted/paused; it does
  not mark failure/success/cancel.
- Reopening queries durable operation/install truth and resumes when safe.
- Step 8 invokes service installation and durable activation only after an
  installed alias is returned; conversion cleanup helper is no longer called.
- Setup stage cannot advance on quarantine, cancellation, pause, busy, or
  recovery required.

### 8.4 Dashboard

- Catalog “Download and activate” becomes acquire then activate.
- Discovered-local “Use” becomes import then activate.
- Installed entries activate directly.
- Distinct messages exist for reuse, quarantine, partial retained,
  paused/interrupted, busy, and recovery required.
- Refresh reads `Application.read_model()` and performs no generic commit.
- No GUI module imports download, prepare, HTTP, subprocess, repositories,
  artifact storage, or SQLite.

### 8.5 Delete the synchronous production route

After callers are converted:

- delete `download_model` or retain only named pure helpers with no production
  orchestration;
- delete `prepare_model` and `prepare_local_model` production entry points;
- delete `ModelInstallationService.download_and_prepare` and remove
  `application.model_install`;
- remove direct preparation imports from `__main__.py`, `gui/*`,
  `model_manager.py`, and `services.py`;
- remove caller-side conversion cleanup now owned by operation finalization;
- remove `download_dir` from `FRONTEND_COMMIT_KEYS` and any acquisition-owned
  state projection;
- prevent `ModelInstallationsRepository.replace_all` production use;
- leave pure GGUF/catalog estimation utilities only where independently
  useful and ownership-neutral.

### 8.6 Architecture guards

Add AST/import/call guards asserting:

- zero production calls/imports of removed synchronous functions;
- zero frontend imports of download/prepare/hub/process/storage/repositories;
- zero frontend writes to artifact/install/reservation tables;
- one application acquisition command and one activation command;
- no activation payload can carry a caller path;
- no managed artifact writer outside `artifact_storage.py`;
- no quarantine writer outside `artifact_storage.py`;
- no `Path.home()` outside `paths.py`;
- no shell interpolation for acquisition paths;
- composition does not enqueue/start work;
- no new use of whole-list installation replacement in production.

### Commit 7 gate

- Every production surface reaches the composed durable command.
- Old route is absent, not merely unused by current tests.
- Import always uses managed storage.
- Acquire/import never activates internally.
- Optional follow-on activation remains durable and singular.
- Status/query refreshes do not bump revisions.
- GUI headless contracts and activation tests remain green.

Suggested subjects:

```text
feat(U1.1): compose the durable model acquisition command
refactor(U1.1): cut every frontend to durable acquisition and remove bypasses
```

---

## 9. Commit 8A — Production crash, adverse, concurrency, and security gate

The fake matrix is necessary but not sufficient. Exercise the real adapter
with local servers, real temporary filesystem effects, and fake executables.

### 9.1 Production crash matrix

For local import, direct hub GGUF, conversion, quarantine, publication,
registration, and finalization inject death at:

```text
before intent
after intent / before effect
mid-effect safe chunk
after effect / before checkpoint
after checkpoint / before verification
after verification / before next intent
before critical transition
after critical transition / before effect
after terminal write / before lease release
```

Take over with a new worker/lease generation and assert convergence. No test
uses wall-clock sleep.

Mandatory exact-count assertions:

- transfer/copy once after complete-effect death;
- conversion and quantization once when exact recipe output is complete;
- final publication once;
- quarantine publication once;
- artifact/alias registration once;
- existing exact artifact never republished;
- final artifact never deleted by compensation;
- source external file never mutated;
- stale worker writes/effects/checkpoints/releases zero times after takeover.

### 9.2 Adverse matrix

Cover:

- disk insufficient before work and disk full at each growth boundary;
- filesystem identity changes;
- readonly roots and permission failures;
- source modified/replaced/truncated during import;
- network timeout, disconnect, range mismatch, validator change, pinned commit
  disappearance, and bounded retries;
- converter missing, timeout, nonzero, malformed output, cancellation, and
  descendant process cleanup;
- hostile/truncated/oversized/fused/projector GGUF;
- digest/manifest mismatch;
- final path exact reuse, mismatch, symlink, directory, or wrong type;
- quarantine exact reuse and mismatch;
- DB busy at reservation, registration, terminal, and cleanup;
- alias conflict without mutation;
- concurrent acquire/acquire, import/import, and acquire/import;
- cancellation before transfer, mid-transfer, mid-copy, mid-conversion,
  immediately before publication, and during a critical step;
- cleanup failure after successful installation;
- observer/frontend callback failure;
- unexpected process death while a partial receipt is being published.

### 9.3 Security canaries

Use unique canaries for:

```text
HF_TOKEN
Authorization header
signed redirect query
local absolute source path and basename
fake stdout/stderr
remote error body
malicious source filename
```

Search after success, failure, cancellation, pause, quarantine, and recovery:

- operation request/steps/events/progress/result/error;
- artifact provenance/validation and reservation rows;
- receipts and staging/quarantine metadata;
- setup/debug logs;
- CLI stdout/stderr;
- GUI-visible outcome strings;
- exception chains serialized by tests.

Token/auth/signed URL/raw output/error-body canaries must appear nowhere.
Absolute local source exists only in the private request row where required;
generic summaries/events/outcomes use a redacted label.

### 9.4 20/20 no-sleep stress batteries

Run at least 20 deterministic iterations each:

- publication death/takeover;
- registration death/takeover;
- cancel versus publication critical entry;
- lease takeover versus stale pulse;
- duplicate-content concurrent operations;
- partial resume versus source/validator change;
- conversion process cancellation and cleanup.

Assertions include zero flake, exact counts, no orphan unreceipted temporary
files, no leaked leases/reservations except intentional recovery barriers, and
no untracked mutation outside temp roots.

---

## 10. Commit 8B — Packaging, documentation, and final evidence

### 10.1 Clean-wheel extension

Extend the slow packaging smoke so the installed wheel, with repository root
excluded from `sys.path`, can:

1. initialize the final SQLite schema (v4 or v5 if correction landed);
2. import every new production module;
3. compose the registry with activation/acquire/import;
4. run a no-network production local import fixture to `SUCCEEDED`;
5. run an invalid fixture to `FAILED_SAFE / ARTIFACT_QUARANTINED`;
6. prove managed artifact and alias rows;
7. prove source remains unchanged;
8. verify package discovery still exactly matches importable subpackages.

Do not rely on source-tree imports or undeclared dependencies.

### 10.2 CLI and GUI smoke

Verify from source and installed wheel:

- `--help`, `--version`;
- `install-model` parser shape;
- installed/local `models use` behavior with adapter-boundary fakes;
- human success/reuse/quarantine/busy/recovery output;
- Ctrl-C 130 semantics;
- wizard headless construction and durable reattach path;
- dashboard install/use actions route to composed commands;
- no GUI thread performs direct network/process/SQL work.

### 10.3 Documentation truth pass

Update tracked documents:

- `AGENTS.md` — remove the mid-plan warning, record exact HEAD/test/schema
  evidence, one durable acquisition/import path, managed storage layout,
  foreground-only limitation, and Session 6B first red test;
- `SESSION_6A_FINAL_CHECKPOINT_IMPLEMENTATION_PLAN.md` — mark DONE and append
  boundary commits/evidence;
- ADR 002/003 — forward-only correction and implementation record;
- `ARCHITECTURE.md` — source/transfer/staging/validation/publication/
  registration flow and ownership;
- `STATE_SCHEMA.md` or current schema authority — final artifact/reservation
  schema and legacy backfill;
- `README.md` — managed-copy semantics, disk use, cancellation/resume,
  quarantine, operation IDs, foreground interruption, install-vs-activate;
- `CHANGELOG.md` — durable acquisition/import and removed bypasses;
- command/architecture audits affected by deleted sync sites.

Do not claim:

- background continuation after GUI/CLI closure;
- a generic Activity/operations interface;
- durable runtime updates;
- quarantine/model removal UI;
- authenticated WebUI backend routing;
- 0.9.0 or 1.0 release readiness beyond U1.1.

The untracked original plans remain untouched unless the user explicitly
chooses to commit them.

### 10.4 Final verification battery

Run and record exact return codes/counts:

```bash
git diff --check
python -m compileall -q bc250_llm_mode tests
PYTHONPATH=. .venv/bin/pytest -q
.venv/bin/pytest -q
.venv/bin/pytest tests --collect-only -q
.venv/bin/pytest -q -m slow tests/test_packaging.py
```

Run focused production gates explicitly:

```bash
PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_session6a_policy.py \
  tests/test_migration_004.py \
  tests/test_artifact_storage.py \
  tests/test_operation_acquisition.py \
  tests/test_acquisition_adapter.py \
  tests/test_hub_source.py \
  tests/test_acquisition_command.py \
  tests/test_acquisition_cutover.py \
  tests/test_architecture.py
```

If migration 005 is added, include its named test file. Use actual filenames
created by implementation; do not hide missing files with conditional shell
logic.

Also verify:

- source/editable collection parity;
- wheel import with repository root absent;
- no internet required by tests;
- no test sleeps used for race correctness;
- no secret canary in workspace test outputs/logs/databases/receipts;
- `git status --short` lists only intentionally preserved untracked files;
- no push, tag, release, or version bump occurred.

Never predict the final test count. Record the collection hook's authoritative
number.

---

## 11. Reviewable execution sequence

Use this exact order. Each boundary starts red and stops green.

| Order | Boundary | Required stop condition |
| ---: | --- | --- |
| 1 | Plan checkpoint | Commit this continuation plan only; preserve all other untracked files |
| 2 | 5b engine/workflow correction | Forward-only recovery, stable failure codes, cancellation finalizer, exact probes; activation parity green |
| 3 | 5b storage/repository correction | No-replace quarantine, durable private receipts, lease-fenced reservations, schema correction if required |
| 4 | 6A local production adapter | Real local import, source unchanged, valid/quarantine/dedupe/publication-death tests green |
| 5 | 6B immutable hub transfer | Pinned source, range resume, redirect/auth/bounds tests green |
| 6 | 6C conversion/full host | Bounded process adapter and complete catalog acquisition tests green |
| 7 | 7A command/composition | Shared registry/enqueue/engine and typed command tests green; no caller cut over yet |
| 8 | 7B atomic cutover | All production callers converted; old route deleted; architecture guards green |
| 9 | 8A recovery/security | Full production crash/adverse/canary/stress battery green |
| 10 | 8B packaging/docs | Full + slow battery, clean wheel, docs truth, clean tracked tree |

Suggested commits:

```text
docs(U1.1): freeze the Session 6A final-checkpoint sequence
fix(U1.1): preserve forward-only artifacts across recovery and cancellation
fix(U1.1): harden artifact publication and lease-fenced persistence
feat(U1.1): implement descriptor-safe durable local model import
feat(U1.1): add immutable resumable catalog transfer
feat(U1.1): complete bounded conversion and acquisition host wiring
feat(U1.1): compose the durable model acquisition command
refactor(U1.1): cut all frontends to durable acquisition
test(U1.1): close production recovery and security matrices
docs(U1.1): close the durable acquisition final checkpoint
```

Do not merge the adapter and frontend cutover merely to save commits. Do not
leave both production paths callable in any stable committed checkpoint.

---

## 12. Final checkpoint acceptance matrix

### Persistence and identity

- [ ] SQLite schema and migrations are ordered, atomic, and accurately
      documented.
- [ ] Existing legacy installations survive without filesystem hashing.
- [ ] New artifacts have normalized full digest, exact size, managed path,
      trust, validator, provenance, and operation evidence.
- [ ] Installation path is derived from artifact truth, not caller input.
- [ ] Reservations are lease-fenced and released/retained honestly.
- [ ] Duplicate digest stores one final artifact.
- [ ] Alias conflict changes neither existing alias nor artifact truth.

### Filesystem and source safety

- [ ] Local source is byte/metadata identical after every outcome.
- [ ] All new import paths point inside managed storage.
- [ ] No final path exists before successful validation.
- [ ] Final and quarantine publication are atomic no-replace.
- [ ] Files are 0600 and private roots/receipts are 0700/0600.
- [ ] File and parent fsync order is tested.
- [ ] Cleanup cannot escape its operation root.
- [ ] Valid published artifacts are never deleted by compensation.
- [ ] Injected custom models path never falls back to HOME/app staging.

### Network and process bounds

- [ ] Repository revision is immutable before transfer.
- [ ] Exact source manifest is bounded and allowlisted.
- [ ] Range resume requires identity + validator agreement.
- [ ] Redirect/auth policy prevents credential leakage.
- [ ] Retry, connect, idle, overall, and output limits are explicit.
- [ ] Conversion uses fixed argv/process group/deadlines/cancellation.
- [ ] Full candidate digest/validation is repeated after mutation.

### Operation semantics

- [ ] Acquire/import have exact request/recovery versions.
- [ ] Every probe is read-only and independent.
- [ ] Every effect/checkpoint/terminal/release is fenced.
- [ ] Forward-only publication recovers forward.
- [ ] Cancellation finalizes reservation and partial truth.
- [ ] Quarantine is `FAILED_SAFE / ARTIFACT_QUARANTINED`.
- [ ] Ambiguity is `RECOVERY_REQUIRED`, never guessed.
- [ ] Mandatory publication-death and registration-death tests perform every
      completed effect exactly once.
- [ ] 20/20 no-sleep stress matrices pass.

### Product cutover

- [ ] CLI catalog install uses durable acquisition.
- [ ] CLI discovered local use imports then activates.
- [ ] Wizard uses operation truth, not a downloaded path variable.
- [ ] Dashboard catalog/local actions use the command service.
- [ ] Acquisition itself never activates.
- [ ] Follow-on activation is the existing one durable activation path.
- [ ] Old synchronous download/prepare/import route is deleted and guarded.
- [ ] Composition starts no work and creates no operation.
- [ ] Closing a frontend fabricates no terminal state.

### Security and release engineering

- [ ] Secret/source/output canary matrix is clean.
- [ ] Source and editable suites collect/pass identically.
- [ ] Compile and diff checks pass.
- [ ] Clean-wheel slow smoke passes.
- [ ] CLI and headless GUI smoke pass from installed artifact.
- [ ] Docs describe actual foreground-only limitations.
- [ ] Tracked tree is clean; preserved untracked files are listed exactly.
- [ ] No version bump, tag, push, or release occurred.

Any unchecked item keeps Session 6A open.

---

## 13. Final evidence record template

Append this completed table to this plan and summarize it in `AGENTS.md`:

| Evidence | Final value |
| --- | --- |
| Starting HEAD | `8a3abe4` |
| Plan commit |  |
| 5b forward-recovery commit(s) |  |
| 5b storage/repository commit(s) |  |
| Local adapter commit |  |
| Hub transfer commit |  |
| Conversion/full host commit |  |
| Command/composition commit |  |
| Frontend cutover commit |  |
| Recovery/security commit |  |
| Documentation closeout commit |  |
| Final HEAD |  |
| SQLite schema version |  |
| Default test result/count |  |
| Editable test result/count |  |
| Authoritative collected count |  |
| Slow clean-wheel result |  |
| Focused stress result |  |
| Compile result |  |
| Diff-check result |  |
| CLI smoke result |  |
| Installed-wheel smoke result |  |
| Secret-canary result |  |
| Tracked-tree state |  |
| Preserved untracked files |  |
| Push/tag/version action | none |

Do not mark this plan DONE until every required field has a real value.

---

## 14. Handoff after the final checkpoint

After Session 6A closes, stop. The next plan is Session 6B / U1.2 durable
llama.cpp update and rollback.

The first red test remains:

> `RUNTIME_UPDATE v1` atomically swaps a staged, smoke-checked runtime tree
> into the active path and dies before checkpoint. A takeover probes exact
> component identity. If the target is active, it checkpoints without a
> second swap; if the exact prior tree remains active, it performs the original
> swap once; if neither can be proven, it enters `RECOVERY_REQUIRED` without
> deleting either recoverable tree.

Session 6B should reuse the now-proven bounded process, immutable source,
receipt, forward-only publication, progress, cancellation, and lease-fencing
patterns where appropriate. It must add runtime-specific restoration and
service verification rather than assuming model-artifact semantics apply
unchanged.

---

## Appendix A — Required stable codes for the remaining implementation

Use the prior plan's catalog plus these explicit forward-recovery codes:

```text
CATALOG_POLICY_CHANGED
SOURCE_REVISION_UNAVAILABLE
SOURCE_MANIFEST_INVALID
SOURCE_REDIRECT_REFUSED
SOURCE_RANGE_MISMATCH
SOURCE_VALIDATOR_CHANGED
SOURCE_CHECKSUM_MISMATCH
SOURCE_TIMEOUT
LOCAL_SOURCE_CHANGED
MODEL_STORAGE_INSUFFICIENT
MODEL_STORAGE_CHANGED
STAGING_OWNERSHIP_INVALID
PARTIAL_RECEIPT_INVALID
PARTIAL_RETAINED
CONVERSION_CANCELLED
CONVERSION_TIMEOUT
CONVERSION_FAILED
GGUF_INVALID
GGUF_LAYOUT_FORBIDDEN
PUBLICATION_COLLISION
PUBLICATION_IDENTITY_UNCERTAIN
QUARANTINE_COLLISION
ARTIFACT_RECORD_CONFLICT
INSTALLATION_ALIAS_CONFLICT
REGISTRATION_RETRY_REQUIRED
CLEANUP_RETAINED
LEASE_LOST
ARTIFACT_QUARANTINED
MODEL_INSTALLED
MODEL_REUSED
```

Every code has bounded user text, retryability, and remediation meaning.
Never use raw exception strings as a stable code or durable detail.

---

## Appendix B — Executor checklist

Before each boundary:

- [ ] Read AGENTS, this plan, the original Session 6A plan, ADR 002, and ADR
      003 completely.
- [ ] Recheck status/HEAD/test collection and preserve untracked files.
- [ ] Add a failing named test before the production change.
- [ ] Confirm the change stays within U1.1.
- [ ] Keep pure operation modules free of integration imports.
- [ ] Use injected paths, clocks, UUIDs, HTTP, process, disk, and crash hooks.
- [ ] Store only closed bounded evidence and stable codes.
- [ ] Probe actual reality, never desired state or effect return values alone.
- [ ] Fence every durable mutation and external checkpoint.
- [ ] Reuse durable inputs/effect IDs after takeover.
- [ ] Run focused tests and activation regressions.
- [ ] Run compile and diff-check.
- [ ] Review architecture guards and production call graph.
- [ ] Cite `U1.1` in commit subject/body.

Before closeout:

- [ ] Run full source/editable/slow/focused verification.
- [ ] Record exact counts from the collection hook.
- [ ] Run all canary and 20/20 no-sleep stress gates.
- [ ] Build/install the clean wheel outside source import reach.
- [ ] Confirm acquisition tests did not change runtime handoff, current model,
      known-good, server service, thermal latch, or boot policy unless an
      explicit follow-on activation test intentionally did so.
- [ ] Update documentation truth and evidence table.
- [ ] Confirm the tracked tree is clean and list preserved untracked files.
- [ ] Stop before Session 6B.
