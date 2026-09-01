# ADR 014 — Appliance Readiness and Integration Lifecycle

**Status:** Accepted for EUF implementation

**Complements:** ADR 002 durable operations, ADR 004 runtime identity, ADR 005
authenticated gateway, ADR 009 named clients, ADR 012 guided repair, ADR 013
two-slot application updates.

**Scope:** truthful readiness, production ownership of the gateway process,
transactional Open WebUI convergence, and the current-boot integration
lifecycle.

---

## 1. Problem

The earlier contracts correctly defined the supported security topology, but
three implementation boundaries were incomplete:

1. process state was sometimes presented as usability state;
2. the gateway had policy and a live socket fixture but no packaged production
   process owner; and
3. the live socket handler buffered an upstream SSE body and labeled it JSON.

On a physical BC250 this allowed a container to report `running` while port
3000 refused connections, a credential to verify while nothing listened on
9071, and Open WebUI to create an empty assistant message after receiving
buffered SSE text as JSON. An interrupted legacy-container migration and
host-specific Podman/SELinux requirements also required manual repair.

The successful manual repair proves the diagnosis only. A temporary unit,
in-place installed-module edit, and manually reconciled Open WebUI provider are
not a product lifecycle or release candidate.

## 2. Decision D1 — readiness is layered truth

Every component reports distinct levels:

- **process:** service/container state;
- **protocol:** bounded endpoint response plus expected identity; and
- **journey:** the user-intended route, including authentication, model alias,
  streaming, and terminal event.

Closed states are `ABSENT`, `STOPPED`, `STARTING`, `READY`, `DEGRADED`,
`BLOCKED`, and `UNKNOWN`. A process cannot imply protocol or journey success.
Remote-client **Ready** requires a fresh explicit negative-auth probe, positive
models probe, and streamed chat bound to the current model invocation,
credential generation, gateway runtime, Serve mapping, and client-card version.

Normal refresh is query-only and bounded. It may perform lightweight local
observations but never starts a component, runs inference, writes freshness, or
mutates a credential. Expensive journey checks are explicit operations. Any
identity-changing event stales dependent evidence.

Native Chat readiness depends only on the local verified model path. Optional
Open WebUI/Tailscale state cannot make native Chat unhealthy.

## 3. Decision D2 — one shared gateway request preparation path

Buffered and streaming gateway adapters use the same sequence:

1. bounded headers/body;
2. endpoint classification;
3. constant-time credential authentication;
4. required scope;
5. JSON/token/context bounds;
6. expected backend readiness;
7. per-principal rate and concurrency reservation; and
8. content-free audit completion and slot release.

The adapter must release an acquired slot exactly once on success, denial,
backend failure, oversized response, timeout, or client disconnect.

For `stream=true`, the live handler opens the upstream response before sending
downstream headers, preserves the upstream status and content type, forwards
bytes as they arrive, flushes each bounded chunk, closes on overflow, and never
adds a `Content-Length` for a connection-delimited stream. The first event must
be observable before the upstream response completes. `[DONE]` is forwarded,
not synthesized.

The gateway bearer credential authenticates the gateway client. It is never
forwarded to the raw loopback backend.

## 4. Decision D3 — the gateway has one packaged current-boot owner

The package gains one runtime entry point and one generated systemd unit. The
unit is installed but disabled for boot. It starts only from an explicit
current-boot integration action and never uses a dependency that silently
starts inference on normal boot.

The runtime binds only loopback and the observed private Open WebUI Podman
bridge address. It never binds a wildcard/LAN address and never hard-codes a
bridge address. Missing, ambiguous, host/default, or changed network topology
fails closed.

The runtime uses ADR 009 named-client authentication. Secret bytes remain in
mode-0600 files and never enter unit text, argv, environment, journal, support
bundles, or durable events. The active application slot owns the executable;
application update and rollback regenerate/verify the unit without changing
its current running or boot-enabled state.

An exact recognized diagnostic unit may be migrated transactionally. An
unfamiliar unit or listener on the managed port is never overwritten or
removed.

The physical live repair observed on 2026-09-01 is frozen by full byte
identity, not its filename: unit SHA-256
`93189f4531e60fcae7752183979cb97ec281d7eaff7947ece73fe972cd049a97`
and runner SHA-256
`96365758dec9fd81c66545e20b8c69da2d39b66dbe3c0bbfb1ec618b4bc48f71`.
Only those exact bytes may use the diagnostic migration path. The canonical
service must establish both listeners and answer bounded health before the
exact runner is removed. A mismatch is retained for support review.

## 5. Decision D4 — Open WebUI converges transactionally

Open WebUI has one validated container specification: immutable image digest,
dedicated private network, loopback-only UI publish, approved data source,
numeric Podman limits, required writable paths, app-owned secret-key and client
credential mounts, SELinux relabel semantics, dropped capabilities,
no-new-privileges, and no boot autostart.

Create/update/migration verifies the data source and candidate specification
before stopping the old container. The old container remains a rollback target
until the candidate passes application HTTP readiness, provider reconciliation,
model discovery, and authenticated streamed chat. No volume-removal flag is
used. Failed stop, promotion, configuration, or verification enters a typed
recovery state and restores the prior running state when safe.

Provider reconciliation is versioned to the pinned image and changes only the
app-owned gateway provider. It preserves unrelated user providers and never
logs or serializes keys in normal output.

## 6. Decision D5 — current-boot dependency order

The explicit integration operation owns this order:

```text
model → gateway → Open WebUI (optional) → Tailscale Serve → verification
```

Compensation removes or stops only effects newly created by that operation.
Serve continues to target the gateway, never raw port 8080. Funnel remains off.
Stopping sharing removes its mappings but does not delete data or revoke named
clients. A normal reboot returns to graphical desktop with model, gateway, and
Open WebUI stopped.

## 7. Privacy and audit

Prompts, completions, raw request bodies, credentials, authorization headers,
Open WebUI sessions, and conversation content are forbidden from logs, audit,
operations, notifications, metrics, and support bundles. Audit contains only
bounded actor/client ID, scope, request ID, method, classified path, outcome,
status, and time.

Fixed non-sensitive qualification prompts may be used by explicit probes; no
response body is persisted.

## 8. Rejected alternatives

- Calling a service/container **Ready** from `active`/`running` alone.
- Running the gateway from an ad-hoc `python -c` or GUI-owned thread.
- Enabling model/gateway/Open WebUI automatically at boot.
- Binding 9071 to `0.0.0.0` to avoid bridge discovery.
- Forwarding the gateway bearer credential to llama.cpp.
- Disabling Open WebUI containment or read-only policy without an ADR/security
  review.
- Editing Open WebUI's database with an unversioned script.
- Treating successful developer socket fixtures as physical client evidence.

## 9. Exit criteria

- Buffered and SSE live-socket tests prove the shared policy, bounded streaming,
  first-event delivery, terminal event, exact slot release, and content-free
  audit.
- One packaged disabled-at-boot unit owns the gateway on source/editable/wheel
  and both application slots.
- Socket inspection proves only loopback/private-bridge listeners.
- Transactional Open WebUI tests and physical journeys preserve existing data
  across update, interruption, rollback, and uninstall-by-default.
- Home, Connections, System, Doctor, CLI, and support metadata consume one
  readiness projection.
- Bazzite/CachyOS fresh/upgrade, second-device, security, resource, human, soak,
  and release evidence binds one exact candidate. Until then the release and
  physical experience remain pending.
