# Release evidence directory

This directory holds **real, candidate-bound release evidence records** for the
BC-250 LLM MODE `1.0.0` release closure. Each record is one JSON document
validated by `bc250_llm_mode.release_evidence.validate_evidence_record` and
consumed by `bc250_llm_mode.release_gate.evaluate_release`.

**No fabricated samples live here.** A record is only added once the event it
describes has actually happened (a real test run, a real hardware soak, a real
security review, a real human acceptance). Placeholder or example records are
prohibited by the release-closure plan (§1.3): a checklist, markdown note,
unit-test-only result, or unsigned JSON document is NOT external qualification.

## Record contract

Every record is a JSON object with (at minimum) these fields — see
`bc250_llm_mode/release_evidence.py` for the authoritative validator:

- `evidence_schema_version` — currently `1`.
- `evidence_id` — stable, unique id for the record.
- `kind` — one of the closed vocabulary in
  `bc250_llm_mode.release_policy.EvidenceKind` (e.g. `DEFAULT_TEST_SUITE`,
  `SBOM`, `BUILD_PROVENANCE`, `HARDWARE_QUALIFICATION`, `SOAK_TEST`,
  `SECURITY_REVIEW`, `HUMAN_ACCEPTANCE`, `BACKUP_RESTORE_HARDWARE`,
  `KNOWN_LIMITATION_ACCEPTANCE`).
- `release_candidate_version` — the exact candidate (e.g. `1.0.0rc1`).
- `source_commit` — the full commit SHA the evidence is bound to.
- `artifact_subjects` — list of `{name, sha256, media_type}`; every `sha256`
  must be a full 64-char lowercase digest.
- `issuer` — `{type, identity}` of the producer (ci / human / tool).
- `issued_at` — timezone-aware ISO-8601 timestamp.
- `expires_at` — optional; an expired record is rejected.
- `environment` — `{os, architecture, python, runner}`.
- `result` — `PASS` (anything else is rejected).
- `measurements` — bounded dict of scalar/summary values (no secrets, no
  prompts, no conversation content).
- `attachments` — optional list with contained relative `location_hint`s only.
- `signature_or_attestation_reference` — where the signature/attestation lives.
- `supersedes_evidence_id` — optional id this record replaces.

## Rejection is fail-closed

A record is rejected (and never satisfies a gate) if it is malformed, an
unknown kind, not `PASS`, bound to the wrong version/commit, expired,
superseded, duplicated, carries a non-contained attachment path, contains
secret-like or prompt material, or has a malformed digest. Rejection reasons
are stable codes in `EvidenceRejectionCode`.

## Producing and evaluating

```bash
python -m tools.release validate release/evidence --candidate 1.0.0rc1
python -m tools.release evaluate --candidate 1.0.0rc1 \
    --source-commit <sha> --evidence release/evidence
```

The evaluator returns `eligible_for_1_0_0 = false` with exact blocking codes
until every required evidence kind is present, every mandatory capability is
available, and every accepted limitation has a reviewed acceptance record.
