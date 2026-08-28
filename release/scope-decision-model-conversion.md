# Scope decision: model conversion — DEFERRED_NOT_ADVERTISED

- **Milestone:** C7 (§C7.2, V1_0_RELEASE_CLOSURE_IMPLEMENTATION_PLAN.md)
- **Issue:** REL-008 — model conversion represented as a known-unavailable
  capability.
- **Decision:** `DEFERRED_NOT_ADVERTISED` for the `1.0.0` support promise.
- **Bound to:** release policy version **2**, policy digest
  `sha256:00a6d4e194767da7bc089a16108e333f9b10e85ec851cf3cb83781afd2ce6c9e`
  (`release/policy-v2.json`); package version at decision time `0.9.0.dev0`.

## Decision

Model conversion (producing a GGUF from a non-GGUF source format via a local
converter) is **outside the 1.0 support promise** and is **not advertised** in
product claims or the primary UI. No pinned, verified converter ships in this
build, and the plan's C7.2 implementation gates (pinned converter source +
binary/container digest + toolchain + license, bounded typed-argv execution,
operation-owned contained paths, cancellation, output verification, crash/
takeover matrix, secret/path canaries, clean-wheel execution, and physical BC250
activation/inference qualification of converted output) are NOT met before RC
freeze. Selecting the implementation route merely to remove a release-manifest
warning is explicitly prohibited by plan §C7.2.

## Supported model-ingestion paths in 1.0

1. **Direct GGUF acquisition** (catalog download, digest-verified).
2. **Local GGUF import** (user-supplied GGUF, validated + quarantined on
   failure).

These remain the only supported ways to add a model in 1.0.

## Product-surface consequences (enforced + tested)

- The `MODEL_CONVERT` operation type keeps its versioned namespace and request
  contract, but **no workflow is registered and no converter ships** (P6.4).
- `ModelConvertCommandService` refuses every request **before any external
  effect** with a clear capability status (`UNAVAILABLE` + honest reason), never
  a generic failure.
- The `convert-model` CLI verb reports `UNAVAILABLE` (exit 1) and is not
  advertised as a supported feature.
- Conversion is not present in primary GUI affordances or feature lists.
- The release manifest keeps `model-conversion` visible as the single genuinely
  unavailable capability (honesty), classified `DEFERRED_NOT_ADVERTISED`.

## What this decision requires to be revisited

To move model conversion into the 1.0 (or a later) support promise, ALL of plan
§C7.2's implementation gates must be met: a pinned converter (source + binary/
container digest + toolchain + license), typed-argv-only execution (no shell),
operation-owned contained input/output paths, bounded CPU/memory/wall-time/
output, cancellation with retained labeled partials, GGUF structure + digest
verification before publication, no-replace artifact publication + alias
transaction, a crash/takeover matrix with exact conversion/publication counts,
secret/path canaries + clean-wheel execution, and physical BC250 activation/
inference qualification of converted output — plus a re-issued, reviewed scope
record and policy revision.

## Acceptance evidence

This limitation is classified in the release policy (`release/policy-v3.json`)
and is enforced by the release gate: a `1.0.0` tag requires a reviewed
`KNOWN_LIMITATION_ACCEPTANCE` evidence record covering `model-conversion`
(`LIMITATION_ACCEPTANCE_MISSING` blocks otherwise). That acceptance record is
human/owner-gated and is NOT fabricated here; this document is the reviewed
scope-decision artifact it references. Formal signing of this record is
owner-gated (plan §1.3: no fabricated evidence).

## Amendment — re-binding to release policy revision 3 (G2)

- **Date:** 2026-08-28
- **Milestone:** G2 (§G2.4, RELEASE_GATE_AND_PIPELINE_REMEDIATION_IMPLEMENTATION_PLAN.md)
- **Change:** Release policy content revision 3 adds the approved attestation
  verification mechanisms (`sigstore-bundle`, `gh-attestation`) to the policy
  digest input. The decision above is unchanged; only the binding is re-issued.
- **Re-bound to:** release policy version **3**, policy digest
  `sha256:1883cbfc7deb694a336b4e2163d8767550a3734e3a93b9f53471b41d15d9ed20`
  (`release/policy-v3.json`). The v2 binding
  (`sha256:00a6d4e194767da7bc089a16108e333f9b10e85ec851cf3cb83781afd2ce6c9e`,
  `release/policy-v2.json`) is superseded by this amendment; `policy-v1.json`
  and `policy-v2.json` remain immutable history.
