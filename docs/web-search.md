# Optional web search with SearXNG

SearXNG is a self-hostable, open-source metasearch service. It queries configured
search engines and exposes an HTTP JSON API. The native application can use
that API to give a local model current source excerpts without requiring the
model to support tool calling. This integration is explicit retrieval: the
user searches, reviews sources, attaches excerpts, then sends a model request.

Upstream references, checked September 17, 2026:

- [SearXNG project and license](https://github.com/searxng/searxng)
- [Official container installation](https://docs.searxng.org/admin/installation-docker.html)
- [Search API and JSON format](https://docs.searxng.org/dev/search_api.html)
- [Open WebUI's SearXNG integration](https://docs.openwebui.com/features/chat-conversations/web-search/providers/searxng/)

## Configure the search provider

1. Run a SearXNG instance using its official installation instructions, or use
   an instance whose operator you trust. Review and pin the chosen container
   image digest when deploying it. BC250 LLM MODE does not install or start
   SearXNG, change boot services, or expose it publicly.
2. Enable JSON results in the instance's `settings.yml`:

   ```yaml
   search:
     formats:
       - html
       - json
   ```

3. For an instance on the BC250, bind its published port to loopback. For
   example, if you publish the container's HTTP service to host port 8888,
   the native provider address is `http://127.0.0.1:8888`. A remote provider
   requires HTTPS with normal certificate verification. Embedded credentials,
   query strings, and URL fragments are refused in provider configuration.
4. In native Chat choose **Add source → Web search…**. Enter the provider
   address and choose **Save provider** if you want to retain it. An empty
   address clears the saved provider. Saving does not make a network request.
5. Write the query you want to send and press **Search**. Review the returned
   text and URLs, select excerpts, then choose **Attach selected excerpts**.
   Write your question in Chat and Send. Sources can be removed before sending.

SearXNG needs a running service and working upstream engines. No live provider
is bundled or assumed available. There are no web claims for a particular
BC250 installation until that provider has been configured and tested there.
Additional service memory/CPU use must be measured alongside inference on the
supported 12 GiB GPU / 4 GiB host memory split; installation instructions are
not evidence that a particular container limit will fit every workload.

## What leaves the machine

Only the explicitly entered query is posted to the selected SearXNG endpoint.
SearXNG then forwards it to configured engines. Its operator and those engines
may observe or retain queries according to their policies. The app sends no
conversation history, instructions, local document contents, or client keys to
the search provider. Queries use POST bodies rather than URL query parameters.

The app keeps no query history or result cache. If you attach results, their
excerpts and source URLs become private conversation content and are included
in an explicit full export or portable backup. Search settings store only the
provider address. Clearing that address does not delete previously saved sources.

The search adapter follows no redirects and fetches no result pages. It accepts
at most 1 MiB of provider JSON, returns up to four HTTP(S) sources, and has a
15-second total request deadline, including DNS and response headers. A
short-lived HTTP child enforces that deadline and a 384 MiB Linux address-space
limit; requests/replies use private descriptors, with no query text in argv.
HTML markup/scripts are stripped from
excerpts. Retrieved text is marked as source material, not system instructions;
this label is not a guarantee against a model following misleading text.
No model-controlled browser, filesystem, shell, or account actions are exposed.

Answers should cite the supplied URLs. An excerpt can be incomplete or wrong,
and a generated citation is not proof that a claim appears on the full page.
Use the original source to verify important claims. The feature adds retrieved
context for a request; it does not train or permanently update the model.

## Troubleshooting

- **403 / JSON refused:** enable `json` in `search.formats`. Many public
  SearXNG instances disable API formats.
- **No usable results:** try another query and check the instance's configured
  engines, rate limits, or upstream CAPTCHA status. There is no silent fallback
  to an unrelated provider.
- **Connection or certificate failure:** check the provider address and its
  listener/certificate. Remote HTTP and TLS bypass are not supported.
- **Too many sources:** remove an attachment or select fewer results. Local
  documents and web excerpts share the four-source/128 KiB message limits.
- **Context too large:** remove sources, shorten the question, or select a
  suitable workload profile through the existing fit-checked controls.

Open WebUI can separately use SearXNG through its own settings; follow the
upstream guide for the installed version. That path has a distinct data owner,
web loader, configuration and qualification surface. Native search does not
change the existing Open WebUI container or its authenticated model gateway.

## September 17 Bazzite development installation

The existing BC250 now runs a separately installed `bc250-searxng` container
from the official image pinned to
`sha256:56d6ce4c64e76ca0b78ab21884d25b6112f81c68a3838afdc9945ad3d315e4c6`.
Its provider address, `http://127.0.0.1:8888`, is saved for the installed dev9
native Chat. Use **Add source → Web search…**, enter a query, review the
excerpts and attach the ones you want. Search is never performed automatically.

The container publishes only to loopback and runs with a read-only root,
dropped capabilities, no-new-privileges, 384 MiB memory, one CPU and 128 PID
limits. It used about 98 MB immediately after the recovery search; that is a
short observation, not a guarantee for every engine/workload. Configured
engines are DuckDuckGo, Brave and Wikipedia. No query logs are retained by the
container's log driver; upstream engines still receive the explicit query.

There is no boot service or automatic restart. On the BC250, under the same
account that owns the installation:

```bash
podman start bc250-searxng  # Start again after reboot, when web search is wanted.
podman stop bc250-searxng   # Stop the optional provider.
```

Live native Chat returned the supplied source URL before and after dev9
activation. A stopped provider produced a bounded error in 0.27 seconds, and
search recovered after restart. A previous 512-token diagnostic exhausted its
allowance without a visible answer; the native Standard (2,048-token) setting
passed. Response quality and citation correctness still depend on the model
and supplied excerpts. These results do not qualify every engine or model.
See the [exact deployment record](experience-deployment-2026-09-17.json).
