# Dev14 prompt-cache fix: installed development checkpoint

The owner explicitly approved installing dev14 and restarting the model service.
The application is now **0.9.0.dev14**, code
`a2639b4658c96e4fe06e0522b3212eea40a97cc2`, with wheel SHA-256
`b8a8f1dc698264f19c140ed3dce4fe2d8512167fd1a884b20e68ff6d9ac01325`.
All 197 installed package files match the qualified source and wheel.
The earlier automatic-review refusal occurred before execution and was resolved
by explicit owner approval. No failed activation or rollback occurred during
this approved cutover.

## Applied change

The live Prism command now includes `--cache-ram 0`, disabling the fork's
extra host-RAM prompt cache. The active slot's Q8 GPU KV cache remains enabled.
Both Bonsai models remain installed; CRACK is active at **8,192 tokens / one
slot** on the same verified, promoted Prism compatibility runtime. The runtime
binary, FP16-disabled policy, GPU governor and model/profile settings are
unchanged. The isolated O2 build remains unpromoted; the experiments showed
no demonstrated generation-speed improvement.

The full developer qualification remains bound to the unchanged code and
wheel: 1,927 selected tests, macOS 1,908 passed + 19 skips, Bazzite 1,912 + 15,
and GitHub Python 3.11/3.14 each 1,920 + seven. All 52 slow gates pass. Installed
features, 82 guest runtime checks, and native fixture checks also pass; these
additional checks overlap the full suite. See the
[exact qualification record](bonsai-cache-qualification-2026-09-18.json).

## Post-installation observations

Real native Chat returned the expected fixed answer, saved it in temporary
conversation storage, and rendered both Bonsai Model Library controls correctly.
One hundred navigation changes completed without Tk or unraisable errors.
The app used about 75–76 MiB RSS around idle/navigation, with 0.76% of one CPU
core over a 15-second idle sample. Four threads during the window returned to
one after close. These checks used an isolated Xvfb display; they do not replace
human desktop acceptance. This Chat check did not attach web-search results.

Two further synthetic SSE requests each completed 64 output tokens. They took
21.012 and 21.340 seconds end to end, including roughly 2.7 seconds to first
output. During those requests, host memory available stayed at or above
**1,871.5 MiB (1.83 GiB)**, server RSS peaked at 352.9 MiB, temperature peaked
at 84°C, and at least 6,124 MiB fast VRAM remained free. The temporary display
was present during this measurement. These are bounded repeated-request checks,
not sustained-throughput or soak claims.

Process DRM counters exposed GFX engine activity unavailable through the
comparison's inspected sysfs interface. Deduplicating the single DRM client
across its file descriptors yielded 87.63% and 86.24% engine activity over the
whole requests; the llama-server process averaged 0.760 and 0.751 CPU cores.
Clock observations ranged from 1,000 to 1,850 MHz. These observations do not
isolate a compiler effect, establish a hardware fault, or measure all system
CPU activity. No GPU controls were changed.

## Preservation and recovery

A fresh SQLite backup, private-file snapshot and prior application-link target
are retained under `/root/.bc250-deployments/20260918-a2639b4/rollback` on the
BC250. That prior link points to dev13. Keep all earlier runtime and rollback
trees, and never restore a database over newer user data.

Twelve persistent private files and 15 protected database tables match the
fresh snapshot. Only `connection_clients.last_used_at` and `last_endpoint_class`
are excluded because authenticated background activity updates them. No
credential/configuration fields are excluded. The generated launcher changes
as intended; private database contents are neither copied to Git nor published.

The model service restarted through normal application code. Its service
definition is unchanged; gateway and governor service identities and definitions,
boot configuration and governor configuration are preserved. Final model and
SearXNG health are 200; anonymous gateway access remains 401. No active operation,
recovery lease or runtime recovery barrier remains. The reconciled historical
failed audit is retained. The temporary display container and its owned socket
are removed; production containers and the base image are retained.

All source upgrades are on GitHub through
[PR #6](https://github.com/caamer20/BC250-LLM-MODE/pull/6). This checkpoint adds
the sanitized deployment evidence. See the [measured record](bonsai-cache-deployment-2026-09-19.json).
Raw logs, prompts/completions, credentials, databases, model weights, and all ten
owner-controlled untracked files remain excluded. Long-context, desktop/phone,
reboot/recovery, soak, CachyOS, accessibility, independent security, human
acceptance and signed-release gates remain pending.
