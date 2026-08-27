# ADR 005 — Authenticated Integration Gateway

**Status:** Accepted (P4)
**Closes:** DEF-005, DEF-006
**Complements:** ADR 002 (durable operations), ADR 004 (immutable runtime
lifecycle), the P3 bounded execution platform (§9), plan §10 (P4).
**Scope:** the threat model and contract for the purpose-built gateway that
terminates tailnet/container inference traffic before it reaches the raw
llama.cpp backend, the scoped-credential model, the contained Open WebUI
topology, and digest-pinned container identity.

---

## 0. Problem

Before P4, Tailscale HTTPS sharing published the **raw loopback llama
backend** (`http://127.0.0.1:8080`) directly:

```text
          tailnet client
                │
                ▼
  tailscale serve :10000 ──► http://127.0.0.1:8080  (raw llama.cpp API)
```

Tailscale's network identity/ACLs are not application authentication. Any
device the operator admits to the tailnet can reach the model API without an
application credential. The management surface and the inference surface are
the same port. A single compromised or misconfigured integration client can
therefore issue arbitrary requests against a backend that is also the one
true appliance runtime. Separately, the managed Open WebUI image identity was
a mutable tag (`ghcr.io/open-webui/open-webui:v0.6.14`) with no digest lock
(DEF-006), so container software was not a reproducible, rollbackable
identity.

## 1. Trust boundaries and supported topology

### 1.1 Assets that must be protected

- model files and their provenance/alias metadata (ADR 003);
- the active llama.cpp runtime and any known-good lineage (ADR 004);
- thermal control, GPU/rebooot policy, and host tuning;
- operation control (cancel/resume/retry/recover) and activity history;
- prompt/completion confidentiality (never logged, never audited);
- the desktop account itself (no host command exposure through integrations);
- install-scoped credentials (never durable in plaintext, argv, or labels).

### 1.2 Trust classes

| Trust class | Client | Boundary |
|---|---|---|
| **Local desktop / CLI** | the installed `bc250-llm-mode` process and the local systemd service | loopback only; full local account authority by construction (it is the appliance owner) |
| **Approved tailnet client** | a device the operator intentionally admits to the tailnet | reaches ONLY the gateway; must present a valid install-scoped credential and a permitted capability scope |
| **Open WebUI container** | the managed podman container on its private network | reaches ONLY the gateway over the container bridge; no raw backend address; no host-network route |

### 1.3 Supported topology (post-P4)

```text
 local desktop / approved tailnet client / Open WebUI container
                          │      (TLS terminate + credential + scope policy)
                          ▼
            tailnet/loopback-bound AUTHENTICATED GATEWAY
                          │  (policy, rate/size limits, audit; fail-closed)
                          ▼
              127.0.0.1 llama.cpp backend (raw, loopback-only)
```

Open WebUI no longer receives `BACKEND_HOST=host.containers.internal:8080`.
It receives a gateway URL with a per-install credential. The gateway itself
is the only component that may speak to the loopback backend, and it refuses
to start or serve unless the expected backend identity is reachable.

### 1.4 Explicitly unsupported

- multi-tenant / shared-host SaaS deployment;
- privileged remote management (nothing exposes thermal/systemd/operation
  mutation over the gateway);
- public/Funnel exposure of inference or management;
- any unauthenticated client reaching the backend, even one that knows the
  loopback port;
- shipping a vendor image whose security posture cannot be brought to the
  plan §10.4 policy (wrapped with a documented exception or not shipped as
  production-supported).

## 2. Adversary model (threats)

| # | Threat | Adversary | Impact | Required disposition |
|---|---|---|---|---|
| T1 | Raw backend exposure | any tailnet/remote client | prompts/inference reachable without auth; management surface exposed | gateway required; raw backend loopback-only and never a serve target |
| T2 | Open WebUI container compromise | malicious/malformed container workload or image | pivot to host runtime/model/thermal/host control | private network only; no raw backend route; scoped gateway credential; no host net; dropped caps; read-only root |
| T3 | Credential theft/replay | any network actor | impersonate a legit client | install-scoped credential, rotation/revoke, constant-time compare, no recycle of revoked tokens |
| T4 | Prompt/completion disclosure | any actor reaching logs/audit/support | user privacy breach | gateway audit records actor/scope/request-id/outcome ONLY, never content |
| T5 | DoS / resource exhaustion | malicious or faulty client | render appliance unusable, exhaust memory/battery/VRAM | per-client concurrency, request/token/body/stream caps, reject oversized context, bounded headers |
| T6 | Mutable image identity | supply-chain drift, rollback invalidation | silently different software, broken rollback | digest-pinned images, digest-verified before start/status, explicit update operation |
| T7 | Privilege escalation from integration | compromised Open WebUI | host control | dropped caps, no-new-privileges, non-root, read-only root, no privileged/host-net/device mounts, seccomp |
| T8 | Credential in argv/logs/labels | any observer | permanent secret disclosure | credential rides a 0600 file/env, never argv or container label, never logged |

## 3. Gateway design decisions

### 3.1 D1 — a small, purpose-built gateway, not the management API

Implement one `gateway.py` module that:

- listens ONLY on configured loopback or tailnet addresses (never `0.0.0.0`
  unless the operator has explicitly bound it and the binding is recorded);
- accepts only the supported scopes (`inference:read`,
  `inference:stream`, optionally `models:list`);
- denies every management action (runtime update/rollback, thermal control,
  filesystem access, operation mutation, host commands, backup/restore);
- requires a valid install-scoped credential on EVERY request (constant-time
  comparison) with bounded header size;
- enforces per-client concurrency, request, token, body, and stream caps;
- rejects oversized context, unsupported parameters, unknown model IDs, and
  invalid content types before reaching the backend;
- sets secure response headers and never reflects sensitive request data;
- emits audit events containing actor/scope/request-id/outcome, never
  prompt/completion content;
- fails closed when the expected backend identity is unreachable or
  mismatched;
- exposes a `/health` endpoint reporting gateway readiness and (redacted)
  backend identity without leaking credentials or arbitrary host data.

### 3.2 D2 — the gateway is the only bridge to the backend

- Production HTTP callers that serve remote/container traffic reach the
  backend **only** through the gateway adapter.
- `sharing.py`, `openwebui.py`, and the tailnet serve target never use
  `http://127.0.0.1:8080` as a publish target; they use the gateway target.
- The raw backend remains loopback-only (already `--host 127.0.0.1`); it is
  removed from any serve/integration publication.
- An AST guard forbids raw `127.0.0.1:8080` serve/publication targets in the
  integration modules (mirroring the bounded-execution inventory census).

### 3.3 D3 — scoped credentials with rotation/revocation

- On provisioning, generate a per-install secret, store it in a 0600
  config/env file within the profile (never argv/logs/labels); record a
  non-secret credential *fingerprint* (sha256 of the secret) in SQLite so
  revocation/rotation is durable and auditable without storing the secret.
- Comparison is constant-time (hmac.compare_digest).
- Revoke/rotate is a durable, idempotent service operation; a rotated secret
  invalidates the old fingerprint immediately.

### 3.4 D4 — Open WebUI contained topology

- Stays on the private `bc250-openwebui` network; the container receives a
  gateway URL + scoped credential, not the raw backend address.
- The legacy host-network container is migrated (named volume preserved)
  before it may start (already implemented in openwebui.py; D4 keeps it and
  adds the gateway target).
- A disabled/`pending-gateway` backend route is reported truthfully; sharing
  refuses before mutation unless the gateway is provisioned and verified.

### 3.5 D5 — digest-pinned container identity (DEF-006)

- Managed images use an immutable `@sha256:` digest reference, not a mutable
  tag, for the production default.
- The registry, digest, source revision, build timestamp, and scan result are
  recorded in a durable identity record (mirroring ADR 004 manifest
  identity).
- Digest is verified before `start` and during `status`; mismatch refuses
  start with a recovery story.
- Container security posture: non-root where supported, read-only root
  filesystem with explicit writable volume/tmpfs, all capabilities dropped,
  no-new-privileges, non-host networking, no privileged/host-PID/devices/
  arbitrary bind mounts, bounded memory/CPU/PID/file-size.
- Image changes are an explicit durable operation with verification/rollback,
  never a silent tag pull.

## 4. Mutable-tag disposition

The plan (§16.1) requires explicit version spaces. For integration images
this ADR mandates:

- image identity = `registry/repo@sha256:<digest>` (the identity);
- tag display = human metadata only, never an identity;
- upgrade = an intentional, verifiable operation that recomputes/re-records
  the digest and is rolled back like any durable runtime operation.

Until an image digest is pinned and verified, the integration is reported as
`pending-gateway` / unverified and is not production-advertised as remote-safe.

## 5. Rejected alternatives

| Alternative | Why rejected |
|---|---|
| Expose the Open WebUI API/management over the same port | The management surface is far larger than inference; we cannot scope it down safely. |
| Rely on Tailscale ACLs as the only auth | Tailscale is network identity, not application authentication (DEF-005). |
| Ship two backends (public inference + private management) | More surfaces, more TLS/config duplication; the gateway concentrates policy. |
| Use a vendor OAuth gateway/proxy | Extra moving parts, indirection, and opaque behavior; a purpose-built constant-time gateway is smaller and auditable. |
| Keep mutable tags and only scan them | Underausable: nothing binds the scanned/rollback identity to a concrete image digest. |

## 6. Consistency with prior ADRs

- ADR 002: gateway audit respects the durable-event contract (no content,
  bounded, correlated by request/operation id).
- ADR 004: gateway health confirms the **verified** active runtime (identity
  chain), never the desired value; fail-closed if the expected backend
  identity chain is missing.
- P3 (§9): all gateway process/HTTP effects are bounded (typed
  `httpx.Timeout`/process port); the gateway itself is a bounded, cancellable
  adapter, never a long-lived uncontrolled handler.

## 7. Exit criterion (plan §10.4)

- This ADR is approved and documented (this file).
- Raw backend API is unreachable from the supported remote/container
  topology.
- Requests without valid credentials/scopes fail closed.
- Scope, rate-limit, size-limit, replay, credential rotation, revoke, and
  audit tests pass.
- Open WebUI performs supported inference through the gateway and cannot
  perform management actions.
- Mutable image tags are absent from production defaults; digest mismatch
  refuses start.
- Container security canaries confirm no host network, privilege escalation,
  secret leakage, or unrestricted egress.
- A clean install and a safe upgrade preserve the named volume without
  preserving unsafe topology.

## 8. Pending hardware/operator evidence (never fabricated)

The following P4 items require a real podman/tailscale/BC250 environment or a
non-developer operator and are therefore **explicit pending-evidence**, not
claimed complete on this repo alone:

- live Open WebUI gateway round-trip and management-denial on the container
  bridge;
- tailnet client credential/scope behavior over a live tailnet;
- container security canaries against a running managed image;
- human acceptance of the first-run "remote access is disabled until you
  enable and verify it" flow.

The in-repo contract, gate, digest-pin, and canned-canary tests are the
progress evidence; hardware/operator results update this section only when
observed on supported hardware.