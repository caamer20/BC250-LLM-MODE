"""Release tooling CLI (C1 §4.6). Repository-only; never mutates runtime state.

Usage:
  python -m tools.release validate <evidence_dir>
  python -m tools.release evaluate --candidate 1.0.0rc1 --source-commit C
                                   [--source-ref refs/heads/main]
                                   [--repository local]
                                   [--evidence <dir>] [--artifacts <dir>]
  python -m tools.release manifest --candidate 1.0.0rc1 --output <path>
  python -m tools.release verify <manifest.json> <dist_dir>

Exit codes: 0 success / 1 gate-or-verification failure / 2 usage error.

G1 (§G1.2/§G1.3, RELEASE_GATE_AND_PIPELINE_REMEDIATION plan): ``evaluate``
REQUIRES ``--source-commit`` and constructs the immutable CandidateIdentity
against the REVIEWED default policy (a candidate whose policy digest does not
match is blocked by the evaluator, never silently re-bound). Without
``--artifacts`` the evaluation is diagnostics-only over an empty inventory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as `python -m tools.release` from the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bc250_llm_mode.release_artifacts import ArtifactInventory  # noqa: E402
from bc250_llm_mode.release_evidence import validate_evidence_record  # noqa: E402
from bc250_llm_mode.release_gate import (  # noqa: E402
    CandidateIdentity, evaluate_release,
)
from bc250_llm_mode.release_policy import default_release_policy  # noqa: E402
from bc250_llm_mode.release_state import (  # noqa: E402
    RELEASE_MANIFEST_SCHEMA_VERSION, ReleaseState, build_release_manifest,
)
from tools.release.artifacts import build_inventory  # noqa: E402
from tools.release.evidence_io import load_evidence_dir  # noqa: E402


def _cmd_validate(args: argparse.Namespace) -> int:
    records, errors = load_evidence_dir(args.evidence_dir)
    for name, reason in errors:
        print(f"REJECT {name}: {reason}")
    accepted = rejected = 0
    for record in records:
        ok, code = validate_evidence_record(
            record, candidate_version=args.candidate)
        if ok:
            accepted += 1
        else:
            rejected += 1
            print(f"REJECT {record.get('evidence_id', '?')}: {code}")
    print(f"accepted={accepted} rejected={rejected} io_errors={len(errors)}")
    return 0 if (not errors and rejected == 0) else 1


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
    # G1.2: without --artifacts this is diagnostics-only (empty inventory);
    # G3 makes --artifacts mandatory for RC/final evaluation.
    artifacts = (build_inventory(args.artifacts) if args.artifacts
                 else ArtifactInventory())
    decision = evaluate_release(
        evidence=records, candidate=candidate, artifacts=artifacts,
        policy=policy)
    print(json.dumps(decision.to_dict(), indent=2, sort_keys=True))
    print(f"eligible_for_rc={decision.eligible_for_rc} "
          f"eligible_for_1_0_0={decision.eligible_for_1_0_0}")
    return 0 if decision.eligible_for_1_0_0 else 1


def _cmd_manifest(args: argparse.Namespace) -> int:
    state = ReleaseState(version=args.candidate)
    doc = build_release_manifest(state)
    doc["manifest_schema_version"] = RELEASE_MANIFEST_SCHEMA_VERSION
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(f"wrote {out} (schema v{RELEASE_MANIFEST_SCHEMA_VERSION})")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print(f"manifest not found: {manifest_path}", file=sys.stderr)
        return 2
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    if doc.get("manifest_schema_version") != RELEASE_MANIFEST_SCHEMA_VERSION:
        print("manifest schema mismatch", file=sys.stderr)
        return 1
    inventory = build_inventory(args.dist_dir)
    print(json.dumps({"manifest_version": doc.get("version"),
                      "manifest_schema_version": doc.get("manifest_schema_version"),
                      "artifacts": inventory.to_dict()["artifacts"]},
                     indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.release", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_val = sub.add_parser("validate", help="validate evidence records")
    p_val.add_argument("evidence_dir")
    p_val.add_argument("--candidate", required=True)
    p_val.set_defaults(func=_cmd_validate)

    p_eval = sub.add_parser("evaluate", help="run the release evaluator")
    p_eval.add_argument("--candidate", required=True)
    # G1: a sourceless evaluation no longer exists.
    p_eval.add_argument("--source-commit", required=True)
    p_eval.add_argument("--source-ref", default="refs/heads/main")
    p_eval.add_argument("--repository", default="local")
    p_eval.add_argument("--evidence", default=None)
    p_eval.add_argument("--artifacts", default=None)
    p_eval.set_defaults(func=_cmd_evaluate)

    p_man = sub.add_parser("manifest", help="write a v2 release manifest")
    p_man.add_argument("--candidate", required=True)
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
