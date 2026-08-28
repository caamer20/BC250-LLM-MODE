# Release evidence directory

This directory holds **real, candidate-bound release evidence records** for the
BC-250 LLM MODE `1.0.0` release closure. Each record is one JSON document
validated by `bc250_llm_mode.release_evidence.validate_evidence_record` and
consumed by `bc250_llm_mode.release_gate.evaluate_release` — but ONLY after it
has been promoted to a `VerifiedEvidenceRecord` through
`verify_evidence_attestation` (parsing ≠ verification).

**No fabricated samples live here.** A record is only added once the event it
describes has actually happened (a real test run, a real hardware soak, a real
security review, a real human acceptance). Placeholder or example records are
prohibited by the release-closure plan (§1.3): a checklist, markdown note,
unit-test-only result, or unsigned JSON document is NOT external qualification.
Test-only fixture records belong in `tests/release_evidence_fixtures.py` and
must NEVER be committed here.

## Record contract (evidence schema v2)

Every record is a JSON object with ALL 18 mandatory envelope fields — unknown
top-level fields are refused; see `bc250_llm_mode/release_evidence.py` for the
authoritative validator:

- `evidence_schema_version` — currently `2`.
- `evidence_id` — stable, unique, non-empty id for the record.
- `kind` — one of the closed 18-kind vocabulary in
  `bc250_llm_mode.release_policy.EvidenceKind`.
- `release_candidate_version` — the exact candidate (e.g. `1.0.0rc1`).
- `source_repository` — repository identity (e.g. `local`).
- `source_commit` — the full 40-char commit SHA the evidence is bound to.
- `policy_digest` — the reviewed release-policy digest the evidence was
  produced under (`sha256:<64 hex>`); a mismatch blocks.
- `artifact_inventory_digest` — canonical digest of the candidate artifact
  inventory the evidence was produced against (subject binding, below).
- `artifact_subjects` — list of `{name, sha256, media_type}`; every `sha256`
  is a full 64-char lowercase digest. MUST be non-empty for artifact-bound
  kinds (CLEAN_WHEEL_SMOKE, SBOM, BUILD_PROVENANCE, ARTIFACT_ATTESTATION,
  RELEASE_APPROVAL).
- `issuer` — `{type, identity}`; type ∈ {ci, human, tool, service}. The
  issuer is responsible for the truth of every measurement in the record.
- `issued_at` — timezone-aware ISO-8601; future timestamps are rejected.
- `expires_at` — `null` or timezone-aware ISO-8601; an expired record is
  rejected (policy max evidence age applies at evaluation).
- `environment` — `{os, architecture, python, runner}`.
- `result` — `PASS` (anything else is rejected).
- `measurements` — bounded dict satisfying the per-kind contract
  (`KIND_MEASUREMENT_CONTRACTS`); no secrets, no prompts, no conversation
  content.
- `attachments` — list with contained relative `location_hint`s only.
- `verification` — `{mechanism, subject, verifier, verified_at,
  bundle_digest}`; mechanism must be one of the policy's
  `approved_verification_mechanisms` (sigstore-bundle, gh-attestation).
- `supersedes_evidence_id` — `null` or the id this record replaces
  (same-kind only; cycles and unknown targets are refused set-wide).

Bounds (RECORD_OVERSIZE): strings ≤ 32 KiB, nesting ≤ 16, ≤ 64 measurement
keys, ≤ 64 subjects, ≤ 32 attachments, ≤ 1 MB total. Secret-like material is
rejected by VALUE (hf_/ghp_/PEM/Bearer/URL-userinfo patterns) under ANY key.

## Verification boundary (G2)

Validation produces a *validated* record; only `verify_evidence_attestation`
promotes it to a `VerifiedEvidenceRecord` — the only shape the evaluator
consumes (raw/validated dicts are refused with `NOT_VERIFIED`). Promotion
verifies: full schema-v2 validation; bundle mechanism approved; bundle subject
== record verification subject ∈ artifact subjects; claimed bundle digest ==
canonical digest of the bundle payload. It never invents a remote trust root:
transparency-log verification happens in CI (the release workflow's
`verify-attestations` job) and stays owner-gated.

## Subject binding (G3)

The evaluator refuses any record whose `artifact_inventory_digest` differs
from the candidate inventory being evaluated (INVENTORY_DIGEST_MISMATCH), and
every subject digest must exist in that inventory
(ARTIFACT_SUBJECT_MISMATCH). Evidence verified against one artifact set can
never qualify a different set.

## Rejection is fail-closed

A record is rejected (and never satisfies a gate) if it is malformed, an
unknown kind, not `PASS`, bound to the wrong version/commit/policy/inventory,
expired, superseded, duplicated, carries a non-contained attachment path,
contains secret-like or prompt material, violates bounds or its kind contract,
or fails attestation verification. Rejection reasons are stable codes in
`EvidenceRejectionCode` (31 values), checked in a PINNED order.

## Producing and evaluating

```bash
python -m tools.release validate release/evidence \
    --candidate 1.0.0rc1 --source-commit <full-40-char-sha>
python -m tools.release evaluate --candidate 1.0.0rc1 \
    --source-commit <sha> --artifacts dist --level rc \
    --evidence release/evidence
```

The evaluator returns `eligible_for_1_0_0 = false` with exact blocking codes
until every required evidence kind is present and verified, every mandatory
capability is available, and every accepted limitation has a reviewed
acceptance record. `1.0.0` is nowhere presented as published or eligible
without that evaluator evidence.
