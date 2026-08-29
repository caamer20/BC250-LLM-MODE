# External-evidence handoff packet (G6 §G6.3)

This packet defines how every REMAINING release-evidence kind must be
produced, verified, and imported for the BC-250 LLM MODE `1.0.0` candidate.
Developer-executable remediation G0–G5 is closed; everything below is
hardware/human/owner/CI-final-workflow gated.

**Evidence for one commit can never qualify another.** Every record binds a
full 40-char `source_commit`, the candidate version, the reviewed policy
digest, and the candidate `artifact_inventory_digest`. If ANY candidate code
changes after evidence collection, the affected evidence is INVALID and must
be recollected against the new candidate. Supersession (`supersedes_evidence_id`,
same kind only) is the only replacement path; historical records are never
edited or deleted.

**No PASS templates.** This packet contains empty checklists and schema
pointers only. A record is written to `release/evidence/` ONLY after the
event it describes actually happened and passed. Fabricated or pre-approved
results are prohibited (release-closure plan §1.3).

Record contract: evidence schema v2 in `release/evidence/README.md`
(18 mandatory fields, no unknown fields, bounds, kind contracts). Prohibited
content in ANY field: secrets/credentials (value-detected), prompt or
conversation content, non-contained paths. Allowed: scalar/summary
measurements, contained relative attachment hints, attestation references.
Import path: place the validated JSON in `release/evidence/`, then
`python -m tools.release validate release/evidence --candidate <version>
--source-commit <sha>`; the evaluator consumes it only after attestation
verification (`verify_evidence_attestation`). Max evidence age at evaluation:
90 days (`max_evidence_age_days`); soak minimum 24 h (`min_soak_hours`).
Approved verification mechanisms: `sigstore-bundle`, `gh-attestation`
(policy revision 3).

Candidate identity at the G6 dry run (recompute for the final candidate):

- candidate version / commit / ref: recorded in `AGENTS.md` (G6 closeout)
- artifact inventory digest: recorded in `AGENTS.md` (G6 closeout)
- policy digest: `sha256:1883cbfc7deb694a336b4e2163d8767550a3734e3a93b9f53471b41d15d9ed20`
  (policy revision 3, `release/policy-v3.json`)

## C4 — physical BC250 qualification (role: hardware operator, independent
of the code author)

Procedure: `release/RUNBOOK.md` + the C4 section of the release-closure
plan; run separately on physical AMD BC-250 / GFX1013 Bazzite and CachyOS
hosts with the supported 12/4 UMA split. Every record and screenshot must bind
to the exact package candidate; one host cannot qualify the other.

### HARDWARE_QUALIFICATION
- [ ] device identity + firmware/BIOS UMA split recorded
- [ ] desktop-menu launch opens exactly one native window
- [ ] five-chapter setup resumes correctly across relaunch/reboot
- [ ] exact disclaimer blocks mutation until acknowledged
- [ ] Home/Models/Chat/Activity/System/Settings/Help stay in the same window
- [ ] clean install → setup completes → server healthy
- [ ] Vulkan load + generation on a standard-layout GGUF
- [ ] measurements: device, driver, UMA split, first paint, idle/active RSS and
      CPU, repeated navigation growth, load/generation results
- [ ] screenshots: setup, Home, Models, native Chat, Activity, System, narrow
      window, keyboard focus, and reduced-motion preference
- [ ] result PASS signed by the operator (mechanism per policy)

### SOAK_TEST
- [ ] continuous operation 24–72 h (policy minimum 24 h)
- [ ] thermal behavior (latch events, safe resets), recovery from any fault
- [ ] measurements: duration hours, thermal events, recovery outcomes

### BACKUP_RESTORE_HARDWARE
- [ ] `backup create` → `backup verify` → `restore start` round trip on device
- [ ] live Linux `renameat2` publication path exercised
- [ ] post-restore inference verification (server healthy, model serves)
- [ ] measurements: round-trip result, exchange mechanism, post-restore check

## C5 — independent security review (role: reviewer INDEPENDENT of the
development line; sign-off is human)

### SECURITY_REVIEW
- [ ] scope: privilege boundaries, secrets handling, service/system changes,
      gateway/sharing surface, supply chain (SBOM + pins)
- [ ] findings + dispositions recorded; reviewer identity in `issuer`
- [ ] result PASS with reviewer attestation

## C6 — non-developer human acceptance (role: a NON-DEVELOPER operator)

### HUMAN_ACCEPTANCE
- [ ] setup, daily chat, operations, recovery, diagnostics exercised
- [ ] acceptance statement + operator identity in `issuer` (type: human)

## Owner acceptance of accepted limitations (role: repository owner)

### KNOWN_LIMITATION_ACCEPTANCE (model-conversion, DEFERRED_NOT_ADVERTISED)
- [ ] owner reviewed `release/scope-decision-model-conversion.md` (bound to
      policy v3 digest) and accepts the deferral for 1.0

## C8 — release approval + publication (role: repository owner; the final
controlled workflow generates the machine evidence)

### RELEASE_APPROVAL
- [ ] owner approval of the exact candidate + decision JSON (evaluator
      exit 0 at the requested level is a PRECONDITION, never a substitute)

### CI/final-workflow kinds (developer/CI-executable ON the final controlled
workflow only — generated and verified by `.github/workflows/release.yml`,
never locally): SOURCE_CHECKOUT, DEFAULT_TEST_SUITE, SLOW_SECURITY_STRESS,
CLEAN_WHEEL_SMOKE, UPGRADE_MATRIX, DOCUMENTATION_RECONCILIATION, SBOM,
BUILD_PROVENANCE, ARTIFACT_ATTESTATION.

### PACKAGE_PUBLISH_ATTESTATION / CONTAINER_IDENTITY
- Exist only when publication actually happens (owner-authorized). Until
  then they are pending by design; never pre-created.

## Rerun procedure after candidate changes

1. New candidate commit → rebuild the complete release set once (RUNBOOK §1).
2. Recompute inventory/SBOM/manifest digests; re-run `verify` + `evaluate`.
3. Every candidate-bound record whose `source_commit` or
   `artifact_inventory_digest` no longer matches is rejected automatically —
   recollect it. There is no manual override.
