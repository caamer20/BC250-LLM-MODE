# Continuation guide for BC250 LLM MODE

## Current state

**RELEASE-GATE REMEDIATION — G0 COMPLETE (audit contract frozen as red tests).**
Active subordinate authority: `RELEASE_GATE_AND_PIPELINE_REMEDIATION_IMPLEMENTATION_PLAN.md`
(committed `df222c9`), under the V1_0 release-closure plan. It repairs the
audited release-control defects BEFORE any C4/C5/C6/C8 evidence collection:
the release-state boolean bypass, the optional source commit, the incomplete
evidence envelope, unverified attestation references, the ignored `--artifacts`
flag, verify-without-comparison, mutable action refs, and the unverified
post-attestation path. Milestone order G0 → G1 → G2 → G3 → G4 → G5 → G6 →
C4/C5/C6 → C8. The C7/C3/C2/C1/C0 records remain below as history.

G0.1 baseline reconciliation (read-only, at `a649ac7`):

- `main` at `a649ac7`, 71 commits ahead of `origin/main` (remote not updated;
  push stays owner-gated).
- Version `0.9.0.dev0` (pyproject + package `__version__`); database schema
  **v10**; release policy content revision **2**; manifest schema **v2**;
  evidence schema **v1**; decision schema **v2**.
- Authoritative collection **1064**; focused release suite **95/95**.
- Mutable action refs: `setup-python@v5` (×5), `upload-artifact@v4` (×2),
  `download-artifact@v4` (×3), `attest-build-provenance@v2` (×1); only
  `actions/checkout` pinned to a full SHA (v4.1.1).
- Bypasses demonstrated live: `ReleaseState(..., evidence_satisfied=True)
  .may_tag_1_0_0()` → **True**; `evaluate_release` accepts `source_commit=
  None`; a record missing evidence_id/issuer/subjects/verification is
  ACCEPTED; an `hf_…` token under a benign key is ACCEPTED; `evaluate
  --artifacts <dir>` parses but never consumes the directory; `verify` exits
  **0** over a fully mismatched dist (prints an inventory, compares nothing).
- Tracked-clean; **10 owner-controlled untracked files preserved** (3 prior
  plan docs, `scripts_audit/*` ×5, `tests/conftest_trace_tmp.py`; the
  remediation plan itself is now tracked).
- Direct git network access to github.com verified (`git ls-remote` resolves
  upstream tags) — G4 action SHA pinning will be network-verified, never
  guessed.

G0.2 landed (this commit): four red-test files, **75 tests = 72 RED for the
intended reasons + 3 always-green guards**, in the default collection (no
marker), per plan §G0.3:

- `tests/test_release_gate_remediation.py` (8 red) — ReleaseState
  `evidence_satisfied` bypass; sourceless evaluation; CLI `--source-commit`
  mandatory; `CandidateIdentity` commit/ref/repository/policy-digest
  validation; final-tag rule (`refs/tags/v1.0.0` exactly); policy-digest
  mismatch blocks; decision must name candidate + inventory digest.
- `tests/test_release_evidence_v2.py` (38 red) — bare field-less records
  accepted today; each of the 18 schema-v2 mandatory envelope fields required
  (MISSING_FIELD); UNKNOWN_FIELD; value-based secret patterns (hf_/ghp_/PEM/
  bearer/URL-userinfo) → SECRET_MATERIAL; empty `artifact_subjects` for
  artifact-bound kinds → EMPTY_FIELD; missing verification block →
  MISSING_FIELD; unverified verification block → BAD_VERIFICATION;
  future-dated `issued_at` → BAD_TIMESTAMP; evidence policy-digest binding →
  POLICY_DIGEST_MISMATCH; kind contracts → KIND_CONTRACT_UNMET; oversize/
  overdeep → RECORD_OVERSIZE; validated-but-unverified records cannot
  qualify; supersession set rules (unknown target / cross-kind / cycle).
- `tests/test_release_artifact_binding.py` (16 red) — evidence subject ↔
  inventory binding (+ matching control); CLI `--artifacts` mandatory and the
  decision JSON binds the inventory digest; `verify` fails on content
  mutation / added / removed artifact / SBOM-subject ≠ wheel digest;
  inventory v2 schema version + roles + canonical digest + duplicate-role
  refusal; SBOM duplicate-component refusal + real TOML parsing; manifest v3
  BLOCKED labeling; final manifest refuses ineligible decisions; manifest
  digest changes with any candidate/artifact change.
- `tests/test_release_workflow_hardening.py` (10 red + 3 green guards) —
  every `uses:` a full 40-char SHA; no TODO(C3); Dependabot for
  github-actions; workflow runs the authoritative evaluator; final-evaluation
  gates approval + publish; post-attestation verification before approval;
  SBOM check via `tools.release verify`; build-once emits the complete
  release set; candidate ref/qualification-level inputs; publish consumes
  decision verification (not a bypassable shell line). Guards (green now,
  must stay green): no wildcard artifact selection in publish; PR paths
  cannot attest/publish; current checkout not publishable.

Red failure classes: AssertionError (audited permissiveness reproduced),
ImportError (remediated modules not yet written), TypeError (remediated
binding signatures not yet present). **No production file changed at G0.**

G0 verification: authoritative collection **1139** (1064 + 75); focused
release suite still **95/95**; default suite green across nine deterministic
alphabetical chunks except the 72 intended red tests — chunk reconciliation
114+128+103+129+151+180+144+114+76 = 1139, i.e. **1067 passed + 72 intended
red** (all failures inside the four new files; zero unrelated regressions);
compileall + `git diff --check` clean.

Next: **G1 — eliminate eligibility bypasses**: delete
`ReleaseState.evidence_satisfied` (make `may_tag_1_0_0()` non-authoritative),
introduce the mandatory immutable `CandidateIdentity` + package-level
`ArtifactInventory` evaluator inputs, bind the policy digest, add
CANDIDATE_REF_MISMATCH / POLICY_DIGEST_MISMATCH gate codes, migrate release
tooling + affected tests, and guard the single evaluator authority.

---

**RELEASE CLOSURE — C7 COMPLETE (known-limitation & conversion decision).**
C0 + C1 + C2 + C3 remain the baseline records below. C7 resolved the "1.0 ready
while a mandatory capability is listed unavailable" contradiction: it refined the
capability classification to the reviewed 5-value 1.0 scope vocabulary, recorded
the model-conversion scope decision (DEFERRED_NOT_ADVERTISED), reconciled
backup-restore-publish as IMPLEMENTED by C2 (its remaining 1.0 requirement is
hardware evidence, tracked by the evidence gate — not capability unavailability),
and added the unclassified-limitation gate. **All developer-executable milestones
(C0, C1, C2, C3, C7) are now COMPLETE. Remaining: C4/C5/C6/C8, which are
hardware/human/owner-gated pending evidence — never fabricated.**

C7 landed (commits in order):

- `912e1ec` `release_policy.py` — CapabilityClass refined from the C1 3-value
  draft to the 5-value scope vocabulary (MANDATORY_FOR_1_0 / SUPPORTED_OPTIONAL /
  EXPERIMENTAL / DEFERRED_NOT_ADVERTISED / REMOVED); mandatory/limitation/
  classified accessors updated (REMOVED is not an accepted limitation);
  default policy records the C7 decision (backup-restore-publish MANDATORY_FOR_
  1_0, model-conversion DEFERRED_NOT_ADVERTISED); RELEASE_POLICY_VERSION → 2;
  reviewed snapshot `release/policy-v2.json` (policy-v1.json kept as C1 history).
- `38d9a63` `release_state.py` — backup-restore-publish leaves
  KNOWN_UNAVAILABLE_CAPABILITIES (implemented by C2; hardware evidence now its
  1.0 requirement via the evidence gate + blocking_gaps); model-conversion stays
  the single genuinely unavailable capability; NEW unclassified-limitation gate
  (may_tag_1_0_0() can never be true with an unclassified limitation).
  `release/scope-decision-model-conversion.md` — reviewed scope-decision record
  bound to policy v2 + digest (the KNOWN_LIMITATION_ACCEPTANCE evidence record
  stays human/owner-gated, never fabricated). Reconciled P9-era release-state
  tests + the C0 red gate; +9 C7 tests.

C7 verification: authoritative collection **1064** (1053 C3 + 11 C7); default
suite green across nine alphabetical chunks reconciling to 1064; release-related
tests **95/95** (policy/gate/state/capability/supply-chain/workflows/fuzz/
convert/tooling); slow battery **51/51**; C1.4 docs-consistency gate green;
compileall + `git diff --check` clean. C7 exit gate met: no mandatory capability
unavailable; model-conversion classified DEFERRED_NOT_ADVERTISED with a reviewed
scope record + consistent product copy (no GUI affordance, convert-model CLI a
clearly labeled UNAVAILABLE refusal); may_tag_1_0_0() cannot be true with an
unclassified limitation. **The evaluator still truthfully reports
eligible_for_1_0_0 = false (hardware/security/human/limitation-acceptance
evidence all pending); NO record fabricated.**

---

**C3 record (supply-chain, SBOM, release pipeline, superseded as "next" by
C7).** C3 produced the developer-executable supply-chain tooling and the
approval-gated release pipeline: deterministic CycloneDX SBOM generation +
fail-closed validation, a hardened content-identity artifact inventory,
least-privilege CI, and a SEPARATE release workflow that builds once, attests,
and publishes only through an approval-gated environment (publication NOT
performed).

C3 landed (commits in order):

- `584222d` `tools/release/sbom.py` — deterministic CycloneDX 1.5 SBOM generator
  + fail-closed validator (package + direct deps + build backend + managed
  third-party/external runtime identities; injectable serial/timestamp for a
  reproducible digest; subject sha256 binds the built artifact; refuses missing
  package/dependency, secret material, non-normalized paths, subject mismatch).
  `tools/release/artifacts.py` hardened (C3.3): identity is the content sha256
  (never the filename), canonical media type, symlink/special-file rejection.
- `a6f5a1c` `.github/workflows/ci.yml` least privilege (contents: read) +
  actions/checkout pinned to a verified full-length SHA (v4.1.1);
  `.github/workflows/release.yml` — validate-candidate → build-once (wheel/
  sdist/checksums/SBOM/inventory) → verify-artifacts (install exact wheel, smoke
  CLI/worker, verify checksums + SBOM subject) → attest → approval-environment →
  publish. OIDC id-token: write only on attest + publish; publish NEVER rebuilds
  and is environment/approval-gated; publication NOT performed (publish exits
  non-zero without owner authorization).

C3 verification: authoritative collection **1053** (1036 C2 + 17 C3); SBOM +
inventory + workflow-gate tests **17/17**; real SBOM generation smoke-tested
against the live pyproject (4 runtime deps + setuptools, subject-bound,
deterministic digest); both workflow files parse as valid YAML with least-
privilege top-level permissions; release tooling + C1 gate tests re-verified
green. **Pending evidence (never fabricated): full-length SHA pinning for the
remaining actions (requires network verification); live attestation generation +
verification, artifact signing, and any publication are CI/owner-gated (C3.5/
C3.6, C8) and have NOT been performed.**

---

**C2 record (durable backup create/restore publish, superseded as "next" by
C3).** C2 made backup/restore REAL durable, crash-recoverable operations
(REL-004) instead of pure manifest/dry-run contracts: `BACKUP_CREATE v1` and
`BACKUP_RESTORE v1` are registered in the ONE frozen registry (now eight
workflows) and driven by the shared engine factory, so creation, publication,
and rollback survive process death and lease takeover.

C2 landed (commits in order):

- `6a33c07` ADR 006 `docs/adr/006-durable-backup-restore.md` — D1 tar container
  `format_version=1`; D2 encryption `none` only this build (`aes-256-gcm`
  designed but fail-closed `ENCRYPTION_UNAVAILABLE` until a reviewed crypto dep;
  secrets excluded by construction); D3 model/runtime bytes excluded by default;
  D4 `sqlite3` backup() hot snapshot; D5 ONE atomic same-fs exchange (no
  copy-then-replace); D6 profile-exclusive barrier + quiescence; D7 verification
  chain + exchange-back + RECOVERY_REQUIRED; D8 evidence-driven repair; D9
  retention/secure cleanup; D10 unsupported-fs refusal; D11 hardware evidence
  reserved for C4.
- `fbb0084` migration 010 `backup-restore-lifecycle` → schema **10**:
  `backup_sets` + `restore_attempts` (CAS revision-fenced repositories in
  `backup_lifecycle.py`).
- `a4cc088` `profile_exchange_helper.py` — digest-pinned `renameat2
  RENAME_EXCHANGE` helper gated to the profile marker + same-fs containment
  (Linux-only real swap; fake-world tests cover the contract).
- `3f7c0a8` `operations/backup.py` — frozen `BACKUP_CREATE v1` (snapshot →
  inventory/stage → publish no-replace → verify → record) and
  `BACKUP_RESTORE v1` (validate source → stage candidate → validate staged →
  publish exchange → post-verify → promote/rollback) workflows; closed request
  decoders (encryption refused fail-closed; restore bound to a full sha256
  confirmation digest); the profile-publication barrier joins only at the
  exchange boundary.
- `47b910d` `backup_adapter.py` — ONE production host satisfies both ports:
  hot-consistent snapshot, secret-free manifest, no-replace tar publish
  (collision refuses), digest verify, fenced record + secure staging cleanup;
  restore digest-bound validation, contained staging + forward migration,
  atomic exchange, post-restore integrity, promote retaining the prior profile.
- `23d12ed` composition + CLI — `BackupCommandService` (create/list/verify +
  restore inspect/start), registered in the single frozen registry + composed;
  `backup create/list/verify` and `restore inspect/start/status` CLI verbs. The
  restore carries the operation's durable rows across the profile swap so the
  engine's fenced checkpoint protocol continues (failure converges to
  RECOVERY_REQUIRED with both profiles retained).

C2 verification: authoritative collection **1036** (1000 C1 + 36 C2); default
suite green across nine alphabetical chunks reconciling to 1036; backup
workflow+adapter+command tests **22/22** incl. engine-driven create + restore
round trips (restore via a platform-neutral exchange standing in for the
Linux-only renameat2); Activity Center lists BACKUP_CREATE/BACKUP_RESTORE rows;
slow battery re-run for the clean-wheel gate. **Pending evidence (never
fabricated): the physical BC250 backup/restore round trip + post-restore
inference verification and the live Linux renameat2 publication remain
hardware-gated until C4.**

---

**C1 record (evidence-driven release gate v2, superseded as "next" by C2).** C1
replaced the boolean readiness model with a closed, fail-closed evaluator:
caller-supplied approval booleans can NEVER qualify a `1.0.0` on their own —
eligibility derives ONLY from validated, candidate-bound evidence. The C0 red
gates are GREEN and folded into the default suite. The evaluator truthfully
reports the current repository as `eligible_for_1_0_0 = false` with exact
missing-evidence codes; NO record is fabricated to turn it green.

C1 landed (pure modules + repository-only tooling):

- `release_policy.py`: versioned policy — closed evidence-kind (18) and
  gate-code (19) vocabularies, RC vs 1.0 required-evidence sets, capability
  classification (`backup-restore-publish` MANDATORY, `model-conversion`
  DEFERRED), canonical policy digest. Reviewed snapshot `release/policy-v1.json`.
- `release_evidence.py`: fail-closed evidence-record validation (unknown kind,
  non-PASS, wrong version/commit, expired, superseded, duplicated, path
  traversal, secret/prompt material, bad digest → stable rejection codes).
- `release_gate.py`: pure deterministic `evaluate_release` (exact blocking
  codes; eligible decision cannot be constructed directly) + read-only
  `check_release_checkout` (rejects untracked build inputs, never deletes).
- `release_state.py` migrated to manifest schema **v2**: `may_tag_1_0_0()`
  needs no informational gaps + no unavailable mandatory capability +
  satisfied evidence (public constructor leaves evidence unsatisfied).
- `tools/release/` (repository-only, NOT packaged): validate/evaluate/
  manifest/verify CLI + strict evidence I/O + bounded artifact inventory +
  C1.4 read-only documentation-consistency gate. Evidence rules in
  `release/evidence/README.md` (no fabricated samples).

C1 verification: authoritative collection **1000**; default suite green across
deterministic alphabetical chunks (998 passed + 1 Linux-gated skip + 1
tkinter-gated skip = 1000 reconciled); release-related tests 61/61; slow
battery **51/51**; compileall + `git diff --check` clean. C1 exit gate met:
evaluator returns `eligible_for_1_0_0 = false` with exact codes; no boolean
combination bypasses a missing evidence requirement; accepted records bind to
candidate commit + artifacts; unknown/stale/mismatched/unsigned records fail
closed; manifest v2 canonical/bounded/redacted; app startup/runtime state does
NOT depend on release-evidence files.

---

**C0 record (baseline + red gates, superseded as "next" by C1 above).** C0
made NO production behavior change: it froze the baseline and added the
intentionally-RED release-gate tests that proved the boolean `release_state.py`
could not enforce evidence requirements.

C0 baseline reconciliation (read-only, exact commands in §C0.1):

- Starting commit `6e91dee4c62fec2594b6e78ed686f13d692a74ba` on `main`,
  **52 commits ahead of `origin/main`** (remote not yet updated; REL-010).
- Tracked files clean; **10 untracked files preserved** (owner-controlled,
  untouched): the three prior plan docs, `scripts_audit/*` (5 files),
  `tests/conftest_trace_tmp.py`, and (until C0 committed it) this release-
  closure plan. Wording is exact: tracked-clean, NOT "strictly clean".
- Version `0.9.0.dev0` (both `pyproject.toml` and package `__version__`).
- Database schema **v9**; 9 migrations (initial-schema … model-library-meta).
- Authoritative collection **949**; default suite green across deterministic
  alphabetical chunks (947 passed + 1 Linux-gated skip + 1 tkinter-gated skip);
  slow battery **51/51** (runtime 6/6, acquisition 41/41, clean-wheel 4/4);
  compileall + `git diff --check` clean.
- CI: single `ci.yml` using MUTABLE action tags (`checkout@v4`,
  `setup-python@v5`, `upload-artifact@v4`) — REL-011 open. No `release/` or
  `tools/` directory. No `v1.0.0` tag; no release artifact set (only a local
  gitignored dev wheel in `dist/`).

C0 red gates (`tests/test_release_gate_v2.py`, marker `release_gate_v2`,
excluded from the default suite until C1 turns them green): **10 tests, all
RED for the intended reasons** — 2 fail `AssertionError: assert True is False`
(caller-supplied booleans alone currently qualify a release, and an
unavailable mandatory capability does not block it) and 8 fail
`ModuleNotFoundError` (the evidence-driven `release_policy`/`release_evidence`/
`release_gate` modules do not exist yet). Run them with
`PYTHONPATH=. .venv/bin/pytest -m release_gate_v2`.

Release-closure milestone status: **C0 + C1 done**; C2 (durable backup
create/restore publish), C3 (supply-chain/SBOM/provenance), and C7
(limitation/conversion decision) are developer-executable and next. **C4
(physical BC250 qualification + soak), C5 (independent security review), C6
(non-developer human acceptance), and C8 (tag/publish) are hardware/human/
owner-gated pending evidence — never fabricated.** No `1.0.0` tag will be
created until the §20 no-go conditions are all clear and the owner authorizes
publication.

---

**P9 COMPLETE (developer-executable scope) — release engineering and 1.0
qualification (§15).** The release state is modeled honestly: the build is NOT
`1.0.0` and `release_state.py` keeps the known-unavailable capabilities
(model conversion; hardware-gated backup-restore publish) VISIBLE rather than
implying completeness. Deterministic property/fuzz gates prove the parse/
validate surfaces are fail-closed and bounded. CHANGELOG now agrees with
README/ARCHITECTURE/ADR index/AGENTS.md on the same release state. **The
hardware- and human-gated P9 exit-gate items remain pending evidence (never
fabricated).**

P9 landed (commits in order):

- `48ac3f7` P9.1 (§15.3): `tests/test_release_fuzz.py` deterministic
  property/fuzz gates — GGUF parsing TOTAL over adversarial inputs (closed
  verdict, never exception/hang), request validation bounded + fail-closed,
  backup manifest digest verification never raises + detects tampering, event
  pagination bounds finite. 5 tests.
- `922df33` P9.2 (§15.1/§15.2): `release_state.py` PURE release-state +
  manifest contract — `KNOWN_UNAVAILABLE_CAPABILITIES` (model-conversion,
  backup-restore-publish), `ReleaseState.blocking_gaps()/may_tag_1_0_0()`
  encode the P9 preconditions (milestone gates, hardware qualification, human
  acceptance, security review), `build_release_manifest` with integration
  identities. 5 tests.
- `5c27650` P9.3 (exit gate item 8): CHANGELOG entries for P3–P8 + a
  Release-state section making the not-yet-1.0.0 status explicit.

§15 exit gate — status per item: (1) all milestone exit gates green +
evidence-linked in this file [DONE]; (2) no open P0/P1 issue [DONE]; (3) signed
wheel/sdist/container/SBOM/provenance — clean-wheel install+smoke gates green,
but SIGNING/attestation is CI/human-gated [PENDING]; (4) upgrade + restore
matrices green [DONE — P8.4 + P8.2]; (5) hardware qualification + soak
[PENDING — physical BC250]; (6) security review sign-off [PENDING — human];
(7) human acceptance sign-off [PENDING — non-developer operator]; (8) docs
agreement [DONE — this commit]; (9) version bump + tag [DEFERRED until 3/5/6/7].

**Pending evidence (never fabricated): hardware qualification + 24–72h soak on
physical BC250; non-developer human acceptance (setup/daily chat/operations/
recovery/diagnostics); security review sign-off; artifact signing/attestation;
and the `1.0.0` version bump + tag, which are gated on those approvals.**

Verification: authoritative collection **949** (`pytest tests
--collect-only -q`); default suite green across deterministic alphabetical
chunks (947 passed + 1 Linux-gated skip + 1 tkinter-gated skip = 949
reconciled); explicit slow battery **51/51** (runtime 6/6, acquisition 41/41,
clean-wheel 4/4); compileall + `git diff --check` clean.

---

## FINAL RECONCILIATION (P0 -> P9)

Every milestone of `FINAL_PRODUCTION_READINESS_IMPLEMENTATION_PLAN.md` has been
executed in order with its exit gate recorded: **P0** foundation correction,
**P1** operation command/query API, **P2** Activity Center, **P3** bounded
execution platform, **P4** authenticated integration boundary, **P5** home/
health/diagnostics, **P6** model library + storage lifecycle v2, **P7** chat
reliability/privacy/daily-use UX, **P8** backup/restore/repair/upgrade safety,
**P9** release engineering + 1.0 qualification (developer scope). Test
collection grew 689 (P0) -> 715 (P1) -> 722 (P2) -> 773 (P4) -> 841 (P5) ->
884 (P6) -> 914 (P7) -> 939 (P8) -> **949** (P9), all green across
deterministic alphabetical chunks with the slow battery held at 51/51
throughout. Schema advanced to v9. The `elevated()` call-site census stayed at
45. What remains is exclusively hardware- and human-gated evidence (soak,
acceptance, security sign-off, artifact signing, and the 1.0.0 tag), recorded
as pending and never fabricated.

P8 landed (commits in order):

- `db81d60` P8.1 (§14.1): `backup_manifest.py` PURE manifest model —
  `BACKUP_MANIFEST_SCHEMA_VERSION=1`; frozen `BackupManifest` with every §14.1
  identity field (release + schema version, legacy-import provenance, runtime
  builds + known-good lineage, model artifact metadata + aliases with
  model_bytes_included default False, settings/thermal/operation-history
  policy, gateway config metadata, per-file digest/size/mode + relative
  containment, tool version) + canonical `manifest_digest`; refuses secret-like
  keys and non-contained paths (fail-closed). 5 tests.
- `7960ed1` P8.2 (§14.2/§14.3): `backup_restore.py` PURE dry-run restore gate —
  closed `RestoreRefusalCode` vocabulary; `validate_restore` checks manifest
  integrity -> completeness -> key -> containment -> schema -> space ->
  permissions -> identity, refusing BEFORE any mutation with a typed result.
  12 tests.
- `1e25770` P8.3 (§14.4): `repair_center.py` PURE Repair Center — closed
  `REPAIR_ACTIONS` catalogue (the eight §14.4 repairs), every action idempotent
  + auditable and available ONLY when every precondition is met;
  `findings_from_conditions` maps conditions to read-only findings (newer-schema
  is FAIL routed to upgrade). 6 tests.
- `5c70cb8` P8.4 (§14.5): `tests/test_upgrade_matrix.py` — a hand-built v8 DB
  (operations + model_installations + model_artifacts + known_good_runtime
  lineage + gateway_credentials) upgrades to v9 preserving EVERY durable row +
  adding model_library_meta; fresh install reaches SCHEMA_VERSION with every
  migration recorded. 2 tests.

§14 exit gate: backup manifest secret-free + digest-verified (P8.1); tampered/
partial/wrong-key/path-traversal/newer-schema/low-space/permission failures
leave the current profile untouched (P8.2 refusal matrix); Repair Center
resolves seeded supported failures via precondition-gated idempotent actions
without manual SQLite/filesystem edits (P8.3); upgrade tests prove the database,
managed artifacts, and runtime lineage are preserved (P8.4). **Pending evidence
(never fabricated): a full BACKUP_CREATE/BACKUP_RESTORE durable-operation
round-trip on physical BC250 hardware + post-restore inference verification
requires hardware; the restore PUBLISH step (atomic profile-level swap) is
designed fail-closed here but its live execution is hardware-gated.**

Verification: authoritative collection **939** (`pytest tests
--collect-only -q`); default suite green across deterministic alphabetical
chunks (937 passed + 1 Linux-gated skip + 1 tkinter-gated skip = 939
reconciled); explicit slow battery **51/51** (runtime 6/6, acquisition 41/41,
clean-wheel 4/4); compileall + `git diff --check` clean.

Next: **P9 — release engineering and 1.0 qualification (§15), then final
reconciliation.**

P7 landed (commits in order):

- `70c05ff` P7.1 (§13.1/§13.2): `chat_lifecycle.py` PURE shared contract —
  `ChatRequest` (request_id + conversation_id + bounded `ChatDeadline`
  connect/read/write/total, never None + prompt/generation token caps),
  thread-safe `ChatCancellation`, closed `ChatResultClassification`
  (COMPLETED/CANCELLED/TIMEOUT/SERVER_UNAVAILABLE/MODEL_MISMATCH/THERMAL_STOP/
  MALFORMED_RESPONSE/UNKNOWN) with deterministic precedence, duck-typed
  `classify_exception` (no httpx import), pure `should_retry` (never after
  tokens emitted; transient pre-response only; at most once), redacted
  `ChatEventRecord` (no prompt/completion), `recoverable_message` (request ID,
  never a traceback). `chat.py` integrates `format_chat_error` into both
  generate() handlers. 10 tests.
- `51a8b9e` P7.2 (§13.3): `conversation_ux.py` PURE presentation contract —
  model/context/slot profile indicator, "active model changed since last
  message" signal, rename/archive/delete confirmation + recovery policy
  (delete is the only destructive action), export privacy warning + optional
  redaction (redacted export never stores content), bounded search, accessible
  streaming status indicator. Local-only defaults preserved. 8 tests.
- `b135098` P7.3 (§13.4): `benchmark_ux.py` PURE contract — tested-vs-
  estimated comparison, model/runtime/config attribution, thermal-condition
  notice, cancellation/partial-result semantics, bounded retention (last 20),
  prompt-content canary, "apply winner" as a SEPARATE verified operation.
  `tests/test_chat_privacy_gate.py` pins the cross-module privacy exit gate
  (redacted event record; benchmark shape carries no prompt; support bundle
  never references conversations_dir; chat.py has no timeout=None; closed
  operation decoders reject unknown fields). 7 + 5 tests.

§13 exit gate: chat has no unbounded HTTP call or accumulation path
(`timeout=None` guard + bounded CHAT_HTTP_TIMEOUT); Stop/timeout/server-death/
thermal-latch/model-swap classification tested end to end (precedence +
duck-typed exception matrix); conversation content does not enter operation
history/logs/metrics/support bundles by default (privacy gate); GUI and
terminal clients share request/result/error semantics (one `chat_lifecycle`
contract); benchmark/tune results bounded, attributable, safe to apply.

Verification: authoritative collection **914** (`pytest tests
--collect-only -q`); default suite green across deterministic alphabetical
chunks (912 passed + 1 Linux-gated skip + 1 tkinter-gated skip = 914
reconciled); explicit slow battery **51/51** (runtime 6/6, acquisition 41/41,
clean-wheel 4/4); compileall + `git diff --check` clean.

Next: **P8 — backup, restore, repair, and upgrade safety (§14), then P9
release qualification (§15).**

P6 landed (commits in order):

- `fc2129a` P6.1 (§12.1): migration 009 `model_library_meta` (pinned,
  last_used_at, last_verified_inference_at, bounded benchmark summary;
  cascades on alias removal) → `SCHEMA_VERSION = 9`;
  `ModelLibraryMetaRepository` + `ModelLibraryQueryService`/
  `ModelLibraryEntry` surfacing every §12.1 field (identity/provenance,
  digest/size/format/quant/arch/tensor, trust/storage + quarantine reason,
  license, active/known-good refs, computed fit, usage, pinned, deletion
  eligibility + blockers); CLI `models library`; AST query-only guard.
  Schema-version assertions made future-proof. 14 tests.
- `84bb0f6` P6.2 (§12.2): durable `MODEL_REMOVE v1` — sixth operation;
  frozen four-step forward-only workflow (resolve_identity → detach_alias →
  quarantine_bytes → record_removal) on `model-storage`; ONE production
  adapter refuses blocked removals before mutation, detaches in a
  transaction, MOVES unreferenced bytes to operation-owned quarantine (never
  deletes), writes a removal receipt for bounded undo, marks the artifact
  QUARANTINED; forward-only rules re-verify references/active/known-good
  before moving bytes. `ModelRemoveCommandService` (query-only accurate
  dry_run + refuse-or-enqueue remove); CLI `remove-model` (dry-run default,
  `--yes` behind acknowledgment). 14 tests incl. TWO death/lease-takeover
  convergence tests.
- `a0231c1` P6.3 (§12.3): `storage_capacity.py` query-only
  `StorageCapacityService` — logical-unique vs logical-installed size, dedup
  savings, physical/staging/quarantine/reserved bytes, free space,
  configurable low-space warning; ranked cleanup suggestions
  (staging→quarantine→unreferenced) with exact identities + reasons;
  dry-run cleanup NEVER deletes; CLI `storage report|cleanup`; AST guard.
  9 tests.
- `e8118d2` P6.4 (§12.4): `MODEL_CONVERT v1` gate — known versioned type +
  request contract, but NO workflow registered and NO converter shipped;
  `ModelConvertCommandService` refuses every request BEFORE any external
  effect with the single honest reason; CLI `convert-model` reports
  UNAVAILABLE (exit 1). 6 tests.

§12 exit gate: Model Library shows provenance/digest/trust/fit/active/
known-good/verification state (P6.1); duplicate import/acquisition converges
to one managed artifact (existing acquisition gate, re-verified); invalid/
untrusted files quarantine safely and never receive an alias (existing +
P6.2); remove dry-run accurate and active/known-good/referenced models cannot
be destructively removed (P6.2); cleanup and undo survive process death and
lease takeover (P6.2 death tests); conversion remains visibly unavailable
with an honest reason (P6.4).

Verification: authoritative collection **884** (`pytest tests
--collect-only -q`); default suite green across deterministic alphabetical
chunks (882 passed + 1 Linux-gated skip + 1 tkinter-gated skip = 884
reconciled); explicit slow battery **51/51** (runtime 6/6, acquisition 41/41,
clean-wheel 4/4); compileall + `git diff --check` clean.

Next: **P7 — chat reliability, privacy, and daily-use UX (§13), then P8
backup/restore/repair/upgrade (§14), P9 release qualification (§15).**

P5 landed (commits in order):

- `fddf8a1` P5.1 (§11.1/§11.2): `health.py` typed health model +
  `home.py` `HomeQueryService`/`ApplianceHomeSnapshot` — ONE read unit,
  ten cards (identity/runtime/model/inference/thermal/operations/storage/
  integrations/backup/host) each with `as_of`+`stale_reason`, query-only
  by construction (AST guard). §11.2: `server.health_check` `model_id` is
  now the OBSERVED identity from the live server (never the desired
  `current_model`), + `desired_model`/`model_matches_desired`;
  `queries.health()` labels the model desired-only. Composition wires
  `application.home`; CLI `home` verb. 13 health + 15 home + 2 server tests.
- `9972b28` P5.2 (§11.3): `doctor.py` read-only `DoctorService` — stable
  finding ids, bounded severity (FAIL>WARN>INFO>PASS), evidence,
  recommended command; query-only (AST guard), never deletes; an unreadable
  DB is itself a finding. Catches all eight seeded failures (DB corruption,
  stale lease, mismatched handoff, bad digest, thermal latch, low disk,
  insecure topology, stale backup). CLI doctor merges structured findings.
  16 tests.
- `8910f57` P5.3 (§11.3): `support_bundle.py` redacted-by-construction
  export — conversations never read; credential files read only to feed the
  scrubber; raw DB/backups/binaries never copied; secret keys masked; free
  text scrubbed; paths normalized to `<profile>/`; model filenames
  replaceable with `<model-N>`; per-file+total size bounds; cancellable;
  manifest records policy + per-file + bundle digests. Embeds home.json +
  doctor.json from the SAME composed services. CLI `support-bundle` verb.
  11 tests. Also: `test_public_import_surface_is_stable` skips its tkinter
  gui import when `_tkinter` is unavailable (mirrors the Linux-gated skip;
  confirmed pre-existing at P4 in this sandbox).
- `f88e5e3` P5.4 (§11.4): `home_ux.py` PURE presentation contract (no
  tkinter/IO; AST guard) — preflight checklist, disk-space estimates, model
  fit explanation, downloaded/installed/active/verified separation,
  recovery-after-frontend-close instructions, bounded redacted copy-
  diagnostic-details, exact operation-history commands. GUI dashboard gains
  an "Appliance home" panel + "Copy diagnostic details" fed by the composed
  `application.home`. 10 home_ux + 1 GUI-contract test.

§11 exit gate: home snapshot query-only and consistent across CLI (`home`
verb), GUI (home panel), and support bundle (home.json) — all read the one
composed `HomeQueryService`; every green readiness claim has a bounded
evidence source; doctor catches all eight seeded failures; support bundles
pass secret/path/prompt canaries + size limits. **Pending evidence (never
fabricated): the novice-user moderated acceptance test (resolving seeded
common failures using only the UI's recommended-action text) requires a
non-developer operator.**

Verification: authoritative collection **841** (`pytest tests
--collect-only -q`); default suite green across deterministic alphabetical
chunks (135+110+94+167+115+135+85 = 841), 839 passed + 1 Linux-gated skip
+ 1 tkinter-gated skip = 841 reconciled; explicit slow battery **51/51**
(runtime 6/6, acquisition 41/41, clean-wheel 4/4); compileall + `git diff
--check` clean.

Next: **P6 — model library and storage lifecycle v2 (§12), then P7 chat
reliability (§13), P8 backup/restore/repair/upgrade (§14), P9 release
qualification (§15).**

P4 landed (commits in order):

- `ea87984` ADR 005 threat model (plan §10.1 prerequisite).
- `2fd40b0` gateway core (`bc250_llm_mode/gateway.py`):
  `GATEWAY_API_VERSION=1`; scopes `inference:read|inference:stream|
  models:list`; `CredentialStore` (fingerprint-only, `hmac.compare_
  digest`, provisioning record/rotate/revoke); `GatewayPolicy.authorize`
  scope matrix; `validate_body_bounds` (4 MiB body, 512 B secret,
  131072 ctx / 8192 gen token caps); `RateLimiter`; `GatewayServer`
  forwarding to the backend with typed timeouts + content-free audit;
  `make_gateway_socket_server` (ThreadingHTTPServer, loopback default).
  16 tests (`tests/test_gateway.py`).
- `b4edcf6` live-socket gate (`tests/test_gateway_live.py`, 2 tests on
  REAL loopback sockets: no-credential fail-closed; inference-through-
  gateway end-to-end) + end-to-end production gate.
- `2cf47a9` Open WebUI digest pin + container hardening
  (`bc250_llm_mode/openwebui.py`): `IMAGE_REF` pinned to
  `ghcr.io/open-webui/open-webui@sha256:f784534835ebbe57ba4f6093040702
  ff962ddab1e9aa2767f88cf3119d474721` (v0.6.14 amd64/linux, resolved via
  the real GHCR token flow); digest mismatch refuses install/start;
  named volume `bc250-open-webui` preserved across install/upgrade;
  UI publish bound to `127.0.0.1:3000:8080`; container security
  canaries (no-new-privileges, dropped capabilities, read-only rootfs
  where supported).
- `e613bfc` sharing routes ONLY through the gateway
  (`bc250_llm_mode/sharing.py`): `API_TARGET` is the gateway port 9071
  (never the raw backend); `https_sharing_status` reports §10.3 fields
  (topology/gateway_state/auth_state/backend_identity/last_verified_at);
  `start_https_sharing` REFUSES before any mutation unless
  `gateway_state == "verified"`; AST guard
  `test_architecture.py::test_gateway_is_the_only_bridge_to_the_backend`
  forbids the raw backend address as a serve target in sharing/openwebui
  forever.
- (this commit) durable credential slice (ADR 005 D3): migration 008
  `gateway_credentials` singleton row (fingerprint CHECK length=64 hex,
  scopes, created/rotated/revoked, revision) → `SCHEMA_VERSION = 8`;
  `bc250_llm_mode/gateway_command.py` `GatewayCredentialService`
  (provision/rotate/revoke/verify; secret persists ONLY in a 0600
  profile file `gateway-credential` via mkstemp+fchmod+atomic replace+
  dir fsync; DB holds the fingerprint alone; `write_state_fields`
  refreshes `gateway_provisioned/verified/backend_identity/
  last_verified_at/credential_file` into view snapshots;
  `resolve_credential_file` for the container mount); composition wires
  it in `app.py` and `OpenWebUIService` refreshes it before every
  install/start/restart/status and passes the credential file to the
  container (`OPENAI_API_KEY_FROM_FILE`); `gateway` CLI verb
  (status/provision/rotate/revoke/verify, `--secret` optional,
  mutating actions behind `require_acknowledgment`, PermissionError →
  exit 1 at the console boundary); serve/webui/gateway paths refresh
  gateway fields into the working snapshot. 9 service tests
  (`tests/test_gateway_credentials.py`, incl. v7→v8 upgrade preserving
  operations rows) + 5 CLI smoke tests (`tests/test_gateway_cli.py`,
  real composed application: fail-closed status, ack-gated provision,
  full provision→verify→rotate→revoke lifecycle with 0600 checks,
  secret-never-in-DB canary). `gateway.py` recorded in the bounded-
  execution inventory (`already_bounded`: typed gateway timeouts, never
  `timeout=None`).

§10.4 exit gate: raw backend unreachable from remote/container topology
(AST guard + gateway-only targets + loopback publish); no-credential
fail-closed (gateway 401 + CLI/service fail-closed tests); scope/rate/
size/rotation/revoke/audit tests pass (16 gateway + 9 credential + 5
CLI); Open WebUI inference-through-gateway (BACKEND_URL =
`http://host.containers.internal:9071/v1` with the mounted credential
file; live-socket end-to-end gate); digest mismatch refuses start;
container security canaries green; clean install+upgrade preserves the
named volume. **Pending evidence (never fabricated): hardware soak and
human-acceptance on physical BC250 hardware / non-developer operators.**

Verification: authoritative collection **773** (`pytest tests
--collect-only -q`); default suite green across six deterministic
alphabetical chunks (114+121+114+135+134+155), 4 slow-marked
deselected + 1 Linux-gated skip = 773 reconciled; explicit slow
battery **51/51** (runtime 6/6, acquisition 41/41, clean-wheel 4/4);
compileall + `git diff --check` clean.

(P5 followed and is recorded above.)

P3 landed:

- `gui/activity.py`: Activity Center reachable from a dashboard button
  (`_open_activity_center`, Toplevel + bounded polling that never blocks
  the GUI thread and never cancels work on close). The §8.2 presentation
  contract is PURE: `headline/message_copy/progress_text/severity_of/
  severity_rank/action_plan/support_text` — plain-language labels for
  every durable state, progress clamped to 99% until terminal
  verification, recovery-required rendered as prominent attention with
  "nothing deleted" safety copy, actions derived ONLY from
  OperationSummary flags, support text reusing view redaction.
- Widget layer is thin (Treeview + labels + action bar) over
  `operation_query`/`operation_commands` from composition; status strip
  shows working/paused/recovery counts and worker-lock ownership.
- Headless gates: the full state matrix (QUEUED/RUNNING/PAUSED/
  SUCCEEDED/FAILED_SAFE/RECOVERY_REQUIRED) rendered over REAL durable
  rows; action availability per state; routing through operation_commands
  verified by mutating durable state from a frame action; AST guard
  forbids sqlite/subprocess/repository/engine/worker imports in the
  module forever. Existing frozen Wizard surface untouched.

Verification: authoritative collection **722**; default suite green
across nine deterministic chunks: 721 passed + 1 Linux-gated skip = 722
reconciled; compileall + diff-check clean. (Slow gates unchanged from P1:
runtime 6/6, acquisition 41/41, clean-wheel 4/4.)

Next: **P3 — one bounded process port (`ProcessCommandSpec` v2) and a
bounded HTTP transport policy; migrate every production caller of raw
subprocess/HTTP; AST guards against regressions; secret canaries.**

---

Previous checkpoint: **P1 (Operation command/query API, U1.4)
COMPLETE on top of P0**

P1 landed:

- **Views** (`operations/views.py`): frozen `OperationSummary/Detail/
  StepView/EventView/LeaseView/WaitResult/Page/ActiveSummary` with
  schema version 1 serialization; absolute paths redacted to
  `file:<basename>` labels; closed event codes degrade to UNKNOWN.
- **Query** (`operations/query_service.py`): list/show/steps/events/
  leases/wait/active_summary; every method one READ unit; windowed SQL
  (no N+1); pagination bounds (page_size ≤ 200, events ≤ 500) refused
  with ValueError; stale leases reported expired while work stays
  recoverable; wait is bounded with an injectable condition waiter
  (production default: bounded-interval poller, never timeout=0).
- **Commands** (`operations/command_service.py`): cancel/resume/retry/
  recover/dismiss/detach, every mutation revision-fenced (CAS) and
  audit-evented. Retry creates a NEW operation from the immutable
  request with `parent_operation_id` lineage; recover takes over ONLY
  interrupted work whose every lease has expired behind `--confirm`
  and REFUSES RECOVERY_REQUIRED barriers with kind-specific guidance
  (exit 78 per plan §7.4); dismiss flips durable `dismissed_at`
  (migration 007) without touching history.
- **CLI** (`operations_cli.py`, wired in `__main__.py`): the full §7.4
  verb set; `--json` emits one schema-versioned document to stdout;
  human output compact with next-action lines; exit codes 0/1/2/78/130.
- **Generic detach (§7.5)**: `OperationCommandService.detach` hands a
  QUEUED operation to THE ONE worker entry point via the ONE spawn
  helper, now profile-bound (`--profile APP_DIR` so the child serves
  the same database) and marked detachable per kind. Exit gate: a real
  detached child completes a production MODEL_IMPORT exactly once
  THROUGH this API.
- **Migration 007**: `operations.dismissed_at` + default-view partial
  index; DATABASE_SCHEMA_VERSION now **7**.

Verification: authoritative collection **715**
(`pytest tests --collect-only -q`); default suite green across nine
deterministic alphabetical chunks: 714 passed + 1 Linux-gated skip =
715 reconciled; slow gates explicit: runtime stress **6/6**, acquisition
stress **41/41**, clean-wheel incl. runtime workflows AND worker-module
and CLI wheel gates **4/4**; compileall + `git diff --check` clean.

Next: **P2 Activity Center v1** — GUI over `operation_query` +
`operation_commands` only (no sqlite/subprocess imports), full
state/action matrix headless-tested, then P3 bounded execution platform.

---

Previous checkpoint: **P0 (foundation correction) COMPLETE**

P0 landed three boundaries:

- **P0.2** — the process-wide `faulthandler.dump_traceback_later(20,
  exit=True)` import-time watchdog in `tests/test_operation_worker.py`
  is DELETED. `tests/support_diagnostics.py` provides
  `ScopedTracebackDiagnostics` (dumps stacks for ONE block/wait without
  exiting, always cancels) and `wait_with_diagnostics` (bounded child
  wait → structured `(returncode, timed_out)`, kills the child's whole
  process group). Guards: AST scan over `tests/` forbids
  `exit=True`/`os._exit`; a child-interpreter probe proves importing the
  previously poisoned module arms nothing.
- **P0.1** — DEF-001 closed: `bc250_llm_mode/worker_main.py` is a REAL
  thin entry (`main(argv)->int`, argparse `--profile/--quiet-period/
  --lease-ttl` with bounded ranges, absolute-path + symlink refusal,
  missing-database → exit 4 with stable codes; 0 idle-exit / 2 usage /
  3 already-running / 4 repair / 5 run-failed / 130 interrupted).
  `worker_service.run_worker_main` now delegates (dead `json_safe`
  removed). Mandatory gates: a session-detached child completes a REAL
  production MODEL_IMPORT v1 of a tiny valid GGUF exactly once after
  parent handoff (artifact+alias exactly once, staging cleaned, boot
  policy untouched); no-work/paused/cancelled/poisoned/lock-conflict/
  malformed-policy cases covered; slow clean-wheel gate runs
  `python -m bc250_llm_mode.worker_main --help` and the repair path from
  an installed wheel with repo root off sys.path.
- **P0 findings fixed in production code**: (a) engine failure
  classification is now exception-safe — a step's classification probe
  that itself raises classifies that step UNCERTAIN so durable
  compensation still decides (previously the exception escaped
  `execute_one`, leaving operations RUNNING under live leases);
  regression tests added for both fail-safe and compensate branches;
  (b) `app.py _wire_services` bound `ThermalStateRepository` (latent
  NameError on the composed runtime thermal barrier), pinned by a new
  symtable composition-hygiene guard (`tests/test_composition_hygiene.py`)
  proving every referenced name in `app.py` resolves through some
  enclosing scope.
- **P0.3** — baseline reconciled (see Verification); CHANGELOG P0 section
  added; user-owned untracked files preserved untouched.

Verification (this sandbox still requires chunked execution):
authoritative collection **689** (`pytest tests --collect-only -q`);
default suite green across nine deterministic alphabetical chunks:
688 passed + 1 Linux-gated skip = 689 reconciled; slow gates explicit:
runtime stress/canaries **6/6**, acquisition stress **41/41**,
clean-wheel incl. runtime workflow execution **2/2** plus the NEW
worker-module clean-wheel gate (**3/3** in `-m slow tests/test_packaging.py
tests/test_worker_main_entry.py::test_installed_wheel_runs_worker_module_without_repository_root`);
compileall + `git diff --check` clean.

Next: **P1 Operation command/query API (U1.4)** — typed view models,
`OperationQueryService`, fenced `OperationCommandService`,
`bc250 operations …` CLI, generic detach contract; then P2 Activity
Center.

Previous checkpoint (U1.3): explicit worker lifecycle landed on top of
the closed Session 6B / U1.2 durable llama.cpp runtime lifecycle gate.

- One durable runtime path: CLI (`llamacpp update|rollback|resume|
  status`), wizard step 3, dashboard buttons, and initial setup all reach
  the composed `RuntimeLifecycleCommandService`
  (`runtime_lifecycle_command.py`), which enqueues through the shared
  `EnqueueService` and drives ONE operation via the shared engine factory
  alongside `MODEL_ACTIVATE v1`, `MODEL_ACQUIRE v1`, and `MODEL_IMPORT v1`.
- `RUNTIME_UPDATE v1` resolves the requested ref to a full immutable
  commit BEFORE any fetch/build mutation (moved refs refuse as
  `SOURCE_REF_MOVED`), builds an operation-owned candidate with bounded,
  cancellable typed-argv processes (no shell anywhere), freezes image +
  toolchain + recipe + per-binary sha256 into a canonical manifest, and
  derives a content build ID `llamacpp:sha256:<hex>` — tags are display
  metadata only.
- Active cutover is ONE no-gap atomic exchange via a fixed,
  digest-verified `renameat2(RENAME_EXCHANGE)` helper; unsupported
  filesystems fail safely before mutation. Initial installs publish with
  a no-replace rename instead.
- Success requires the seven-link identity chain: active manifest → live
  binary digest → handoff schema v2 binding → launcher start receipt →
  NEW systemd invocation marker → expected model/context/slots → bounded
  inference. Promotion is one generation-CAS database unit of work that
  also advances known-good identity.
- Any unproven state becomes `RECOVERY_REQUIRED`: both trees retained,
  both leases held as the barrier, remediation data persisted; cleanup
  never touches protected/uncertain paths.
- Rollback selects the repository's current rollback target, revalidates
  identities, and toggles lineage so an accidental rollback is itself
  reversible without rebuilding.
- Phase-scoped leases (ADR 002 §17): builds hold only
  `runtime-installation`; `runtime-active` joins at the activation
  boundary through promotion. Conflicts refuse/pause BEFORE any work.
- Handoff schema v2 + launcher start receipt (0600) bind configuration to
  the exact component; stale receipts and swapped binaries fail closed.
- Legacy routes DELETED with hard AST guards: `env.update_llamacpp`,
  `env.rollback_llamacpp`, `record_llamacpp_build`, `llamacpp_status`,
  mutable `llamacpp_history`, fixed `-staging/-backup/-rolled` paths,
  `ComponentLifecycleService.update/rollback`; setup cannot clone/build
  llama.cpp; frontends import no runtime infrastructure.
- Operations survive frontend closure: `llamacpp update --detach` hands
  the queued operation to ONE profile-scoped `WorkerHost`
  (`operations/worker_host.py`, spawned via `worker_service.py`) that
  resumes abandoned work exactly once, idles out after a bounded quiet
  period, pauses poisoned operations after bounded failures, and never
  changes reboot policy. Composition/boot/frontends never auto-start it
  (hard guards). Foreground remains the default; second Ctrl-C pauses
  durably with exit 130 and resume instructions.

Verification record for the U1.3 checkpoint (superseded by the P0 record
above; kept for count provenance): authoritative collection **662**;
chunked default execution green across eight deterministic alphabetical
chunks, 1 Linux-gated skip; slow gates explicit: runtime stress/canaries
**6/6**, acquisition stress **41/41**, clean-wheel incl. runtime workflow
execution **2/2**; compile/diff-check clean.

~~Next: U1.4 Operation command/query API~~ → now **P1** of
`FINAL_PRODUCTION_READINESS_IMPLEMENTATION_PLAN.md`.

The application is a `llama.cpp` Vulkan server behind a single systemd
service, with a resumable native tkinter wizard/dashboard and a terminal
chat client. The working tree is at **`0.9.0.dev0`** on reviewed commits
covering: 24-model catalog, chat/benchmark, thermal latch watchdog,
autotune, ordered atomic migrations to **schema v6** (runtime builds/
verifications/trees/component state), production hardening, the `gui/`
package, SQLite cutover with facade removed, R1/R2 exit gate, Session 5C
durable `MODEL_ACTIVATE v1`, Session 6A durable `MODEL_ACQUIRE/MODEL_IMPORT
v1`, and Session 6B durable `RUNTIME_UPDATE/RUNTIME_ROLLBACK v1`
(ADR 004).

## Where we are in the master plan

**Sequencing authority is now `FINAL_PRODUCTION_READINESS_IMPLEMENTATION_PLAN.md`
(U1.3 checkpoint → defensible 1.0.0). P0 foundation correction is DONE;
next boundary P1 Operation command/query API (U1.4), then P2 Activity
Center v1.** Historical context: `POST_R2_PRODUCTION_IMPLEMENTATION_PLAN.md`
drove Sessions 4–6B; the master plan remains requirements authority.
**Phase 0 (Session 4.1) is
DONE**: one SQLite connection policy (`db.open_database`) with test-proven
FK/query-only contracts and deterministic composition close; production
wiring repaired (host-mode imports, composed-activation single sequence,
rollback inference verification); launcher is handoff-only with strict
fail-closed validation; legacy canonicalization is pure (`legacy_schema.py`)
and the writable JSON store exists only as test support; duplicate
post-service commits removed with owners recorded; docs truth pass complete.
~~**Session 5A**~~ **DONE**; ~~**Session 5B**~~ **DONE**;
~~**Session 5C**~~ **DONE**; ~~**Phase U0**~~ **DONE**;
~~**Session 6A / U1.1** durable acquisition/import~~ **DONE**
(`SESSION_6A_DURABLE_MODEL_ACQUISITION_IMPLEMENTATION_PLAN.md` is the
completed authority); ~~**Session 6B / U1.2** durable llama.cpp runtime
lifecycle~~ **DONE** (`SESSION_6B_DURABLE_RUNTIME_LIFECYCLE_IMPLEMENTATION_PLAN.md`
is the completed authority; ADR 004 accepted; schema v6 with
worker locks).
`ULTIMATE_BC250_APPLIANCE_IMPLEMENTATION_PLAN.md` remains the sequencing
authority for U1+. ~~**U1.3 explicit worker lifecycle**~~ **DONE**.
Next: **U1.4 Operation command/query API**, then **U1.5 Activity Center
v1** toward the R3-complete exit gate (`0.9.0` tag candidate).

1. ~~Sessions 1–4: sweeps, facade removal, R1/R2 exit gate~~ **DONE**.
2. ~~Session 4.1: post-R2 production wiring stabilization~~ **DONE**.
3. ~~Session 5A: ADR 002 + migration 003 + operation state machine +
   repositories~~ **DONE** (`docs/adr/002-durable-operations.md`;
   `bc250_llm_mode/operations/` model/validation/repositories; schema v3
   with operations/operation_steps/operation_events/operation_leases;
   FAILED_SAFE terminal added per ADR 002; CAS transitions against state +
   revision; leases with owner+revision ownership and expired takeover;
   secret/bounds validation before persistence). **No executor, worker,
   host adapter, CLI command, or Activity UI exists yet** — that is 5B+.
4. ~~Session 5B: executor, leases, cancellation, recovery~~ **DONE**
   (`operations/workflow.py` typed registry + EnqueueService;
   `engine.py` fenced intent/effect/probe/checkpoint/verify protocol with
   deterministic `execute_one`; `recovery.py` closed classification
   vocabulary; `worker.py` bounded claim/run/shutdown loop — never
   auto-started by composition; lease `assert_owned` fencing and
   `list_expired`; durable cancel timestamps; RECOVERY_REQUIRED acquisition
   barrier; death-after-effect-before-checkpoint test proves effect count
   stays exactly 1 across takeover; full named crash-point matrix;
   20/20 focused stress iterations, no sleeps). **Still no real host
   adapter, CLI operation command, or Activity UI** — 5C/6C.
5. ~~**Session 5C**: durable model activation~~ **DONE**
   (`02b7e72` plan freeze → `dbacbdd` entry corrections → `b87e87f`
   workflow → `3ff497f` adapter → `ee8a5fe` cutover → Commit 6 evidence).
   Entry corrections landed first (ADR 002 §15: `COMMITTING → VERIFYING`
   cycle + durable compensation resume; intent reuse; per-step versions;
   fenced pulse). Production shape: `operations/activation.py` (request v1,
   evidence, typed port, eight steps), `activation_adapter.py` (ONE
   production host), `activation_command.py` (foreground enqueue/execute/
   terminal mapping; resumes interrupted activations, RECOVERY_REQUIRED
   barrier), strict handoff observation, bounded GGUF identity
   (`model_artifact.py`). Frontends (`__main__`, chat, GUI, setup) all
   reach `switch_model`/`change_context`/`change_parallel_slots` → the one
   command; `_apply_legacy_or_raise`, `restart_with_rollback`, and the
   synchronous orchestrator are deleted with AST guards. Mandatory
   handoff-death test passes BOTH branches (candidate-complete succeeds
   without a second restart; prior-still-active rolls back with exactly
   one restoration); 18-case crash matrix converges under takeover;
   20/20 focused stress iterations, no sleeps. **No operations CLI,
   Activity UI, detach, background worker, or auto-start** — Session 6C.
   Then **Session 6A** (acquisition), runtime update, R4 typed
   adapters/timeouts, and the later phases of the post-R2 plan.
6. ~~**Session 6A / U1.1**: durable model acquisition & import~~ **DONE**.
7. ~~**Session 6B / U1.2**: durable llama.cpp runtime lifecycle~~ **DONE**
   (ADR 004 `docs/adr/004-immutable-runtime-lifecycle.md`; migrations 005/006
   add `runtime_builds`/`runtime_build_verifications`/`runtime_trees`/
   `runtime_component_state`; `operations/runtime_lifecycle.py` pure
   workflows; `runtime_lifecycle_adapter.py` ONE production host;
   `runtime_lifecycle_command.py` composed command; `runtime_process.py`
   bounded typed-argv execution; `runtime_exchange_helper.py` fixed
   digest-checked renameat2 exchange; handoff schema v2 + start receipt;
   phase-scoped leases per ADR 002 §17; mandatory exchange-death test
   green in both fake world and crash matrix; legacy routes deleted with
   hard guards).
8. **U1.3: explicit worker lifecycle** **DONE**
   (`operations/worker_host.py`, `worker_service.py`, migration 006
   `worker_locks`). Mandatory abandoned-frontend resume test proves ONE
   supervised worker finishes an operation exactly once without touching
   reboot policy; single-instance via heartbeated profile lock; idle exit
   on injected clocks; bounded restart policy pauses poisoned operations;
   condition-backed bounded waiting; graceful shutdown checkpoints;
   `llamacpp update --detach` spawns exactly one typed helper process.
   Composition/boot/frontends never auto-start workers (hard guards).
   Foreground remains the default path.

## Layout highlights

| Area | Files |
| --- | --- |
| GUI package | `gui/app.py`, `gui/steps.py`, `gui/dashboard.py`, `gui/forms.py`; `Wizard`/`run_gui` composed in `gui/__init__.py`; surface frozen by headless contract test |
| State | `state.py` (legacy JSON defaults only), `repositories.py` + `runtime_builds.py` (typed SQL access), `paths.py` (AppPaths incl. database/migration paths), `db.py` (SQLite PRAGMA contract + ordered migrations to v6 incl. worker locks), `legacy_import.py` (one-time importer) |
| Safety runtime | `thermals.py` (hysteresis/latch/baseline/reset_latch), `optimize.py` (`apply_gpu_clock_limit`, `restore_gpu_profile`) |
| Durable activation (5C) | `operations/activation.py` (request v1 + evidence + typed port + eight steps), `activation_adapter.py` (one production host), `activation_command.py` (foreground enqueue/execute/terminal), `model_artifact.py` (bounded GGUF/digest identity); `runtime_handoff.py` strict `observe()`; `server.py` `service_observation` |
| Runtime lifecycle (6B) | `operations/runtime_lifecycle.py` (requests/evidence/port/steps), `runtime_lifecycle_adapter.py` (ONE production host), `runtime_lifecycle_command.py` (composed command/status), `runtime_builds.py` (immutable identities + repositories), `runtime_process.py` (bounded typed-argv runner), `runtime_exchange_helper.py` (digest-pinned RENAME_EXCHANGE); `env.py` is provisioning-only |
| Durable ops engine | `operations/engine.py` (fenced executor with phase-scoped leases), `operations/workflow.py` (registry/enqueue), `operations/repositories.py` (leases incl. `acquire_many`) |
| Composition | `app.py` (`Application.compose`; ONE frozen registry + enqueue + engine factory serve activation/acquisition/import/runtime update/runtime rollback via five command services) |

## Invariants (do not break)

- One service owner: only `server.py` touches `bc250-llm.service`.
- Fit gate: model/context/slot changes pass `calculate_fit`; NO-FIT never runs.
- Reboot safety: next boot is always the desktop; nothing auto-starts.
- Reversibility: host tuning records prior state; uninstall reverts it.
- Secrets never appear in argv or logs (HF token rides a 0600 env-file).
- Runtime updates never touch the active checkout until a smoke-checked,
  identity-bound candidate is atomically exchanged (RENAME_EXCHANGE);
  promotion happens only after the seven-link live verification chain,
  and any unproven state becomes RECOVERY_REQUIRED retaining every tree.
- Thermal stops latch until an explicit safe-temperature `thermals reset`.
- After SQLite cutover: no dual writes; JSON stays a read-only backup;
  derived paths come from injected `AppPaths`.

## Verification

```bash
PYTHONPATH=. .venv/bin/pytest -q        # default suite (slow-marked gates
                                        # excluded); the terminal summary
                                        # prints the authoritative collected
                                        # count (never infer from dots)
.venv/bin/pytest tests --collect-only -q
python3 -m compileall -q bc250_llm_mode tests
# Session verification battery additionally runs the slow gates explicitly:
.venv/bin/pytest -m slow tests/test_runtime_security_stress.py   # U1.2 canaries+stress
.venv/bin/pytest -m slow tests/test_acquisition_security_stress.py
.venv/bin/pytest -m slow tests/test_packaging.py   # clean-wheel incl. runtime v1 execution
```

On constrained sandboxes that kill long CPU-bound processes (~20 s), run
the same suite as deterministic alphabetical chunks and reconcile their
pass counts against `--collect-only` (see Current state). The behavioral
launcher tests need only bash ≥3.2 and python3 on PATH.

### Test-count reconciliation record (Session 4.1 §3.1)

- Handoff at `7672e7d` claimed **313**; the audited checkout collected **301**
  — the earlier figure was stale because Session 4C deleted the facade-only
  cutover tests without updating the handoff.
- Session 4.1 added connection-contract, production-wiring, canonicalizer,
  and launcher fail-closed tests; the reconciled baseline is now **330**,
  printed automatically by `tests/conftest.py` in every run's summary.
- Source (`PYTHONPATH=.`) and editable-install invocation collect identically.
- Session 6B closeout (+follow-through, +U1.3): collection is **662**
  (default executed green + slow-marked gates). This sandbox's ~20 s CPU
  kill prevents single-shot full runs; evidence comes from eight
  alphabetical chunk runs plus explicit slow-gate runs (runtime 6/6,
  acquisition 41/41, packaging 2/2). Never quote a count without naming
  how it was produced.

## Development conventions

Keep changes small and test-first where practical; extend fakes rather than
invoking system services; keep command construction inspectable (no shell
interpolation for user/model paths); preserve atomic state writes, rollback
behavior, and the README/ARCHITECTURE documentation contract. Cite master-plan
task IDs (e.g., R2.2) in commit messages.
