# Continuation guide for BC250 LLM MODE

## Current state

**ADR 007 CACHYOS HOST INTEGRATION — IMPLEMENTED AND DEVELOPER-QUALIFIED;
PHYSICAL EVIDENCE PENDING.** The durable appliance remains distribution-
neutral above one new capability-driven host boundary. Bazzite behavior is
preserved; CachyOS now has a native Pacman/systemd integration without any
rpm-ostree dependency. The repository again stops at the real-world evidence
boundary, now expanded to require qualification on both advertised host
profiles.

**ADR 007 checkpoint (`ccd1777`):**

- Branch: `main`, **82 commits ahead of `origin/main`**; push remains owner-
  gated. Version `0.9.0.dev0`; database schema v10 and release-control schema/
  policy revisions are unchanged.
- New `host_platform.py`: bounded `/etc/os-release`, immutable-root, systemd,
  command, boot-manager, and Cyan-capability observations; native Bazzite +
  CachyOS profiles; compatible-unqualified Arch/Fedora/Debian/SUSE families;
  unknown/non-systemd hosts fail closed. Observations are recomputed and never
  persisted as durable truth.
- CachyOS Tk bootstrap is the fixed argv `pacman -S --needed --noconfirm tk`
  after `pacman -Qu`; pending upgrades refuse with manual `pacman -Syu`
  guidance. The app never runs `pacman -Sy` or an automatic full upgrade.
  Runtime host dependencies are preview-only; the Fedora Distrobox remains the
  controlled guest on both hosts.
- Current-boot host mode requires only systemd/udev on CachyOS, uses the
  distribution-neutral display-manager alias, and preserves graphical/no-
  autostart reboot safety. systemd-boot, GRUB, Limine, and rEFInd are detected
  but persistent kernel configuration is observation-only; rpm-ostree cleanup
  remains available only for app-owned Bazzite state.
- CLI/chat/wizard/dashboard all route through the ONE composed HostModeService.
  `platform status|plan` is pre-composition/read-only and writes nothing.
  Status, doctor, appliance home, and support bundles expose the disposable
  platform result. Cyan controls are disabled when unavailable; the watchdog
  retains monitoring + its latched emergency stop without guessing a clock
  backend.
- Fresh-bootstrap stage-order defect fixed: safety acknowledgement now advances
  through HARDWARE_VALIDATED before TKINTER_READY.
- Verification over the exact commit content: default collection **1174**
  (**1172 passed + 2 platform skips**, exit 0); complete slow battery **52/52**;
  focused platform/bootstrap/setup/guard battery **43/43**; both clean-wheel
  packaging gates green with installed `host_platform`; compileall +
  `git diff --check` clean; elevated-call-site guard remains 45.
- The nine owner-controlled untracked files remain untouched. Tracked tree is
  clean.
- Candidate truth: the old G6 artifact digests below remain historical and do
  NOT identify `ccd1777`. No release artifact, signature, tag, or PASS hardware
  record was fabricated. **Next authorized action is C4 on physical BC-250
  hardware for Bazzite AND CachyOS**, including install/reboot, model inference,
  host-mode round trip, thermal behavior, runtime rollback, backup/restore, and
  soak. Any later code change invalidates that candidate-bound evidence.

---

**PRIOR RELEASE-GATE REMEDIATION — G6 COMPLETE (historical predecessor to ADR
007).** Every developer-executable remediation item was proven closed at its
then-current checkout; ADR 007 subsequently changed package code and therefore
supersedes its artifact identity, not its release-control conclusions.

**G6.4 final handoff state:**

- Closeout: `main`, qualification executed at `fe24ee9` (this docs commit
  changes ONLY documentation — no package code — so the qualified artifacts
  remain bound). Branch divergence: **80 commits ahead of `origin/main`**
  (push stays owner-gated).
- Version `0.9.0.dev0`; database schema **v10**; release policy content
  revision **3** (digest `sha256:1883cbfc…ed20`); evidence schema **v2**;
  manifest schema **v3**; decision schema **v3**; inventory schema **v2**.
- Authoritative totals: default collection **1154** + slow battery **52**
  (51 pre-G6 + the G6 qualification gate) = **1206**.
- G6.1 fresh-worktree qualification at `fe24ee9` (clean `git worktree`,
  deps verified via `pip check` against the declared-metadata venv):
  default suite in one run **1152 passed + 2 skipped** (Linux `renameat2`
  ×2) = 1154, exit 0 — the tkinter skip appears only when
  `test_packaging.py` runs in a separate process (3 platform skips) because
  the in-suite gui-contract tkinter stub makes the import-surface test pass;
  slow battery **52/52** (runtime stress 6, acquisition stress 41,
  packaging 2 + operations-cli 1 + worker-main 1, G6 qualification 1);
  focused release suite **178/178**; compileall + `git diff --check` clean.
- G6.1 dry-run artifacts (built ONCE at `fe24ee9`): wheel
  `sha256:5b78463db4baf7c93cc6f0da9314989f506b73d54ff3ef7d4b4b434414abb6a0`;
  sdist `sha256:37f1786f1b7831d798c9205400503d6f0140d6ec5fc31a68b02faaa1506ffe2e`;
  SBOM `sha256:62bd9f087615237b69aa0a1cf1f192a3873d8061593f1e311b5c3df491f91d64`;
  blocked draft manifest digest
  `sha256:09025284fbd9ec58d7e1a649094a706f08935fca9a143d89e8f58bb5dbb06937`;
  evaluator decision inventory digest
  `sha256:a4392bb94532713ef24be9d16d9542fb2b2bcadde00d451448de05b010ab0822`.
  `tools.release verify` over the set: green. Packaged CLI + schema init +
  operation worker exercised by the clean-wheel slow gates.
- Evaluator over the exact dry-run outputs: `eligible_for_1_0_0 = false`,
  exit 1, blocking codes LIMITED to the genuine external gates:
  BACKUP_RESTORE_EVIDENCE_MISSING, CLEAN_WHEEL_EVIDENCE_MISSING,
  DOCUMENTATION_DRIFT, HARDWARE_QUALIFICATION_MISSING,
  HUMAN_ACCEPTANCE_MISSING, LIMITATION_ACCEPTANCE_MISSING,
  MILESTONE_EVIDENCE_MISSING, PROVENANCE_MISSING_OR_MISMATCHED,
  REPOSITORY_NOT_PUBLISHED, SBOM_MISSING_OR_MISMATCHED,
  SECURITY_REVIEW_MISSING, SIGNATURE_MISSING_OR_INVALID,
  SOAK_EVIDENCE_MISSING, TEST_EVIDENCE_MISSING. ZERO developer/structural
  blockers. The frozen `tests/test_release_qualification_g6.py` (slow)
  re-proves this on every run.
- Owner-controlled untracked files: exactly **9**, preserved, enumerated
  and classified in the G5 record below. Working tree tracked-clean.
- **Next authorized action: begin C4 evidence collection on the physical
  BC-250 (handoff packet `release/EVIDENCE_HANDOFF.md`) — NOT a version
  bump, tag, or publication.** Any candidate code change after evidence
  collection INVALIDATES the candidate-bound evidence for that commit and it
  must be recollected; there is no manual override. No `v1.0.0` tag, package
  upload, or publication exists; no PASS evidence was fabricated.

---

**G5 record (documentation truth reconciliation, superseded as "next" by
G6).** G5.1 status vocabulary (implemented / developer-qualified / evidence
pending / release blocked / published) applied everywhere; current release
status: **release blocked**. G5.2: README Release-status section;
ARCHITECTURE "Release authority (separate from runtime)" + stale U1.3 claim
corrected; CHANGELOG G0–G5 remediation section; evidence README rewritten
for schema v2; NEW operator runbook `release/RUNBOOK.md`; owner-controlled
release-closure plan NOT edited (distinction lives in tracked remediation
plan §1 + CHANGELOG); C3 record carries a dated reconciliation. G5.3:
owner-untracked recount = exactly 9 (3 plan docs, 5 scripts_audit, 1
scratch test file); `check_release_checkout` gains build-input-scoped
blocking (`DEFAULT_BUILD_INPUT_PREFIXES`) + `diagnostics_only` mode; result
carries `blocking`; never deletes. G5.4: docs gate extended
(ACTION_REF_MUTABLE, C3_CLAIM_WITHOUT_REMEDIATION,
POLICY_SNAPSHOT_MISMATCH, EVIDENCE_SCHEMA_DOC_MISMATCH) + NEW
`tests/test_release_docs_consistency_g5.py` (6 tests; live-repo test is
the exit gate). Verification: 6/6; focused 178/178; collection 1154.
Commit `7b7a2f0`.

---

**G4 record (pinned actions + hardened workflows, superseded as "next" by
G5).** All `uses:` full 40-char SHAs network-verified 2026-08-28 (checkout
v4.1.1, setup-python v5.6.0, upload-artifact v4.6.2, download-artifact
v4.3.0, attest-build-provenance v2.4.0 peeled); no TODO(C3); NEW
`.github/dependabot.yml`. `release.yml`: candidate_version/candidate_ref/
qualification_level inputs; build-once (complete release set incl. manifest
v3) → verify-artifacts → attest → verify-attestations (before approval) →
final-evaluation (evaluator gates approval + publish) → approval-environment
→ publish (named bundle; refusal = evaluator final-level exit). NEW
`tests/test_release_workflow_policy_g4.py` (6 §G4.9 tests). Verification:
13/13 hardening green; focused 172/172; chunked default suite **1139 passed
+ 0 failed** + 3 skips = 1142 (first fully-green default suite); collection
1148. Commit `1bf0c9e`.

---

**G3 record (artifact-bound tooling, superseded as "next" by G4).**
Inventory schema **v2** (`Artifact.role`; roles from exact names; duplicate
roles refused). NEW `release_manifest.py`: decision-derived manifest schema
**v3** (BLOCKED drafts, final refusal, canonical `manifest_digest`, manifest
excluded from its own inventory; `release_state` facade stays v2). Evaluator
subject↔inventory binding (INVENTORY_DIGEST_MISMATCH /
ARTIFACT_SUBJECT_MISMATCH); gate codes 21 → 23. CLI: `evaluate` REQUIRES
`--artifacts` + `--level` (JSON-only stdout); `manifest` decision-derived;
`verify` FULL comparison (inventory equality, completeness, checksums
cross-check, SBOM subject == actual wheel digest, manifest digest
integrity). SBOM: tomllib parsing + SBOM_DUPLICATE_COMPONENT. NEW §17
isolated release fixture pipeline test + tampered-bundle negative twin.
Verification: 16/16 red tests green; focused 152/152 (+ pipeline 2/2);
collection 1142; chunked 1129 passed + 10 intended red.

---

**G2 record (evidence schema v2 + verification boundary, superseded as
"next" by G3).** `release_evidence.py` EVIDENCE_SCHEMA_VERSION **2**: 18
mandatory envelope fields; unknown fields refused; PINNED validation order;
VALUE-based credential patterns (hf_/ghp_/PEM/Bearer/URL-userinfo) rejected
as SECRET_MATERIAL under any key (linear-time trigger pre-filter + scan cap);
RECORD_OVERSIZE bounds; per-kind measurement contracts (KIND_CONTRACT_UNMET);
`validate_evidence_record` REQUIRES `source_commit` + `policy_digest`;
`validate_evidence_set` (duplicate ids, unknown/cross-kind/cyclic
supersession, duplicate coverage); Raw/Validated/Verified boundary
(`VerifiedEvidenceRecord` via module-private sentinel;
`verify_evidence_attestation` verifies bundle integrity + subject binding +
approved mechanism + canonical digest — never invents a remote trust root).
Evaluator consumes ONLY VerifiedEvidenceRecord (dicts → NOT_VERIFIED).
Policy content revision **3** (`approved_verification_mechanisms`;
snapshot `release/policy-v3.json`; digest
`sha256:1883cbfc7deb694a336b4e2163d8767550a3734e3a93b9f53471b41d15d9ed20`;
scope-decision record re-bound via dated amendment). CLI `validate`
candidate-bound. NEW test-only factory `tests/release_evidence_fixtures.py`.
Verification: 38/38 red tests green; focused 142/142; collection 1140;
chunked 1115 passed + 25 intended red.

---

**G1 record (evaluator sole authority, superseded as "next" by G2).**
G1 deleted both eligibility bypasses and made every release decision
candidate-bound:

- `bc250_llm_mode/release_artifacts.py` (NEW) — pure `Artifact` /
  `ArtifactInventory` identity types with a canonical `inventory_digest()`
  (sorted (name, sha256) pairs); the disk I/O stays in `tools/release/
  artifacts.py`, which re-exports the names.
- `release_gate.py` — NEW `CandidateIdentity` (fail-closed construction:
  full 40-char lowercase commit, never all-zeros; full `refs/…` ref;
  non-empty version/repository; `sha256:<64 hex>` policy digest).
  `evaluate_release` now REQUIRES `candidate=` + `artifacts=` keyword inputs
  (TypeError otherwise — the sourceless `candidate_version=`/optional
  `source_commit=None` surface is DELETED); binds the candidate policy digest
  (POLICY_DIGEST_MISMATCH blocks, so a weaker caller policy can never qualify
  a reviewed-digest candidate); enforces the final-tag rule (a final version
  must ride exactly `refs/tags/v<version>`, CANDIDATE_REF_MISMATCH blocks);
  DERIVES the unavailable-capability set from the reviewed product state
  (`release_state.KNOWN_UNAVAILABLE_CAPABILITIES` via module attribute, never
  a caller default) and limitations from the reviewed policy alone
  (`declared_limitations` parameter deleted). `ReleaseDecision` schema v3
  carries source_ref/repository/inventory_digest and never serializes the
  private marker. Gate-code vocabulary extended 19 → 21 (the two binding
  codes).
- `release_state.py` — `evidence_satisfied` field DELETED (constructing one
  raises TypeError); `may_tag_1_0_0()` is a non-authoritative constant False
  pointing at the evaluator (plan §15.1 explicit compatibility decision);
  manifest facade keeps the known limitations visible.
- `tools/release/__main__.py` — `evaluate` REQUIRES `--source-commit`
  (argparse exit 2), gains `--source-ref`/`--repository`, constructs the
  CandidateIdentity against the REVIEWED default policy only, and passes the
  artifact inventory (diagnostics-only empty inventory until G3 makes
  `--artifacts` mandatory).
- Migrated with explicit compatibility decisions: `test_release_state.py`
  (facade-never-tags replaces the "legitimate 1.0 path" test),
  `test_release_gate_v2.py` (evaluator-based unavailable-capability block),
  `test_release_gate_c1.py` (candidate/inventory fixtures; vocabulary count
  21; decision schema 3). NEW single-authority architecture guard: no
  `may_tag_1_0_0`/`evidence_satisfied` reference outside the authority pair.

G1 verification: all 8 G0 gate-remediation red tests GREEN (+ guard); focused
release suite **104/104**; authoritative collection **1140** (1139 + guard);
chunked default suite **1077 passed + 63 intended red** (the remaining reds
are exactly the G2 evidence-v2, G3 artifact-binding, and G4 workflow-
hardening scopes; zero unrelated regressions); compileall + `git diff --check`
clean. Note: `test_red_validated_but_unverified_evidence_cannot_qualify`
passes early at G1 (v2 records rejected by the still-v1 schema check) and
continues to enforce the G2 verified-boundary contract after schema v2 lands.

Next: **G2 — evidence schema v2 + verification boundary**: mandatory 18-field
envelope, value-based secret detection, kind contracts, bounds, supersession
set rules, Raw/Validated/Verified types with a verifier-sentinel, attestation
verification adapter, policy content revision 3.

---

**G0 record (baseline + red gates for the remediation).**
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

**Dated reconciliation (2026-08-28, G5 §G5.2):** the original C3 completion
report preceded the release-control audit. Under the G5.1 vocabulary, C3 at
delivery was "implementation scaffold complete; remediation G1–G4 pending":
its action refs were still mutable, attestations were never verified, the
evaluator was never run in the pipeline, and `verify` compared nothing.
Remediation G1–G4 (commits `6f2fce5`/`f5c8e87`/`57306f2`/`1bf0c9e`) closed
every one of those findings with frozen green tests, so the C3 scope is now
**developer-qualified**; live attestation generation/verification and any
publication remain **evidence pending** (CI/owner-gated C3.5/C3.6, C8). The
historical record above is preserved unchanged.

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

Compressed milestone records (full detail in git history P0→P9 commits):

- **P8** (`db81d60`/`7960ed1`/`1e25770`/`5c70cb8`): PURE backup manifest v1
  (secret-free, digest-verified, contained paths), dry-run restore gate with
  closed refusal vocabulary (refuses BEFORE mutation), Repair Center (eight
  idempotent precondition-gated actions), v8→v9 upgrade matrix preserving
  every durable row. Collection **939**.
- **P7** (`70c05ff`/`51a8b9e`/`b135098`): `chat_lifecycle.py` bounded shared
  contract (never timeout=None; closed result classification; redacted event
  records), `conversation_ux.py` + `benchmark_ux.py` presentation contracts,
  cross-module privacy gate (conversation content never enters operations/
  logs/metrics/support bundles). Collection **914**.
- **P6** (`fc2129a`/`84bb0f6`/`a0231c1`/`e8118d2`): migration 009 model
  library meta → schema v9; durable `MODEL_REMOVE v1` (quarantine, never
  deletes, undo receipt); query-only storage capacity + dry-run cleanup;
  `MODEL_CONVERT v1` gate — visibly UNAVAILABLE, honest reason. Collection
  **884**.
- **P5** (`fddf8a1`/`9972b28`/`8910f57`/`f88e5e3`): typed health + home
  snapshot (ONE composed read unit across CLI/GUI/support bundle), read-only
  doctor (eight seeded failures caught), redacted-by-construction support
  bundle, pure home UX contract. Collection **841**.
- **P4** (`ea87984`/`2fd40b0`/`b4edcf6`/`2cf47a9`/`e613bfc` + migration
  008): ADR 005 threat model; gateway (scopes, fingerprint-only credentials,
  rate/size bounds) as the ONLY bridge to the backend (AST guard); Open WebUI
  digest-pinned container; sharing refuses before mutation unless gateway
  verified; 0600 credential file, DB holds fingerprint only. Collection
  **773**.
- **P3**: Activity Center GUI over operation query/commands only (AST guard
  forbids sqlite/subprocess imports); full durable-state matrix headless-
  tested. Collection **722**.
- **P2**: Activity Center v1 scaffolding (collection 722).
- **P1**: frozen operation views, windowed query service (bounded
  pagination), revision-fenced command service (cancel/resume/retry/recover/
  dismiss/detach; exit 78 on RECOVERY_REQUIRED), operations CLI (0/1/2/78/
  130), generic detach through ONE worker entry; migration 007. Collection
  **715**.
- **P0**: faulthandler watchdog poison deleted (scoped diagnostics instead);
  `worker_main.py` real thin entry; engine failure classification made
  exception-safe; composition-hygiene symtable guard. Collection **689**.
- **U1.3** (pre-P0 checkpoint, collection **662**): explicit worker
  lifecycle — one durable runtime path via `RuntimeLifecycleCommandService`;
  `RUNTIME_UPDATE v1` full-commit resolution before mutation, content build
  IDs, seven-link identity chain, atomic digest-verified RENAME_EXCHANGE
  cutover, phase-scoped leases, handoff schema v2 + start receipts, legacy
  routes deleted with AST guards, profile-scoped WorkerHost for detached
  work (never auto-started).

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
