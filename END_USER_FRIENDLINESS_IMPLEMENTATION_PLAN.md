# BC250 LLM MODE — End-User Friendliness Completion Implementation Plan

**Status:** Execution in progress. EUF-0 through EUF-7 are developer-
implemented; later EUF milestones and all physical, security,
human-acceptance, soak, publication, and release gates remain pending.
Developer tests and live diagnostics on one BC250 do not constitute those
external gates.

**Plan IDs:** EUF-0 through EUF-10

**Planning baseline:** `5dd0895667a2fe4eb0c73743595d890979b1a7e6`
on `main`, version `0.9.0.dev0`, database schema v14.

**Working-tree note:** the baseline checkout contains an uncommitted live-SSE
gateway repair in `bc250_llm_mode/gateway.py` and
`tests/test_gateway_live.py`, plus owner-controlled untracked planning and
scratch files. Preserve all owner-controlled files. EUF-0 decides the repair's
final architecture and commit boundary; no later phase may silently discard or
assume it is qualified.

**Parent authorities:** `UNIFIED_NATIVE_GUI_IMPLEMENTATION_PLAN.md`,
`APPLIANCE_EXPERIENCE_COMPLETION_IMPLEMENTATION_PLAN.md`, accepted ADRs,
`V1_0_RELEASE_CLOSURE_IMPLEMENTATION_PLAN.md`, and `AGENTS.md`.

**Purpose:** close the gap between the developer-implemented appliance and the
experience observed on a real BC250: components could report “running” while
the user-facing route was unusable, the gateway had credential management but
no packaged runtime owner, Open WebUI needed manual container repair, SSE was
buffered by the live gateway adapter, and generic clients received a 403
without enough guidance to correct their configuration.

This is a remediation and completion plan, not a replacement GUI redesign. It
reuses the existing Home, Models, Profiles, Chat, Connections, Activity,
Maintenance, Repair, System, Settings, Help, desktop integration, signed update,
and qualification work.

---

## 1. Outcome

When this plan is complete, a non-expert user can install and launch BC250 LLM
MODE, choose a recommended model, select **Start and Chat**, optionally connect
Open WebUI or another named client, and receive a useful answer without using
SSH, editing a container, reading systemd, copying a filesystem model path, or
diagnosing HTTP/SSE internals.

The application will:

1. call the appliance **Ready** only after the exact supported journey works;
2. own the gateway process through a packaged, current-boot systemd lifecycle;
3. create, migrate, configure, verify, and roll back Open WebUI safely;
4. provide one guided Connections flow with separate revocable credentials;
5. translate failures into stable plain-language problems and one safe action;
6. keep the primary navigation focused while retaining advanced routes;
7. recommend models from fit, provenance, compatibility, and local evidence;
8. show bounded, truthful progress for model and integration startup;
9. publish a versioned client capability/compatibility contract; and
10. pass clean-install, upgrade, physical-host, security, usability, and release
    gates on one exact candidate before any public completion claim.

The central product promise is:

> If Home says **Ready to chat**, native Chat works. If Connections says a
> named client is **Ready**, that client has passed an authenticated streamed
> completion through the exact displayed URL, key generation, and model alias.

---

## 2. Evidence from the live incident

The implementation must retain tests for each confirmed failure rather than
treating the live repair as a one-off:

| Finding | Misleading user experience | Required permanent disposition |
| --- | --- | --- |
| Open WebUI container existed and reported `running`, but `127.0.0.1:3000` refused connections | “Running” appeared equivalent to usable | Separate process state, HTTP readiness, provider readiness, and journey readiness |
| A legacy host-network container remained after an interrupted update | Update stopped halfway and left an unsafe/obsolete topology | Transactional recreate with verified data source and rollback |
| Podman rejected `--ulimit fsize=1g` | Install/update failed on the physical host | Generate Podman-compatible numeric soft/hard limits and test on both hosts |
| Read-only Open WebUI needed an app-owned secret-key mount and SELinux relabeling | Container startup or data access failed despite a valid image | Make secret and relabel requirements explicit, validated container inputs |
| Gateway credential provision/verify succeeded while no production listener owned port 9071 | Sharing advertised a backend that did not exist | Package and own the gateway runtime service; readiness must observe its socket |
| The live HTTP gateway buffered upstream SSE and labeled it JSON | Open WebUI created an empty assistant message | Preserve real SSE status, content type, framing, flushing, bounds, cancellation, and audit behavior |
| Open WebUI retained stale provider configuration | The model server worked but the model was absent from the UI | Add idempotent provider/key/model reconciliation for the pinned Open WebUI image |
| A generic app received 403 with little context | The user could not distinguish bad key, bad scope, or unsupported path | Return stable OpenAI-shaped errors and show a client capability matrix |
| A shared credential was copied manually | One leak or rotation can disrupt every client | Complete migration to one named, independently revocable credential per client |

The successful manual HTTPS and SSE probes prove only that the repaired live
machine worked at that moment. They are diagnostic evidence, not packaged or
release evidence.

---

## 3. Non-negotiable boundaries

Every phase preserves these existing product decisions:

- The raw llama.cpp backend stays on loopback and is never a Serve/Funnel/LAN
  publication target.
- Remote inference uses the authenticated gateway and tailnet-only Tailscale
  Serve. Public Funnel remains off.
- A normal reboot returns to the graphical desktop with no model, gateway, or
  Open WebUI auto-start. Services may be installed but must remain disabled for
  boot and start only from an explicit current-boot action.
- Prompts, completions, bearer tokens, credentials, raw request bodies, and
  conversation content never enter logs, events, metrics, notifications, or
  support bundles.
- Each external app receives its own purpose-scoped credential. Existing secret
  values are not re-revealed; users rotate or replace them.
- Widgets do not call systemd, Podman, Tailscale, HTTP, or privileged helpers.
  Composed services and durable operations own mutation.
- Model fit, standard-layout, mmap, thermal, 12 GiB fast-VRAM, host-RAM, lease,
  revision, and recovery barriers remain authoritative.
- No automatic model, container, runtime, catalog, application, or host update.
- No telemetry, cloud account, tray daemon, credential QR code, plugin
  marketplace, browser-shell rewrite, or generic one-click repair.
- Unknown or unrecognized data mounts, services, units, credentials, and Serve
  mappings fail closed and remain untouched.
- Any package-code fix made after candidate evidence begins creates a new
  candidate and invalidates affected evidence.

---

## 4. Target architecture

### 4.1 One observed readiness projection

Add a pure, bounded readiness contract, proposed in
`bc250_llm_mode/appliance_readiness.py`:

```text
ApplianceReadinessSnapshot
  generated_at
  overall_state
  primary_problem_code
  primary_action
  model
  gateway
  openwebui
  tailscale
  serve
  client_verification

ComponentReadiness
  component_id
  state
  observed_identity
  expected_identity
  observed_at
  fresh_until
  problem_code
  action_id
```

Closed component states:

```text
ABSENT | STOPPED | STARTING | READY | DEGRADED | BLOCKED | UNKNOWN
```

The snapshot is query-only. It composes current observations and previously
committed verification evidence; it never starts a service, runs a completion,
changes a credential, or marks an observation fresh merely because the GUI
refreshed.

### 4.2 Separate process, protocol, and journey truth

Each integration exposes three levels:

1. **Process:** systemd/container reports active/running.
2. **Protocol:** a bounded local health or models request succeeds and identity
   matches.
3. **Journey:** the intended client path completes, including auth, streaming,
   model alias, and `[DONE]`.

Only level 3 may produce **Ready** for a remote client. Home may show native
Chat ready from the verified local model path without requiring Tailscale or
Open WebUI.

Lightweight observations may refresh in the existing bounded coordinator.
Completion/SSE probes are explicit actions with persisted redacted outcomes.
Use named freshness constants rather than indefinite historical truth:

- local process/socket observation: at most 10 seconds old;
- explicit end-to-end verification: at most 5 minutes old;
- model activation identity: the current systemd invocation and model/config
  fingerprint must still match the verified receipt;
- any component restart, credential rotation, model switch, Serve change, or
  container recreation immediately stales dependent journey evidence.

### 4.3 Packaged runtime owners

Add typed adapters/services rather than shell fragments in frontends:

```text
gateway_runtime.py          # minimal executable server entry point
gateway_service.py          # unit plan/install/start/stop/status
openwebui_runtime.py        # container spec and live observation adapter
integration_lifecycle.py    # durable start/verify/stop orchestration
client_compatibility.py     # versioned cards/capability matrix
problem_details.py          # stable problem and safe-action projection
```

Names are proposals; reuse an existing module when it already owns the domain.
Do not create duplicate status, credential, or operation truth.

### 4.4 One durable integration operation

Introduce `INTEGRATION_ENABLE v1` only after its ADR and crash matrix are
frozen. It owns the multi-component user request:

```text
PREFLIGHT
START_MODEL
START_GATEWAY
START_OPENWEBUI (when selected)
RECONCILE_OPENWEBUI (when selected)
CONFIGURE_SERVE
VERIFY_LOCAL_NEGATIVE
VERIFY_LOCAL_POSITIVE
VERIFY_TAILNET_NEGATIVE
VERIFY_TAILNET_STREAM
COMMIT
```

Each stage records identities and redacted outcomes, not request bodies. The
operation remembers which components/mappings it started so compensation stops
or removes only its own new effects. It never stops a component that was
already running before the operation.

---

## 5. EUF-0 — Freeze the remediation contract and preserve the live repair

**Priority:** P0. No other EUF implementation begins first.

### Tasks

1. Add an ADR for appliance readiness, gateway runtime ownership, current-boot
   lifecycle, and transactional Open WebUI convergence.
2. Reconcile ADR 005's “live handler uses SSE pass-through” claim with the
   actual adapter and the uncommitted repair.
3. Convert the live SSE fix into the smallest reviewed production change:
   shared auth/bounds/rate/audit preparation, upstream headers, byte bounds,
   disconnect handling, and slot release must have one owner.
4. Add red-to-green tests that prove the first SSE frame is observable before
   the backend finishes, content type is `text/event-stream`, `[DONE]` arrives,
   the in-flight slot returns to zero, and no content enters the audit log.
5. Record the temporary live service/container deviations without embedding
   secrets or treating them as intended installation state.
6. Freeze exact ownership markers for any temporary unit that may be migrated.
   Unknown units are reported, never overwritten or removed.

### In-scope files

- `docs/adr/014-appliance-readiness-and-integration-lifecycle.md` (proposed)
- `bc250_llm_mode/gateway.py`
- `tests/test_gateway.py`
- `tests/test_gateway_live.py`
- `tests/test_connection_protocol_qualification_exp2.py`
- `CHANGELOG.md`, `README.md`, and operator/end-user guidance as needed

### Exit gate

- Buffered and streaming chat both pass over real loopback sockets.
- Invalid auth, missing scope, oversized body/headers, upstream timeout,
  disconnect, and response-bound cases all release concurrency exactly once.
- No prompt/completion/credential canary appears in audit, logs, exceptions, or
  default test output.
- The current working-tree repair is either committed as reviewed code or
  replaced by a better reviewed implementation; it is not left ambiguous.

---

## 6. EUF-1 — Truthful appliance readiness

**Priority:** P0.

### Tasks

1. Implement the pure readiness types and state-reduction policy.
2. Refactor Home, Connections, System, Maintenance, Doctor, CLI status, and
   support bundle projections to consume the same readiness vocabulary.
3. Make `open_webui_status` distinguish:
   - container installed;
   - process running;
   - loopback HTTP responsive;
   - gateway provider configured;
   - expected model visible; and
   - end-to-end chat verified.
4. Make gateway status distinguish:
   - credential metadata ready;
   - service installed;
   - service active;
   - private listeners present;
   - backend identity verified; and
   - authenticated SSE verified.
5. Preserve desired/observed/verified identity for the model. A process on port
   8080 with the wrong model/configuration is `BLOCKED`, never `READY`.
6. Define a deterministic priority reducer: thermal and recovery first,
   identity/security next, missing backend next, stale verification next, then
   routine maintenance.
7. Add a `Run connection check` command for expensive verification. Normal GUI
   refresh remains lightweight and query-only.

### Acceptance

- Every process-active/protocol-dead combination in the fault matrix produces
  `DEGRADED` or `BLOCKED`, never `READY`.
- Restarting any dependency stales the exact downstream verification.
- Native Chat readiness does not depend on optional Open WebUI/Tailscale.
- Remote-client readiness cannot be inferred from local gateway health alone.
- Home shows one safe next action and a short explanation; technical evidence
  remains one click away.

---

## 7. EUF-2 — Package and own the gateway runtime

**Priority:** P0.

### Service contract

Create one production entry point and one generated systemd unit. The unit:

- is installed from the active signed application slot/launcher;
- is disabled at boot and started only for the current boot by an explicit
  Open WebUI/sharing/client action;
- uses `After=bc250-llm.service` without `Requires=` or another dependency that
  would silently auto-start inference at boot;
- stops when the owning explicit integration lifecycle stops it;
- binds only `127.0.0.1:9071` and the observed private
  `bc250-openwebui` bridge address;
- resolves the bridge address from bounded Podman inspection instead of
  hard-coding `10.89.0.1`;
- refuses an absent, host, default/LAN, ambiguous, or changed bridge topology;
- uses the named multi-client authentication store, not a new singleton;
- reads secrets from mode-0600 files without putting values in argv, unit text,
  environment, journal, or support bundles;
- has bounded restart policy and systemd hardening compatible with Python,
  SQLite credential lookup, and the selected app slot; and
- reports the expected backend identity, not just an open socket.

### Lifecycle integration

Add composed operations for:

```text
gateway service plan|install|status|start|stop|restart|remove
```

GUI buttons and CLI commands use the same service. `serve start` and Open
WebUI start call the composed gateway owner before publishing/starting their
dependent route. `serve stop` removes its mappings but stops the gateway only
when no explicit current-boot consumer still owns it.

Installation/update regenerates the unit without changing running or enabled
state. Uninstall removes only an app-owned unit with a verified marker/hash.

### Migration of the live workaround

The installer recognizes the exact temporary unit/runner identity created by
the live repair. It previews replacement, installs the canonical disabled unit,
switches the current running process, verifies both private listeners and SSE,
then removes only the exact recognized temporary unit. An unfamiliar unit on
port 9071 blocks mutation with support guidance.

### Tests and exit gate

- Unit text/argv contains no credential and no unstable source-checkout path.
- Source, editable install, wheel, active update slot, and rollback slot launch
  the same runtime entry point.
- Start/stop/restart, crash/restart, stale PID, port conflict, bridge absence,
  bridge address change, DB unavailable, revoked client, and model-down cases.
- Socket inspection proves no wildcard, LAN, raw-backend, or Funnel exposure.
- Reboot test proves graphical desktop and no model/gateway/WebUI auto-start.
- Uninstall preserves models, Open WebUI data, and client metadata by default.

---

## 8. EUF-3 — Transactional Open WebUI lifecycle

**Priority:** P0.

### 8.1 Typed container specification

Replace scattered argv assumptions with a pure `OpenWebUIContainerSpec` that
renders and validates exactly one Podman create command. It includes:

- immutable image digest and expected architecture;
- dedicated private network and loopback-only `127.0.0.1:3000:8080` publish;
- numeric soft/hard `fsize` limit accepted by Bazzite and CachyOS Podman;
- memory, CPU, PID, tmpfs, capability, seccomp, and no-new-privileges bounds;
- verified data source limited to the managed volume or historical fixed bind;
- SELinux private relabel semantics for every approved bind;
- app-owned Open WebUI secret-key file mounted read-only at the vendor-required
  path;
- app-owned Open WebUI client credential mounted read-only at a fixed path;
- no host network, privileged mode, device mount, host PID, wildcard publish,
  raw llama backend route, or secret in environment/argv; and
- explicit no-boot-autostart behavior.

The pinned image's actual writable-path requirements must be measured. Keep the
root read-only only with deterministic writable mounts/tmpfs for required paths;
otherwise stop for an ADR/security review rather than tolerating noisy writes or
silently weakening containment.

### 8.2 Transactional create/update/migration

The durable workflow is:

1. inspect and verify the existing data mount before mutation;
2. pull and verify the new immutable image before stopping anything;
3. create a stopped candidate container with a unique app-owned name;
4. validate its spec, mounts, network, labels, limits, and secret files;
5. stop the old container with a bounded settle check;
6. retain/rename the old container as the rollback target;
7. promote and start the candidate;
8. wait for real HTTP/application readiness;
9. reconcile the provider and expected model;
10. run an authenticated streamed completion;
11. commit the new identity and remove the old container only after success;
12. on failure, stop the candidate, restore the old name/state, start it when it
    was previously running, and verify data remains available.

An ambiguous mount, failed stop, premature `conmon` exit, data-volume conflict,
port conflict, digest mismatch, provider failure, or health timeout stops at an
explicit recovery checkpoint. No volume-removal flag is ever used.

### 8.3 Provider reconciliation

Build one versioned adapter for the pinned Open WebUI release. It must:

- observe current configured providers without logging keys;
- install/update exactly the app-owned gateway provider;
- preserve unrelated user providers;
- update the Open WebUI client credential after rotation;
- use `http://host.containers.internal:9071/v1`, never raw port 8080;
- ensure the expected public model alias is visible;
- avoid brittle direct DB edits unless frozen in a version-specific ADR and
  backed by backup/restore tests;
- fail closed if the pinned image's supported configuration mechanism changes;
  and
- record only provider identity/version and redacted verification outcome.

### 8.4 Status and startup UX

Starting Open WebUI uses bounded phases: container start, application warm-up,
provider reconciliation, model discovery, stream verification. The GUI remains
responsive, shows elapsed time, and presents the last safe checkpoint. A
container process alone never satisfies the action.

### Tests and exit gate

- Pure spec snapshots for Bazzite and CachyOS Podman capabilities.
- SELinux enforcing/permissive, legacy bind/named volume, missing/mis-mode
  secret, read-only filesystem, failed stop, crash at every promotion boundary,
  startup timeout, wrong provider, stale key, wrong model, and rollback tests.
- Existing Open WebUI accounts/conversations remain visible across migration,
  update, failure, rollback, application update, and uninstall-by-default.
- Real pinned-image tests run from a clean wheel and on both physical hosts.
- Secret canaries are absent from inspect output, argv, journal, operations,
  notices, support bundles, and test reports.

---

## 9. EUF-4 — One guided connection assistant and integration action

**Priority:** P0.

### User flow

Connections begins with intent, not components:

```text
What do you want to connect?
  Open WebUI on this BC250
  Phone/tablet app
  Desktop OpenAI-compatible app
  Developer/curl client
```

For the selected card, the app:

1. shows what it will start and what remains private;
2. creates or selects one named purpose-scoped client;
3. runs the durable integration operation;
4. displays the exact Base URL, model alias, streaming support, and timeout;
5. reveals/copies the new key once for at most the existing bounded reveal
   window;
6. runs mandatory unauthorized and authorized tests; and
7. marks only that named client Ready.

### Credential migration

- Preserve the legacy singleton initially; never reveal or silently rotate it.
- Offer explicit creation of separate `Open WebUI` and user-named external-app
  credentials.
- Reconcile Open WebUI to its new credential and verify it.
- Verify each external app separately.
- Recommend revoking the legacy/shared credential only after replacements pass.
- Because the current shared key was manually displayed during diagnosis, it
  cannot qualify the final candidate; rotate/revoke it during the explicit
  migration journey without recording its bytes.

### Client cards

Each versioned card contains:

- app/client name and tested version;
- evidence level: `hardware-tested`, `protocol-tested`, or `example-only`;
- exact field labels used by that client;
- whether the field expects an origin, `/v1` base, or full endpoint;
- supported chat/streaming/tool behavior;
- timeout recommendation;
- known automatic probe routes the client may call; and
- one troubleshooting decision tree.

Do not use QR codes. Copy feedback must not echo a key. Screenshots, if added,
are local versioned assets with dummy endpoints/keys only.

### Acceptance

- Open WebUI, one phone client, and one generic desktop client complete from a
  second tailnet device using only displayed values.
- `/v1` is never duplicated and `/api` is never advertised.
- Revoking one app does not interrupt Open WebUI or another app.
- “Disable all remote API access” works when the model and gateway are down.
- The user never needs SSH or the filesystem credential path in a supported
  journey.

---

## 10. EUF-5 — Plain-language, protocol-correct failures

**Priority:** P0 and cross-cutting.

### Stable problem contract

Extend the message catalog with a frontend-safe `ProblemDetail`:

```text
code
category
severity
title
user_message
component
request_id
safe_action_id
safe_action_label
technical_summary
```

`technical_summary` is bounded and redacted. GUI, CLI JSON, notifications,
operations, and support bundle use the same stable code while rendering for
their audience.

Minimum codes include:

```text
MODEL_NOT_RUNNING
MODEL_IDENTITY_MISMATCH
MODEL_WARMING
GATEWAY_NOT_INSTALLED
GATEWAY_NOT_RUNNING
GATEWAY_BACKEND_UNVERIFIED
AUTH_MISSING
AUTH_INVALID
SCOPE_NOT_GRANTED
ENDPOINT_UNSUPPORTED
OPENWEBUI_START_FAILED
OPENWEBUI_WARMING
OPENWEBUI_PROVIDER_STALE
OPENWEBUI_MODEL_MISSING
TAILSCALE_DISCONNECTED
SERVE_MAPPING_MISMATCH
FUNNEL_MUST_BE_DISABLED
STREAM_INTERRUPTED
UPSTREAM_TIMEOUT
RECOVERY_REQUIRED
```

### Gateway HTTP behavior

- Missing/invalid auth: 401 with `WWW-Authenticate: Bearer` and a generic
  OpenAI-shaped error.
- Authenticated client missing a permitted scope: 403 with
  `SCOPE_NOT_GRANTED`.
- Authenticated request to a known-but-unsupported inference path such as
  `/v1/embeddings`, `/v1/completions`, or `/v1/responses`: a reviewed 404 or
  501 `ENDPOINT_UNSUPPORTED`, never a misleading scope error.
- Non-inference/management paths remain denied without proxying.
- Backend unavailable: 502/503 with a stable redacted code.
- Every response carries a request ID that correlates only with content-free
  audit/operation evidence.

Amend ADR 005 before changing the existing deny-all classification. Error
detail must not reveal credentials, internal file paths, prompts, responses, or
unnecessary management surface.

### GUI behavior

Every failure presents:

1. what did not work;
2. whether data/configuration was changed;
3. the single recommended safe action; and
4. an optional Details view with redacted evidence/request ID.

Raw Python exception class names, `conmon`, SQLite, systemd properties, Podman
argv, and HTTP parsing errors do not become the primary message.

### Tests and exit gate

- Exhaustive problem-code mapping and unknown-safe fallback.
- HTTP status/body/header fixtures for every supported and rejected path.
- GUI/CLI parity for code and safe action.
- Localization placeholders, keyboard focus, screen reader label, and contrast.
- Secret/prompt/completion/path canaries absent from every rendered surface.

---

## 11. EUF-6 — Focused navigation and progressive disclosure

**Priority:** P1 after readiness is truthful.

Do not delete existing capabilities. Reorganize the one-window shell around
five primary destinations:

```text
Home | Models | Chat | Connections | More
```

`More` contains Activity, Maintenance, System, Settings, and Help. Repair and
Updates remain contextual children of Maintenance. Direct route activation,
command palette, keyboard shortcuts, and deep links continue to work.

### Home

- One readiness headline and one primary action.
- At most five small cards: Model, Chat, Connections, Safety, Maintenance.
- Optional integrations that are stopped do not make the whole appliance red.
- “Details” opens the exact owning page and selected component/problem.

### Progressive disclosure

- Basic views use model names, goals, and outcomes.
- Advanced views expose aliases, quantization, context, slots, unit/container
  identity, request IDs, and logs.
- Destructive/privileged actions keep their existing preview and confirmation.
- No second window, permanent dashboard, tray, or terminal handoff returns.

### Acceptance

- First-time and returning-user journeys require no more top-level navigation
  choices than the five above.
- Every prior route remains reachable by keyboard and command palette.
- Back/forward/focus behavior is deterministic after async completion.
- Existing one-window, bounded-widget, refresh-lane, close-safety, scaling,
  reduced-motion, contrast, and screen-reader contracts remain green.

---

## 12. EUF-7 — Recommended model and “Start and Chat” journey

**Priority:** P1.

### Recommendation policy

Add one pure `ModelRecommendationPolicy` that ranks only objective local
evidence:

1. accepted standard GGUF layout and immutable identity;
2. comfortable/tight fit under the selected workload profile;
3. curated support tier and architecture compatibility;
4. successful activation/inference on the current runtime identity;
5. measured local performance/thermal evidence when fresh; and
6. installed/available state.

Do not invent a subjective quality score. If throughput, first-token latency,
or thermals have not been measured on this machine, say **Not measured**.
Qwen 3.8 9B may carry a curated “Recommended for this BC250” label only when
its exact artifact/layout/quantization/profile passes the frozen policy.

### Model card

The default summary shows:

- friendly model name and size;
- quantization in a Details row;
- fit: Comfortable, Tight, or Does not fit;
- recommended profile and supported context/slots;
- local measured speed/temperature only when evidence exists;
- provenance/support tier; and
- one primary action.

Primary actions are contextual:

```text
Install
Start and Chat
Switch and Chat
Open Chat
Resolve recovery
View why it cannot start
```

Starting a model invalidates dependent gateway/Open WebUI/client verification.
When the user chose Open WebUI, the post-start integration operation refreshes
the provider/model list before opening the URL.

### Acceptance

- A fresh supported install reaches a first native-chat response from one
  recommended model path without advanced settings.
- Tight/unsafe/thermal/recovery states cannot be hidden by the recommendation.
- The displayed model name always maps to the observed public API alias.
- Switching models cannot leave Connections falsely Ready.
- Model list/detail remain bounded for large catalogs and external folders.

---

## 13. EUF-8 — Truthful progress for long startup and repair

**Priority:** P1.

### Progress projection

Reuse durable operations and add a shared bounded projection:

```text
task_title
phase_label
state
elapsed_seconds
last_progress_at
determinate_fraction (optional)
cancel_available
close_safe
next_checkpoint
problem (optional)
```

Rules:

- Show a percentage only when the underlying operation has measured total work.
- Model/Open WebUI startup uses phase labels and elapsed time, not fabricated
  percentages or ETAs.
- After a bounded quiet interval, show “Still working” and the current phase.
- After its deadline, produce a typed problem and recovery action.
- Cancellation appears only at a checkpoint where the owning operation supports
  it. Closing the window never implies cancellation.
- A completion updates Home/Chat/Connections through the one refresh
  coordinator and generation fences.

### Deadlines to freeze/test

- gateway socket readiness: 10 seconds;
- Open WebUI application warm-up: 120 seconds;
- model health/identity/inference: use the existing bounded 120-second contract;
- Tailscale/Serve convergence: 30 seconds;
- complete integration operation: bounded aggregate deadline with per-stage
  receipts; never one uninterruptible blocking call.

The ADR may adjust exact values from physical evidence, but every value must be
finite, named, surfaced, and tested.

### Acceptance

- No GUI callback blocks the Tk thread on process, network, or filesystem work.
- Restart/close during every phase resumes or reports recovery truthfully.
- Activity and the initiating page show the same phase/result.
- Logs remain bounded and collapsed unless requested.
- Idle/active CPU, RSS, thread, widget, list, and event budgets remain within
  the existing GUI qualification limits.

---

## 14. EUF-9 — Client capability and compatibility contract

**Priority:** P1 for connected-client claims.

### Supported API v1 matrix

Publish the following truth from one versioned local contract:

| Capability | Initial support target | Notes |
| --- | --- | --- |
| `GET /v1/models` | Supported | Returns observed public alias only |
| `POST /v1/chat/completions` JSON | Supported | Auth, bounds, model identity |
| `POST /v1/chat/completions` SSE | Supported | Real pass-through and `[DONE]` |
| Tools/function calling | Conditional | Claim only for exact model/runtime/client evidence |
| `/v1/embeddings` | Unsupported initially | Explain before setup when a client requires it |
| `/v1/completions` | Unsupported initially | Do not silently route legacy semantics |
| `/v1/responses` | Deferred adapter | Requires separate contract/threat/performance review |
| Open WebUI `/api/...` | Not a client API | Browser application route only |

The matrix appears in Connections, CLI, Help, docs, and support metadata. It is
not fetched from an online service and contains no credentials.

### Compatibility qualification

For each advertised client/version:

1. record the fields and automatic probe paths it sends;
2. verify base URL normalization and TLS/Tailscale requirements;
3. test models and streamed chat with its own named key;
4. test invalid key, revoked key, missing scope, wrong model, unsupported path,
   model restart, and gateway restart;
5. record hardware-tested/protocol-tested/example-only status; and
6. remove or downgrade the card when a client update breaks the contract.

Do not add broad compatibility endpoints merely to suppress a client error.
Add `/v1/responses`, tools, embeddings, or legacy completions only under a
separate bounded implementation slice with explicit model/runtime semantics,
resource limits, security review, and real-client tests.

### Acceptance

- A 403 is never the only explanation for an unsupported client feature.
- Every advertised field/value is copied exactly and is independently testable.
- Client cards cannot claim a version or capability without evidence.
- Documentation and live gateway behavior are checked for drift in CI.

---

## 15. EUF-10 — Installation, upgrade, physical qualification, and release

**Priority:** final P0 gate; no public completion before it.

This phase completes and requalifies the already developer-implemented EXP-1,
EXP-6, EXP-7, and EXP-8 surfaces against the new candidate.

### Install and upgrade

- Build clean sdist/wheel artifacts; never install the candidate from a dirty
  source tree or historical `build/` directory.
- Fresh install creates the stable launcher/menu entry, disabled current-boot
  service definitions, app-owned secrets with correct modes/labels, and no
  model/Open WebUI/Tailscale auto-start.
- Upgrade preserves schema v14+ state, model artifacts, profiles, operations,
  backups, named clients, conversations, and the verified Open WebUI data
  source.
- The signed two-slot updater stages and verifies the new gateway entry point,
  unit generation, container spec, and migrations before promotion.
- Rollback restores the prior application/runtime behavior and does not roll
  the database backward unsafely.
- Uninstall previews and removes app-owned units/launchers/Serve mappings while
  preserving models and Open WebUI data unless separately confirmed.

### Exact-candidate physical matrix

Run all existing 14 EXP-8 journeys plus the live-incident regressions in:

```text
Bazzite fresh install
Bazzite upgrade
CachyOS fresh install
CachyOS upgrade
```

Required new journeys:

1. legacy host-network Open WebUI migration with preserved data;
2. interrupted Open WebUI stop/recreate and verified rollback;
3. SELinux-enforcing bind/secret mounts;
4. model active but gateway absent;
5. gateway active but backend/model identity wrong;
6. Open WebUI process running but port/provider/model unavailable;
7. real streamed Open WebUI completion;
8. phone and desktop generic-client connection with separate keys;
9. wrong key, revoked key, wrong `/api`, duplicated `/v1`, embeddings probe,
   and unsupported Responses endpoint;
10. credential rotation without disrupting the other clients;
11. model switch invalidating and then refreshing client readiness;
12. reboot returning to desktop with no inference/integration autostart; and
13. application update/rollback with gateway/Open WebUI data preserved.

### Human acceptance

Use the existing five participant roles. At minimum, observe without coaching:

- first successful native chat;
- starting Open WebUI;
- connecting a phone/desktop app;
- correcting a deliberately wrong base URL from the displayed guidance;
- understanding a 401, unsupported endpoint, and backend-down error;
- switching models and returning to chat;
- finding Activity/Repair through contextual navigation; and
- returning to the normal desktop/uninstall preview.

Record task outcome, wrong turns, time-to-recovery, assistance required, and
participant language suggestions. Never store prompts, responses, or keys.

### Release consequence

Developer tests alone cannot close C4 physical, C5 security, C6 non-developer
acceptance, soak, provenance/signature, or owner-gated C8 publication. The
release remains blocked until the exact candidate and artifact inventory pass
the existing evaluator and every required external record is verified.

---

## 16. Cross-cutting test matrix

### Pure policy

- readiness reduction and dependency invalidation;
- primary problem/action priority;
- model recommendation and evidence freshness;
- client capability matrix and card rendering;
- stable problem/status mapping;
- Open WebUI container-spec validation;
- service unit generation and ownership markers; and
- progress labels/deadlines/cancellation visibility.

### Component and fault tests

- systemd absent/inactive/failed/restarting/stale/port-conflict;
- Podman missing, network missing/wrong, image digest mismatch, invalid limits,
  SELinux denial, read-only denial, failed stop/start/restart, `conmon` death;
- data mount absent/ambiguous/unrecognized and rollback identity;
- gateway invalid/revoked/scopeless credentials, bounds, rate/concurrency,
  disconnect, malformed upstream, SSE split/coalesced frames, no `[DONE]`;
- Open WebUI stale provider/key/model and unrelated-provider preservation;
- Tailscale disconnected, DNS absent, Serve mismatch, Funnel present;
- stale observations after every identity-changing event; and
- database busy/newer schema/repair-mode behavior.

### Crash/recovery

Kill before and after every external effect and receipt for gateway install,
Open WebUI promotion, provider reconciliation, Serve mapping, verification,
credential rotation, and final commit. Prove takeover, idempotency,
compensation ownership, and no duplicate credential/container/unit effects.

### Security/privacy

- secret, bearer, prompt, completion, URL-userinfo, and path canaries across DB,
  argv, environment, inspect, logs, journal, events, notifications, support
  bundle, GUI notices, CLI JSON, exceptions, and test reports;
- path traversal, symlink, XDG/desktop-entry injection, unit-name/label
  injection, malicious Open WebUI mount metadata, hostile HTTP headers/body;
- exact listener/interface and Tailscale/Funnel exposure tests;
- credential scope, independent revoke, overlap expiry, and emergency disable;
  and
- independent security review before release claims.

### Packaging and architecture

- source/editable/sdist/wheel collection parity;
- clean-wheel CLI, GUI, worker, gateway runtime, active-slot and rollback-slot
  smoke;
- package inventory contains new modules/templates/assets and no stale deleted
  GUI paths;
- widgets/import-safe modules do not own external effects;
- one composition root and no legacy status/container/gateway bypass; and
- docs/message/card/capability drift gates.

### Resource and accessibility

- existing GUI RSS/CPU/thread/widget/event/list/log limits;
- gateway/Open WebUI/model aggregate host-RAM behavior on the 12/4 system;
- bounded stream/body/audit/client tables and shutdown;
- keyboard-only, screen reader, 100–200% scale, reduced motion, focus restore,
  text-not-color status, and high-contrast checks; and
- Bazzite/CachyOS measurements on the exact candidate.

---

## 17. Commit and delivery sequence

Each line is a reviewable commit boundary. Do not combine later UI polish with
an unproven runtime transition.

1. **EUF-0A:** ADR/readiness/runtime/container contract plus red tests.
2. **EUF-0B:** production SSE adapter and live-socket regression closure.
3. **EUF-1A:** pure readiness types/reducer and freshness invalidation.
4. **EUF-1B:** composed readiness query; Home/System/Connections/Doctor parity.
5. **EUF-2A:** gateway runtime entry point and generated disabled unit.
6. **EUF-2B:** service composition, CLI, repair/uninstall/update-slot wiring.
7. **EUF-2C:** exact temporary-unit migration and clean-wheel gates.
8. **EUF-3A:** pure Open WebUI spec and physical-Podman red tests.
9. **EUF-3B:** transactional recreate/rollback durable workflow.
10. **EUF-3C:** pinned-version provider reconciliation and model verification.
11. **EUF-4A:** durable integration operation and compensation/recovery matrix.
12. **EUF-4B:** guided Connections cards and explicit legacy-key migration.
13. **EUF-5:** stable problem catalog, gateway error bodies, GUI/CLI actions.
14. **EUF-6:** five-item primary navigation and contextual advanced routes.
15. **EUF-7:** recommendation policy, model summary, Start/Switch and Chat.
16. **EUF-8:** shared progress projection and startup/deadline UX.
17. **EUF-9:** versioned compatibility matrix/cards, docs, and drift gates.
18. **EUF-10A:** clean artifact install/upgrade/rollback qualification.
19. **EUF-10B:** exact-candidate physical, security, resource, and human handoff.
20. **EUF-10C:** evidence reconciliation only; owner decides publication.

After every package-code commit:

- run focused tests for the changed boundary;
- run the default authoritative collection;
- run the complete slow/security/clean-wheel battery when required by the
  parent plan;
- force compileall and `git diff --check`;
- verify tracked/untracked ownership and secret absence;
- update changelog/docs/AGENTS evidence truthfully; and
- stop at the phase gate before beginning the next architectural boundary.

---

## 18. Definition of done by user promise

### “Ready to chat”

- Expected model/config identity is active and fresh.
- Bounded inference succeeded for that invocation.
- Native Chat can stream and finish.
- No optional integration is required.

### “Open WebUI ready”

- Contained pinned container is running and HTTP responsive.
- Data source and secret mounts verify.
- App-owned gateway provider/key reconcile.
- Expected public model alias appears.
- Authenticated SSE chat completes with `[DONE]`.

### “Named client ready”

- Tailscale DNS and exact Serve mapping verify; Funnel is off.
- Client credential is active with required scope.
- Unauthorized negative request fails.
- Authorized `/v1/models` returns the alias.
- Authorized streamed chat completes before the verification deadline.
- Evidence is fresh and bound to the current model, gateway, key generation,
  Serve mapping, and client-card version.

### “Safe to update”

- Candidate artifact, signature, manifest, SBOM, provenance, schema and slot
  compatibility, backup/rollback, service templates, and data preservation all
  verify before promotion.
- The updater remains explicit; no automatic apply exists.

### “End-user friendly”

- Supported first chat and connection journeys require no SSH/terminal.
- One page communicates current truth and one safe next action.
- Errors explain what happened and how to recover without jargon.
- Advanced evidence remains accessible but is not prerequisite knowledge.
- Non-developer participants complete the required journeys within the
  accepted assistance/error thresholds recorded by EXP-8.

---

## 19. Stop conditions

Stop implementation or qualification and require review when:

- a secret must enter argv, environment, UI persistence, logs, or support data;
- the gateway would bind wildcard/LAN or Serve/Funnel would expose raw port
  8080;
- a systemd unit would auto-start the model/gateway/WebUI on normal boot;
- Open WebUI data ownership/mount identity is ambiguous;
- transactional rollback cannot preserve the existing container/data;
- the pinned Open WebUI image cannot meet required writable-path containment;
- a GUI refresh would run inference, network mutation, hashing, doctor, or
  unbounded work;
- a recommendation bypasses fit, thermal, provenance, recovery, or standard
  layout policy;
- client compatibility requires an unbounded/unauthenticated management
  surface;
- physical thermals/resources fail the accepted BC250 limits;
- candidate package code changes after evidence begins; or
- any release gate remains unverified.

---

## 20. First implementation handoff

Begin only EUF-0:

1. freeze the ADR and dependency/readiness graph;
2. inventory the exact uncommitted SSE repair and live temporary unit without
   copying credentials;
3. add/confirm red tests for buffered SSE, missing gateway listener, false
   Open WebUI `running`, unsupported endpoint errors, and boot-autostart refusal;
4. refactor/land the bounded SSE adapter and focused tests;
5. run the EUF-0 exit gate and record the new candidate identity; and
6. stop for review before adding a service unit, schema change, container
   migration, provider write, navigation change, or physical evidence.

EUF-1 begins only after EUF-0 is committed, documented, and green. The release
remains blocked throughout this plan until the existing external gates close.
