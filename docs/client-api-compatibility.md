# Model API and client compatibility

This is the human-readable copy of the offline
`bc250-openai-compatible-v1` contract bundled with BC250 LLM MODE. The running
application, Connections, Help, `connections capabilities`, client cards,
gateway routing, and redacted support bundle consume the same versioned local
contract. Nothing on this page expands the implemented gateway surface.

## Supported API matrix

| Capability | Contract status | What it means |
| --- | --- | --- |
| `GET /v1/models` | **supported** | Returns only the currently observed public model alias after named-key authentication. |
| `POST /v1/chat/completions` | **supported** | Bounded OpenAI-compatible JSON chat with credential scope and backend identity checks. |
| `POST /v1/chat/completions` | **supported** | With `stream=true`, passes through real SSE data and terminal `[DONE]`. |
| `POST /v1/chat/completions` | **conditional** | Tools/function calling requires exact model, runtime, request-shape, and client-version evidence; it is not a general promise. |
| `POST /v1/embeddings` | **unsupported** | Embeddings are not implemented. A client that requires them is not compatible with this profile. |
| `POST /v1/completions` | **unsupported** | Legacy completions are not silently translated to chat semantics. |
| `POST /v1/responses` | **deferred** | Responses requires a separate bounded adapter, threat/performance review, and client qualification. |
| `ANY /api/...` | **not-client-api** | `/api` belongs to the Open WebUI browser application; it is not the model endpoint for another app. |

Known unsupported model endpoints return an authenticated, OpenAI-shaped 404
with code `ENDPOINT_UNSUPPORTED`. Missing or rejected keys return a 401 with a
stable problem code. A valid key without the required named scope returns a
403 `SCOPE_NOT_GRANTED`. Private management paths remain denied.

Connection Doctor presents these failures in plain language. The same bounded,
secret-free result is available with `bc250-llm-mode connections doctor`:

- `401`: replace the missing or rejected named key;
- `403`: review that key's scopes and confirm the client is using Chat
  Completions at the displayed `/v1` Base URL;
- `404`: use a supported path rather than `/api`, Embeddings, legacy
  Completions, or Responses; and
- `502`: the private address is reachable, but the selected model backend must
  be started or repaired.

## Exact settings

For a phone, desktop application, SDK, curl, or SSE client:

- copy the **OpenAI Base URL** displayed in Connections exactly; it ends once
  in `/v1`;
- use a separate named API key created for that app;
- use the displayed public model alias, never a filesystem path;
- keep the device on the same private tailnet and use HTTPS; and
- configure Chat Completions. Do not choose Embeddings, legacy Completions,
  Responses, or an Open WebUI `/api` path.

The managed Open WebUI provider is different: version 0.11.3 is
protocol-tested with the private container gateway Base URL
`http://host.containers.internal:9071/v1`. The application writes that provider
transactionally and verifies models plus a real streamed chat. Unrelated
Open WebUI providers are preserved.

## Client evidence labels

- **protocol-tested** means the advertised request/response contract has local
  automated evidence. It is not a physical third-party-app claim unless an
  exact version is named.
- **example-only** means the fields are a bounded configuration example and
  the real client/version still requires physical qualification.
- **hardware-tested** may appear only after evidence on the exact candidate.

At this development checkpoint, Open WebUI 0.11.3 is the only advertised
third-party version. PocketPal and Python SDK cards remain example-only, and no
client card claims hardware-tested status. Refer to
`docs/connection-physical-qualification.md` for the pending evidence matrix.
