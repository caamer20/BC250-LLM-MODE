"""Release tooling CLI (C1 §4.6). Repository-only; never mutates runtime state.

Usage:
  python -m tools.release validate <evidence_dir> --candidate V --source-commit C
  python -m tools.release evaluate --candidate 1.0.0rc1 --source-commit C
                                   --artifacts <dir> --level {rc,final}
                                   [--source-ref refs/heads/main]
                                   [--repository local] [--evidence <dir>]
  python -m tools.release manifest --candidate 1.0.0rc1 --source-commit C
                                   --artifacts <dir> --output <path>
                                   [--level {draft,final}] [--evidence <dir>]
  python -m tools.release verify <manifest.json> <dist_dir>

Exit codes: 0 success / 1 gate-or-verification failure / 2 usage error.

G1 (§G1.2/§G1.3, RELEASE_GATE_AND_PIPELINE_REMEDIATION plan): ``evaluate``
REQUIRES ``--source-commit`` and constructs the immutable CandidateIdentity
against the REVIEWED default policy (a candidate whose policy digest does not
match is blocked by the evaluator, never silently re-bound).

G3 (§G3.3–§G3.5): ``evaluate`` REQUIRES ``--artifacts`` (actually consumed)
and ``--level`` (exit 0 only for the requested level); stdout is ONLY the
JSON decision. ``manifest`` is decision-derived schema v3 (drafts of blocked
candidates are labeled BLOCKED; ``--level final`` refuses ineligible
candidates). ``verify`` performs full comparison: inventory equality,
checksums cross-check, SBOM subject == actual wheel digest, manifest digest
integrity — any mismatch exits non-zero.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# Allow running as `python -m tools.release` from the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bc250_llm_mode.release_evidence import (  # noqa: E402
    validate_evidence_record, validate_evidence_set,
)
from bc250_llm_mode.release_gate import (  # noqa: E402
    CandidateIdentity, evaluate_release,
)
from bc250_llm_mode.release_manifest import (  # noqa: E402
    RELEASE_MANIFEST_NAME,
    RELEASE_MANIFEST_SCHEMA_VERSION,
    build_release_manifest,
)
from bc250_llm_mode.release_policy import default_release_policy  # noqa: E402
from tools.release.artifacts import build_inventory, sha256_file  # noqa: E402
from tools.release.evidence_io import load_evidence_dir  # noqa: E402
from tools.release.sbom import validate_sbom  # noqa: E402


def _cmd_validate(args: argparse.Namespace) -> int:
    records, errors = load_evidence_dir(args.evidence_dir)
    for name, reason in errors:
        print(f"REJECT {name}: {reason}")
    # G2: validation is candidate-bound — commit + reviewed policy digest are
    # mandatory binding inputs.
    policy = default_release_policy()
    accepted = rejected = 0
    for record in records:
        ok, code = validate_evidence_record(
            record, candidate_version=args.candidate,
            source_commit=args.source_commit,
            policy_digest=policy.policy_digest())
        if ok:
            accepted += 1
        else:
            rejected += 1
            print(f"REJECT {record.get('evidence_id', '?')}: {code}")
    set_ok, problems = validate_evidence_set(
        records, candidate_version=args.candidate,
        source_commit=args.source_commit,
        policy_digest=policy.policy_digest())
    for problem in problems:
        print(f"SET {problem}")
    print(f"accepted={accepted} rejected={rejected} io_errors={len(errors)}")
    return 0 if (not errors and rejected == 0 and set_ok) else 1


def _cmd_evaluate(args: argparse.Namespace) -> int:
    records, errors = load_evidence_dir(args.evidence) if args.evidence else ([], [])
    for name, reason in errors:
        print(f"REJECT {name}: {reason}", file=sys.stderr)
    # G1.3: production tooling evaluates against the REVIEWED policy only.
    policy = default_release_policy()
    try:
        candidate = CandidateIdentity(
            version=args.candidate,
            source_commit=args.source_commit,
            source_ref=args.source_ref,
            repository=args.repository,
            policy_digest=policy.policy_digest())
    except ValueError as exc:
        print(f"invalid candidate identity: {exc}", file=sys.stderr)
        return 2
    # G3: the artifact directory is MANDATORY and actually consumed.
    try:
        artifacts = build_inventory(args.artifacts)
    except ValueError as exc:
        print(f"invalid artifact inventory: {exc}", file=sys.stderr)
        return 2
    decision = evaluate_release(
        evidence=records, candidate=candidate, artifacts=artifacts,
        policy=policy)
    # G3 §G3.3: stdout is ONLY the JSON decision; diagnostics go to stderr.
    print(json.dumps(decision.to_dict(), indent=2, sort_keys=True))
    eligible = (decision.eligible_for_1_0_0 if args.level == "final"
                else decision.eligible_for_rc)
    print(f"level={args.level} eligible={eligible}", file=sys.stderr)
    return 0 if eligible else 1


def _cmd_manifest(args: argparse.Namespace) -> int:
    """G3 §G3.4: the manifest is DERIVED FROM the evaluator's decision over a
    full candidate identity + artifact inventory (never caller booleans)."""
    policy = default_release_policy()
    try:
        candidate = CandidateIdentity(
            version=args.candidate,
            source_commit=args.source_commit,
            source_ref=args.source_ref,
            repository=args.repository,
            policy_digest=policy.policy_digest())
    except ValueError as exc:
        print(f"invalid candidate identity: {exc}", file=sys.stderr)
        return 2
    try:
        artifacts = build_inventory(args.artifacts)
    except ValueError as exc:
        print(f"invalid artifact inventory: {exc}", file=sys.stderr)
        return 2
    records, errors = load_evidence_dir(args.evidence) if args.evidence else ([], [])
    for name, reason in errors:
        print(f"REJECT {name}: {reason}", file=sys.stderr)
    decision = evaluate_release(
        evidence=records, candidate=candidate, artifacts=artifacts,
        policy=policy)
    try:
        doc = build_release_manifest(
            decision=decision, inventory=artifacts,
            sbom_digest=args.sbom_digest, final=(args.level == "final"))
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(f"wrote {out} (schema v{RELEASE_MANIFEST_SCHEMA_VERSION}, "
          f"status {doc['release_status']})", file=sys.stderr)
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    """G3 §G3.5: full-comparison verification (never print-and-pass).

    Compares the manifest's recorded inventory against the actual dist
    contents (added/removed/mutated artifacts all fail), cross-checks the
    checksums file against real file digests, validates the SBOM against the
    ACTUAL wheel digest, and verifies the manifest's own digest. The manifest
    file itself is excluded from the comparison.
    """
    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print(f"manifest not found: {manifest_path}", file=sys.stderr)
        return 2
    try:
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"manifest unreadable: {exc}", file=sys.stderr)
        return 1
    if not isinstance(doc, dict) or \
            doc.get("manifest_schema_version") != RELEASE_MANIFEST_SCHEMA_VERSION:
        print("manifest schema mismatch", file=sys.stderr)
        return 1

    problems: list[str] = []

    # Candidate binding fields must be present (fail-closed).
    for binding_field in ("source_commit", "source_ref", "policy_digest",
                          "inventory_digest"):
        if not doc.get(binding_field):
            problems.append(f"manifest missing binding field {binding_field}")

    recorded: dict[str, dict] = {
        a["name"]: a
        for a in (doc.get("inventory") or {}).get("artifacts", [])
        if isinstance(a, dict) and a.get("name")}
    try:
        actual_inv = build_inventory(args.dist_dir)
    except ValueError as exc:
        print(f"invalid artifact inventory: {exc}", file=sys.stderr)
        return 1
    actual = {a.name: a for a in actual_inv.artifacts
              if a.name != RELEASE_MANIFEST_NAME}

    # 1. Exact inventory comparison: added / removed / mutated artifacts.
    for name in sorted(set(recorded) | set(actual)):
        rec, act = recorded.get(name), actual.get(name)
        if rec is None:
            problems.append(f"added artifact not in manifest: {name}")
        elif act is None:
            problems.append(f"manifest artifact missing from dist: {name}")
        elif (rec.get("sha256") != act.sha256
              or rec.get("size") != act.size
              or rec.get("role", "") != act.role):
            problems.append(f"artifact mutated: {name}")

    # 2. Release-set completeness (wheel + checksums + SBOM are mandatory).
    recorded_roles = {a.get("role", "") for a in recorded.values()}
    for required_role in ("python-wheel", "checksums", "cyclonedx-sbom"):
        if required_role not in recorded_roles:
            problems.append(
                f"ARTIFACT_INVENTORY_INCOMPLETE: missing role {required_role}")

    # 3. Checksums file cross-check against real file digests.
    dist_root = Path(args.dist_dir)
    checksums = actual.get("checksums.sha256")
    if checksums is not None:
        for line in (dist_root / checksums.name).read_text(
                encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 2:
                problems.append(f"malformed checksums line: {line[:60]}")
                continue
            listed_sha, listed_name = parts
            listed_path = dist_root / listed_name
            if not listed_path.is_file():
                problems.append(f"checksums entry missing file: {listed_name}")
            elif sha256_file(listed_path) != listed_sha:
                problems.append(f"checksums digest mismatch: {listed_name}")

    # 4. SBOM subject must equal the ACTUAL wheel digest (audit finding 8).
    sbom_artifact = actual.get("sbom.cdx.json")
    wheel = next((a for a in actual.values() if a.role == "python-wheel"), None)
    if sbom_artifact is not None:
        try:
            sbom_doc = json.loads((dist_root / sbom_artifact.name).read_text(
                encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"sbom unreadable: {exc}")
            sbom_doc = None
        if sbom_doc is not None:
            sbom_package = ((sbom_doc.get("metadata") or {}).get("component")
                            or {}).get("name", "")
            ok, code = validate_sbom(
                sbom_doc, required_dependencies=[],
                package_name=sbom_package,
                expected_subject_sha256=(wheel.sha256 if wheel else ""))
            if not ok:
                problems.append(f"sbom validation failed: {code}")

    # 5. Manifest digest integrity.
    claimed_digest = doc.get("manifest_digest")
    body = {k: v for k, v in doc.items() if k != "manifest_digest"}
    actual_digest = "sha256:" + hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")).hexdigest()
    if claimed_digest != actual_digest:
        problems.append("manifest digest mismatch")

    if problems:
        for problem in problems:
            print(f"VERIFY FAIL: {problem}", file=sys.stderr)
        return 1

    print(json.dumps({"manifest_version": doc.get("version"),
                      "manifest_schema_version":
                          doc.get("manifest_schema_version"),
                      "artifacts": [a.to_dict()
                                    for a in sorted(
                                        actual.values(),
                                        key=lambda a: a.name)]},
                     indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.release", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_val = sub.add_parser("validate", help="validate evidence records")
    p_val.add_argument("evidence_dir")
    p_val.add_argument("--candidate", required=True)
    # G2: candidate-bound validation requires the full commit.
    p_val.add_argument("--source-commit", required=True)
    p_val.set_defaults(func=_cmd_validate)

    p_eval = sub.add_parser("evaluate", help="run the release evaluator")
    p_eval.add_argument("--candidate", required=True)
    # G1: a sourceless evaluation no longer exists.
    p_eval.add_argument("--source-commit", required=True)
    p_eval.add_argument("--source-ref", default="refs/heads/main")
    p_eval.add_argument("--repository", default="local")
    p_eval.add_argument("--evidence", default=None)
    # G3: the artifact directory is mandatory and actually consumed; the
    # qualification level selects the exit-code semantics.
    p_eval.add_argument("--artifacts", required=True)
    p_eval.add_argument("--level", required=True, choices=["rc", "final"])
    p_eval.set_defaults(func=_cmd_evaluate)

    p_man = sub.add_parser(
        "manifest", help="write a decision-derived v3 release manifest")
    p_man.add_argument("--candidate", required=True)
    p_man.add_argument("--source-commit", required=True)
    p_man.add_argument("--source-ref", default="refs/heads/main")
    p_man.add_argument("--repository", default="local")
    p_man.add_argument("--artifacts", required=True)
    p_man.add_argument("--evidence", default=None)
    p_man.add_argument("--sbom-digest", default=None)
    p_man.add_argument("--level", default="draft", choices=["draft", "final"])
    p_man.add_argument("--output", required=True)
    p_man.set_defaults(func=_cmd_manifest)

    p_ver = sub.add_parser("verify", help="verify a manifest against dist/")
    p_ver.add_argument("manifest")
    p_ver.add_argument("dist_dir")
    p_ver.set_defaults(func=_cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
