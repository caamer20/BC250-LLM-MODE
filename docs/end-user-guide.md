# BC250 LLM MODE end-user guide

BC250 LLM MODE is a native Linux desktop application for an AMD BC-250 with
approximately 12 GiB GPU UMA and 4 GiB host memory. It supports Bazzite and
CachyOS integrations. It is beta software, makes privileged host changes only
after acknowledgment and preview, and requires adequate cooling.

For the shortest happy path, start with the [quick start](quick-start.md). If a
chat or external client fails, use the [troubleshooting guide](troubleshooting.md)
before changing credentials, containers, or models.

## Install and open the application

The development line currently supports a source installation. A production
online/offline installer is intentionally unavailable until signed release and
publication gates pass.

```bash
git clone https://github.com/caamer20/BC250-LLM-MODE.git
cd BC250-LLM-MODE
python3 -m venv ~/.bc250-llm-mode/app-venv
~/.bc250-llm-mode/app-venv/bin/pip install .
~/.bc250-llm-mode/app-venv/bin/bc250-llm-mode
```

Run that final command in the BC-250's local graphical desktop. A plain SSH
shell cannot display the native window. To add the application menu entry:

```bash
~/.bc250-llm-mode/app-venv/bin/bc250-llm-mode desktop-integration plan
~/.bc250-llm-mode/app-venv/bin/bc250-llm-mode desktop-integration install
```

Then open **BC250 LLM MODE** from the desktop menu. It creates no login
autostart. A second launch activates the existing window instead of starting a
second GUI owner.

## Complete first run

1. **Machine check:** confirm the detected AMD GPU, approximately 12 GiB VRAM,
   usable host RAM, disk, Vulkan, and host integration.
2. **Safety:** read the complete heat, 40-CU, and 12/4 UMA warning; select all
   three acknowledgments and type `I ACCEPT`.
3. **Prepare system:** enter current-boot LLM Mode, create/reuse the controlled
   Fedora Distrobox, and install/verify the Vulkan llama.cpp runtime.
4. **Choose workload:** select an existing standard-layout GGUF or a curated
   catalog artifact. Choose context per user and concurrent slots only after
   the live VRAM result says FITS or after explicitly accepting a tight plan.
5. **Install and verify:** let the durable operation finish. Health and a
   bounded inference probe must pass before the model is called active.

The application never changes BIOS settings and never reboots automatically.
Restarting the machine returns to the normal graphical desktop with no model
auto-start.

## Use the one-window application

- **Home** shows one primary safe next action and five bounded health cards.
- **Models** combines installed, discovered, and curated models. Selecting a
  row shows its provenance, quantization, fit, and allowed actions. With focus
  in the table, Up/Down moves the highlight and Enter runs the highlighted
  row's displayed primary action, including starting or switching an installed
  model through the normal safe activation workflow. During installation, the
  page shows the current phase, transferred bytes, percentage, and final result;
  **View installation details** opens its durable Activity record, where
  supported work can be safely stopped, resumed, or recovered.
- **Profiles** offers Interactive, Long context, Shared, Cool, and Throughput
  goals plus bounded custom profiles. Preview before Apply.
- **Chat** streams locally without opening a browser, preserves unsent drafts
  when you leave, and stores conversations in private local files. It can copy,
  retry, and explicitly export either redacted or full Markdown.
- **Connections** shows exact Open WebUI/OpenAI-compatible client values and
  creates one independently revocable key per client. Connection Doctor turns
  model/gateway/Tailscale/key failures into one safe next action.
- **Activity** is the durable source of progress, cancellation, retry, resume,
  and recovery truth.
- **Maintenance** prioritizes safety and recovery before routine suggestions.
- **System** controls the one model service, optional Open WebUI, optional
  Tailscale, tailnet HTTPS, current-boot host mode, runtime, and backups.
- **Settings** stages workload/appearance changes and contains Privacy.
- **Help** contains quick start, checks, redacted support export, keyboard help,
  and an offline glossary.

Press `Ctrl+K` to search local pages and previews. The palette never runs a
protected action. Press `Ctrl+F` to focus the page's primary control, `Ctrl+L`
for bounded logs, and `Escape` to close the drawer.

**System → Enter LLM Mode** asks for confirmation, closes the graphical
desktop and its applications, and opens the full-screen tty1 login for this
boot. Sign in there to use the BC250 as a text-console appliance. Reboot to
return to the desktop, or run:

```bash
~/.bc250-llm-mode/app-venv/bin/bc250-llm-mode desktop --now
```

## Select a model and workload

Use only standard per-tensor-layout GGUFs. Fused, MAX, and imatrix-MAX repacks
are rejected because a large single fused allocation fails on this GPU even
when total VRAM appears sufficient. Never use `--no-mmap`; host RAM is too
small for a full model copy.

The fit calculation includes weights, KV cache multiplied by context and
concurrent slots, and runtime overhead against the 12 GiB fast-VRAM budget.
GTT spill is slower and not treated as comfortable capacity. A named profile
is the simplest way to choose a goal. Calibration is explicit and proposes a
winner; it never silently applies one.

## Connect Open WebUI or a phone

Keep the raw llama.cpp server on `127.0.0.1:8080`. After connecting Tailscale
and explicitly enabling tailnet HTTPS, Connections shows values in this form:

```text
Open WebUI:       https://<node>.<tailnet>.ts.net:8443/
OpenAI Base URL:  https://<node>.<tailnet>.ts.net:10000/v1
Models endpoint:  https://<node>.<tailnet>.ts.net:10000/v1/models
Chat endpoint:    https://<node>.<tailnet>.ts.net:10000/v1/chat/completions
```

There is no `/api` **model-client** endpoint. Open WebUI owns browser `/api`
routes, but PocketPal and other model clients must use the exact `/v1` Base URL,
one-time API key, and public model alias shown by Connections. Save the key when
it appears; it disappears after 30 seconds and cannot be revealed again. Create
separate client records so one phone can be revoked without interrupting
another. Public Tailscale Funnel remains off.

The initial contract supports `/v1/models` and JSON or streaming
`/v1/chat/completions`. Embeddings and legacy completions are unsupported,
Responses is deferred, and tools require exact model/runtime/client evidence.
Connections and Help show the complete offline matrix. The same JSON is
available with `bc250-llm-mode connections capabilities`; see
`docs/client-api-compatibility.md` for the reviewed human-readable copy.

Physical PocketPal and Open WebUI qualification remains pending for the exact
candidate even though the protocol fixtures pass in development.

## Maintenance, repair, updates, and recovery

Normal Maintenance refresh is read-only. **Run full check** explicitly gathers
fresh evidence. Guided Repair always requires a current preview and verifies
the result. Storage cleanup starts as a dry run; quarantine can be undone only
while its exact receipt, retained identity, deadline, and leases still verify.
Expired purge is permanent and separately confirmed.

Application Updates accepts only eligible signed releases. This development
build has no production signing root or online channel, so an unavailable
result is expected. An offline archive is not trusted by location and passes
the identical verification chain. Never replace this with an arbitrary wheel,
branch, or `pip install --upgrade` from an unknown source.

If an operation says `RECOVERY_REQUIRED`, open Activity. Do not start a
conflicting action or manually delete its staging. Resume the recorded action
or use its recommended Repair entry.

## Return to the desktop or uninstall

Return to the regular graphical desktop without deleting models:

```bash
bc250-llm-mode desktop-mode --now
```

The next boot is already graphical with the model disabled. Uninstall app-owned
service/host integration while preserving model data by default:

```bash
bc250-llm-mode uninstall
bc250-llm-mode desktop-integration remove
```

`--remove-container` removes containers but preserves the Open WebUI named
volume. `--remove-models` permanently deletes app-managed model files and
cannot be undone. External model folders are never deleted by that flag.

## Privacy and accessibility

Open **Settings → Privacy** or run `bc250-llm-mode privacy`. The inventory
states what is retained, its actual owner/location, when it leaves the machine,
and where it can be managed. The app has no telemetry and performs no automatic
application-update check.

Interface scaling supports 100%, 125%, 150%, 175%, and 200%; reduced motion is
available. Status is written as text rather than color alone. Tk table and
screen-reader behavior varies by desktop, so important selections are repeated
in adjacent text/Details views. Full physical screen-reader qualification is
pending. See `docs/accessibility-privacy.md`.
