# EXP-2 physical connection qualification

Status: **evidence pending**. This is an empty operator checklist, not a PASS
record. Results must bind to the exact candidate commit and artifact inventory;
developer fixtures and evidence from an older package do not qualify it.

Run on each advertised host profile (Bazzite and CachyOS) with a physical
BC-250, a second tailnet device, a live standard-layout model, and Funnel off.
Do not record API keys, authorization headers, prompt/completion content,
device addresses, user labels, or screenshots containing secrets.

## Preconditions

- [ ] Record candidate commit, package version/digest, host profile, GPU/driver,
      12/4 UMA split, client name/version, phone OS, and Tailscale versions.
- [ ] Record the bundled compatibility schema/profile and the client card's
      evidence level; never upgrade example-only to hardware-tested early.
- [ ] Confirm the model server reports a safe public alias (no filesystem path).
- [ ] Create a dedicated client in Connections and save its one-time key
      outside the evidence packet.
- [ ] Confirm displayed endpoints are exactly WebUI `:8443/` and OpenAI base
      `:10000/v1`; neither `/api` nor raw port 8080 is offered.

## PocketPal

- [ ] Enter the displayed Base URL, API Key, and Model verbatim.
- [ ] `/v1/models` lists the displayed alias.
- [ ] A minimal streaming chat produces at least one SSE event by the deadline.
- [ ] Wrong/missing key fails with 401/403.
- [ ] Record only terminal classification, timing, and client version.
- [ ] If the client automatically probes embeddings, legacy completions, or
      Responses, confirm the stable unsupported explanation appears before
      deciding whether this client can complete setup.

## Open WebUI

- [ ] Open `https://<dns-name>:8443/` from the second device.
- [ ] The selected alias appears; if a Workspace model is required, record that
      non-secret configuration fact.
- [ ] Minimal chat succeeds through the authenticated gateway.
- [ ] Management/unknown gateway paths remain denied.
- [ ] Open WebUI browser `/api` works only at its browser origin and is never
      offered as the model Base URL.

## Generic OpenAI and raw SSE

- [ ] Authorized `GET <base>/models` returns the displayed alias.
- [ ] Unauthorized `GET <base>/models` fails.
- [ ] Authorized `POST <base>/chat/completions` with `stream=true` yields one
      valid `data:` event; response content is discarded and not recorded.
- [ ] Funnel is disabled and both Serve targets match the reviewed loopback
      Open WebUI/gateway targets.
- [ ] Missing key, invalid key, revoked key, missing scope, wrong model,
      duplicated `/v1`, embeddings, legacy completions, Responses, model
      restart, and gateway restart each produce the reviewed result without
      leaking response content or credentials.

## Independent revocation and emergency stop

- [ ] Create a second client and prove both work.
- [ ] Revoke the phone only: its old key fails while the second client works.
- [ ] Rotate with overlap off: the old key fails immediately; the new key works.
- [ ] Optional overlap test uses 1–900 seconds and the old key fails after expiry.
- [ ] With llama.cpp stopped/unhealthy, **Disable all remote API access** still
      revokes all keys; every authorized probe then fails.
- [ ] Recovery requires a newly created client plus explicit sharing start.

## Evidence handoff

- [ ] Record PASS only after every required item passes on the exact candidate.
- [ ] Attach privacy-reviewed screenshots with key fields hidden.
- [ ] Re-run this entire checklist after any package-code change.
