# BC250 LLM MODE

BC250 LLM MODE is a lightweight native desktop application, setup wizard, and terminal chat client for turning an AMD BC-250 running Bazzite or CachyOS into a dedicated local `llama.cpp`/Vulkan inference station—and operating it afterward from one place.

The interface is a real local `tkinter` window—not a web app. One persistent,
resizable window owns five-chapter setup, task-oriented Home, Model Library,
native streaming Chat, Connections, Activity, System, Settings, Help, inline notices,
Maintenance/Repair/Updates, a local command palette, a Privacy Center,
confirmations, and a bounded log drawer. It uses one Tk root, one refresh
coordinator, and three lazily-created bounded worker lanes. Terminal chat
remains available as an optional client with the same lifecycle semantics.

> [!WARNING]
> **Public beta — use at your own risk.** BC250 LLM MODE is under active development and may contain bugs or incomplete behavior. It changes boot targets, sleep settings, kernel arguments, system services, GPU power policy, and—when explicitly selected—performance settings. These changes can cause instability, data loss, overheating, reduced hardware lifespan, or an unbootable system. Back up important data, provide adequate cooling, monitor temperatures, and understand every option before continuing. You are solely responsible for BIOS changes and for the consequences of running this software. The software is provided without warranty.

Detailed guides: [end-user guide](docs/end-user-guide.md),
[operator guide](docs/operator-guide.md), and
[accessibility and privacy](docs/accessibility-privacy.md). Candidate-bound
journey and resource qualification uses the deliberately pending
[EXP-8 worksheet](docs/appliance-experience-physical-qualification.md).

## Release status

Current version: **0.9.0.dev0** (development line; not `1.0.0`). Status
vocabulary: *implemented* (code + developer tests pass), *developer-qualified*
(all executable local/CI checks pass), *evidence pending* (hardware/human/
external evidence absent), *release blocked* (a policy-required item is
unsatisfied), *published* (exact artifacts externally published and verified).

- Supported capability set: unified native setup and daily-operation GUI, model
  acquisition/import/activation/removal, durable llama.cpp runtime
  update/rollback, chat/benchmark, backup create/restore (implemented;
  hardware qualification **evidence pending**), gateway sharing, Open WebUI
  container. Model conversion is **deferred, not advertised** in 1.0 (no
  pinned, verified converter ships; see
  `release/scope-decision-model-conversion.md`).
- The EXP-2 Connection Assistant and multi-client credential protocol are
  developer-qualified with bounded real-socket fixtures. Physical PocketPal,
  Open WebUI, and second-device SSE evidence is still pending for the exact
  candidate; no hardware-tested client claim is inferred from those fixtures.
- EXP-3 named workload profiles, evidence-bound coaching, durable calibration,
  and stop-only idle behavior are developer-qualified. Physical calibration on
  Bazzite and CachyOS with small and 9B standard-layout models remains pending;
  estimates and simulated metrics are never presented as hardware results.
- EXP-4 Maintenance/notification and EXP-5 guided repair, durable cleanup, and
  evidence-gated Undo are implemented with bounded deterministic tests. Their
  KDE delivery and repair/cleanup/restore physical matrices remain evidence
  pending on both advertised host profiles; see
  `docs/notification-physical-qualification.md` and
  `docs/repair-physical-qualification.md`.
- EXP-6 signed two-slot application-update mechanics are developer-qualified,
  including hostile offline-bundle rejection and deterministic crash recovery.
  The production channel remains honestly unavailable because no reviewed
  production trust root or eligible signed release ships in this development
  build. Physical update/rollback evidence is pending on both hosts.
- EXP-7 stable copy, bundled glossary, `Ctrl+K` local command palette,
  100–200% interface scaling, text alternatives, locale-ready display
  formatting, and the query-only Privacy Center are developer-qualified.
  Linux screen-reader behavior and resource/keyboard journeys remain physical
  and human evidence pending; see `docs/accessibility-privacy.md`.
- Release qualification is decided ONLY by the evidence-driven evaluator
  (`python -m tools.release evaluate`); the release workflow builds once,
  verifies, attests, verifies the attestations, and gates approval/publish on
  the evaluator. `1.0.0` is **release blocked**: hardware qualification +
  soak (C4), independent security review (C5), non-developer human acceptance
  (C6), and owner-authorized publication (C8) are all **evidence pending**.
  Nothing has been published. Operator commands: `release/RUNBOOK.md`.

## Supported platform

This application is intentionally hardware- and operating-system-specific.

| Component | Supported or required configuration |
| --- | --- |
| Device | AMD BC-250 / GFX1013 (RDNA1, integrated UMA GPU) |
| Memory | 16 GB total with approximately 12 GiB assigned to GPU UMA and 4 GiB left for the OS |
| Operating system | Bazzite (`rpm-ostree`) or CachyOS (`pacman`), with systemd, Podman, Distrobox, AMDGPU, and RADV |
| Desktop | A local graphical desktop capable of opening a `tkinter` window |
| Python | Python 3.11 or newer |
| Inference backend | `llama.cpp` built with Vulkan; CUDA and ROCm are not used |
| Model format | Standard per-tensor-layout GGUF models; fused/MAX/imatrix-MAX repacks are forbidden |

The Bazzite and CachyOS host integrations are implemented and covered by deterministic command, detection, bootstrap, and regression tests. Physical BC-250 qualification evidence remains pending for the release candidate. Arch-family, Fedora, Debian-family, and openSUSE hosts may be reported as `compatible-unqualified` when their required capabilities are present; they are not advertised as qualified until the hardware matrix is completed. Windows, macOS, non-systemd hosts, unrelated AMD GPUs, NVIDIA GPUs, and Apple Silicon are refused.

Run `bc250-llm-mode platform status` for the detected distribution, package manager, boot manager, service manager, optional GPU-tuning backend, blockers, and support tier. `bc250-llm-mode platform plan` prints read-only reviewed package plans; it never changes the system.

The BC-250 has no Re-Size BAR/Above-4G option that this tool can enable. Changing the recommended 12/4 UMA split does not solve the card's per-allocation limit, and fused/MAX repacks remain unsupported even when total free VRAM appears sufficient.

The UMA carve-out is established by firmware before Linux boots. It cannot be safely changed on the fly. In particular, a 14 GiB GPU / 2 GiB OS split is rejected as host-starved: Linux, Podman, the AMD driver, and mmap bookkeeping need the supported approximately 4 GiB host allocation.

## Before installation

1. Make sure all 40 compute units are unlocked in BIOS/firmware if your board exposes that option.
2. Configure approximately 12 GiB GPU UMA and 4 GiB system memory on a 16 GB unit.
3. Install adequate cooling and arrange a way to monitor GPU temperature during sustained inference.
4. Keep at least 20 GiB free on the filesystem that will contain models. More space is needed for conversion workflows and multiple models.
5. Connect the BC-250 to the network for the initial Fedora container, build dependencies, `llama.cpp`, Python packages, and model downloads.
6. Back up important data. Setup makes privileged system changes only after its mandatory acknowledgment screen, but this is still beta software.

Bazzite normally includes Podman and Distrobox. On CachyOS, complete a normal full system upgrade first, then install the reviewed host dependencies:

```bash
sudo pacman -Syu
sudo pacman -S --needed podman distrobox vulkan-radeon vulkan-tools tk
```

BC250 LLM MODE never runs `pacman -Sy`, never initiates an automatic full-system upgrade, and refuses its Tkinter bootstrap when Pacman reports pending upgrades. The wizard checks Podman and Distrobox and reports a platform-specific recovery plan if either is unavailable. Build tooling and Python model utilities remain inside the controlled Fedora Distrobox on both hosts.

## Installation

Clone the repository on the BC-250 and install it into a dedicated virtual environment:

```bash
git clone https://github.com/caamer20/BC250-LLM-MODE.git
cd BC250-LLM-MODE
python3 -m venv ~/.bc250-llm-mode/app-venv
~/.bc250-llm-mode/app-venv/bin/pip install --upgrade pip
~/.bc250-llm-mode/app-venv/bin/pip install .
```

Launch it from a terminal inside the BC-250 desktop session:

```bash
~/.bc250-llm-mode/app-venv/bin/bc250-llm-mode
```

After verifying that launch, install the owned user-local application-menu
entry. Previewing shows every target and writes nothing:

```bash
~/.bc250-llm-mode/app-venv/bin/bc250-llm-mode desktop-integration plan
~/.bc250-llm-mode/app-venv/bin/bc250-llm-mode desktop-integration install
```

Open **BC250 LLM MODE** from the desktop application menu. Reopening it
activates the existing same-user window; it does not create a second refresh
owner. The launcher creates no login autostart and starts no model. Remove only
the app-owned menu files with `desktop-integration remove`.

This is currently the documented developer/source installation path. A
production online or offline installer bundle must remain unavailable until an
exact signed release passes the evaluator and publication gates. Do not treat a
source checkout, arbitrary wheel, or `curl | sh` command as a verified release.

The application invokes `pkexec` when available or falls back to `sudo` for privileged operations. Installing the Python package itself does not need `sudo` when the virtual environment is in your home directory.

### If the native GUI does not open

Bazzite and CachyOS package `tkinter` differently. When it is absent, BC250 LLM MODE first shows the same mandatory safety gate using Zenity or an interactive terminal. After acknowledgment, the reviewed platform plan is:

```bash
# Bazzite (reboot required)
rpm-ostree install python3-tkinter

# CachyOS (relaunch required; fully update the system first)
sudo pacman -S --needed tk
```

On Bazzite, reboot after the package is staged. On CachyOS, relaunch the application after installation. Setup progress is saved in both cases.

A plain SSH session cannot display the local desktop window unless graphical forwarding is configured. For the normal experience, launch the wizard from a terminal on the BC-250's physical desktop. SSH can still be used for commands such as `status`.

## What first-run setup does

Setup is resumable and presented as five understandable chapters. The
chapters preserve every canonical durable setup stage, and each action remains
safe to resume or re-run:

1. **This machine** — finds the AMD GPU by PCI vendor ID instead of assuming `card0` or `card1`; checks memory, disk, Vulkan, host capabilities, and the supported 12/4 profile. The exact safety warning then requires all three checkboxes and the typed text `I ACCEPT` before any mutation.
2. **System mode** — starts current-boot LLM Mode with runtime-only sleep/GPU-awake rules while guaranteeing `graphical.target` and no model auto-start for the next boot.
3. **Runtime** — creates or reuses the controlled Fedora Distrobox, installs the pinned Vulkan `llama.cpp` runtime through the durable workflow, and smoke-tests Vulkan.
4. **Model** — lets the user choose a curated download or existing local GGUF, shows live context/slot/VRAM fit, applies only selected bounded optimizations, resumes downloads, and validates the artifact.
5. **Ready** — activates through the one owning systemd service, verifies health and inference, optionally configures Open WebUI, then routes directly into native Chat/Home.

The application never changes BIOS settings and never reboots the computer automatically.

### Performance optimizations

Runtime tuning (applied to the generated launcher, always reversible through the Optimize step):

- **Flash attention** (`auto`/`on`/`off`), **batch/ubatch sizing**, and **Q8_0/Q4_0 KV-cache quantization** with automatic VRAM fit re-projection (Q4 KV halves the projected cache).
- **CPU thread autodetection** — the launcher counts physical cores from `/proc/cpuinfo` at each start and passes `--threads`/`--threads-batch`; override with the bounded `threads` setting.
- **KV-cache reuse and defragmentation** — `--cache-reuse 256` keeps agentic/tool-calling sessions fast across context shifts; `--defrag-threshold 0.1` reduces fragmentation in long multi-slot sessions.
- **`fast_sync` (experimental, off by default)** — when enabled, the conservative `GGML_VK_FORCE_SYNC=1` workaround is dropped so advanced users can measure throughput on newer drivers. The `--no-mmap` ban is never relaxed.

Host-level options (all opt-in and reverted by `revert-optimizations`/uninstall):

- **Governor profiles** — `cool-quiet` (500–1400 MHz), `balanced` (500–1850 MHz, default), `maximum` (800–2000 MHz), or a custom range, available only when the cyan-skillfish SMU governor is detected. The controls are disabled on a standard CachyOS host rather than guessing at an unsafe clock interface.
- **Thermal watchdog** — when `thermal_watchdog_enabled`, each poll reads the GPU hwmon sensor and applies hysteresis. With the reviewed Cyan backend, the clock ceiling is capped and restored; without it, the app reports degraded throttling but continues monitoring and still stops the server at `thermal_stop_c`. A thermal stop stays latched until a human safely resets it. Drive it with `bc250-llm-mode thermals status|once|watch`.
- **Service memory guards** — with safeguards enabled, `bc250-llm.service` runs under `MemoryHigh`/`MemoryMax` sized for the ~4 GiB host share, with `OOMScoreAdjust` and idle I/O scheduling so an out-of-memory event takes out the server, never the desktop.
- **Memory/service trimming** — bounded swappiness and optional stopping of listed game-oriented services.
- **`autotune`** — opt-in sweep over `{ubatch 256/512} × {kv q8_0/q4_0} × {flash-attention auto/on}`: each combo is fit-checked, restarted, benchmarked, and rolled back on failure; the fastest safe winner stays applied and results are kept in state history.

### llama.cpp runtime updates

Runtime updates are durable operations. Setup provisions the container and toolchain, then installs the first runtime through the same pinned update workflow used later — llama.cpp is never cloned ad hoc. Updates are **never automatic**: each release ships a `KNOWN_GOOD_LLAMACPP` pin vetted for BC-250/GFX1013 Vulkan, and `bc250-llm-mode llamacpp status` shows the promoted immutable build (content-derived ID), the retained rollback target, and any recovery barrier.

`llamacpp update [--tag TAG]` resolves the ref to an exact commit, builds an operation-owned candidate with bounded processes, smoke-checks and hashes every binary into an immutable manifest, exchanges it with the active tree in ONE atomic filesystem operation, restarts, and verifies the live process end-to-end (binary digest, handoff binding, fresh systemd invocation, expected model/context/slots, bounded inference) before promoting it; the previous build stays retained for rollback. Any failure that cannot be proven safe restores the prior runtime or stops in RECOVERY_REQUIRED — both trees are kept and nothing is guessed.

Operations run in the foreground: keep the window/terminal open until the outcome prints. Closing early leaves the operation durably paused; `llamacpp resume --operation-id …` continues it safely. `llamacpp rollback` restores the retained target (and can be undone without a rebuild). A latched thermal stop requires an explicit `thermals reset`. The System page exposes the same runtime actions with rollback enabled only when a verified target exists.


### Reboot safety policy

LLM Mode is intentionally limited to the current boot. Every setup, repair, and **Start current-boot LLM Mode** action guarantees that:

- the next boot target is the host's normal systemd `graphical.target`;
- `bc250-llm.service` is disabled for boot, although an explicitly started model may continue running now;
- sleep/display-manager masks and the AMD GPU-awake udev rule are runtime-only;
- any older app-owned Bazzite `amdgpu.runpm=0` kernel argument and `/etc` udev rule are removed from the next deployment.

On CachyOS, LLM Mode does not add or edit persistent kernel arguments. CachyOS may use systemd-boot, GRUB, Limine, or rEFInd; the active manager is reported, and an externally supplied `amdgpu.runpm=0` is surfaced with manual recovery guidance instead of being edited by guesswork.

After a reboot, the desktop starts normally and no LLM model is loaded. Starting inference again is an explicit GUI, CLI, or chat action.

## Daily use after setup

Running `bc250-llm-mode` after setup opens Home in the same native window.
Home, Models, Profiles, Chat, Connections, Activity, Maintenance, System,
Settings, and Help all stay in that
window. The experience provides:

- live state for the single `bc250-llm.service`, with **Start**, **Stop**, and **Restart** controls;
- Open WebUI installation-on-first-start plus **Start**, **Stop**, **Restart**,
  **Update pinned Open WebUI**, and **Open WebUI** controls; the update action
  preserves the named data volume and reapplies only the app-approved digest;
- optional Tailscale daemon **Start**, **Stop**, and **Restart** controls, with separate **Connect** and **Disconnect** actions;
- installed and newly discovered GGUF models, including validation/registration and safe switching through the one owning systemd service;
- named Interactive, Long context, Shared, Cool, Throughput, and bounded custom workload profiles with exact model/quant/context/slot/VRAM previews;
- a query-only Performance Coach that shows at most three evidence-labelled suggestions and never applies a change automatically;
- durable fixed-prompt calibration with thermal/fit preflight, cancellation only between candidates, exact prior-runtime restoration, and a separately applied winner proposal;
- a five-item, risk-ordered Maintenance inbox with explicit evidence freshness,
  an on-demand full check, cleanup preview, and optional fixed-copy local
  notifications that are disabled by default;
- a native guided Repair page whose closed actions show exact preconditions,
  mutation steps, reversibility, expected revisions, confirmation, verified
  result, and a privacy-safe local support handoff;
- durable storage quarantine for proven abandoned app staging, exact restore,
  explicit expired purge, and Undo only while the retained receipt, identity,
  deadline, lineage, and resource leases still prove the inverse is true;
- a bounded context-size control with a fresh VRAM fit check before restart;
- native bounded streaming chat plus optional terminal-chat launch;
- recent model-server and setup logs only when the in-window drawer is opened;
- `Ctrl+K` bounded local command search; protected results open their normal
  preview page and never execute from the palette;
- a bundled offline glossary plus keyboard guidance, 100–200% interface scale,
  reduced motion, written status labels, and adjacent text for important
  tables where Tk accessibility is weak;
- a Privacy Center that inventories conversation, log, operation, benchmark,
  credential, Open WebUI, backup, bundle, notification, and update-network
  behavior without reading those stores or offering a fictional telemetry
  toggle—the application has no telemetry;
- platform diagnostics, optimization controls, repair, current-boot LLM Mode, and non-destructive desktop mode.

One coalescing refresh coordinator updates service, API, WebUI, Tailscale,
sharing, activity, and memory state without multiplying timers. Background
results are generation-fenced, lists/transcripts/logs are bounded, and
minimized windows back off. Model, context, and user-slot activations are
transactional: if a new configuration fails its health check, the application
restores and restarts the last working configuration.

The GUI never starts `llama-server` directly. Every start, switch, and context change goes through `bc250-llm.service`, preserving the single-owner rule and preventing competing processes from consuming the UMA allocation. Closing the GUI in normal Desktop mode stops and verifies that service before exiting; minimizing the window does not. Closing the control window during explicit current-boot LLM Mode leaves that serving session running.

Tailscale is optional and is not installed by this application. On Linux, `tailscaled` is the systemd-managed daemon, while `tailscale up` joins/connects the machine to its tailnet. The app exposes those as separate actions so stopping the daemon is not confused with signing out or changing tailnet state. A first-time **Connect** may print an authentication URL in the application log.

## Choosing a model

The curated catalog currently includes forty models. Projected totals below use Q8 KV cache, the default four concurrent request slots, and approximately 1 GiB runtime overhead. Context values are per user/slot. The sixteen newest entries remain **Preview** until each completes a physical BC-250 Vulkan load and generation check.

| Model | Role | Recommended quant | 8k × 4 users | 16k × 4 users | 32k × 4 users |
| --- | --- | --- | ---: | ---: | ---: |
| [LFM2.5 2.6B](https://huggingface.co/LiquidAI/LFM2.5-2.6B-GGUF) | Agentic/long-context/multi-user | Q5_K_M | 3.06 GiB | 3.31 GiB | 3.81 GiB |
| [LFM2.5 1.2B Instruct](https://huggingface.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF) | Small chat/long-context/multi-user | Q5_K_M | 1.97 GiB | 2.16 GiB | 2.54 GiB |
| [Qwen3.8 9B (Empero distill)](https://huggingface.co/empero-ai/Qwen3.8-9B-Distill-GGUF) | Reasoning/function calling | Q4_K_M | 8.63 GiB | 10.88 GiB (tight) | No fit |
| [Qwen3.5 9B Instruct](https://huggingface.co/bartowski/Qwen_Qwen3.5-9B-GGUF) | General/reasoning | Q5_K_M | 9.52 GiB | 11.77 GiB (tight) | No fit |
| [Qwen3.8 9B Distill (converted)](https://huggingface.co/empero-ai/Qwen3.8-9B-Distill) | Reasoning/function calling (local conversion) | Q5_K_M | 9.49 GiB | 11.74 GiB (tight) | No fit |
| [The Defiant Fable 9B](https://huggingface.co/pipenetwork/Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-MLX-bf16) | Creative/uncensored conversion | Q5_K_M | 9.55 GiB | 11.80 GiB (tight) | No fit |
| [Qwen3 8B](https://huggingface.co/Qwen/Qwen3-8B-GGUF) | General/fast | Q5_K_M | 7.45 GiB | 8.45 GiB | 10.45 GiB |
| [Qwen3 14B](https://huggingface.co/bartowski/Qwen_Qwen3-14B-GGUF) | Larger general model | Q4_K_M | 10.63 GiB (tight) | 11.88 GiB (tight) | No fit |
| [Llama 3.2 3B Instruct](https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF) | Fast/low-power | Q8_0 | 5.94 GiB | 7.69 GiB | 11.19 GiB (tight) |
| [Llama 3.1 8B Instruct](https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF) | Mature general chat | Q5_K_M | 8.34 GiB | 10.34 GiB | No fit |
| [Qwen2.5 Coder 7B Instruct](https://huggingface.co/bartowski/Qwen2.5-Coder-7B-Instruct-GGUF) | Coding/debugging | Q6_K | 7.70 GiB | 8.57 GiB | 10.32 GiB |
| [DeepSeek R1 Distill Qwen 7B](https://huggingface.co/bartowski/DeepSeek-R1-Distill-Qwen-7B-GGUF) | Reasoning/math | Q6_K | 7.70 GiB | 8.57 GiB | 10.32 GiB |
| [Mistral Nemo 12B Instruct](https://huggingface.co/bartowski/Mistral-Nemo-Instruct-2407-GGUF) | Capable multilingual chat | Q5_K_M | 11.63 GiB (tight) | No fit | No fit |
| [Phi-4 14B](https://huggingface.co/bartowski/phi-4-GGUF) | Reasoning/math/code | Q4_K_M | No fit | No fit | No fit |
| [Qwen3 4B Instruct 2507](https://huggingface.co/bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF) | Fast general/long-context | Q4_K_M | 7.84 GiB | No fit | No fit |
| [Qwen2.5 3B Instruct](https://huggingface.co/bartowski/Qwen2.5-3B-Instruct-GGUF) | Tiny multi-user assistant | Q8_0 | 5.19 GiB | 6.31 GiB | 8.56 GiB |
| [Mistral 7B Instruct v0.3](https://huggingface.co/bartowski/Mistral-7B-Instruct-v0.3-GGUF) | Classic general chat | Q5_K_M | 10.15 GiB | No fit | No fit |
| [DeepSeek R1 Distill Llama 8B](https://huggingface.co/bartowski/DeepSeek-R1-Distill-Llama-8B-GGUF) | Reasoning/math | Q5_K_M | 10.34 GiB | No fit | No fit |
| [Gemma 2 9B IT](https://huggingface.co/bartowski/gemma-2-9b-it-GGUF) | Prose quality (single user, 8K) | Q4_K_M | No fit | No fit | No fit |
| [Llama 3.2 1B Instruct](https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF) | Ultra-light/multi-user | Q8_0 | 3.32 GiB | 4.32 GiB | 6.32 GiB |
| [Ornith 1.5 9B](https://huggingface.co/ornith-ai/Ornith-1.5-9B-GGUF) | Newest generalist (compat. candidate) | Q6_K | 10.48 GiB | No fit | No fit |
| [Qwen3.8 2B Distill](https://huggingface.co/empero-ai/Qwen3.8-2B-Distill-GGUF) | Small reasoning/long context | Q8_0 | 5.40 GiB | 7.65 GiB | No fit |
| [Qwen2.5 Coder 3B Instruct](https://huggingface.co/Qwen/Qwen2.5-Coder-3B-Instruct-GGUF) | Smallest dedicated coder | Q8_0 | 5.49 GiB | 6.62 GiB | 8.87 GiB |
| [Qwen2.5 Coder 14B Instruct](https://huggingface.co/bartowski/Qwen2.5-Coder-14B-Instruct-GGUF) | Largest coder that fits (single user) | Q4_K_M | No fit² | No fit | No fit |
| [SmolLM2 360M Instruct](https://huggingface.co/HuggingFaceTB/SmolLM2-360M-Instruct-GGUF) | Ultra-small appliance checks | Q8_0 | 2.61 GiB | Trained limit 8K | Trained limit 8K |
| [Qwen2.5 0.5B Instruct](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF) | Tiny multilingual/multi-user | Q8_0 | 2.00 GiB | 2.38 GiB | 3.13 GiB |
| [Qwen3 0.6B](https://huggingface.co/Qwen/Qwen3-0.6B-GGUF) | Tiny reasoning/chat | Q8_0 | 5.10 GiB | 8.60 GiB | No fit |
| [TinyLlama 1.1B Chat](https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF) | Mature tiny baseline | Q8_0 | Trained limit 2K | Trained limit 2K | Trained limit 2K |
| [DeepSeek R1 Distill Qwen 1.5B](https://huggingface.co/bartowski/DeepSeek-R1-Distill-Qwen-1.5B-GGUF) | Small reasoning/math | Q8_0 | 3.64 GiB | 4.51 GiB | 6.26 GiB |
| [Qwen3 1.7B](https://huggingface.co/Qwen/Qwen3-1.7B-GGUF) | Compact reasoning/chat | Q8_0 | 6.21 GiB | 9.71 GiB | No fit |
| [SmolLM2 1.7B Instruct](https://huggingface.co/HuggingFaceTB/SmolLM2-1.7B-Instruct-GGUF) | Compact general assistant | Q4_K_M | 7.98 GiB | Trained limit 8K | Trained limit 8K |
| [Gemma 2 2B IT](https://huggingface.co/bartowski/gemma-2-2b-it-GGUF) | Compact prose/general chat | Q8_0 | 6.84 GiB | Trained limit 8K | Trained limit 8K |
| [Phi-3.5 Mini Instruct](https://huggingface.co/bartowski/Phi-3.5-mini-instruct-GGUF) | Mature small reasoning/code | Q4_K_M | No fit³ | No fit | No fit |
| [Phi-4 Mini Instruct](https://huggingface.co/bartowski/microsoft_Phi-4-mini-instruct-GGUF) | Modern small reasoning/code | Q8_0 | 8.80 GiB | No fit | No fit |
| [Gemma 3 4B IT](https://huggingface.co/bartowski/google_gemma-3-4b-it-GGUF) | Text-only multilingual/general | Q8_0 | 9.10 GiB | No fit | No fit |
| [OpenHermes 2.5 Mistral 7B](https://huggingface.co/TheBloke/OpenHermes-2.5-Mistral-7B-GGUF) | Mature ChatML/tool-use baseline | Q5_K_M | 9.78 GiB | No fit | No fit |
| [Falcon3 10B Instruct](https://huggingface.co/tiiuae/Falcon3-10B-Instruct-GGUF) | Large multilingual generalist | Q4_K_M | 11.86 GiB (tight) | No fit | No fit |
| [Gemma 3 12B IT](https://huggingface.co/bartowski/google_gemma-3-12b-it-GGUF) | Large text-only generalist | Q4_K_M | No fit³ | No fit | No fit |
| [Qwen2.5 14B Instruct](https://huggingface.co/bartowski/Qwen2.5-14B-Instruct-GGUF) | Large mature generalist | Q3_K_M | No fit³ | No fit | No fit |
| [DeepSeek Coder V2 Lite 16B](https://huggingface.co/bartowski/DeepSeek-Coder-V2-Lite-Instruct-GGUF) | Large 2.4B-active MoE coder | Q3_K_M | 10.57 GiB (tight) | No fit | No fit |

² Qwen2.5 Coder 14B carries a heavy 192 KiB/token KV cache: plan a single user at 8K (Q3_K_M 9.51 GiB FITS; Q4_K_M 11.49 GiB TIGHT).

³ These larger or KV-heavy entries are intended for one short-context slot. The four-user table is deliberately fail-closed; reduce **User slots** before choosing them.

The expansion now spans 360M through 16B total parameters: ultra-small appliance checks, current and mature chat baselines, reasoning, multilingual, coding, and a 2.4B-active MoE. Every direct-download catalog entry uses a literal verified filename rather than a wildcard, preventing the durable downloader from selecting projectors, split files, or incompatible layouts. Gemma 2 9B IT carries a wide 336 KiB/token KV cache and a native 8192-token limit, so it remains a single-user short-context model: it projects to roughly 9.07 GiB for one slot at 8K and no fit for four shared slots.

LFM2.5 is especially useful when several clients share the server. LiquidAI advertises a 128K-trained context window, and its hybrid convolution/attention layout uses only eight attention layers in the 2.6B model and six in the 1.2B model. At Q8 KV this projects to roughly 8 KiB/token and 6 KiB/token respectively. A 128K LFM2.5 2.6B Q5 configuration projects to about 3.78 GiB for one slot or 6.71 GiB for four; the 1.2B Instruct model projects to about 2.52 GiB for one slot or 4.71 GiB for four.

The LFM2.5 2.6B Q5 model is hardware-validated on the project BC-250: Vulkan loaded 128,000 tokens per slot across four slots, measured about 6.54 GiB VRAM in use, and produced approximately 121 tokens/second in the smoke-test response. The 1.2B Instruct entry uses official GGUF/config metadata and remains a compatibility candidate until separately tested on-card.

Qwen3.8 9B is Empero's full-parameter reasoning distillation into the Qwen3.5-9B architecture, not an official Alibaba Qwen model release. The catalog downloads Empero's exact standard-layout GGUF filenames and deliberately excludes BF16, MTP, vision-projector, fused, and MAX artifacts. Its model card requires a recent llama.cpp build with Qwen3.5/Gated DeltaNet support. It is currently a metadata-validated compatibility candidate until it completes an on-card Vulkan load and generation test.

The separate [Qwen3.8 9B Distill](https://huggingface.co/empero-ai/Qwen3.8-9B-Distill) release ships only a 19.3 GB BF16 safetensors checkpoint — no GGUF — so the catalog entry uses the same local-conversion workflow as The Defiant Fable: the wizard downloads the official checkpoint (plus tokenizer/config files), converts it inside the container with `convert_hf_to_gguf.py`, quantizes to a standard per-tensor K-quant (Q5_K_M recommended; Q6_K available), applies the guarded `qwen3_5` block-count/nextn repairs, and verifies before activation. Approximately 46 GiB of temporary space coexists during conversion. The multimodal projector is excluded: this catalog is text-only. Sampling follows the publisher's reasoning profile (`temperature 0.6`, `top-p 0.95`, `top-k 20`, `min-p 0`).

Catalog entries can carry model-specific sampling profiles. Qwen3.8 launches with the publisher-recommended temperature `0.6`, top-p `0.95`, top-k `20`, and min-p `0`; older installed records retain conservative application defaults. The generated launcher is refreshed on every controlled server restart.

The context control is per user/slot. The launcher reserves `context × slots` in llama.cpp, and the VRAM fit engine applies the same multiplier before allowing a restart. Select 1–8 **User slots** on the Optimization page. More slots allow more simultaneous requests, but reserve more KV memory and divide available compute throughput; reducing slots restores headroom for larger models.

The original seed models and Defiant Fable workflow come from direct BC-250 development. Compatibility candidates are selected because current llama.cpp supports their architectures, their verified Hugging Face artifacts are single-file standard K-quants, and their projected allocations fit the BC-250 budget. Models not explicitly called hardware-validated may not yet have been individually load-tested on the target unit. Treat the fit badge as a conservative planning tool and report real-hardware results.

Only the listed standard K-quants are offered for the larger candidates. I-quants, ARM/CPU-interleaved formats, multimodal projectors, MTP artifacts, and fused/MAX repacks remain excluded.

The fit calculation is:

```text
required GiB = weights GiB + (context per user × user slots × KV bytes/token) + approximately 1 GiB overhead
```

Up to approximately 10.5 GiB is marked comfortable, 10.5–12 GiB is tight, and anything above the 12 GiB fast UMA budget is rejected as a safe fit. The approximately 2.5 GiB GTT spill area is slower and is not treated as dependable fast-model capacity.

### Reusing GGUF models already on disk

The Model Selection page searches:

- `~/.bc250-llm-mode/models`;
- paths of models already registered in application state;
- conventional `models`/`Models` directories;
- any directory selected with **Add folder…**.

Existing files appear with an **Installed** source label. Selecting one skips downloading, but the Prepare step still validates it before the server can use it. Discovery reads directory entries and file sizes rather than loading multi-gigabyte weights into host RAM.

The scanner excludes fused/MAX/imatrix-MAX files, vision projectors, MTP artifacts, undersized/incomplete files, and temporary f16 conversion outputs. Unknown standard GGUF models receive a conservative KV-cache estimate until validated.

## Optimization controls

All host changes start unchecked. The page validates every numeric range before applying it.

- **Runtime:** Flash Attention (`auto`, `on`, or `off`), Q8/Q4 KV cache, batch size 128–2048, micro-batch size 64–512, and 1–8 concurrent request slots (default 4).
- **BC-250 GPU tuning (Cyan controller):** 500–1200 MHz minimum, 1500–2000 MHz maximum, 75–90°C throttle, and 60–85°C recovery. Recovery must remain at least 5°C below throttling. This tunes the onboard BC-250 compute units used by Vulkan; it does not switch inference to the CPU.
- **Server safeguards:** restart window 60–900 seconds, burst 1–10, delay 5–60 seconds, and server-log rotation at 10–500 MiB.
- **Memory policy:** optional persistent swappiness from 10–200.
- **Service trimming:** individual opt-in disabling of selected gaming/desktop services to recover host RAM.

Original Cyan, service, and swappiness settings are remembered and restored when their options are disabled or the application is reverted.

## Tailnet HTTPS access

The model server and Open WebUI remain bound to localhost. The application can publish both through Tailscale Serve with automatically managed HTTPS, without opening the raw llama.cpp listener on the LAN or public Internet:

```text
Open WebUI: https://<node>.<tailnet>.ts.net:8443/
OpenAI Base URL: https://<node>.<tailnet>.ts.net:10000/v1
Models:     https://<node>.<tailnet>.ts.net:10000/v1/models
Chat:       https://<node>.<tailnet>.ts.net:10000/v1/chat/completions
Health:     https://<node>.<tailnet>.ts.net:10000/health
```

Use **Connections** in the native window to create a separately revocable key
for each phone, browser, or tool. Enter the displayed **Base URL**, one-time
**API Key**, and observed **Model** exactly. The key is shown once for 30
seconds; existing keys cannot be revealed again and must be rotated. Client
labels never enter filenames, and SQLite stores only SHA-256 fingerprints,
scopes, bounded timestamps, endpoint class, and revision—not the key, address,
model history, prompt, or response.

The page checks model and gateway health, a positive and mandatory negative
local request, Tailscale DNS, exact Serve targets, Funnel disabled, authorized
and unauthorized tailnet requests, and one bounded SSE event. A green state is
not inferred from health alone. Bundled setup cards currently label Open WebUI,
generic OpenAI, curl, and SSE as **protocol-tested** and PocketPal/Python as
**example-only** until the exact-candidate physical checklist in
`docs/connection-physical-qualification.md` passes.

Use the Tailnet HTTPS control in System, or:

```bash
bc250-llm-mode serve start
bc250-llm-mode serve status
```

Enabling sharing starts the selected model and Open WebUI, removes any public Funnel rules on the two managed ports, and creates tailnet-only HTTPS proxies. Disabling sharing removes only those proxies; it does not stop the local model or delete data. Because the machine intentionally returns to desktop with no LLM after reboot, the HTTPS proxy configuration may remain present while the backend is offline until the user starts a model again.

The API uses the standard llama.cpp/OpenAI-compatible routes. There is no raw `/api` route; use `/v1/models` and `/v1/chat/completions`.

CLI parity is available without exposing keys in JSON:

```bash
bc250-llm-mode connections status
bc250-llm-mode connections clients
bc250-llm-mode connections add-client --label "My phone" --type pocketpal
bc250-llm-mode connections instructions pocketpal
bc250-llm-mode connections test <client-id>
bc250-llm-mode connections rotate-client <client-id>
bc250-llm-mode connections revoke-client <client-id>
bc250-llm-mode connections disable-all
```

Creating or rotating is refused when output is not an interactive terminal,
because the new key cannot be safely shown once. `disable-all` is an emergency
credential revocation path and remains available when llama.cpp is unhealthy.

## Chat and server usage

After setup, start the terminal client with:

```bash
~/.bc250-llm-mode/app-venv/bin/bc250-llm-mode chat
```

The client streams responses and retains conversation history for the current session. Every request enables llama.cpp prompt caching (`cache_prompt`), so follow-up turns reuse the KV cache for the shared prefix instead of reprocessing it, and each answer ends with a live tokens/second reading from the server's own timing data. It is also a full management console. Available commands are:

| Command | Purpose |
| --- | --- |
| `/help` | Show chat commands |
| `/status` | Show server health, active model/context, and VRAM usage |
| `/model` | List installed models |
| `/model <id>` | Switch to an installed model through the single systemd service |
| `/scan` | Find compatible standard-layout GGUF files in configured model folders |
| `/ctx <tokens>` | Change context size from 512 to 262144 after a fit check |
| `/slots <1-8>` | Set concurrent request slots after a multiplied KV/VRAM fit check |
| `/llm start\|stop\|restart\|status\|ensure` | Manage the single systemd-owned model server; `ensure` self-heals (starts when stopped, restarts when unhealthy) |
| `/recommend [tag]` | Suggest catalog models that safely fit the current context/slots budget |
| `/webui start\|stop\|restart\|status` | Install/start or manage Open WebUI |
| `/tailscale start\|stop\|restart\|status\|connect\|disconnect` | Manage the optional daemon and tailnet connection separately |
| `/serve start\|stop\|restart\|status` | Manage tailnet-only HTTPS for Open WebUI and the model API |
| `/logs [server\|setup] [lines]` | Show 1–1000 recent log lines |
| `/sys` | Show GPU temperature, clocks, utilization, and memory metrics |
| `/bench [tokens]` | Measure prompt-processing and generation speed (tokens/second) |
| `/save [name]` / `/load [name]` | Save or restore the conversation under `~/.bc250-llm-mode/conversations/` |
| `/system [text\|clear]` | Show, set, or clear the system prompt for this session |
| `/temp <0.0-2.0\|off>` | Per-request sampling temperature override (server default when off) |
| `/think on\|off` | Hide or show `<think>` reasoning blocks (raw text is always stored) |
| `/trim [messages]` | Drop oldest turns; auto-trim also runs near the context limit |
| `/export [name]` | Export this conversation as Markdown |
| `/retry` | Regenerate the last answer from the same history |
| `/clear` | Clear the current in-memory conversation |
| `/quit` | Exit the terminal client |

`llama-server` and Open WebUI remain local-only backends. When explicitly enabled, Tailscale Serve terminates HTTPS and proxies tailnet traffic to them; public Funnel is not used.

## Command reference

Use the full virtual-environment path shown below, or activate the environment and run `bc250-llm-mode` directly.

```text
bc250-llm-mode                         Open setup or the completed management GUI
bc250-llm-mode gui [--route PAGE]      Open or activate the one native GUI instance
bc250-llm-mode desktop-integration     status | plan | install | remove the
  <action>                             user-local application-menu launcher
bc250-llm-mode setup                   Open/resume the native wizard
bc250-llm-mode repair                  Restart validation and safely rerun setup
bc250-llm-mode status                  Print hardware, saved state, and server status as JSON
bc250-llm-mode privacy                 Print the query-only local data and network inventory
bc250-llm-mode platform [status|plan]  Detect host support and print read-only package/boot plans
bc250-llm-mode memory-profile          Analyze the fixed boot-time UMA split and host-RAM safety
bc250-llm-mode chat                    Start terminal chat
bc250-llm-mode llm <action>            start | stop | restart | status | ensure
                                       (ensure self-heals: starts when stopped, restarts when sick)
bc250-llm-mode webui <action>          start | stop | restart | status
bc250-llm-mode tailscale <action>      start | stop | restart | status | connect | disconnect
bc250-llm-mode serve <action>          start | stop | restart | status (tailnet HTTPS)
bc250-llm-mode connections status|clients
                                       Show exact endpoints/readiness or redacted clients
bc250-llm-mode connections add-client --label NAME [--type TYPE] [--scope SCOPE]
                                       Create one independently revocable client (TTY only)
bc250-llm-mode connections rotate-client CLIENT [--overlap-seconds 0..900]
bc250-llm-mode connections revoke-client CLIENT
bc250-llm-mode connections disable-all Emergency-revoke all remote API clients
bc250-llm-mode connections instructions TYPE
bc250-llm-mode connections test CLIENT Run required positive/negative/SSE probes
bc250-llm-mode models list             List registered models
bc250-llm-mode models scan             Discover compatible local GGUF models
bc250-llm-mode models search [query]   Search the catalog by tag/name with live fit
                                       recommendations at the current context/slots
bc250-llm-mode models recommend        Rank catalog models that safely fit a budget
  [--ctx N] [--slots N] [--tag X] [--limit N]
bc250-llm-mode models use <model-id>   Select an installed/discovered model and restart safely
bc250-llm-mode profiles list|show      List or inspect built-in/custom workload profiles
bc250-llm-mode profiles preview        Preview or compare up to three profiles without writes
bc250-llm-mode profiles create|edit|delete
                                       Manage bounded custom profiles; edits never alter a running server
bc250-llm-mode profiles apply <id> --revision N --fingerprint SHA256
                                       Apply one exact preview through durable model activation
bc250-llm-mode coach [--profile ID] [--model ID] [--ctx N] [--users 1-8]
                                       Show up to three query-only evidence-bound suggestions
bc250-llm-mode calibrate --profile ID [--model ID] [--accept-tight]
                                       Run up to three restored fixed-prompt trials; never auto-apply
bc250-llm-mode bench [--max-tokens N] [--repeat 1-10] [--prompt "..."]
                                       Measure generation speed; repeats report min/median/max
bc250-llm-mode doctor                  Run local diagnostics and print a JSON report
bc250-llm-mode maintenance status      Show the bounded cached/live Maintenance inbox
bc250-llm-mode maintenance check       Explicitly refresh doctor, storage, topology,
                                       optional-service, and thermal evidence
bc250-llm-mode maintenance cleanup --dry-run
                                       Preview ranked cleanup candidates; never deletes
bc250-llm-mode repair list
bc250-llm-mode repair preview ACTION [TARGET]
bc250-llm-mode repair run ACTION [TARGET] --preview SHA256 --confirm TOKEN
bc250-llm-mode repair verify ACTION [TARGET]
                                       Inspect, run, and verify one closed typed repair
bc250-llm-mode storage cleanup --dry-run [--mode QUARANTINE|RESTORE|PURGE]
  [--target OPAQUE-ID]                 Preview exact app-owned durable cleanup
bc250-llm-mode storage cleanup --apply --preview SHA256 --confirm TOKEN
  [--mode MODE] [--target OPAQUE-ID]   Apply only that unexpired cleanup preview
bc250-llm-mode undo list
bc250-llm-mode undo preview UNDO-ID
bc250-llm-mode undo run UNDO-ID --preview SHA256 --confirm TOKEN
                                       Run an exact evidence-backed child inverse
bc250-llm-mode notifications status|test
                                       Show redacted delivery state or send fixed test copy
bc250-llm-mode notifications set CATEGORY on|off
                                       Change master/all or one closed category preference
bc250-llm-mode support-bundle --output DIRECTORY
                                       Create one bounded redacted local diagnostic bundle
bc250-llm-mode backup create LABEL [--include-models] [--include-runtime]
bc250-llm-mode backup list|verify [BACKUP-ID]
bc250-llm-mode restore inspect BACKUP-ID
bc250-llm-mode restore start BACKUP-ID --confirmation-digest SHA256
bc250-llm-mode restore status OPERATION-ID
                                       Create, inspect, and run durable profile backup/restore
bc250-llm-mode update status|check
bc250-llm-mode update preview VERSION
bc250-llm-mode update import-bundle ARCHIVE
bc250-llm-mode update apply VERSION --preview SHA256 --confirm TOKEN
bc250-llm-mode update rollback [--preview SHA256 --confirm TOKEN]
bc250-llm-mode update cleanup --dry-run
                                       Verify, preview, apply, roll back, or clean signed releases
bc250-llm-mode autotune [--repeat 1-3] [--max-tokens N]
                                       Benchmark runtime combos and apply the fastest safe one
bc250-llm-mode thermals status|once|watch [--interval SEC]
                                       GPU thermal watchdog controls
bc250-llm-mode llamacpp status|update|rollback [--tag TAG]
                                       Manage the pinned llama.cpp Vulkan build;
                                       staged rebuild, atomic switch, auto-rollback
bc250-llm-mode ctx <tokens>            Change context after a VRAM fit check and restart
bc250-llm-mode slots <1-8>             Set concurrent users after a VRAM fit check and restart
bc250-llm-mode boot-policy [status|desktop]
                                        Show or restage desktop/no-LLM next boot
bc250-llm-mode logs [server|setup]     Tail a log [--lines 1..1000]
bc250-llm-mode llm-mode                Start LLM Mode for the current boot only
bc250-llm-mode install-model <id>      Install a curated catalog model; without --quant the
  [--quant <quant>] [--ctx <tokens>]   best-fitting quantization is chosen automatically
bc250-llm-mode switch <model-id>       Switch the single server to an installed model
bc250-llm-mode desktop-mode [--now]    Restore the regular Linux desktop mode
bc250-llm-mode uninstall               Remove the service and revert LLM Mode
  [--remove-container] [--remove-models]
```

## Returning to regular desktop mode

This is the non-destructive way to leave dedicated LLM Mode while keeping downloaded models and setup data:

```bash
~/.bc250-llm-mode/app-venv/bin/bc250-llm-mode desktop-mode --now
```

It stops/disables the model service, stops retained LLM/Open WebUI containers, restores `graphical.target`, unmasks sleep and display-manager units, removes the GPU-awake rule, and reverts opted-in host optimizations. App-owned `amdgpu.runpm=0` is removed automatically on Bazzite; externally managed CachyOS boot arguments are reported without being guessed or edited. `--now` starts the graphical target in the current boot. Without it, the graphical desktop starts after the next reboot.

Reboot when the command reports that a kernel-argument change is pending:

```bash
systemctl reboot
```

The command does not delete models, containers, or setup records.

### Workload profiles

Use **Profiles** when the goal is more important than individual llama.cpp
flags. **Interactive** favors one responsive conversation, **Long context**
reserves more history, **Shared** budgets for concurrent users, **Cool** lowers
thermal pressure, and **Throughput** favors batch work. Select a profile and
review its exact model, context per user, concurrent slots, quantization, KV
cache, projected VRAM, thermal readiness, evidence class, and rollback status.
Applying always uses the normal durable activation path; a failed verification
restores the last known-good runtime where that identity is still proven.

The Performance Coach shows no more than three evidence-labelled suggestions.
Estimated results are not hardware measurements. Calibration is explicit,
uses fixed prompts whose content is not retained in events, restores the prior
runtime after every trial, and proposes rather than automatically applies a
winner.

### Maintenance and privacy

**Maintenance** shows at most five items ordered by safety, recovery, security,
integrity, storage, backup, operation, update, then information. Normal refresh
uses existing evidence; **Run full check** is the explicit action that invokes
live diagnostics. Notifications are local, fixed-copy, privacy-safe, and off by
default. There is no tray process or notification polling thread.

Open **Settings → Privacy**, or run `bc250-llm-mode privacy`, for the same
query-only inventory of retained data, actual storage locations, retention,
network behavior, and safe management pages. BC250 LLM MODE has no telemetry
and does not send usage analytics. See
[`docs/accessibility-privacy.md`](docs/accessibility-privacy.md) for keyboard,
scale, screen-reader-limit, privacy, and network details.

## Repair, update, and uninstall

Open the native Maintenance · Repair page:

```bash
~/.bc250-llm-mode/app-venv/bin/bc250-llm-mode repair
```

Opening Repair does not reset setup or mutate the host. Choose a repair,
provide an opaque target only when requested, and preview it. The preview binds
the exact action, mutation steps, observed revisions/lease generations,
evidence digest, and expiry. **Run verified repair** is enabled only when every
precondition passes; the service re-observes the same facts immediately before
the effect and verifies the result afterward.

Storage cleanup defaults to a dry run and only discovers app-owned staging
associated with terminal durable operations. Quarantine is a same-filesystem,
no-replace move with a retained receipt. Undo is offered only while that exact
receipt and tree still verify, the seven-day deadline has not elapsed, no child
operation superseded it, and both storage resources are available. Purge is a
separate explicit irreversible mode after retention; it is never called Undo.
External models, active/known-good/runtime/backup/application/profile/
credential/conversation/log data, symlinks, special files, and mount crossings
are excluded.

Repair failure output contains stable IDs and bounded offline argv only. A
support bundle is created solely by an explicit local action, self-checks its
manifest, and is never uploaded by the application. One-time credential
material is excluded from JSON/support output and shown only through the
existing time-limited interactive reveal.

The exact physical qualification checklist is
[`docs/repair-physical-qualification.md`](docs/repair-physical-qualification.md).
No physical PASS is inferred from developer tests.

### Application updates (late-gated beta capability)

The native **Maintenance · Updates** page and matching `update` CLI never run
an automatic check, download, or install. They accept only a complete release
set that passes the project's sole release evaluator, immutable tag/commit,
manifest/inventory/checksum/SBOM, provenance, signature, platform-evidence,
and database-compatibility gates. Release notes are displayed as literal plain
text. There is no fallback to `pip install --upgrade`, a branch, an arbitrary
URL, or an unsigned local wheel.

```bash
bc250-llm-mode update status
bc250-llm-mode update check
bc250-llm-mode update preview VERSION
bc250-llm-mode update import-bundle /path/to/signed-release.tar
bc250-llm-mode update apply VERSION --preview DIGEST --confirm TOKEN
bc250-llm-mode update rollback                         # preview only
bc250-llm-mode update rollback --preview DIGEST --confirm TOKEN
bc250-llm-mode update cleanup --dry-run
```

An eligible update is staged into a new immutable venv without modifying the
running installation, smoke-tested offline, backed up, and published through
the `current`/`previous` two-slot pointers. A bounded replacement process
verifies the new slot, database, composition, model-library read, and host
observation while starting no model or managed service. Failure restores exact
evidence where safe or stops at `RECOVERY_REQUIRED`; the prior readable slot is
retained. Offline imports use the identical verifier and reject traversal,
links, special files, duplicate/unknown members, oversized content, and
mutation.

The development build intentionally packages no reviewed production signing
root or eligible release channel yet, so normal production status is
`SIGNED_UPDATE_CHANNEL_UNAVAILABLE`. This is an honest release gate, not a
prompt to bypass verification. Physical Bazzite/CachyOS update and rollback
qualification remains pending.

An **offline update bundle** is not a general installer and is not trusted
because it arrived on removable media. `update import-bundle` streams the
selected archive through the same manifest, inventory, checksum, SBOM,
provenance, signature, platform, schema, member-set, size, and path checks as
the signed channel. Until a qualified production signing root and release
exist, both online and offline production updates remain unavailable by
design.

Update a source checkout:

```bash
cd BC250-LLM-MODE
git pull --ff-only
~/.bc250-llm-mode/app-venv/bin/pip install --upgrade .
```

Revert LLM Mode and remove the server service while retaining models and containers:

```bash
~/.bc250-llm-mode/app-venv/bin/bc250-llm-mode uninstall
```

Optionally remove containers:

```bash
~/.bc250-llm-mode/app-venv/bin/bc250-llm-mode uninstall --remove-container
```

Permanently remove application-managed model files as well:

```bash
~/.bc250-llm-mode/app-venv/bin/bc250-llm-mode uninstall --remove-container --remove-models
```

> [!CAUTION]
> `--remove-models` permanently deletes model files inside the application-managed models directory. It cannot be undone by the app. Models selected from external folders are not deleted by this option.

The default uninstall preserves `~/.bc250-llm-mode/models/`, external model
folders, profile data, backups, conversations, and the Open WebUI volume. It
marks setup incomplete and removes/reverts the selected service and host-mode
integration. Reinstall the package into the same user profile, reinstall the
desktop entry if needed, and run setup; the Model chapter scans the managed
and configured external folders again. Use `--remove-models` only when the
permanent deletion is intentional. Removing the Open WebUI container does not
remove its named data volume.

## Files, services, and ports

Default paths are relative to the account that runs setup:

| Item | Default |
| --- | --- |
| Application state (SQLite) | `~/.bc250-llm-mode/state.db` |
| Runtime handoff (rendered, mode 0600) | `~/.bc250-llm-mode/runtime-handoff.json` |
| Legacy JSON | `~/.bc250-llm-mode/state.json` — immutable import source only |
| Managed models | `~/.bc250-llm-mode/models/` |
| Saved conversations (mode 0600) | `~/.bc250-llm-mode/conversations/` |
| Setup/worker/server logs | `~/.bc250-llm-mode/logs/` |
| Named client secret files (mode 0600) | `~/.bc250-llm-mode/connection-secrets/` |
| Durable profile backups | `~/.bc250-llm-mode/backups/` |
| Verified offline update bundles | `~/.bc250-llm-mode/update-bundles/` |
| Immutable application release slots | `~/.bc250-llm-mode/releases/` |
| Generated launcher | `~/.bc250-llm-mode/run-model.sh` |
| llama.cpp source/build in container | `/root/llama.cpp` |
| Preparation environment in container | `/root/.venvs/hf` |
| Podman/Distrobox container | `llm` |
| Model systemd service | `bc250-llm.service` |
| Root-run model server log | `/var/log/bc250-llm-server.log` |
| Model API | `127.0.0.1:8080` |
| Optional Open WebUI | `127.0.0.1:3000` |
| Open WebUI persistent data | Podman named volume `bc250-open-webui` |
| Tailnet HTTPS Open WebUI | `https://<node>.<tailnet>.ts.net:8443/` |
| Tailnet HTTPS model API | `https://<node>.<tailnet>.ts.net:10000/v1` |

When setup is run as root, the model server log is `/var/log/bc250-llm-server.log`. For a regular user installation, it is stored under the application's logs directory.

## Troubleshooting

Check the overall report:

```bash
~/.bc250-llm-mode/app-venv/bin/bc250-llm-mode status
```

Check the service and recent model log:

```bash
systemctl status bc250-llm.service
journalctl -u bc250-llm.service -n 100 --no-pager
tail -n 100 /var/log/bc250-llm-server.log
```

Common failure guidance:

- **No native window:** launch from the local desktop and run `bc250-llm-mode platform plan`. Bazzite stages `python3-tkinter` and requires a reboot; CachyOS installs `tk` and requires an app relaunch.
- **Low VRAM:** verify the BIOS UMA allocation is approximately 12 GiB GPU / 4 GiB OS.
- **14/2 or less than 3 GiB usable host RAM:** return to the supported 12/4 firmware split and reboot; this allocation cannot be changed safely at runtime.
- **Vulkan initialization fails:** run `repair` and repeat the environment smoke test.
- **Missing `blk.N` tensor:** GGUF block-count metadata may be inconsistent; the Prepare step attempts a guarded repair.
- **Missing `nextn` tensor:** unsupported MTP metadata may be declared; the Prepare step attempts a guarded repair.
- **`ErrorOutOfDeviceMemory` with MAX/fused in the filename:** that repack cannot load on this GPU; select a standard-layout GGUF.
- **Server unavailable:** inspect the model server log first; load failures are surfaced there automatically by the wizard.
- **Hugging Face rate limits:** set `HF_TOKEN` in the launch environment and retry the resumable download.

Never add `--no-mmap`. The BC-250 host has too little system RAM for full model copies. The application keeps mmap enabled and never intentionally reads an entire multi-gigabyte GGUF into host RAM.

## Development

Install test dependencies and run the suite:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```

The current suite covers hardware and memory-profile discovery, the safety gate, state migration, VRAM fit calculations, forbidden artifacts, catalog search, best-fit quantization selection, and budget-aware model recommendations, download-space and checksum safeguards, optimizer bounds including governor profiles and thermal limits, named workload-profile resolution and revision fencing, evidence-bound coaching, durable calibration crash/cancel recovery and exact restoration, idle-policy suppression, the hysteresis thermal watchdog state machine, launcher thread/cache-reuse generation, systemd memory guards, transactional activation rollback, existing-model discovery, GGUF metadata healing, service lifecycle management including self-healing restarts, Tailscale state separation, server generation, chat benchmarking with persisted history, streaming timing capture, reasoning-block filtering, conversation trimming/export/persistence, and desktop-mode reversion.

## License

MIT. See [LICENSE](LICENSE).
