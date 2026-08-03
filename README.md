# BC250 LLM MODE

A lightweight native `tkinter` setup wizard and terminal chat client for turning an AMD BC-250 running Bazzite into a local llama.cpp/Vulkan station.

> [!WARNING]
> **Public beta — use at your own risk.** BC250 LLM MODE is still under active development and may contain bugs or incomplete behavior. It changes boot targets, sleep settings, kernel arguments, system services, GPU power policy, and—when explicitly selected—performance settings. These changes can cause instability, data loss, overheating, reduced hardware lifespan, or an unbootable system. Back up important data, provide adequate cooling, monitor temperatures, and understand every option before continuing. You are solely responsible for BIOS changes and for the consequences of running this software. The software is provided without warranty.

The application treats the hardware constraints as invariants: it discovers the AMD GPU by PCI vendor ID rather than `cardN`, budgets against 12 GiB fast UMA VRAM, never passes `--no-mmap`, excludes fused/MAX repacks, and reads only the first 4 MiB when applying guarded GGUF metadata patches.

## Install and run

On the BC-250, install the application into a small host Python environment:

```bash
python3 -m venv ~/.bc250-llm-mode/app-venv
~/.bc250-llm-mode/app-venv/bin/pip install .
~/.bc250-llm-mode/app-venv/bin/bc250-llm-mode
```

The first command with no subcommand opens the local native wizard. Run it from the BC-250 desktop session. Privileged steps use `pkexec` when available, otherwise `sudo`. For SSH with X forwarding, ensure `$DISPLAY` reaches the Bazzite desktop; otherwise use `status` remotely and complete setup from the local desktop.

Bazzite currently packages `tkinter` separately. If it is absent, the app uses a native Zenity bootstrap when a display is available. From a plain interactive SSH terminal it provides the same full safety gate in text. Both paths validate hardware and require all three acknowledgments plus exact `I ACCEPT` before offering to stage `python3-tkinter` with `rpm-ostree`; no system change occurs before acknowledgment. Reboot once after it is staged, then relaunch the app from the Bazzite desktop or an X11-forwarded SSH session so the native wizard has a display.

Commands:

```text
bc250-llm-mode setup
bc250-llm-mode repair
bc250-llm-mode status
bc250-llm-mode chat
bc250-llm-mode install-model qwen35-9b --quant Q5_K_M --ctx 8192
bc250-llm-mode switch qwen3-8b
bc250-llm-mode desktop-mode [--now]
bc250-llm-mode uninstall [--remove-container] [--remove-models]
```

`desktop-mode` is the non-destructive way back to normal Bazzite operation. It stops and disables the model service, stops the retained LLM/Open WebUI containers, restores `graphical.target`, unmasks sleep and display-manager units, removes the app's GPU-awake rule and `amdgpu.runpm=0`, and reverts opted-in host optimizations. Models, containers, and setup records are preserved. It never reboots automatically; reboot normally, or pass `--now` to start `graphical.target` in the current boot. A reboot is still required to finish a staged kernel-argument change.

`--remove-models` permanently deletes downloaded model files. Without it, uninstall disables/removes the server service and reverts headless/no-sleep/GPU-runpm configuration while preserving models.

## Setup behavior

The resumable wizard validates hardware and disk, enforces the mandatory thermal/40-CU/BIOS warning, configures LLM Mode, creates the `llm` distrobox, builds llama.cpp with Vulkan, installs a model, validates and self-heals GGUF metadata, and enables the single owning systemd service. The API binds only to `127.0.0.1:8080`. Open WebUI is optional on port 3000.

The Model Selection page also scans the configured model directory, previously registered model locations, conventional `models` folders, and any folders added with **Add folder…** for existing GGUF files. These appear as **Installed** choices next to catalog downloads. Selecting one skips the network download but still runs the standard-layout safety check and full GGUF verification before registration. Scanning stats file paths and sizes only; it does not read multi-gigabyte weights into host RAM. Fused/MAX, vision/MTP, incomplete, and f16 conversion-intermediate artifacts are not offered.

The **Optimize** page appears after model selection and provides bounded, persistent controls:

- llama.cpp runtime tuning: Flash Attention (`auto`/`on`/`off`), Q8/Q4 KV cache, batch size 128–2048, and micro-batch size 64–512;
- optional Cyan governor tuning: 500–1200 MHz minimum, 1500–2000 MHz maximum, 75–90°C throttle, and 60–85°C recovery (with relationship validation);
- optional restart-loop limits and 10–500 MiB server-log rotation;
- optional swappiness from 10–200;
- individual opt-in trimming of Input Remapper, controller inhibition, DisplayLink, the gaming memory booster, and the gaming resource allocator.

All host changes are unchecked by default. Runtime tuning alone defaults to conservative values. The page rejects unsafe combinations, logs every action, remembers previous service/swappiness state, and restores host changes when unchecked during repair or when the application is uninstalled. Completed installations expose the same page through **Optimization settings** without re-downloading a model.

Every command is streamed to `~/.bc250-llm-mode/logs/setup.log`. Model server output is appended to `/root/llama-server.log`, and failed health checks automatically show its tail with targeted guidance.

## Safety

The wizard will not perform system changes until the full warning is acknowledged. It never changes BIOS settings and does not reboot automatically. If `amdgpu.runpm=0` is newly staged, it saves progress and asks the user to reboot.

The seed catalog uses standard-layout GGUF releases, including [Qwen3.5 9B](https://huggingface.co/bartowski/Qwen_Qwen3.5-9B-GGUF), [Qwen3 8B](https://huggingface.co/Qwen/Qwen3-8B-GGUF), and [Qwen3 14B](https://huggingface.co/ggml-org/Qwen3-14B-GGUF). Defiant Fable uses the [MLX BF16 source](https://huggingface.co/pipenetwork/Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-MLX-bf16) for local text-only conversion; the fused/MAX GGUF is deliberately forbidden.
