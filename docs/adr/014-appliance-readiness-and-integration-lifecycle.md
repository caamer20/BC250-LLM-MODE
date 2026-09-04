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

For pinned Open WebUI 0.11.3, the supported container environment does not
implement `OPENAI_API_KEY_FROM_FILE`. The frozen adapter therefore sends a
fixed, secret-free Python program over `podman exec -i` stdin. That program
reads `/run/secrets/bc250-openwebui-client` inside the container and uses the
vendor's awaited `open_webui.models.config.Config.get_many` and
`Config.upsert` APIs for `openai.enable`, `openai.api_base_urls`,
`openai.api_keys`, and `openai.api_configs`. It never issues raw SQL. The
adapter refuses duplicate app-owned URLs, normalizes only list length, appends
or updates only `http://host.containers.internal:9071/v1`, preserves every
unrelated provider/config entry, restarts the application to reload persistent
configuration, then verifies the credential match, expected model, real SSE
content, and `[DONE]`. Any image/version contract change fails closed and
requires a reviewed adapter revision.

The Bazzite observation for the pinned image confirms a read-only root with a
one-GiB `/tmp` tmpfs, two-GiB memory, four CPUs, 256 PIDs, and numeric
1,073,741,824-byte soft/hard `RLIMIT_FSIZE`. The typed specification freezes
those bounds, the image's amd64 manifest, `restart=no`, private SELinux `:Z`
relabels for each approved host bind, and mode-0600 regular-file validation for
both mounted secrets. It never emits an API key environment variable.

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

The implemented durable type is `INTEGRATION_SETUP v1`. Its request contains
only bounded public client/model identities and pre-effect observations; no
credential bytes are allowed in operation request, output, event, or receipt
data. Open WebUI and an external app must use different client identities.
All six steps acquire the same closed exclusion set covering client metadata,
active runtime, gateway, Open WebUI, and sharing. Recovery adopts an
operation-owned mode-0600 credential file left before metadata commit instead
of generating a second key. Compensation revokes only operation-created
clients and stops/releases only effects absent from the request baseline.

The terminal `INTEGRATION_READY` decision requires the selected client's
negative-auth, authorized models, and real streamed completion evidence. The
legacy singleton remains usable during migration but cannot be retired until
the Open WebUI replacement receipt and one separate external-client probe both
pass; retirement requires the exact phrase `REVOKE LEGACY`.

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

## 9. Decision D6 — one truthful progress projection

Every durable operation surface consumes `TaskProgressProjection v1` with the
same task title, phase, durable state, elapsed seconds, last progress time,
optional measured fraction, cancellation availability, close safety, next
checkpoint, and optional typed problem. Models, Connections, Activity, and the
human-readable operations CLI may arrange those fields differently but may not
derive competing progress truth.

Only totals with the closed measured units bytes, files, or items produce a
fraction. Workflow step counts, startup stages, and verification stages never
produce a percentage or ETA. After 15 seconds without a durable progress
timestamp change, an active operation says **Still working** and retains its
current phase. Closing the window never cancels an operation; the cancel action
is present only when the durable operation summary allows it.

The finite stage deadlines are:

- gateway socket readiness: 10 seconds;
- Open WebUI application/provider warm-up: 120 seconds;
- model health, identity, and inference: 120 seconds;
- Tailscale/Serve convergence: 30 seconds; and
- complete `INTEGRATION_SETUP`: 300 seconds aggregate.

An elapsed deadline projects `OPERATION_DEADLINE_EXCEEDED` with the bounded
View Activity recovery action. Projection never mutates the operation or
declares the external effect failed; the owning adapter and durable workflow
remain authoritative for timeout, compensation, and terminal state. Physical
evidence may motivate a later reviewed ADR revision, but no deadline may become
unbounded or invisible.

## 10. Decision D7 — one offline client compatibility contract

`bc250-openai-compatible-v1`, schema 1, is the sole published model-client
capability matrix. It owns the gateway route vocabulary, Connections and Help
rows, CLI `connections capabilities` output, versioned client cards,
human-readable documentation, and secret-free `compatibility.json` support
metadata. The contract is bundled and never fetched from an online service.

The supported routes are authenticated `GET /v1/models` plus JSON and real SSE
`POST /v1/chat/completions`. Tool calling is conditional on exact model,
runtime, request-shape, and client-version evidence. Embeddings and legacy
completions are unsupported; Responses is deferred; Open WebUI `/api/...`
belongs only to the browser application. Known unsupported inference routes
return an authenticated OpenAI-shaped 404 rather than a bare 403. Private and
unknown management routes remain denied.

Client card schema 2 records exact field names, automatic probe paths, Base URL
rule, transport requirement, evidence level, and optional tested version. A
card cannot require an unsupported capability or claim a tested version while
remaining example-only. At this checkpoint only pinned Open WebUI 0.11.3 names
a third-party version. PocketPal and Python remain example-only; no card claims
hardware-tested status.

## 11. Exit criteria

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
