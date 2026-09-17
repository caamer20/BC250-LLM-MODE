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
short-lived HTTP child enforces that deadline and a 256 MiB Linux address-space
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
