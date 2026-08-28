# Release operator runbook (G5 §G5.2)

Exact commands for the remediated release pipeline. The authoritative
evaluator (`bc250_llm_mode.release_gate.evaluate_release`, driven via
`python -m tools.release`) is the ONLY eligibility authority — no shell flag,
workflow input, or environment approval can override it. Publication has NOT
been performed; it remains owner-gated (C8).

Status vocabulary (G5.1): **implemented** (code + developer tests pass),
**developer-qualified** (all executable local/CI checks pass), **evidence
pending** (hardware/human/external evidence absent), **release blocked**
(a policy-required item is unsatisfied), **published** (exact artifacts
externally published and verified). Current release status: **release
blocked** — `eligible_for_1_0_0 = false` with exact codes (C4/C5/C6/C8
evidence pending).

## 1. Cut an RC candidate

```bash
# From a tracked-clean checkout at the candidate commit (owner untracked
# files may exist; they are classified in AGENTS.md and never committed):
COMMIT=$(git rev-parse HEAD)
python -m build                       # build wheel + sdist EXACTLY ONCE

# Emit the complete release set into dist/:
python - <<'PY'
import json
from pathlib import Path
from tools.release.artifacts import build_inventory
from tools.release.sbom import build_sbom, parse_pyproject_dependencies
dist = Path("dist")
inv = build_inventory(dist)
(dist / "checksums.sha256").write_text(
    "".join(f"{a.sha256}  {a.name}\n" for a in inv.artifacts))
deps = parse_pyproject_dependencies(
    Path("pyproject.toml").read_text(encoding="utf-8"))
wheel = next(a for a in inv.artifacts if a.name.endswith(".whl"))
sbom = build_sbom(package_name="bc250-llm-mode",
                  package_version="<candidate-version>",
                  dependencies=deps,
                  build_requires=[("setuptools", ">=68")],
                  subject_sha256=wheel.sha256)
(dist / "sbom.cdx.json").write_text(json.dumps(sbom, sort_keys=True, indent=2))
(dist / "inventory.json").write_text(
    json.dumps(build_inventory(dist).to_dict(), sort_keys=True, indent=2))
PY

python -m tools.release manifest \
    --candidate <candidate-version> \
    --source-commit "$COMMIT" \
    --source-ref refs/heads/main \
    --artifacts dist \
    --output dist/release-manifest.json     # draft: release_status=BLOCKED

python -m tools.release verify dist/release-manifest.json dist
```

Or run the whole chain through `.github/workflows/release.yml`
(`workflow_dispatch` with `candidate_version`, `candidate_ref`,
`qualification_level=rc`).

## 2. Evidence ingest (never fabricate)

Real evidence only — each record is added to `release/evidence/` AFTER the
event it describes actually happened (see `release/evidence/README.md` for
the schema-v2 contract + verification boundary). Then:

```bash
python -m tools.release validate release/evidence \
    --candidate <candidate-version> --source-commit "$COMMIT"
```

## 3. Final evaluation (the gate)

```bash
python -m tools.release evaluate \
    --candidate <candidate-version> \
    --source-commit "$COMMIT" \
    --source-ref refs/heads/main \
    --artifacts dist \
    --level rc \
    --evidence release/evidence
```

Exit 0 only if the candidate is eligible at the requested level. Stdout is
ONLY the JSON decision; keep it as the release-decision artifact. A final
version must ride exactly `refs/tags/v<version>` (final-tag rule).

## 4. Approval

The `release-approval` environment (required reviewers configured in the
repository settings) gates the publish job and runs ONLY after the
`final-evaluation` job is eligible — environment approval can never override
the evaluator. Attestations are verified (`verify-attestations` job) BEFORE
approval.

## 5. Publish (owner-authorized; NOT yet performed)

The publish job downloads the exactly named artifact bundle (never a
wildcard), re-runs `python -m tools.release verify` over the manifest +
artifacts, and re-runs the evaluator at `--level final` as the release-state
blocker. The actual PyPI Trusted Publishing / GitHub Releases upload is added
only with explicit owner authorization (C8) — until then the pipeline stops
at the evaluator/approval boundary by design.

## Recovery

- Any verify/evaluate failure: fix forward; never edit records or digests.
- Superseded evidence: add a new record with `supersedes_evidence_id`
  (same kind only); never delete the historical record.
- Policy changes: new content revision + new `release/policy-vN.json`
  snapshot + dated amendment where a decision record binds the old digest.
  Historical snapshots and ADRs are immutable.
