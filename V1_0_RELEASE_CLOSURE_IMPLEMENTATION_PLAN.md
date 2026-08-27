# BC250 LLM MODE — 1.0 Release Closure Implementation Plan

## From developer-complete P9 to evidence-backed `1.0.0`

**Status:** Proposed execution authority for the remaining release blockers

**Starting checkpoint:** `6e91dee` on `main`, version `0.9.0.dev0`, database schema v9, 949 authoritative collected tests, P0–P9 developer-executable work recorded as complete.

**Honest current verdict:** the implementation is an advanced release candidate, not a completed `1.0.0`. The remaining work is not ordinary feature development. It is release enforcement, durable backup/restore publication, physical BC250 qualification and soak evidence, independent security review, non-developer acceptance, signed/attested artifacts, and controlled release publication.

**Purpose:** provide another agentic coder with a complete, ordered, testable plan that closes the gap between “developer scope complete” and “the product may safely be tagged and distributed as 1.0.0.”

---

## 1. Authority and execution contract

This document supersedes the closeout portion of `FINAL_PRODUCTION_READINESS_IMPLEMENTATION_PLAN.md` without rewriting the historical P0–P9 record. That prior plan remains the requirements source for the completed implementation milestones. This plan is the sequencing authority for release closure.

### 1.1 Mandatory first action

Before changing production code:

1. Reconcile the exact starting commit, version, schema, test collection, CI state, and untracked-file inventory.
2. Commit this plan as its own review boundary after owner approval.
3. Do not automatically add, delete, rename, or commit any other untracked file.
4. Record the plan’s adoption and starting evidence in `AGENTS.md`.
5. Add the first red release-gate tests before implementing the gate changes.

The local checkout currently has tracked changes clean but contains untracked files. Do not describe that as a strictly clean working tree. Release artifacts must be built from a fresh CI checkout of the reviewed commit, where untracked local files cannot influence the build.

### 1.2 Session discipline

Each session must:

- select one numbered milestone or a clearly bounded sub-slice;
- identify the first failing acceptance test;
- list all production paths changed;
- make narrow commits in the prescribed order;
- run the focused, default, slow, packaging, and hardware gates required by that slice;
- append evidence to `AGENTS.md` without deleting historical count provenance;
- stop at the milestone exit gate;
- leave unsupported or unverified behavior visibly unavailable rather than creating a permissive fallback.

### 1.3 Prohibited shortcuts

- Do not set release booleans manually to make `may_tag_1_0_0()` return true.
- Do not represent a checklist, markdown statement, unit-test fake, or unsigned JSON file as external qualification evidence.
- Do not tag or publish from a dirty checkout or rebuild after approval without regenerating evidence.
- Do not publish an artifact that differs from the artifact tested and attested.
- Do not implement custom cryptography.
- Do not restore over the only known-good profile.
- Do not induce unsafe temperatures or destructive power loss on physical hardware without an approved lab procedure and human supervision.
- Do not claim model conversion is supported merely because a request type or refusal command exists.
- Do not remove a known limitation from the release manifest without either implementing it or recording an approved scope decision.
- Do not push, tag, publish, configure PyPI, or create a GitHub release without explicit owner authorization.

---

## 2. Verified remaining problems

| ID | Problem | Present evidence | Required closure |
|---|---|---|---|
| REL-001 | “All P0–P9 complete” currently means developer-executable scope, not the full P9 exit gate. | `AGENTS.md` lists signing, hardware, security, human acceptance, and tag as pending. | Split implementation completion from release qualification and make the latter machine-enforced. |
| REL-002 | `ReleaseState` is boolean-driven rather than evidence-driven. | Four booleans determine `may_tag_1_0_0()`. | Derive gate results from validated, immutable evidence records bound to the exact candidate commit and artifact digests. |
| REL-003 | Signing/attestation/SBOM/provenance are documented as pending but not modeled by the release gate. | No corresponding fields in `ReleaseState`; current CI only builds and uploads `dist/`. | Add required evidence kinds and a release workflow that generates and verifies them. |
| REL-004 | Hardware-gated backup/restore publication is still unavailable. | `backup_restore.py` validates a dry run only; release manifest labels publish unavailable. | Implement durable create/restore operations, atomic profile publication, rollback, and post-restore inference verification. |
| REL-005 | Physical BC250 qualification and 24–72 hour soak are absent. | No signed hardware qualification evidence exists. | Create a safe qualification harness, evidence schema, supervised runbook, and pass/fail policy. |
| REL-006 | Non-developer human acceptance is absent. | No acceptance protocol or signed result exists. | Define tasks, metrics, privacy rules, accessibility checks, and required sign-off evidence. |
| REL-007 | Independent security review is absent. | Gateway and canary tests exist, but no review record or disposition ledger. | Conduct a scoped review, close blocking findings, and produce signed review evidence. |
| REL-008 | Model conversion is represented as a known unavailable capability. | `MODEL_CONVERT v1` refuses before effects. | Make an explicit 1.0 scope decision: implement safely or remove it from the supported 1.0 promise while retaining an honest limitation. |
| REL-009 | The release plan itself is untracked. | `git ls-files` does not include the prior final plan. | Track the adopted release-closure authority explicitly; preserve unrelated untracked files. |
| REL-010 | The branch is ahead of `origin/main`; no release tag exists. | Local `HEAD` is ahead; version is `0.9.0.dev0`; no `v1.0.0`. | Review, push, run release-candidate CI, approve evidence, then tag/publish in a controlled final boundary. |
| REL-011 | Current CI actions use mutable major-version references. | `actions/checkout@v4`, `setup-python@v5`, `upload-artifact@v4`. | Pin third-party actions to reviewed full commit SHAs and automate update review. |
| REL-012 | The current release gate permits “all green” while known-unavailable capabilities remain. | Unit tests assert `may_tag_1_0_0()` true while unavailable capability entries remain. | Define mandatory capabilities versus accepted limitations; require an approved scope record for every accepted limitation. |
| REL-013 | A prior successful test report is documentary evidence, not reproducible release evidence. | `AGENTS.md` records 949/51, but no candidate-bound result bundle exists. | Produce machine-readable test evidence tied to commit, environment, artifact digest, and workflow run. |
| REL-014 | Local “tracked clean” and strict clean-checkout semantics are conflated. | User-owned untracked files remain. | Make release builds use clean checkouts and make status wording exact. |

---

## 3. Definition of done

This plan is complete only when all of the following are true:

1. `release_state.py` evaluates signed or cryptographically bound evidence, not caller-supplied approval booleans.
2. Every required evidence item names the exact source commit, release version, build workflow, artifact digest, target profile/hardware where relevant, issuer, issuance time, and result.
3. Backup creation and restore publication are real durable operations with crash recovery and post-restore verification.
4. The physical BC250 qualification and soak meet the approved thresholds and produce reviewable evidence.
5. A non-developer operator completes the acceptance scenarios within the defined pass criteria.
6. A security reviewer signs off, and all P0/P1 findings are closed or the release is blocked.
7. Wheel/sdist and any owned container artifacts have checksums, SBOMs, provenance attestations, and verifiable publishing identity.
8. The exact artifacts that passed qualification are the artifacts published.
9. Every known-unavailable capability is either implemented or explicitly accepted as out of scope for 1.0 by a signed scope decision and described consistently in CLI, UI, README, changelog, and manifest.
10. A fresh supported-system install and every supported upgrade path pass.
11. The release gate returns a closed set of blocking reasons and cannot be bypassed by setting ordinary booleans.
12. Only after all evidence is present does the project move through `1.0.0rc1` to `1.0.0`, create the tag, and publish.

---

## 4. Target release-evidence architecture

### 4.1 Separate policy, evidence, evaluation, and I/O

Use four layers:

```text
ReleasePolicy (pure, versioned)
        +
ReleaseEvidence records (immutable, validated)
        +
ArtifactInventory (digests from the exact candidate build)
        │
        ▼
ReleaseGate.evaluate(...) (pure, fail-closed)
        │
        ▼
ReleaseDecision + ReleaseManifest v2
```

Filesystem, GitHub, PyPI, hardware, and human-signature collection belong in tooling/adapters. The evaluator remains pure and deterministic.

### 4.2 Required evidence kinds

Define a closed, versioned vocabulary:

- `SOURCE_CHECKOUT`
- `DEFAULT_TEST_SUITE`
- `SLOW_SECURITY_STRESS`
- `CLEAN_WHEEL_SMOKE`
- `UPGRADE_MATRIX`
- `BACKUP_RESTORE_HARDWARE`
- `SBOM`
- `BUILD_PROVENANCE`
- `ARTIFACT_ATTESTATION`
- `PACKAGE_PUBLISH_ATTESTATION`
- `CONTAINER_IDENTITY`
- `HARDWARE_QUALIFICATION`
- `SOAK_TEST`
- `SECURITY_REVIEW`
- `HUMAN_ACCEPTANCE`
- `KNOWN_LIMITATION_ACCEPTANCE`
- `DOCUMENTATION_RECONCILIATION`
- `RELEASE_APPROVAL`

Unknown evidence kinds must never count toward a required gate.

### 4.3 Evidence record contract

Every record must contain:

```text
evidence_schema_version
evidence_id
kind
release_candidate_version
source_repository
source_commit
source_tree_digest or clean-checkout assertion
artifact_subjects[] {name, sha256, media_type}
issuer {type, identity}
issued_at
expires_at or null
environment {os, kernel, architecture, Python, runner/workflow}
result {PASS|FAIL|INCONCLUSIVE}
measurements or findings (bounded, kind-specific)
attachments[] {name, sha256, media_type, location_hint}
signature_or_attestation_reference
supersedes_evidence_id or null
```

Validation rules:

- timestamps are timezone-aware and ordered;
- digests are full lowercase SHA-256 values;
- release version and commit match the candidate exactly;
- attachment paths are relative and contained;
- measurements have bounded keys, values, array sizes, and units;
- secrets, prompts, completion content, private keys, and raw credentials are refused;
- `FAIL` and `INCONCLUSIVE` never satisfy a requirement;
- expired or superseded evidence never satisfies a requirement;
- an evidence record for one artifact digest cannot qualify another artifact;
- hardware evidence is bound to a declared hardware fingerprint and supported platform policy;
- human/security evidence identifies the reviewer role without embedding unnecessary personal data.

### 4.4 Release policy

Create `ReleasePolicyV1` with:

- required evidence kinds and minimum counts;
- required artifact subjects;
- supported Python/platform matrix;
- supported database upgrade sources;
- hardware fingerprint constraints;
- minimum soak duration and maximum permitted unresolved incidents;
- maximum evidence age;
- required security finding policy;
- required human-acceptance scenarios;
- mandatory capabilities;
- accepted limitations and their approval evidence;
- documentation/version/tag consistency rules.

Policy must live in reviewed source and be included in the release manifest by digest.

### 4.5 Release decision

`ReleaseDecision` should expose:

- `eligible_for_rc`;
- `eligible_for_1_0_0`;
- stable blocking codes;
- human-readable blocking explanations;
- accepted limitations;
- candidate/artifact identities;
- evidence used for each gate;
- ignored/rejected evidence with reasons;
- decision schema and policy digest.

No caller should be able to instantiate an “eligible” decision directly. It must be returned by the evaluator.

### 4.6 Release tooling surface

Add a development/release command, separate from normal appliance mutations:

```text
python -m tools.release collect --kind ... --output release/evidence/...
python -m tools.release validate release/evidence
python -m tools.release evaluate --candidate 1.0.0rc1 --artifacts dist/
python -m tools.release manifest --output dist/release-manifest.json
python -m tools.release verify dist/release-manifest.json dist/
```

If a package-level CLI is preferred, use a hidden/admin namespace and ensure it cannot mutate runtime state. Never place release credentials in the application database.

---

## 5. Milestone sequence

| Milestone | Objective | Depends on | Stop condition |
|---|---|---|---|
| C0 | Adopt plan and freeze baseline | None | Plan and first red tests reviewed; no production change. |
| C1 | Evidence-driven release gate v2 | C0 | All documented blockers are machine-enforced. |
| C2 | Durable backup create/restore publish | C1 | Fake-world crash matrix green; hardware execution still blocked pending C4. |
| C3 | Supply-chain/release workflow | C1 | Candidate artifacts have verified SBOM/provenance/attestations. |
| C4 | Physical hardware qualification and soak | C2, C3 | Signed candidate-bound BC250 evidence exists. |
| C5 | Security review and remediation | C1–C4 as needed | Zero unresolved P0/P1; signed review record. |
| C6 | Human acceptance and accessibility | C2–C4 | Required non-developer scenarios pass. |
| C7 | Known-limitation and conversion decision | C1 | Every unavailable capability is implemented or accepted explicitly. |
| C8 | RC, final audit, tag, and publish | C1–C7 | Exact qualified artifacts published as `1.0.0`. |

Critical path:

```text
C0 → C1 ─┬→ C2 ─┐
         ├→ C3 ─┼→ C4 → C5 ─┐
         └→ C7 ─┘            ├→ C8
                    C6 ──────┘
```

---

## 6. C0 — plan adoption, baseline, and red gates

### Objective

Create a reviewable starting point and prove the current release gate is insufficient before replacing it.

### C0.1 Baseline reconciliation

Record, without modifying state:

- `git rev-parse HEAD` and `git status --porcelain=v1`;
- local branch divergence from `origin/main`;
- package and `pyproject.toml` versions;
- database schema and migration list;
- authoritative pytest collection;
- default chunk pass/skip totals;
- slow battery totals;
- clean-wheel gate totals;
- `compileall` and `git diff --check`;
- current CI workflow identity and most recent green run, if available;
- all local untracked files, labeled as owner-controlled and untouched;
- absence of a `v1.0.0` tag and release artifact set.

The report must say “tracked files clean; N untracked files preserved” rather than “working tree clean” when untracked files exist.

### C0.2 First red tests

Add tests proving the current weaknesses:

1. `may_tag_1_0_0()` must remain false when signing evidence is missing.
2. It must remain false when SBOM evidence is missing.
3. It must remain false when provenance/attestation evidence is missing.
4. It must remain false when backup-restore hardware evidence is missing.
5. Caller-supplied boolean flags without evidence must not qualify a release.
6. Evidence for the wrong commit, version, artifact digest, hardware profile, or policy digest is rejected.
7. Unknown, expired, failed, inconclusive, duplicated, or superseded evidence is rejected.
8. A known-unavailable mandatory capability blocks release.
9. An optional limitation without a reviewed acceptance record blocks release.
10. A strict release checkout check rejects untracked inputs in the build workspace while not deleting the developer’s unrelated files.

### C0.3 Plan adoption commit

Recommended commit:

`docs(C0): adopt evidence-backed 1.0 release closure plan`

Only this plan, the baseline record, and the intentionally red tests belong in the boundary. Stop before implementing the evaluator.

### C0 exit gate

- The red tests fail for the intended reasons.
- The baseline is reproducible and names exact commands.
- No production behavior changes.
- No user-owned untracked file is touched.

---

## 7. C1 — evidence-driven release gate v2

### Objective

Replace the boolean readiness model with a closed, fail-closed evaluator that proves every release requirement from candidate-bound evidence.

### C1.1 New modules

Recommended layout:

```text
bc250_llm_mode/release_policy.py       pure policy and requirement types
bc250_llm_mode/release_evidence.py     pure evidence models/validation
bc250_llm_mode/release_gate.py         pure evaluator and decisions
bc250_llm_mode/release_state.py        compatibility/public manifest facade
tools/release/__main__.py              filesystem/CLI orchestration
tools/release/artifacts.py             bounded artifact inventory/digests
tools/release/evidence_io.py           strict JSON read/write and containment
release/policy-v1.json                 generated/reviewed policy snapshot
release/evidence/README.md             evidence rules; no fabricated samples
```

Keep package declarations and clean-wheel tests synchronized if `tools` is packaged. If release tooling stays repository-only, test it from a clean checkout and explicitly exclude it from runtime package claims.

### C1.2 Migrate `ReleaseState`

1. Preserve the existing v1 manifest decoder only if consumers exist.
2. Add `RELEASE_MANIFEST_SCHEMA_VERSION = 2`.
3. Remove public constructor booleans as release authority, or mark them deprecated and ensure they cannot produce an eligible decision.
4. Build state from `ReleasePolicy`, `ArtifactInventory`, and validated evidence.
5. Add explicit gate codes:
   - `MILESTONE_EVIDENCE_MISSING`;
   - `TEST_EVIDENCE_MISSING`;
   - `CLEAN_WHEEL_EVIDENCE_MISSING`;
   - `SBOM_MISSING_OR_MISMATCHED`;
   - `PROVENANCE_MISSING_OR_MISMATCHED`;
   - `SIGNATURE_MISSING_OR_INVALID`;
   - `BACKUP_RESTORE_EVIDENCE_MISSING`;
   - `HARDWARE_QUALIFICATION_MISSING`;
   - `SOAK_EVIDENCE_MISSING`;
   - `SECURITY_REVIEW_MISSING`;
   - `HUMAN_ACCEPTANCE_MISSING`;
   - `CAPABILITY_UNAVAILABLE`;
   - `LIMITATION_ACCEPTANCE_MISSING`;
   - `VERSION_MISMATCH`;
   - `SOURCE_COMMIT_MISMATCH`;
   - `ARTIFACT_DIGEST_MISMATCH`;
   - `DOCUMENTATION_DRIFT`;
   - `REPOSITORY_NOT_PUBLISHED`;
   - `UNKNOWN_EVIDENCE`.
6. Make the release decision deterministic regardless of evidence input order.
7. Include rejected evidence and reason codes in diagnostics without exposing sensitive fields.

### C1.3 Milestone evidence

Historical markdown is useful context but should not be the sole release input. Generate a `MILESTONE_SUITE` evidence record from a clean candidate run containing:

- authoritative collection count;
- every deterministic chunk selector and outcome;
- slow suite selectors and outcomes;
- clean-wheel selectors and outcomes;
- compile and diff-check outcomes;
- Python/platform matrix;
- commit and artifact subject digests;
- workflow URL/run identity;
- bounded test report attachments.

The evaluator must verify that the sum of chunk results reconciles to collection and that required slow/packaging selectors are present.

### C1.4 Documentation consistency gate

Implement a read-only checker that compares:

- package version;
- `pyproject.toml` version;
- changelog release heading;
- README release-state wording;
- `AGENTS.md` current-state summary;
- database schema constant;
- release policy version;
- candidate tag/ref;
- known limitations;
- CLI `--version` from the built wheel.

It must fail on contradictory claims such as “P9 complete” without the “developer scope” qualification or “working tree clean” when the release build contains untracked inputs.

### C1.5 Tests

- table-driven evidence-kind validation;
- bounded payload and hostile JSON tests;
- property tests for total, deterministic evaluation;
- duplicate/superseded/expired evidence tests;
- artifact digest substitution tests;
- wrong-commit/version/policy tests;
- unknown fields fail closed for signed schemas;
- v1 manifest compatibility tests, if retained;
- golden v2 manifest with stable canonical bytes;
- secret/path/prompt canaries;
- clean-wheel/public import surface tests.

### C1 exit gate

- Current repository evaluates `eligible_for_1_0_0 = false` with exact missing-evidence codes.
- No combination of ordinary booleans can bypass a missing evidence requirement.
- Every accepted record is bound to the candidate commit and relevant artifacts.
- Unknown, stale, mismatched, or unsigned records fail closed.
- Manifest v2 is canonical, bounded, redacted, and independently verifiable.
- Existing application startup and runtime state do not depend on release-evidence files.

Recommended commits:

1. `test(C1): freeze evidence-driven release gate failures`
2. `feat(C1): add release policy and evidence contracts`
3. `feat(C1): replace boolean readiness with fail-closed evaluator`
4. `feat(C1): add release manifest tooling and documentation gate`
5. `docs(C1): record release-gate v2 evidence`

Stop after the evaluator truthfully reports the remaining gaps. Do not fabricate records to turn it green.

---

## 8. C2 — durable backup creation and restore publication

### Objective

Close the largest functional gap: make backup and restore real, durable, crash-recoverable operations rather than pure manifest and dry-run contracts.

### C2.1 Architecture decision

Write ADR 006 before production code. Resolve:

- backup archive format and versioning;
- maintained authenticated-encryption mechanism;
- key/passphrase flow and memory lifetime;
- whether model/runtime bytes are included, referenced, or optional;
- profile-exclusive lease semantics;
- application/service quiescence before publication;
- staging location and same-filesystem requirement;
- atomic profile publication mechanism;
- rollback and recovery-required behavior;
- post-restore database/runtime/model/inference verification;
- interaction with active workers, gateway, Open WebUI, systemd, and thermal state;
- retention and secure cleanup;
- unsupported cross-device/network filesystems;
- exact hardware evidence required for release.

Do not choose a cross-filesystem “copy then replace” path for active profile publication. Either prove an atomic same-filesystem mechanism or refuse before mutation.

### C2.2 Database additions

If repository state is needed, migration 010 should add narrowly scoped tables such as:

```text
backup_sets
  backup_id, manifest_digest, format_version, created_by_operation_id,
  storage_path_label, bytes_total, encryption_mode, created_at,
  verification_state, verified_at

restore_attempts
  restore_id, source_manifest_digest, created_by_operation_id,
  staging_identity, prior_profile_identity, candidate_profile_identity,
  publish_state, post_verify_state, rollback_state, created_at, updated_at
```

Never persist passphrases, raw encryption keys, private paths, prompts, or full archive listings. Store labels and digests. Use revision/CAS and repository-only SQL.

Migration tests must cover v9→v10, rollback after partial DDL, newer-schema refusal, preservation of every durable row, and clean fresh creation.

### C2.3 `BACKUP_CREATE v1`

Create a versioned immutable request and workflow:

1. validate destination policy, inclusion policy, available space, and key mode;
2. acquire `profile-backup` plus read leases on protected resources;
3. establish a consistent SQLite snapshot through the database backup API or an equivalent supported mechanism;
4. inventory selected metadata/assets with containment, mode, size, and digest;
5. stage archive contents in an operation-owned 0700 directory;
6. encrypt using the approved maintained mechanism when requested;
7. fsync archive and parent, then publish with no-replace semantics;
8. re-open, authenticate/decrypt as needed, and verify manifest/digests;
9. record receipt and backup metadata in one fenced unit;
10. release reservations/leases and retain labeled partials on cancellation.

Requirements:

- default excludes model/runtime bytes unless the user explicitly includes them;
- secrets are excluded by construction;
- output size and file count are bounded by the request and policy;
- destination must not be inside a protected active/staging tree;
- collision never overwrites an existing backup;
- progress is throttled and meaningful;
- cancellation points are explicit and safe;
- crash after publication but before receipt converges without creating a second archive.

### C2.4 `BACKUP_RESTORE v1`

Create a versioned workflow:

1. read the source once and authenticate/decrypt;
2. validate manifest digest, completeness, containment, schema, space, permissions, and identities;
3. produce a durable dry-run result and require explicit confirmation bound to that digest;
4. reserve storage and acquire `profile-restore-staging`;
5. extract into a fresh contained sibling staging profile;
6. migrate the staged database only;
7. validate permissions, database integrity, artifact aliases/digests, runtime manifests, handoff inputs, and gateway metadata;
8. acquire the profile-exclusive publication barrier and prove no active operation/worker/service can write the profile;
9. stop or quiesce composed services through typed controllers;
10. invoke a fixed, digest-verified helper to atomically exchange the active and staged profiles on the same filesystem;
11. reopen the new database and verify schema, runtime identity, model identity, handoff/start receipt, health, and bounded inference;
12. promote success and retain the prior profile for rollback retention;
13. if verification fails, atomically exchange back and verify the prior profile;
14. if any identity/publication state is uncertain, enter `RECOVERY_REQUIRED`, retain both profiles, hold the barrier, and emit exact remediation data.

The helper should follow the runtime exchange precedent: fixed operation, expected path identities and digests, no arbitrary source/destination arguments, no shell, bounded execution, and clear unsupported-filesystem refusal.

### C2.5 Recovery and repair integration

Extend the Repair Center with evidence-driven actions:

- resume staged restore;
- verify candidate profile;
- verify prior profile;
- complete publish;
- exchange back;
- acknowledge and retain both for manual review;
- clean an abandoned candidate only after proof it is neither active nor known-good.

Every action must be idempotent, revision-fenced, and unavailable without complete preconditions.

### C2.6 CLI and GUI

CLI:

```text
bc250 backup create DEST [--include-models] [--include-runtime] [--encrypt]
bc250 backup list
bc250 backup verify BACKUP
bc250 restore inspect BACKUP
bc250 restore start BACKUP --confirmation-digest DIGEST [--detach]
bc250 restore status OPERATION_ID
```

GUI:

- space-aware backup wizard;
- explicit inclusion and privacy summary;
- visible backup freshness on Home;
- restore inspection and impact preview;
- typed confirmation showing current and candidate identities;
- Activity Center integration;
- prominent recovery-required treatment;
- no GUI filesystem/SQLite/subprocess imports.

### C2.7 Tests

- manifest, key, space, permission, traversal, and identity refusal matrix;
- secret/prompt/path canaries;
- archive collision and destination containment;
- crash matrix at every numbered step;
- duplicate worker/takeover stress;
- WAL/SQLite consistency under concurrent read activity;
- candidate corruption after staging;
- process death immediately before/after profile exchange;
- post-publish health failure and successful rollback;
- rollback verification failure → `RECOVERY_REQUIRED`;
- active operation/worker barrier refusal;
- clean-wheel operation execution;
- real same-filesystem helper test on Linux;
- physical BC250 full round trip reserved for C4 evidence.

### C2 exit gate

- Create and restore operations are fully composed and visible in CLI/Activity Center.
- No source backup or active profile is overwritten.
- Every pre-publication failure leaves the active profile byte-for-byte/identity unchanged.
- Publication death converges to a known active identity or `RECOVERY_REQUIRED` with both profiles retained.
- Successful restore passes database, runtime, model, handoff, health, and bounded inference verification in the production-shaped fake/Linux gate.
- Physical hardware evidence remains pending until C4 and cannot be substituted by these tests.

Recommended commits:

1. `docs(C2): accept atomic backup and restore ADR`
2. `feat(C2): add migration 010 and backup repositories`
3. `feat(C2): add durable BACKUP_CREATE v1`
4. `feat(C2): add fixed profile exchange helper`
5. `feat(C2): add durable BACKUP_RESTORE v1 and recovery`
6. `feat(C2): wire backup and restore CLI/GUI surfaces`
7. `test(C2): close crash, security, and clean-wheel gates`
8. `docs(C2): record backup/restore implementation evidence`

---

## 9. C3 — supply-chain, signing, SBOM, and publication pipeline

### Objective

Produce immutable, verifiable artifacts from the exact reviewed candidate and make publication a least-privilege, approval-gated workflow.

### C3.1 Harden CI dependencies

1. Replace mutable action tags with reviewed full-length commit SHAs.
2. Add comments recording the human-readable action release for maintainability.
3. Configure Dependabot or an equivalent reviewed update path for GitHub Actions.
4. Set top-level workflow permissions to read-only/none and grant only job-specific permissions.
5. Ensure pull-request workflows never receive publish credentials or OIDC publication permissions.
6. Pin build/test tool versions in a release constraints file with hashes where practical.
7. Use a clean checkout and fail if generated/untracked inputs influence artifact contents.
8. Record the exact runner image, Python version, build frontend, and dependency lock digest in provenance.

### C3.2 Split CI and release workflows

Keep `.github/workflows/ci.yml` for unprivileged validation. Add `.github/workflows/release.yml` with distinct jobs:

```text
validate-candidate
    ├── full software matrix
    ├── slow/security/clean-wheel gates
    ├── docs/version/policy consistency
    └── source tree cleanliness

build-once
    ├── wheel
    ├── sdist
    ├── checksums
    ├── SBOM(s)
    └── release manifest draft

verify-artifacts
    ├── install exact wheel from job artifact
    ├── verify sdist rebuild consistency policy
    ├── smoke CLI/worker/operations
    ├── inspect metadata and forbidden files
    └── vulnerability/license policy

attest
    ├── build provenance attestation
    ├── SBOM attestation
    └── verification receipt

approval-environment
    └── required maintainer approval

publish
    ├── retrieve exact previously built artifacts
    ├── verify digests/attestations again
    ├── publish through short-lived identity
    └── create immutable release receipt
```

Do not rebuild in the publish job.

### C3.3 Artifact inventory

For each wheel, sdist, owned container image, SBOM, checksum file, and release manifest, record:

- filename/subject name;
- SHA-256 digest;
- media type;
- size;
- source commit;
- candidate version;
- builder workflow identity;
- provenance/attestation reference;
- publication destination and receipt, once published.

Inventory generation must stream/hash boundedly, reject symlinks/special files, sort canonically, and never trust filenames as identity.

### C3.4 SBOM

Generate a standard SBOM covering:

- wheel/sdist dependencies;
- bundled Python modules/assets;
- release tooling dependencies relevant to the build;
- managed third-party container/image identities;
- external runtime/toolchain identities referenced by the appliance.

Use a standard format such as CycloneDX JSON. Validate the SBOM, include hashes, and attest it alongside provenance. Add tests that required direct dependencies and the package itself appear, secrets do not, paths are normalized, and the SBOM subject digest matches the built artifact.

### C3.5 Provenance and attestations

Use the hosting platform’s supported artifact-attestation mechanism for wheel, sdist, release manifest, checksums, and any owned image. The attestation must bind:

- repository and workflow identity;
- source commit/ref;
- artifact subject digest;
- build invocation/environment;
- release policy digest.

Add a verification job and documented offline/CLI verification command. A generated attestation that is never verified does not satisfy the gate.

### C3.6 Package publication

If publishing to PyPI:

- use PyPI Trusted Publishing/OIDC rather than a long-lived API token;
- configure the exact repository, workflow filename, and protected environment;
- grant `id-token: write` only to the publish job;
- require manual approval through a dedicated environment;
- retrieve artifacts from the approved build job;
- verify digests before upload;
- publish release candidates separately from final releases as policy permits;
- record the PyPI project/release identity and uploaded digests.

If publishing only through GitHub Releases, document that as the supported distribution channel and apply the same artifact/attestation requirements. Do not assume a PyPI project exists.

### C3.7 Vulnerability, license, and secret gates

Add pinned tools/policies for:

- Python dependency vulnerability scanning;
- SBOM validation;
- source and history secret scanning;
- package content inspection;
- container/integration digest vulnerability review;
- license inventory and blocked-license policy;
- static security analysis where signal is acceptable.

Findings require stable IDs, severity, affected artifact/version, disposition, expiry, and reviewer. No open critical/high finding may be waived silently.

### C3.8 Tests and dry runs

- release workflow syntax/lint;
- fork/PR permission denial;
- wrong tag/version/commit refusal;
- missing approval refusal;
- artifact substitution refusal;
- missing/mismatched SBOM refusal;
- attestation verification failure;
- clean-wheel and sdist install from downloaded job artifacts;
- release manifest canonicalization;
- dry-run GitHub release with no external publication;
- TestPyPI or equivalent only after explicit owner configuration/approval;
- prove publish job performs no build step.

### C3 exit gate

- CI actions are SHA-pinned and least privilege is documented.
- A candidate workflow produces exact wheel/sdist/checksums/SBOM/provenance/manifest artifacts.
- Attestations verify against the repository/workflow identity and artifact digests.
- The release evaluator consumes the resulting evidence and closes only the supply-chain gates.
- Publication remains approval-gated and has not been performed without owner authorization.

Recommended commits:

1. `ci(C3): pin actions and least-privilege workflow permissions`
2. `feat(C3): add artifact inventory and SBOM generation`
3. `ci(C3): build once and attest release candidates`
4. `ci(C3): add approval-gated publication workflow`
5. `test(C3): verify artifact substitution and permission refusals`
6. `docs(C3): document verification and publishing runbook`

---

## 10. C4 — physical BC250 qualification and soak

### Objective

Generate repeatable evidence that the exact release candidate is safe and functional on the supported appliance hardware.

### C4.1 Qualification policy

Define supported hardware precisely:

- BC250 board/revision identifiers;
- CPU/GPU/PCI identity;
- UMA/memory profile;
- supported OS image/version;
- kernel, Mesa/Vulkan, firmware, and driver ranges;
- storage/filesystem requirements;
- required cooling/sensor availability;
- runtime build and model classes;
- unsupported configurations and safe refusal behavior.

Hardware matching must be evidence-based and must not rely on a user-editable display string alone.

### C4.2 Qualification harness

Add a bounded harness that:

- runs from the installed candidate wheel, not the source tree;
- records the artifact digest and release manifest digest;
- validates hardware identity before any mutation;
- records pre-test health, thermal baseline, storage, runtime, model, gateway, and operation state;
- uses typed commands/ports and existing durable operations;
- emits machine-readable bounded measurements and a human-readable summary;
- can resume after frontend closure/reboot;
- stops safely on thermal latch, sensor loss, identity mismatch, storage pressure, or recovery-required state;
- never modifies BIOS/firmware or performs destructive power interruption automatically;
- signs or attests the resulting evidence through the approved reviewer/workflow process.

Suggested command surface:

```text
bc250-llm-mode qualify preflight --json
bc250-llm-mode qualify functional --candidate-manifest PATH
bc250-llm-mode qualify soak --duration HOURS --candidate-manifest PATH
bc250-llm-mode qualify report --run RUN_ID
```

Qualification is an operator/lab action, never auto-started by install, boot, GUI composition, or worker discovery.

### C4.3 Functional qualification matrix

Run and record:

1. clean install from the candidate wheel;
2. first-run setup and acknowledgement persistence;
3. hardware detection and no-fit refusal;
4. direct GGUF acquisition and local import;
5. model activation and identity verification;
6. representative context/slot configurations, including boundary refusals;
7. bounded inference and chat stream/cancellation;
8. benchmark capture without prompt persistence;
9. thermal nominal → throttle → recovery behavior using safe controlled load;
10. thermal stop/latch/reset only within the approved safety procedure;
11. llama.cpp candidate update, atomic exchange, verification, rollback, and reboot;
12. detached operation after frontend closure;
13. worker single-instance/idle-exit behavior;
14. gateway/Open WebUI inference through authenticated topology;
15. raw backend remote access refusal;
16. storage reservation/low-space refusal;
17. backup create, verify, restore publish, post-restore inference, and rollback;
18. upgrade from every supported 0.9 checkpoint;
19. support-bundle generation and secret canary inspection;
20. return to desktop mode and reboot-policy verification.

### C4.4 Crash/reboot tests

On physical hardware, test process death or controlled reboot at approved boundaries for:

- model acquisition publication;
- model activation handoff;
- runtime exchange;
- detached worker ownership;
- backup publication;
- restore staging and profile exchange.

Do not conduct uncontrolled mains-power interruption unless a separate lab procedure, hardware recovery path, owner authorization, and data-loss containment plan are approved. Process kill and controlled reboot evidence are the default requirement.

### C4.5 Soak

Run 24–72 hours according to policy. The soak schedule should include:

- repeated inference with bounded concurrency;
- alternating idle and load periods;
- representative context/slot configurations;
- periodic model status/identity verification;
- thermal and GPU profile observation;
- controlled chat cancellations/timeouts;
- periodic gateway request tests if enabled;
- detached operation/worker cycles;
- bounded log and database growth checks;
- storage/temp/staging/quarantine leak checks;
- systemd restart and reboot checkpoints if policy requires.

Measurements:

- max/average temperatures and throttle/stop events;
- time-to-first-token and throughput distribution;
- operation success/failure/recovery counts;
- process count, memory, file descriptors, disk use, WAL/log growth;
- worker lock/lease anomalies;
- runtime/model identity drift;
- gateway auth/rate-limit failures;
- kernel/service errors relevant to the appliance.

Pass policy must define numerical or categorical thresholds before the run. Do not select thresholds after seeing results.

### C4.6 Evidence integrity

The hardware/soak evidence must include:

- exact candidate artifacts and release policy digest;
- hardware fingerprint and environment;
- test plan version;
- start/end times and monotonic elapsed duration;
- every scenario result;
- bounded measurements and incident list;
- reviewer/operator identity role;
- attachment digests;
- PASS/FAIL/INCONCLUSIVE result.

Any candidate-changing fix invalidates the evidence unless the evaluator can prove artifact digests are unchanged.

### C4 exit gate

- Functional matrix passes on supported BC250 hardware.
- Backup restore publishes and passes post-restore inference on hardware.
- Soak meets the predeclared duration/thresholds with zero unresolved P0/P1 incident.
- Evidence validates against the exact candidate artifact and policy digests.
- Release evaluator closes hardware, soak, and backup-restore-hardware gates only.

---

## 11. C5 — security review and sign-off

### Objective

Obtain independent evidence that the release’s privilege, process, network, storage, update, and recovery boundaries match the documented threat model.

### C5.1 Review scope

Review at minimum:

- elevated command inventory and ownership;
- typed argv/no-shell guarantees;
- process timeout/output/cancellation/group-kill behavior;
- HTTP origin/TLS/redirect/size/deadline policy;
- gateway credential storage, comparison, rotation, revocation, scopes, rate limits, and audit;
- raw backend exposure and container network topology;
- Open WebUI digest pinning, non-root/read-only/capability/volume/egress policy;
- model source redirect and credential confinement;
- GGUF/parser and manifest hostile-input bounds;
- SQLite permissions, migrations, query-only units, and integrity behavior;
- operation lease fencing, takeover, cancellation, and forward-only effects;
- runtime/model/profile atomic publication helpers;
- backup encryption/key lifecycle and restore traversal protection;
- support bundle and logging redaction;
- CI workflow trust, action pinning, OIDC permissions, publication approval, and artifact substitution defense;
- upgrade/downgrade/refusal behavior;
- local attacker and compromised-container assumptions.

### C5.2 Automated evidence

Run from the exact candidate:

- secret scan across source, generated artifacts, package contents, logs, and support bundle fixtures;
- dependency vulnerability scan tied to the SBOM;
- static security analysis with a reviewed rule set;
- container/image scan for supported integration identities;
- architecture guards;
- security canary suites;
- hostile input/property tests;
- gateway live socket tests;
- process/HTTP timeout and cleanup stress;
- artifact attestation verification.

Automated output is evidence for the reviewer, not a substitute for review.

### C5.3 Findings workflow

Use stable findings:

```text
finding_id
severity {CRITICAL|HIGH|MEDIUM|LOW|INFO}
component
description
evidence
exploit/precondition summary
required remediation
status {OPEN|FIXED|ACCEPTED|NOT_APPLICABLE}
fix_commit
verification_evidence
accepted_by / expires_at (if accepted)
```

Policy:

- CRITICAL/HIGH open findings block release.
- MEDIUM findings require fix or explicit time-bounded acceptance with rationale.
- Safety, privilege escalation, credential leakage, raw backend exposure, arbitrary command execution, destructive restore, and artifact-substitution findings cannot be silently accepted.
- Any fix that changes artifacts invalidates prior candidate-bound evidence and triggers the relevant reruns.

### C5.4 Human sign-off

The security review evidence must name:

- candidate commit/artifact digests;
- threat-model/ADR versions;
- tools and versions used;
- reviewed surfaces;
- findings and dispositions;
- residual risks/known limitations;
- reviewer role and issuance time;
- PASS/FAIL/INCONCLUSIVE.

The release evaluator accepts only PASS evidence matching the candidate and policy.

### C5 exit gate

- Automated security gates pass.
- Zero open CRITICAL/HIGH finding.
- Required MEDIUM dispositions are explicit and valid.
- Independent reviewer signs off on the exact candidate.
- The release evaluator closes only the security-review gate.

---

## 12. C6 — human acceptance and accessibility evidence

### Objective

Verify that a non-developer can operate and recover the appliance using the shipped GUI/CLI guidance, without coaching that hides product defects.

### C6.1 Test protocol

Write the protocol before recruiting/running participants:

- participant is not a contributor to the codebase;
- facilitator may observe and enforce hardware safety, but may not provide product instructions unless the scenario is terminated and marked non-pass;
- use the exact release candidate artifact and supported BC250 configuration;
- seed faults through documented test fixtures/adapters, never by corrupting the operator’s real data;
- record no raw prompts, credentials, or unnecessary personal information;
- define completion, assistance, safety, timing, and accessibility pass criteria in advance;
- obtain consent for any screen recording; otherwise use structured observation notes.

### C6.2 Required scenarios

The operator must:

1. install and launch the candidate;
2. read/accept safety guidance and complete preflight;
3. import or acquire a supported model;
4. understand downloaded vs installed vs active vs verified states;
5. activate a model and start a chat;
6. stop a generation and preserve/retry a draft;
7. start a long operation, close the frontend, reopen, and find it in Activity Center;
8. cancel and resume where permitted;
9. inspect a failed-safe operation and follow its recommended action;
10. respond to a seeded thermal-latch scenario using safe reset guidance;
11. inspect desired/observed/verified mismatch;
12. update and roll back llama.cpp;
13. enable and verify the supported gateway/Open WebUI path, if in 1.0 scope;
14. create and verify a backup;
15. inspect and perform a supervised restore;
16. use Doctor and Repair Center for seeded supported faults;
17. create a redacted support bundle;
18. return to desktop mode and verify expected reboot behavior;
19. identify a known limitation such as unavailable conversion without mistaking it for a defect or supported feature.

### C6.3 Acceptance metrics

Capture per scenario:

- completion without facilitator intervention;
- time to completion;
- incorrect/destructive action attempts;
- unclear labels or missing next actions;
- accessibility issues;
- confidence rating and observed confusion;
- privacy-reviewed notes/attachments;
- finding severity and required fix.

Pass policy:

- all safety/recovery scenarios complete without unsafe action;
- no critical task requires developer-only commands or direct DB/filesystem edits;
- no P0/P1 usability finding remains;
- all destructive actions are understood before confirmation;
- keyboard navigation, focus, screen-reader labels, non-color state indicators, and large-text behavior meet the project’s accessibility contract;
- support bundle generation does not leak scenario content or credentials.

### C6.4 Remediation loop

If acceptance finds a defect:

1. file a stable finding;
2. classify safety/security/functional/usability severity;
3. fix in a narrow commit with regression coverage;
4. rebuild candidate artifacts;
5. invalidate evidence tied to prior artifact digests;
6. rerun affected software/hardware/security tests;
7. repeat the failed human scenario with a fresh participant when practical.

### C6 exit gate

- Every required scenario has PASS evidence on the exact candidate.
- No P0/P1 acceptance finding remains.
- Accessibility checks pass.
- Participant/reviewer sign-off is recorded without unnecessary personal data.
- Release evaluator closes only the human-acceptance gate.

---

## 13. C7 — model conversion and known-limitation decision

### Objective

Resolve the semantic contradiction between “1.0 ready” and a release manifest that always lists unavailable capabilities.

### C7.1 Capability policy

Classify each capability:

- `MANDATORY_FOR_1_0`: release blocks until implemented and evidenced;
- `SUPPORTED_OPTIONAL`: available and supported when enabled;
- `EXPERIMENTAL`: intentionally outside the stable 1.0 support contract, visibly labeled;
- `DEFERRED_NOT_ADVERTISED`: not present in product claims or primary UI;
- `REMOVED`: command/UI/API deleted with migration/compatibility guidance.

`backup-restore-publish` is mandatory because safe ownership/recovery is part of the plan’s 1.0 definition. It cannot remain merely visible and unavailable.

### C7.2 Model conversion decision

Recommended 1.0 route: classify model conversion as `DEFERRED_NOT_ADVERTISED` unless a pinned, bounded converter and hardware evidence are available before RC freeze. Direct GGUF acquisition and local GGUF import remain the supported model-ingestion paths.

If deferred:

1. remove conversion from feature lists and primary GUI affordances;
2. keep a stable CLI refusal only if compatibility requires it;
3. return a clear capability status rather than a generic failure;
4. state that only GGUF acquisition/import is supported in 1.0;
5. create a signed scope-decision record tied to policy/version;
6. preserve the future `MODEL_CONVERT` version namespace without registering a workflow;
7. test docs/CLI/UI/manifest consistency.

If implemented instead:

- use `MODEL_CONVERT v1` as a real registered operation;
- pin converter source, binary/container digest, toolchain, and license;
- typed argv only, no shell;
- operation-owned contained input/output paths;
- bounded CPU, memory, wall time, output bytes/file count;
- cancellation with retained labeled partials;
- verify resulting GGUF structure and digest before publication;
- no-replace artifact publication and alias transaction;
- crash/takeover matrix with exact conversion/publication count;
- secret/path canaries and clean-wheel execution;
- physical BC250 activation/inference qualification for converted output.

Do not select the implementation route merely to remove a release-manifest warning if it cannot meet these gates.

### C7.3 Other limitations

Review every `KNOWN_UNAVAILABLE_CAPABILITIES` entry and every “pending,” “preview,” “candidate,” or “not supported” statement in code/docs. For each:

- map to a capability classification;
- decide whether it blocks 1.0;
- ensure CLI/UI/docs/manifest agree;
- add evidence or an accepted limitation record;
- prevent a default path from advertising unsupported behavior.

### C7 exit gate

- No mandatory capability is unavailable.
- Every optional/deferred limitation has explicit reviewed evidence and consistent product copy.
- `may_tag_1_0_0()` cannot become true with an unclassified limitation.
- Model conversion is either fully implemented/evidenced or clearly outside the 1.0 support promise.

---

## 14. C8 — release candidate, final audit, version, tag, and publication

### Objective

Publish the exact qualified artifacts only after every evidence gate passes.

### C8.1 Candidate preparation

1. Ensure all release-closure commits are reviewed.
2. Push the reviewed branch/commit to the authoritative GitHub repository after owner approval.
3. Create `1.0.0rc1`; update package version, changelog, README, manifest policy, and CLI output together.
4. Run the candidate workflow from a protected ref in a fresh checkout.
5. Build artifacts once and record their digests.
6. Run software, slow, clean-wheel, security, upgrade, backup/restore, hardware, soak, and human acceptance against those exact artifacts.
7. Assemble and validate release manifest v2.
8. Fix any defect through a new candidate; never reuse evidence from changed artifact digests.

### C8.2 Final no-code audit

Perform an independent read-only audit:

- all policy gates satisfied by valid evidence;
- no release blocker hidden in docs or known-limitations list;
- no open P0/P1 finding;
- version/schema/CLI/changelog/tag consistency;
- exact wheel/sdist/container/SBOM/provenance/checksum subjects agree;
- clean checkout reproducibility;
- upgrade and rollback instructions verified;
- support and incident-response instructions present;
- protected publication workflow and environment configured;
- no local-only commit omitted from authoritative remote;
- no untracked local file included in release artifacts;
- no unpublished dependency or mutable image/action identity;
- release notes distinguish supported, experimental, deferred, and removed capabilities.

### C8.3 Final version/tag boundary

Only after the audit and explicit owner approval:

1. prepare the minimal `1.0.0` version/release-notes commit from the passing RC;
2. run consistency and build checks;
3. create an annotated, protected `v1.0.0` tag on that exact commit;
4. run the release workflow;
5. verify checksums, SBOM, provenance, and publishing attestations;
6. publish the exact prebuilt artifacts;
7. install from the public distribution channel into a fresh supported environment;
8. run public-artifact CLI, worker, database creation/upgrade, and no-host operation smoke tests;
9. create and archive a release receipt containing public URLs and verified digests;
10. update the post-release development version only after publication succeeds.

### C8.4 Release notes and support

Publish:

- supported hardware/OS matrix;
- installation and upgrade instructions;
- backup-before-upgrade guidance;
- recovery and rollback instructions;
- known limitations;
- security contact/reporting path;
- how to verify artifacts and attestations;
- support bundle procedure;
- incident response and release revocation process;
- patch-release policy and supported database downgrade/refusal behavior.

### C8 exit gate

- Release evaluator returns `eligible_for_1_0_0=true` with zero blockers.
- The evaluated manifest and evidence refer to the final artifact digests and tag commit.
- Public artifacts verify and install successfully.
- `v1.0.0` exists on the authoritative remote and matches the published release.
- Documentation and application output report `1.0.0` consistently.
- Release receipt is archived and independently verifiable.

---

## 15. Detailed test strategy

### 15.1 Release evaluator tests

- total over arbitrary bounded JSON inputs;
- stable decision regardless of evidence order;
- one missing required kind produces one stable blocker;
- multiple blockers sort deterministically;
- wrong commit/version/artifact/policy/hardware refuses;
- stale/expired/superseded evidence refuses;
- duplicate IDs or conflicting records refuse;
- signed evidence with altered payload refuses;
- accepted limitation requires matching capability/version/policy;
- mandatory unavailable capability blocks;
- all valid evidence produces eligible decision;
- boolean-only legacy state cannot qualify release;
- manifest v2 canonical digest is stable;
- no secret/private content accepted into evidence.

### 15.2 Backup/restore tests

- source snapshot consistency;
- manifest and archive digest integrity;
- encryption wrong-key/tamper behavior;
- traversal/symlink/device/special-file refusal;
- size/file-count/space/permission bounds;
- no-replace publication;
- cancellation at every safe checkpoint;
- process death before/after each effect;
- lease loss and takeover fencing;
- duplicate backup/restore request behavior;
- active profile never overwritten without a verified prior target;
- successful exchange and post-restore inference;
- failed candidate verification and successful rollback;
- rollback uncertainty retains both profiles and enters recovery-required;
- no secret in manifest, operation data, events, logs, or support bundle;
- clean-wheel workflow execution.

### 15.3 Supply-chain tests

- all action references full SHAs;
- workflow permissions least privilege;
- untrusted PR cannot publish or obtain OIDC;
- publish job cannot execute a build;
- artifact download digest matches approved inventory;
- SBOM subject/content validation;
- provenance/attestation verification;
- public wheel/sdist metadata and content inspection;
- source tree excluded from clean-wheel test path;
- package inventory matches declared packages;
- vulnerability/license policy fixtures;
- release tag/version mismatch refuses.

### 15.4 Qualification tests

- harness refuses unsupported/wrong hardware before mutation;
- sensor loss/thermal latch aborts safely;
- evidence cannot be generated from a source checkout when candidate artifact is required;
- elapsed soak duration uses monotonic time plus signed wall timestamps;
- interrupted run resumes without losing incident history;
- report has bounded logs/measurements;
- altered measurement attachment breaks digest verification;
- threshold policy fixed before run;
- hardware evidence for another fingerprint does not qualify the candidate.

### 15.5 Human/security evidence tests

- incomplete scenario matrix refuses;
- facilitator intervention marks the scenario non-pass;
- unresolved P0/P1 finding blocks;
- wrong candidate/reviewer role/expired acceptance refuses;
- personal information is bounded/redacted;
- security sign-off with open high finding refuses;
- evidence modification invalidates digest/signature.

---

## 16. Proposed file map

| Area | Files |
|---|---|
| Pure release model | `bc250_llm_mode/release_state.py`, `bc250_llm_mode/release_evidence.py`, `bc250_llm_mode/release_policy.py` |
| Release tooling | `tools/release/__main__.py`, `tools/release/evidence_io.py`, `tools/release/artifacts.py`, `tools/release/qualification_report.py` |
| Policy/evidence | `release/policy-v1.json`, `release/evidence/README.md`, generated evidence only from real runs |
| Backup domain | `backup_manifest.py`, `backup_restore.py`, `operations/backup.py`, `backup_adapter.py`, `backup_command.py` |
| Profile publication | `profile_exchange_helper.py`, repositories/migration 010 |
| Qualification | `qualification.py`, `qualification_command.py`, `docs/HARDWARE_QUALIFICATION.md` |
| Security | `docs/SECURITY_REVIEW.md`, machine-readable findings schema/location |
| Human acceptance | `docs/HUMAN_ACCEPTANCE.md`, evidence schema/examples without fabricated PASS data |
| CI/release | `.github/workflows/ci.yml`, `.github/workflows/release.yml`, Dependabot config, release constraints |
| Tests | `test_release_gate_v2.py`, `test_release_evidence.py`, `test_backup_operations.py`, `test_profile_exchange.py`, `test_release_workflow.py`, `test_qualification.py` plus existing gates |

Names may change to match repository conventions, but responsibilities and boundaries must remain distinct.

---

## 17. Verification battery

Every development milestone:

```bash
PYTHONPATH=. .venv/bin/pytest tests --collect-only -q
PYTHONPATH=. .venv/bin/pytest -q
PYTHONPATH=. .venv/bin/pytest -m slow tests
python3 -m compileall -q bc250_llm_mode tests tools
git diff --check
```

On a constrained executor, the default suite may use reconciled deterministic chunks, but an unconstrained CI runner must also run the full suite in one process.

Release candidate additionally requires:

```text
fresh-checkout source suite
editable-install suite
clean-wheel and sdist install suite
operation crash/concurrency/security slow gates
release evidence/property tests
workflow policy/permission checks
SBOM validation
vulnerability/license/secret scans
artifact attestation verification
upgrade matrix
backup-create/restore crash matrix
physical hardware functional qualification
24–72 hour soak
human acceptance
security review
public artifact install smoke
```

Record commands, selectors, counts, skips, environment, commit, artifact digests, and evidence IDs. Never infer counts from progress dots.

---

## 18. Recommended commit boundaries

| Boundary | Commit theme | Required evidence before next boundary |
|---:|---|---|
| 0 | Adopt plan + baseline + red gates | Current deficiencies reproduced. |
| 1 | Evidence and policy types | Validation/property tests green. |
| 2 | Release evaluator/manifest v2 | Every missing gate blocks correctly. |
| 3 | Release tooling/docs consistency | Candidate report reproducible. |
| 4 | Backup ADR + migration | Atomicity and schema tests green. |
| 5 | BACKUP_CREATE v1 | Crash/no-replace/security tests green. |
| 6 | Profile exchange helper | Linux identity/exchange/death tests green. |
| 7 | BACKUP_RESTORE v1 + recovery | Full fake-world crash matrix green. |
| 8 | Backup CLI/GUI/Activity | Frontend architecture/accessibility gates green. |
| 9 | CI action pinning/permissions | Workflow security tests green. |
| 10 | SBOM/inventory/provenance | Artifacts verify locally. |
| 11 | Approval-gated release workflow | Dry run succeeds without publication. |
| 12 | Qualification harness | Fake/host-simulation tests green. |
| 13 | BC250 functional evidence | Candidate-bound PASS record. |
| 14 | BC250 soak evidence | Duration/threshold policy satisfied. |
| 15 | Security fixes/sign-off | No blocking finding. |
| 16 | Human acceptance fixes/sign-off | Scenario matrix passes. |
| 17 | Known-limitation decision | No unclassified capability remains. |
| 18 | RC evidence assembly | Evaluator green for RC policy. |
| 19 | Final version/tag/publish | Owner-approved public verification complete. |

After any candidate-changing commit, invalidate and rerun every evidence item whose subject digest or reviewed surface changed.

---

## 19. Agent handoff template

```text
Session / boundary:
Objective:
Starting commit/version/schema:
First red test:
Commits landed:
Production paths changed:
Release policy/evidence versions:
Focused tests:
Authoritative collection and default execution:
Slow/security/clean-wheel results:
Candidate artifact digests:
Hardware/human/security evidence IDs:
Tracked status and preserved untracked files:
Remaining blockers from ReleaseDecision:
Exact next boundary:
Stop condition satisfied:
```

Do not write “all complete” when the meaning is “developer-executable portion complete.” State the exact evidence boundary.

---

## 20. Final no-go conditions

Do not create or publish `v1.0.0` if any of the following is true:

- release eligibility depends on manually supplied booleans;
- signing, SBOM, provenance, or artifact verification evidence is missing;
- the artifact tested differs from the artifact to be published;
- backup restore publication or post-restore inference has not passed on supported hardware;
- hardware qualification or soak is missing, expired, inconclusive, or tied to a different candidate;
- human acceptance or security review is missing;
- an open CRITICAL/HIGH security or P0/P1 functional/safety finding remains;
- a mandatory capability is unavailable;
- an optional/deferred limitation lacks explicit approval or is advertised as supported;
- actions/images/toolchains use mutable identity in the release path;
- the authoritative remote lacks the release commit;
- the candidate version, package version, changelog, CLI, manifest, and tag disagree;
- a release build includes local untracked inputs;
- public artifact attestations fail verification;
- `ReleaseDecision` contains any blocker;
- owner authorization to tag/publish has not been given.

---

## 21. External standards and primary references

Implementation should re-check current official documentation when the workflow is built, then pin reviewed versions/SHAs:

- GitHub artifact attestations and provenance: `https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations`
- GitHub Actions secure-use guidance: `https://docs.github.com/en/actions/reference/security/secure-use`
- PyPI Trusted Publishing: `https://docs.pypi.org/trusted-publishers/using-a-publisher/`
- PyPI attestations: `https://docs.pypi.org/attestations/producing-attestations/`
- Python Packaging User Guide release publishing: `https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/`
- CycloneDX SBOM guidance: `https://cyclonedx.org/guides/sbom/`

These references inform implementation; the repository’s reviewed policy remains the authority for what qualifies this product.

---

## 22. Immediate next session

Start with **C0**, not backup code or release workflow changes:

1. adopt and commit this plan after owner review;
2. reconcile the exact baseline;
3. add the release-gate red tests proving signing/SBOM/provenance/restore/hardware/human/security evidence cannot be bypassed;
4. stop with those tests failing for the intended reasons.

The following session starts C1 with the pure evidence and policy contracts. This order prevents later hardware, human, and CI work from producing evidence that the application cannot validate or bind to the exact release candidate.
