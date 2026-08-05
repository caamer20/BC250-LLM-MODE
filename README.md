# BC250 LLM MODE

BC250 LLM MODE is a lightweight native desktop application, setup wizard, and terminal chat client for turning an AMD BC-250 running Bazzite into a dedicated local `llama.cpp`/Vulkan inference station—and operating it afterward from one place.

The interface is a real local `tkinter` window—not a web app. After the resumable setup flow completes, the same window becomes an operations dashboard for the model server, models, context, Open WebUI, Tailscale HTTPS sharing, logs, performance settings, and desktop/LLM mode transitions. The streaming terminal chat provides matching management commands.

> [!WARNING]
> **Public beta — use at your own risk.** BC250 LLM MODE is under active development and may contain bugs or incomplete behavior. It changes boot targets, sleep settings, kernel arguments, system services, GPU power policy, and—when explicitly selected—performance settings. These changes can cause instability, data loss, overheating, reduced hardware lifespan, or an unbootable system. Back up important data, provide adequate cooling, monitor temperatures, and understand every option before continuing. You are solely responsible for BIOS changes and for the consequences of running this software. The software is provided without warranty.

## Supported platform

This application is intentionally hardware- and operating-system-specific.

| Component | Supported or required configuration |
| --- | --- |
| Device | AMD BC-250 / GFX1013 (RDNA1, integrated UMA GPU) |
| Memory | 16 GB total with approximately 12 GiB assigned to GPU UMA and 4 GiB left for the OS |
| Operating system | Bazzite, using its Fedora Atomic/immutable, `rpm-ostree`, `systemd`, Podman, and Distrobox environment |
| Desktop | Bazzite KDE Plasma or another Bazzite desktop capable of opening a local `tkinter` window |
| Python | Python 3.11 or newer |
| Inference backend | `llama.cpp` built with Vulkan; CUDA and ROCm are not used |
| Model format | Standard per-tensor-layout GGUF models; fused/MAX/imatrix-MAX repacks are forbidden |

The software has been developed and tested for the BC-250 on Bazzite. It is not a general-purpose installer for Windows, macOS, Ubuntu, unrelated AMD GPUs, NVIDIA GPUs, or Apple Silicon. Other Fedora Atomic variants may look similar but are not currently supported or validated.

The BC-250 has no Re-Size BAR/Above-4G option that this tool can enable. Changing the recommended 12/4 UMA split does not solve the card's per-allocation limit, and fused/MAX repacks remain unsupported even when total free VRAM appears sufficient.

## Before installation

1. Make sure all 40 compute units are unlocked in BIOS/firmware if your board exposes that option.
2. Configure approximately 12 GiB GPU UMA and 4 GiB system memory on a 16 GB unit.
3. Install adequate cooling and arrange a way to monitor GPU temperature during sustained inference.
4. Keep at least 20 GiB free on the filesystem that will contain models. More space is needed for conversion workflows and multiple models.
5. Connect the BC-250 to the network for the initial Fedora container, build dependencies, `llama.cpp`, Python packages, and model downloads.
6. Back up important data. The wizard makes privileged system changes only after its mandatory acknowledgment screen, but this is still beta software.

Bazzite normally includes Podman and Distrobox. The wizard checks both and reports a clear error if either is unavailable.

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

The wizard invokes `pkexec` when available or falls back to `sudo` for privileged operations. Installing the Python package itself does not need `sudo` when the virtual environment is in your home directory.

### If the native GUI does not open

Bazzite may package `tkinter` separately. When `python3-tkinter` is absent, BC250 LLM MODE first shows the same mandatory safety gate using Zenity or an interactive terminal. After acknowledgment, it can stage the package with:

```text
rpm-ostree install python3-tkinter
```

Reboot after the package is staged, return to the Bazzite desktop, and run the launch command again. Progress is saved.

A plain SSH session cannot display the local desktop window unless graphical forwarding is configured. For the normal experience, launch the wizard from a terminal on the BC-250's physical desktop. SSH can still be used for commands such as `status`.

## What the setup wizard does

The wizard is resumable and each step is designed to be safe to run again:

1. **Hardware validation** — finds the AMD GPU by PCI vendor ID instead of assuming `card0` or `card1`; checks VRAM, GTT, host RAM, disk space, and Vulkan identity.
2. **Mandatory safety warning** — requires three checkboxes and the exact typed text `I ACCEPT` before any setup change.
3. **LLM Mode** — starts a current-boot inference session with runtime-only sleep masks and a vendor-matched GPU-awake rule. It simultaneously stages normal `graphical.target` desktop mode and disables model auto-start for the next boot.
4. **Inference environment** — creates or reuses the `llm` Distrobox/Podman container, builds `llama.cpp` with Vulkan, creates the model-preparation Python environment, and runs a Vulkan smoke test.
5. **Model selection** — offers the curated BC-250 catalog and GGUF models already present on disk, with live context/VRAM fit estimates.
6. **Optimize** — applies only the bounded options selected by the user. Host-level performance changes are opt-in.
7. **Download** — performs a resumable Hugging Face download, or skips the network entirely for an existing local GGUF.
8. **Prepare** — verifies GGUF architecture and tensor block metadata, applies guarded self-healing metadata patches when appropriate, and handles supported text-only conversion workflows.
9. **Server** — creates the single owning `bc250-llm.service`, starts the model, and checks `/health` and `/v1/models`.
10. **Open WebUI** — optionally starts Open WebUI on port 3000.
11. **Complete** — transitions into the persistent operations dashboard; setup does not become the application's only purpose.

The application never changes BIOS settings and never reboots the computer automatically.

### Reboot safety policy

LLM Mode is intentionally limited to the current boot. Every setup, repair, and **Start current-boot LLM Mode** action guarantees that:

- the next boot target is Bazzite's normal `graphical.target`;
- `bc250-llm.service` is disabled for boot, although an explicitly started model may continue running now;
- sleep/display-manager masks and the AMD GPU-awake udev rule are runtime-only;
- any older persistent `amdgpu.runpm=0` kernel argument and `/etc` udev rule are removed from the next deployment.

After a reboot, the desktop starts normally and no LLM model is loaded. Starting inference again is an explicit dashboard, CLI, or chat action.

## Operations dashboard after setup

Running `bc250-llm-mode` after setup opens the native management dashboard directly. It provides:

- live state for the single `bc250-llm.service`, with **Start**, **Stop**, and **Restart** controls;
- Open WebUI installation-on-first-start plus **Start**, **Stop**, **Restart**, and **Open WebUI** controls;
- optional Tailscale daemon **Start**, **Stop**, and **Restart** controls, with separate **Connect** and **Disconnect** actions;
- installed and newly discovered GGUF models, including validation/registration and safe switching through the one owning systemd service;
- a bounded context-size control with a fresh VRAM fit check before restart;
- recent model-server and setup logs in the existing live log pane;
- terminal chat launch, optimization controls, repair, current-boot LLM Mode, and non-destructive Bazzite desktop mode.

The dashboard never starts `llama-server` directly. Every start, switch, and context change goes through `bc250-llm.service`, preserving the single-owner rule and preventing competing processes from consuming the UMA allocation.

Tailscale is optional and is not installed by this application. On Linux, `tailscaled` is the systemd-managed daemon, while `tailscale up` joins/connects the machine to its tailnet. The app exposes those as separate actions so stopping the daemon is not confused with signing out or changing tailnet state. A first-time **Connect** may print an authentication URL in the application log.

## Choosing a model

The curated catalog currently includes twelve models. Projected totals below use Q8 KV cache, the default four concurrent request slots, and approximately 1 GiB runtime overhead. Context values are per user/slot.

| Model | Role | Recommended quant | 8k × 4 users | 16k × 4 users | 32k × 4 users |
| --- | --- | --- | ---: | ---: | ---: |
| [LFM2.5 2.6B](https://huggingface.co/LiquidAI/LFM2.5-2.6B-GGUF) | Agentic/long-context/multi-user | Q5_K_M | 3.06 GiB | 3.31 GiB | 3.81 GiB |
| [LFM2.5 1.2B Instruct](https://huggingface.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF) | Small chat/long-context/multi-user | Q5_K_M | 1.97 GiB | 2.16 GiB | 2.54 GiB |
| [Qwen3.5 9B Instruct](https://huggingface.co/bartowski/Qwen_Qwen3.5-9B-GGUF) | General/reasoning | Q5_K_M | 9.52 GiB | 11.77 GiB (tight) | No fit |
| [The Defiant Fable 9B](https://huggingface.co/pipenetwork/Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-MLX-bf16) | Creative/uncensored conversion | Q5_K_M | 9.55 GiB | 11.80 GiB (tight) | No fit |
| [Qwen3 8B](https://huggingface.co/Qwen/Qwen3-8B-GGUF) | General/fast | Q5_K_M | 7.45 GiB | 8.45 GiB | 10.45 GiB |
| [Qwen3 14B](https://huggingface.co/ggml-org/Qwen3-14B-GGUF) | Larger general model | Q4_K_M | 10.63 GiB (tight) | 11.88 GiB (tight) | No fit |
| [Llama 3.2 3B Instruct](https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF) | Fast/low-power | Q8_0 | 5.94 GiB | 7.69 GiB | 11.19 GiB (tight) |
| [Llama 3.1 8B Instruct](https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF) | Mature general chat | Q5_K_M | 8.34 GiB | 10.34 GiB | No fit |
| [Qwen2.5 Coder 7B Instruct](https://huggingface.co/bartowski/Qwen2.5-Coder-7B-Instruct-GGUF) | Coding/debugging | Q6_K | 7.70 GiB | 8.57 GiB | 10.32 GiB |
| [DeepSeek R1 Distill Qwen 7B](https://huggingface.co/bartowski/DeepSeek-R1-Distill-Qwen-7B-GGUF) | Reasoning/math | Q6_K | 7.70 GiB | 8.57 GiB | 10.32 GiB |
| [Mistral Nemo 12B Instruct](https://huggingface.co/bartowski/Mistral-Nemo-Instruct-2407-GGUF) | Capable multilingual chat | Q5_K_M | 11.63 GiB (tight) | No fit | No fit |
| [Phi-4 14B](https://huggingface.co/bartowski/phi-4-GGUF) | Reasoning/math/code | Q4_K_M | No fit | No fit | No fit |

LFM2.5 is especially useful when several clients share the server. LiquidAI advertises a 128K-trained context window, and its hybrid convolution/attention layout uses only eight attention layers in the 2.6B model and six in the 1.2B model. At Q8 KV this projects to roughly 8 KiB/token and 6 KiB/token respectively. A 128K LFM2.5 2.6B Q5 configuration projects to about 3.78 GiB for one slot or 6.71 GiB for four; the 1.2B Instruct model projects to about 2.52 GiB for one slot or 4.71 GiB for four.

The LFM2.5 2.6B Q5 model is hardware-validated on the project BC-250: Vulkan loaded 128,000 tokens per slot across four slots, measured about 6.54 GiB VRAM in use, and produced approximately 121 tokens/second in the smoke-test response. The 1.2B Instruct entry uses official GGUF/config metadata and remains a compatibility candidate until separately tested on-card.

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
- **Cyan GPU governor:** 500–1200 MHz minimum, 1500–2000 MHz maximum, 75–90°C throttle, and 60–85°C recovery. Recovery must remain at least 5°C below throttling.
- **Server safeguards:** restart window 60–900 seconds, burst 1–10, delay 5–60 seconds, and server-log rotation at 10–500 MiB.
- **Memory policy:** optional persistent swappiness from 10–200.
- **Service trimming:** individual opt-in disabling of selected gaming/desktop services to recover host RAM.

Original Cyan, service, and swappiness settings are remembered and restored when their options are disabled or the application is reverted.

## Tailnet HTTPS access

The model server and Open WebUI remain bound to localhost. The application can publish both through Tailscale Serve with automatically managed HTTPS, without opening the raw llama.cpp listener on the LAN or public Internet:

```text
Open WebUI: https://<node>.<tailnet>.ts.net:8443/
Model API:  https://<node>.<tailnet>.ts.net:10000/v1
Health:     https://<node>.<tailnet>.ts.net:10000/health
```

Use **Tailnet HTTPS → Enable** in the management GUI, or:

```bash
bc250-llm-mode serve start
bc250-llm-mode serve status
```

Enabling sharing starts the selected model and Open WebUI, removes any public Funnel rules on the two managed ports, and creates tailnet-only HTTPS proxies. Disabling sharing removes only those proxies; it does not stop the local model or delete data. Because the machine intentionally returns to desktop with no LLM after reboot, the HTTPS proxy configuration may remain present while the backend is offline until the user starts a model again.

The API uses the standard llama.cpp/OpenAI-compatible routes. There is no raw `/api` route; use `/v1/models` and `/v1/chat/completions`.

## Chat and server usage

After setup, start the terminal client with:

```bash
~/.bc250-llm-mode/app-venv/bin/bc250-llm-mode chat
```

The client streams responses and retains conversation history for the current session. It is also a full management console. Available commands are:

| Command | Purpose |
| --- | --- |
| `/help` | Show chat commands |
| `/status` | Show server health, active model/context, and VRAM usage |
| `/model` | List installed models |
| `/model <id>` | Switch to an installed model through the single systemd service |
| `/scan` | Find compatible standard-layout GGUF files in configured model folders |
| `/ctx <tokens>` | Change context size from 512 to 262144 after a fit check |
| `/slots <1-8>` | Set concurrent request slots after a multiplied KV/VRAM fit check |
| `/llm start\|stop\|restart\|status` | Manage the single systemd-owned model server |
| `/webui start\|stop\|restart\|status` | Install/start or manage Open WebUI |
| `/tailscale start\|stop\|restart\|status\|connect\|disconnect` | Manage the optional daemon and tailnet connection separately |
| `/serve start\|stop\|restart\|status` | Manage tailnet-only HTTPS for Open WebUI and the model API |
| `/logs [server\|setup] [lines]` | Show 1–1000 recent log lines |
| `/sys` | Show GPU temperature, clocks, utilization, and memory metrics |
| `/clear` | Clear the current in-memory conversation |
| `/quit` | Exit the terminal client |

`llama-server` and Open WebUI remain local-only backends. When explicitly enabled, Tailscale Serve terminates HTTPS and proxies tailnet traffic to them; public Funnel is not used.

## Command reference

Use the full virtual-environment path shown below, or activate the environment and run `bc250-llm-mode` directly.

```text
bc250-llm-mode                         Open setup or the completed management GUI
bc250-llm-mode setup                   Open/resume the native wizard
bc250-llm-mode repair                  Restart validation and safely rerun setup
bc250-llm-mode status                  Print hardware, saved state, and server status as JSON
bc250-llm-mode chat                    Start terminal chat
bc250-llm-mode llm <action>            start | stop | restart | status
bc250-llm-mode webui <action>          start | stop | restart | status
bc250-llm-mode tailscale <action>      start | stop | restart | status | connect | disconnect
bc250-llm-mode serve <action>          start | stop | restart | status (tailnet HTTPS)
bc250-llm-mode models list             List registered models
bc250-llm-mode models scan             Discover compatible local GGUF models
bc250-llm-mode models use <model-id>   Select an installed/discovered model and restart safely
bc250-llm-mode ctx <tokens>            Change context after a VRAM fit check and restart
bc250-llm-mode slots <1-8>             Set concurrent users after a VRAM fit check and restart
bc250-llm-mode boot-policy [status|desktop]
                                        Show or restage desktop/no-LLM next boot
bc250-llm-mode logs [server|setup]     Tail a log [--lines 1..1000]
bc250-llm-mode llm-mode                Start LLM Mode for the current boot only
bc250-llm-mode install-model <id>      Install a curated catalog model
  [--quant <quant>] [--ctx <tokens>]
bc250-llm-mode switch <model-id>       Switch the single server to an installed model
bc250-llm-mode desktop-mode [--now]    Restore regular Bazzite desktop mode
bc250-llm-mode uninstall               Remove the service and revert LLM Mode
  [--remove-container] [--remove-models]
```

## Returning to regular Bazzite desktop mode

This is the non-destructive way to leave dedicated LLM Mode while keeping downloaded models and setup data:

```bash
~/.bc250-llm-mode/app-venv/bin/bc250-llm-mode desktop-mode --now
```

It stops/disables the model service, stops retained LLM/Open WebUI containers, restores `graphical.target`, unmasks sleep and display-manager units, removes the GPU-awake rule and `amdgpu.runpm=0`, and reverts opted-in host optimizations. `--now` starts the graphical target in the current boot. Without it, the graphical desktop starts after the next reboot.

Reboot when the command reports that a kernel-argument change is pending:

```bash
systemctl reboot
```

The command does not delete models, containers, or setup records.

## Repair, update, and uninstall

Rerun the idempotent setup flow:

```bash
~/.bc250-llm-mode/app-venv/bin/bc250-llm-mode repair
```

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

## Files, services, and ports

Default paths are relative to the account that runs setup:

| Item | Default |
| --- | --- |
| Application state | `~/.bc250-llm-mode/state.json` |
| Managed models | `~/.bc250-llm-mode/models/` |
| Setup log | `~/.bc250-llm-mode/logs/setup.log` |
| Generated launcher | `~/.bc250-llm-mode/run-model.sh` |
| llama.cpp source/build in container | `/root/llama.cpp` |
| Preparation environment in container | `/root/.venvs/hf` |
| Podman/Distrobox container | `llm` |
| Model systemd service | `bc250-llm.service` |
| Root-run model server log | `/var/log/bc250-llm-server.log` |
| Model API | `127.0.0.1:8080` |
| Optional Open WebUI | `127.0.0.1:3000` |
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

- **No native window:** launch from the local Bazzite desktop; if prompted, stage `python3-tkinter`, reboot, and retry.
- **Low VRAM:** verify the BIOS UMA allocation is approximately 12 GiB GPU / 4 GiB OS.
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

The current suite covers hardware discovery, the safety gate, state migration, VRAM fit calculations, forbidden artifacts, optimizer bounds, existing-model discovery, GGUF metadata healing, service lifecycle management, Tailscale state separation, server generation, and desktop-mode reversion.

## License

MIT. See [LICENSE](LICENSE).
