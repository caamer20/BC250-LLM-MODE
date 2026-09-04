# BC250 LLM MODE troubleshooting

Start with the first user-visible failure. Avoid rotating every key, deleting a
container, or reinstalling a model before the app identifies which layer is
unhealthy.

## Chat does not return a response

1. Open **System** and confirm the selected model is running and verified.
2. Open **Models**, select the model, and review the workload fit. A timeout may
   improve with **Interactive** or **Cool / conservative**.
3. Retry the last response in Chat. If it fails again, open **Activity** for the
   stable classification and request ID.

Partial responses and unsent drafts remain local. A thermal stop must be cooled
and explicitly cleared through the safety flow; do not bypass it.

## Another app cannot connect

Open **Connections** and run Connection Doctor. It checks, in order, the local
model, private API, named key, no-key refusal, Tailscale DNS, exact Serve
mappings, Funnel-off policy, remote model list, and streaming response.

| Symptom | Meaning | Safe next action |
| --- | --- | --- |
| `401` | The API key is missing, expired, revoked, or copied incorrectly. | Rotate only that named client, save the newly revealed key, and replace it in the client. |
| `403` | The key was recognized but lacks the required permission, or the app is using a private/wrong route. | Use Chat Completions, the displayed `/v1` Base URL, and a key created for that client. |
| `404` | The client requested an unsupported path. | Use `/v1/models` or `/v1/chat/completions`; embeddings, Responses, and Open WebUI `/api` are not model endpoints. |
| `502` | The private HTTPS address works, but the model backend did not complete the request. | Start and verify the selected model, then rerun the connection test. |
| Model missing | The running public model name and the client/Open WebUI view disagree. | Start the intended model and rerun guided setup so the provider and alias are reconciled. |

From SSH, the same read-only diagnosis is available as:

```bash
~/.bc250-llm-mode/app-venv/bin/bc250-llm-mode connections doctor
```

It returns the first failed step and a safe next action. It never prints an API
key. Use `connections instructions CLIENT_TYPE` for copy-safe settings.

## Download, repair, or update stopped

- Open **Activity** to pause, resume, safely stop, or recover durable work.
- Build a new preview after any state change; stale previews are intentionally
  refused.
- Maintenance and Repair show what changes, whether Undo exists, expected
  duration, and what to do next. Raw IDs and digests live under **Technical
  details**.
- Online application updates are unavailable until a trusted signed source is
  configured. Use only the original signed offline bundle; never extract or
  repack it.

If the guided action still cannot verify a safe state, create a redacted
support bundle from Help. It excludes credentials, prompts, and completions.
