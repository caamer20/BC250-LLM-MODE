"""Single-window native tkinter setup wizard."""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .catalog import CATALOG, calculate_fit, model_by_id
from .desktop import switch_to_desktop_mode
from .disclaimer import DISCLAIMER_TEXT, acknowledge, acknowledgment_valid
from .download import download_model
from .env import setup_environment
from .hardware import HardwareReport, detect_hardware
from .llmmode import apply_llm_mode, stage_desktop_boot
from .local_models import (
    LocalModel,
    discover_local_models,
    fit_entry_for_local,
    selected_fit_entry,
)
from .logging_utils import CommandRunner, configure_logging
from .model_manager import change_context, register_and_switch_local, switch_model
from .openwebui import (
    install_open_webui,
    open_webui_status,
    restart_open_webui,
    start_open_webui,
    stop_open_webui,
)
from .optimize import (
    DEFAULT_OPTIMIZATIONS,
    TRIMMABLE_SERVICES,
    apply_optimizations,
    kv_scale_for_settings,
    normalized_settings,
    validate_settings,
)
from .prepare import (
    cleanup_conversion_intermediates,
    prepare_local_model,
    prepare_model,
)
from .server import (
    health_check,
    install_service,
    restart_and_wait,
    restart_service,
    service_status,
    start_service,
    stop_service,
)
from .state import StateStore
from .tailscale import (
    connect_tailscale,
    disconnect_tailscale,
    restart_tailscale,
    start_tailscale,
    stop_tailscale,
    tailscale_status,
)

STEP_TITLES = (
    "Welcome & Hardware", "Safety Warning", "LLM Mode", "Inference Environment",
    "Model Selection", "Optimize", "Download", "Prepare", "Server", "Open WebUI", "Setup Complete",
)


class Wizard(tk.Tk):
    def __init__(self, store: StateStore | None = None, management: bool = False) -> None:
        super().__init__()
        self.store = store or StateStore()
        self.state_data = self.store.load()
        self.management = management
        self.title("BC250 LLM MODE")
        self.geometry("920x760")
        self.minsize(760, 620)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.current_step = 10 if management and self.state_data.get("setup_complete") else min(
            max(int(self.state_data.get("setup_phase", 0)), 0), 10
        )
        if self.state_data.get("reboot_required"):
            try:
                active = "amdgpu.runpm=0" in Path("/proc/cmdline").read_text(encoding="utf-8").split()
            except OSError:
                active = False
            pending = self.state_data.get("pending_karg_mode", "enable")
            change_is_active = (pending == "enable" and active) or (pending == "disable" and not active)
            if change_is_active:
                self.state_data.update(reboot_required=False, pending_karg_mode=None)
                self.save()
            elif pending == "enable":
                self.current_step = 2
        self.busy = False
        self.hardware_report: HardwareReport | None = None
        self.downloaded_path: Path | None = None
        self.optimization_return_to_complete = False
        self._build_shell()
        self.show_step(self.current_step)
        self.after(100, self._drain_events)

    def _build_shell(self) -> None:
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        self.heading = ttk.Label(outer, font=("TkDefaultFont", 16, "bold"))
        self.heading.pack(anchor="w")
        self.progress = ttk.Progressbar(outer, maximum=10)
        self.progress.pack(fill="x", pady=(8, 10))
        self.content = ttk.Frame(outer)
        self.content.pack(fill="both", expand=True)
        ttk.Label(outer, text="Setup log").pack(anchor="w", pady=(8, 2))
        log_frame = ttk.Frame(outer)
        log_frame.pack(fill="both", expand=False)
        self.log_text = tk.Text(log_frame, height=9, wrap="word", state="disabled")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")
        nav = ttk.Frame(outer)
        nav.pack(fill="x", pady=(10, 0))
        self.back_button = ttk.Button(nav, text="Back", command=self.back)
        self.back_button.pack(side="left")
        self.continue_button = ttk.Button(nav, text="Continue", command=self.continue_step)
        self.continue_button.pack(side="right")

    def emit(self, line: str) -> None:
        self.events.put(("log", line))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.log_text.configure(state="normal")
                    self.log_text.insert("end", str(payload) + "\n")
                    self.log_text.see("end")
                    self.log_text.configure(state="disabled")
                elif kind == "done":
                    self.busy = False
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=self.current_step)
                    self.continue_button.configure(state="normal")
                    payload()
                elif kind == "error":
                    self.busy = False
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=self.current_step)
                    self.continue_button.configure(state="normal")
                    messagebox.showerror("Setup failed", str(payload))
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def runner(self) -> CommandRunner:
        return CommandRunner(configure_logging(self.state_data["logs_dir"]), self.emit)

    def save(self) -> None:
        self.store.save(self.state_data)

    def _clear(self) -> None:
        for widget in self.content.winfo_children():
            widget.destroy()

    def show_step(self, step: int) -> None:
        self.current_step = max(0, min(step, 10))
        self._clear()
        self.heading.configure(text=f"Step {self.current_step}: {STEP_TITLES[self.current_step]}")
        self.progress.configure(value=self.current_step)
        self.back_button.configure(state="disabled" if self.current_step == 0 else "normal")
        self.continue_button.configure(text="Continue", state="normal")
        renderers = (
            self._hardware, self._disclaimer, self._llm_mode, self._environment, self._catalog,
            self._optimize, self._download, self._prepare, self._server, self._webui, self._complete,
        )
        renderers[self.current_step]()

    def _body_label(self, text: str) -> ttk.Label:
        label = ttk.Label(self.content, text=text, wraplength=820, justify="left")
        label.pack(anchor="w", fill="x", pady=8)
        return label

    def _hardware(self) -> None:
        self._body_label("The GPU is rediscovered by PCI vendor ID on every run; card0/card1 is never assumed.")
        self.hardware_report = detect_hardware(self.state_data["models_dir"])
        report = tk.Text(self.content, height=13, wrap="word")
        report.insert("1.0", json.dumps(self.hardware_report.to_dict(), indent=2))
        report.configure(state="disabled")
        report.pack(fill="both", expand=True)
        if self.hardware_report.errors:
            self.continue_button.configure(state="disabled")

    def _disclaimer(self) -> None:
        warning = tk.Text(self.content, wrap="word", height=20)
        warning.insert("1.0", DISCLAIMER_TEXT)
        warning.configure(state="disabled")
        warning.pack(fill="both", expand=True)
        self.ack_vars = [tk.BooleanVar(value=bool(self.state_data.get("disclaimer_ack"))) for _ in range(3)]
        labels = (
            "I understand the thermal risk and will monitor the GPU.",
            "I understand all 40 CUs should be unlocked for best performance.",
            "I have set approximately 12 GiB GPU / 4 GiB system RAM in BIOS.",
        )
        for variable, label in zip(self.ack_vars, labels):
            ttk.Checkbutton(self.content, text=label, variable=variable, command=self._update_ack).pack(anchor="w")
        row = ttk.Frame(self.content)
        row.pack(fill="x", pady=7)
        ttk.Label(row, text='Type "I ACCEPT":').pack(side="left")
        self.accept_var = tk.StringVar(value="I ACCEPT" if self.state_data.get("disclaimer_ack") else "")
        self.accept_var.trace_add("write", lambda *_: self._update_ack())
        ttk.Entry(row, textvariable=self.accept_var).pack(side="left", fill="x", expand=True, padx=8)
        self._update_ack()

    def _update_ack(self) -> None:
        allowed = acknowledgment_valid(*(v.get() for v in self.ack_vars), self.accept_var.get())
        self.continue_button.configure(state="normal" if allowed else "disabled")

    def _llm_mode(self) -> None:
        self._body_label(
            "Starts a current-boot LLM session with runtime-only sleep and GPU-awake rules. "
            "The model service remains disabled for boot: restarting always returns to normal Bazzite desktop mode with no LLM running."
        )
        self.mask_desktop = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self.content, text="Also mask heavy desktop/display-manager services (optional)", variable=self.mask_desktop
        ).pack(anchor="w", pady=8)

    def _environment(self) -> None:
        self._body_label(
            "Creates/reuses the llm distrobox, builds llama.cpp with Vulkan, creates the Hugging Face venv, and tests Vulkan visibility. This can take a while."
        )

    def _catalog(self) -> None:
        discovery = discover_local_models(self.state_data)
        self.model_choices: dict[str, tuple[str, Any]] = {}
        frame = ttk.Frame(self.content)
        frame.pack(fill="both", expand=True)
        self.model_tree = ttk.Treeview(
            frame, columns=("source", "family", "size", "notes"), show="headings", height=7
        )
        model_scroll = ttk.Scrollbar(frame, orient="vertical", command=self.model_tree.yview)
        self.model_tree.configure(yscrollcommand=model_scroll.set)
        for key, title, width in (
            ("source", "Source", 85),
            ("family", "Family", 80),
            ("size", "Size", 70),
            ("notes", "Model", 500),
        ):
            self.model_tree.heading(key, text=title)
            self.model_tree.column(key, width=width, stretch=key == "notes")
        for model in CATALOG:
            iid = f"catalog::{model.id}"
            self.model_choices[iid] = ("catalog", model)
            self.model_tree.insert(
                "", "end", iid=iid,
                values=("Download", model.family, f"{model.params_b:g}B", f"{model.display_name} — {model.notes}"),
            )
        for model in discovery.models:
            iid = f"local::{model.id}"
            self.model_choices[iid] = ("local", model)
            recognized = f"matches {model.catalog_id}" if model.catalog_id else "custom GGUF"
            self.model_tree.insert(
                "", "end", iid=iid,
                values=("Installed", model.family, f"{model.weights_gib:.2f} GiB", f"{model.display_name} — {recognized}"),
            )
        self.model_tree.pack(side="left", fill="both", expand=True)
        model_scroll.pack(side="right", fill="y")
        if self.state_data.get("selected_source") == "local":
            selected = f"local::{self.state_data.get('selected_model')}"
        else:
            selected = f"catalog::{self.state_data.get('selected_model') or CATALOG[0].id}"
        if selected not in self.model_choices:
            selected = f"catalog::{CATALOG[0].id}"
        self.model_tree.selection_set(selected)
        self.model_tree.focus(selected)
        summary = f"Found {len(discovery.models)} existing GGUF model(s) in configured/common model folders."
        if discovery.rejected:
            summary += f" Rejected {len(discovery.rejected)} unsafe or unreadable artifact(s); details are in the setup log."
            for rejection in discovery.rejected:
                self.emit(f"Local model rejected: {rejection}")
        scan_row = ttk.Frame(self.content)
        scan_row.pack(fill="x", pady=(6, 0))
        ttk.Label(scan_row, text=summary).pack(side="left", fill="x", expand=True)
        ttk.Button(scan_row, text="Rescan", command=lambda: self.show_step(4)).pack(side="right")
        ttk.Button(scan_row, text="Add folder…", command=self._add_model_folder).pack(side="right", padx=5)
        controls = ttk.Frame(self.content)
        controls.pack(fill="x", pady=10)
        ttk.Label(controls, text="Quant:").pack(side="left")
        self.quant_var = tk.StringVar()
        self.quant_box = ttk.Combobox(controls, textvariable=self.quant_var, state="readonly", width=12)
        self.quant_box.pack(side="left", padx=(5, 15))
        ttk.Label(controls, text="Context:").pack(side="left")
        self.ctx_var = tk.IntVar(value=int(self.state_data.get("current_ctx", 8192)))
        ttk.Spinbox(controls, from_=512, to=262144, increment=512, textvariable=self.ctx_var, width=10, command=self._fit).pack(side="left", padx=5)
        self.fit_label = ttk.Label(controls)
        self.fit_label.pack(side="left", padx=15)
        self.model_tree.bind("<<TreeviewSelect>>", self._model_changed)
        self.quant_box.bind("<<ComboboxSelected>>", lambda _event: self._fit())
        self.ctx_var.trace_add("write", lambda *_: self._fit())
        self._model_changed()

    def _add_model_folder(self) -> None:
        selected = filedialog.askdirectory(title="Choose a folder containing GGUF models")
        if not selected:
            return
        paths = [str(item) for item in self.state_data.get("model_search_paths", [])]
        resolved = str(Path(selected).expanduser().resolve())
        if resolved not in paths:
            paths.append(resolved)
            self.state_data["model_search_paths"] = paths
            self.save()
        self.show_step(4)

    def _model_changed(self, _event=None) -> None:
        selection = self.model_tree.selection()
        if not selection:
            return
        source, selected = self.model_choices[selection[0]]
        model = fit_entry_for_local(selected) if source == "local" else selected
        values = [selected.quant] if source == "local" else list(model.allow_globs)
        self.quant_box.configure(values=values)
        preferred = self.state_data.get("selected_quant")
        self.quant_var.set(preferred if preferred in values else values[0])
        self._fit()

    def _fit(self) -> None:
        try:
            source, selected = self.model_choices[self.model_tree.selection()[0]]
            model = fit_entry_for_local(selected) if source == "local" else selected
            ctx = int(self.ctx_var.get())
            slots = int(normalized_settings(self.state_data.get("optimizations"))["parallel_slots"])
            fit = calculate_fit(model, self.quant_var.get(), ctx, parallel_slots=slots)
            concurrency = f" · {ctx:,} tokens per user across {slots} slots"
            if fit.verdict == "NO-FIT":
                q4_fit = calculate_fit(
                    model, self.quant_var.get(), ctx, kv_scale=0.5, parallel_slots=slots
                )
                if q4_fit.verdict != "NO-FIT":
                    self.fit_label.configure(text=f"Q8 NO-FIT; Q4 KV can fit at ~{q4_fit.required_gib:.2f} GiB{concurrency}")
                    self.continue_button.configure(state="normal")
                else:
                    self.fit_label.configure(text=fit.detail + concurrency)
                    self.continue_button.configure(state="disabled")
            else:
                self.fit_label.configure(text=fit.detail + concurrency)
                self.continue_button.configure(state="normal")
        except ValueError as exc:
            if hasattr(self, "fit_label"):
                self.fit_label.configure(text=str(exc))
                self.continue_button.configure(state="disabled")
        except (KeyError, IndexError, tk.TclError):
            if hasattr(self, "fit_label"):
                self.fit_label.configure(text="Enter a valid context size")
                self.continue_button.configure(state="disabled")

    @staticmethod
    def _labeled_spin(parent: ttk.Frame, label: str, variable: tk.IntVar, low: int, high: int, step: int) -> None:
        ttk.Label(parent, text=label).pack(side="left", padx=(8, 3))
        ttk.Spinbox(parent, from_=low, to=high, increment=step, textvariable=variable, width=7).pack(side="left")

    def _optimize(self) -> None:
        settings = normalized_settings(self.state_data.get("optimizations"))
        canvas = tk.Canvas(self.content, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.content, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window_id, width=event.width))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        ttk.Label(
            inner,
            text="Choose only the optimizations you want. Host changes are opt-in, bounded, logged, and restored when unchecked during repair or uninstall.",
            wraplength=800,
            justify="left",
        ).pack(anchor="w", fill="x", pady=(0, 6))

        runtime = ttk.LabelFrame(inner, text="llama.cpp runtime (safe; stored in launcher)", padding=7)
        runtime.pack(fill="x", pady=4)
        self.opt_runtime = tk.BooleanVar(value=bool(settings["runtime_enabled"]))
        ttk.Checkbutton(runtime, text="Apply runtime tuning", variable=self.opt_runtime).pack(anchor="w")
        runtime_row = ttk.Frame(runtime)
        runtime_row.pack(fill="x", pady=3)
        ttk.Label(runtime_row, text="Flash Attention").pack(side="left")
        self.opt_flash = tk.StringVar(value=str(settings["flash_attention"]))
        ttk.Combobox(runtime_row, values=("auto", "on", "off"), state="readonly", textvariable=self.opt_flash, width=7).pack(side="left", padx=4)
        ttk.Label(runtime_row, text="KV cache").pack(side="left", padx=(12, 3))
        self.opt_kv = tk.StringVar(value=str(settings["kv_cache_type"]))
        ttk.Combobox(runtime_row, values=("q8_0", "q4_0"), state="readonly", textvariable=self.opt_kv, width=7).pack(side="left")
        self.opt_batch = tk.IntVar(value=int(settings["batch_size"]))
        self.opt_ubatch = tk.IntVar(value=int(settings["ubatch_size"]))
        self.opt_parallel = tk.IntVar(value=int(settings["parallel_slots"]))
        self._labeled_spin(runtime_row, "Batch", self.opt_batch, 128, 2048, 64)
        self._labeled_spin(runtime_row, "Micro-batch", self.opt_ubatch, 64, 512, 64)
        self._labeled_spin(runtime_row, "User slots", self.opt_parallel, 1, 8, 1)
        self.opt_memory_note = ttk.Label(runtime)
        self.opt_memory_note.pack(anchor="w")
        self.opt_kv.trace_add("write", lambda *_: self._update_optimization_fit())
        self.opt_parallel.trace_add("write", lambda *_: self._update_optimization_fit())
        self._update_optimization_fit()

        gpu = ttk.LabelFrame(inner, text="Cyan GPU governor (host change; reversible)", padding=7)
        gpu.pack(fill="x", pady=4)
        self.opt_gpu = tk.BooleanVar(value=bool(settings["gpu_enabled"]))
        ttk.Checkbutton(gpu, text="Tune frequency range and thermal limits", variable=self.opt_gpu).pack(anchor="w")
        gpu_row = ttk.Frame(gpu)
        gpu_row.pack(fill="x", pady=3)
        self.opt_gpu_min = tk.IntVar(value=int(settings["gpu_min_mhz"]))
        self.opt_gpu_max = tk.IntVar(value=int(settings["gpu_max_mhz"]))
        self.opt_throttle = tk.IntVar(value=int(settings["thermal_throttle_c"]))
        self.opt_recovery = tk.IntVar(value=int(settings["thermal_recovery_c"]))
        self._labeled_spin(gpu_row, "Min MHz", self.opt_gpu_min, 500, 1200, 25)
        self._labeled_spin(gpu_row, "Max MHz", self.opt_gpu_max, 1500, 2000, 25)
        self._labeled_spin(gpu_row, "Throttle °C", self.opt_throttle, 75, 90, 1)
        self._labeled_spin(gpu_row, "Recover °C", self.opt_recovery, 60, 85, 1)
        ttk.Label(gpu, text="Recommended: 500–1850 MHz, throttle 85°C, recover 75°C. Values above 1850 MHz are experimental.").pack(anchor="w")

        safeguards = ttk.LabelFrame(inner, text="Server safeguards", padding=7)
        safeguards.pack(fill="x", pady=4)
        self.opt_safeguards = tk.BooleanVar(value=bool(settings["safeguards_enabled"]))
        ttk.Checkbutton(safeguards, text="Limit restart loops and rotate the server log", variable=self.opt_safeguards).pack(anchor="w")
        safeguard_row = ttk.Frame(safeguards)
        safeguard_row.pack(fill="x", pady=3)
        self.opt_restart_window = tk.IntVar(value=int(settings["restart_window_sec"]))
        self.opt_restart_burst = tk.IntVar(value=int(settings["restart_burst"]))
        self.opt_restart_delay = tk.IntVar(value=int(settings["restart_delay_sec"]))
        self.opt_log_max = tk.IntVar(value=int(settings["log_max_mib"]))
        self._labeled_spin(safeguard_row, "Window sec", self.opt_restart_window, 60, 900, 30)
        self._labeled_spin(safeguard_row, "Max restarts", self.opt_restart_burst, 1, 10, 1)
        self._labeled_spin(safeguard_row, "Delay sec", self.opt_restart_delay, 5, 60, 5)
        self._labeled_spin(safeguard_row, "Log MiB", self.opt_log_max, 10, 500, 10)

        memory = ttk.LabelFrame(inner, text="Advanced host-memory policy", padding=7)
        memory.pack(fill="x", pady=4)
        memory_row = ttk.Frame(memory)
        memory_row.pack(fill="x")
        self.opt_memory = tk.BooleanVar(value=bool(settings["memory_enabled"]))
        ttk.Checkbutton(memory_row, text="Set persistent swappiness", variable=self.opt_memory).pack(side="left")
        self.opt_swappiness = tk.IntVar(value=int(settings["swappiness"]))
        self._labeled_spin(memory_row, "Value", self.opt_swappiness, 10, 200, 10)
        ttk.Label(memory, text="Leave off unless benchmarking shows swap latency. The current zswap-backed value may already be appropriate.").pack(anchor="w")

        services = ttk.LabelFrame(inner, text="Optional headless service trimming", padding=7)
        services.pack(fill="x", pady=4)
        self.opt_trim = tk.BooleanVar(value=bool(settings["trim_services_enabled"]))
        ttk.Checkbutton(services, text="Stop/disable selected services", variable=self.opt_trim).pack(anchor="w")
        self.opt_service_vars: dict[str, tk.BooleanVar] = {}
        for unit, description in TRIMMABLE_SERVICES.items():
            variable = tk.BooleanVar(value=bool(settings["trim_services"].get(unit, False)))
            self.opt_service_vars[unit] = variable
            ttk.Checkbutton(services, text=f"{description} [{unit}]", variable=variable).pack(anchor="w")

        buttons = ttk.Frame(inner)
        buttons.pack(fill="x", pady=6)
        ttk.Button(buttons, text="Balanced defaults", command=self._balanced_optimizations).pack(side="left")
        ttk.Button(buttons, text="Disable all host changes", command=self._disable_host_optimizations).pack(side="left", padx=8)

    def _balanced_optimizations(self) -> None:
        defaults = DEFAULT_OPTIMIZATIONS
        self.opt_runtime.set(True)
        self.opt_flash.set(defaults["flash_attention"])
        self.opt_kv.set(defaults["kv_cache_type"])
        self.opt_batch.set(defaults["batch_size"])
        self.opt_ubatch.set(defaults["ubatch_size"])
        self.opt_parallel.set(defaults["parallel_slots"])
        self.opt_gpu.set(True)
        self.opt_gpu_min.set(defaults["gpu_min_mhz"])
        self.opt_gpu_max.set(defaults["gpu_max_mhz"])
        self.opt_throttle.set(defaults["thermal_throttle_c"])
        self.opt_recovery.set(defaults["thermal_recovery_c"])
        self.opt_safeguards.set(True)

    def _update_optimization_fit(self) -> None:
        try:
            model = selected_fit_entry(self.state_data)
            quant = str(self.state_data["selected_quant"])
            scale = 0.5 if self.opt_kv.get() == "q4_0" else 1.0
            slots = int(self.opt_parallel.get())
            fit = calculate_fit(
                model,
                quant,
                int(self.state_data["current_ctx"]),
                kv_scale=scale,
                parallel_slots=slots,
            )
            caveat = " Approximate; validate model quality." if scale < 1 else " Quality-first KV cache."
            self.opt_memory_note.configure(
                text=(
                    fit.detail + caveat
                    + f" Reserves {int(self.state_data['current_ctx']):,} tokens per request slot."
                )
            )
        except (KeyError, ValueError):
            self.opt_memory_note.configure(text="VRAM projection unavailable until a model is selected.")

    def _disable_host_optimizations(self) -> None:
        self.opt_gpu.set(False)
        self.opt_memory.set(False)
        self.opt_trim.set(False)
        self.opt_safeguards.set(False)
        for variable in self.opt_service_vars.values():
            variable.set(False)

    def _collect_optimization_settings(self) -> dict[str, Any]:
        settings = {
            "runtime_enabled": self.opt_runtime.get(),
            "flash_attention": self.opt_flash.get(),
            "batch_size": self.opt_batch.get(),
            "ubatch_size": self.opt_ubatch.get(),
            "kv_cache_type": self.opt_kv.get(),
            "parallel_slots": self.opt_parallel.get(),
            "gpu_enabled": self.opt_gpu.get(),
            "gpu_min_mhz": self.opt_gpu_min.get(),
            "gpu_max_mhz": self.opt_gpu_max.get(),
            "thermal_throttle_c": self.opt_throttle.get(),
            "thermal_recovery_c": self.opt_recovery.get(),
            "memory_enabled": self.opt_memory.get(),
            "swappiness": self.opt_swappiness.get(),
            "safeguards_enabled": self.opt_safeguards.get(),
            "restart_window_sec": self.opt_restart_window.get(),
            "restart_burst": self.opt_restart_burst.get(),
            "restart_delay_sec": self.opt_restart_delay.get(),
            "log_max_mib": self.opt_log_max.get(),
            "trim_services_enabled": self.opt_trim.get(),
            "trim_services": {unit: variable.get() for unit, variable in self.opt_service_vars.items()},
        }
        checked = validate_settings(settings)
        model = selected_fit_entry(self.state_data)
        scale = kv_scale_for_settings(checked)
        fit = calculate_fit(
            model,
            str(self.state_data["selected_quant"]),
            int(self.state_data["current_ctx"]),
            kv_scale=scale,
            parallel_slots=int(checked["parallel_slots"]),
        )
        if fit.verdict == "NO-FIT":
            raise ValueError(f"Selected runtime settings do not fit: {fit.detail}")
        return checked

    def _download(self) -> None:
        if self.state_data.get("selected_source") == "local":
            local = LocalModel.from_dict(self.state_data["selected_local_model"])
            self._body_label(
                f"Using existing model {local.display_name} ({local.quant}) at {local.path}. "
                "No download will occur; Continue records the skip."
            )
        else:
            model = model_by_id(self.state_data["selected_model"])
            self._body_label(f"Ready to resume-download {model.display_name} ({self.state_data['selected_quant']}).")

    def _prepare(self) -> None:
        self._body_label("Verifies GGUF metadata and tensor blocks, then applies only guarded, known-safe repairs. Conversion never reads a full model into host RAM.")

    def _server(self) -> None:
        self._body_label("Installs the single-owner systemd service, starts it, waits up to 120 seconds, and displays server-log guidance on failure.")

    def _webui(self) -> None:
        self.webui_var = tk.BooleanVar(value=bool(self.state_data.get("openwebui_installed")))
        ttk.Checkbutton(self.content, text="Install optional Open WebUI on host port 3000", variable=self.webui_var).pack(anchor="w", pady=10)
        self._body_label("If a model is absent from its selector, log in as admin and create a Workspace model pinned to the base model id.")

    def _complete(self) -> None:
        self.continue_button.configure(text="Start CLI chat", state="normal")
        self.back_button.configure(state="disabled")
        canvas = tk.Canvas(self.content, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.content, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window_id, width=event.width))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        ttk.Label(
            inner,
            text=f"Operations dashboard — model {self.state_data.get('current_model')} at {self.state_data.get('current_ctx')} tokens",
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor="w", pady=(0, 6))
        ttk.Label(
            inner,
            text="Next boot: Bazzite graphical desktop · LLM auto-start: OFF",
            foreground="#207020",
        ).pack(anchor="w", pady=(0, 6))

        self.dashboard_status_vars: dict[str, tk.StringVar] = {
            "llm": tk.StringVar(value="Checking…"),
            "webui": tk.StringVar(value="Checking…"),
            "tailscale": tk.StringVar(value="Checking…"),
        }
        services = ttk.Frame(inner)
        services.pack(fill="x")
        self._dashboard_service_card(
            services, "LLM server", self.dashboard_status_vars["llm"],
            (("Start", lambda: self._dashboard_action(lambda r: start_service(self.state_data, r))),
             ("Stop", lambda: self._dashboard_action(lambda r: stop_service(self.state_data, r))),
             ("Restart", lambda: self._dashboard_action(lambda r: restart_and_wait(self.state_data, r)))),
        ).pack(side="left", fill="both", expand=True, padx=(0, 4))
        self._dashboard_service_card(
            services, "Open WebUI", self.dashboard_status_vars["webui"],
            (("Start", lambda: self._dashboard_action(lambda r: start_open_webui(self.state_data, r))),
             ("Stop", lambda: self._dashboard_action(lambda r: stop_open_webui(self.state_data, r))),
             ("Restart", lambda: self._dashboard_action(lambda r: restart_open_webui(self.state_data, r)))),
        ).pack(side="left", fill="both", expand=True, padx=4)
        self._dashboard_service_card(
            services, "Tailscale", self.dashboard_status_vars["tailscale"],
            (("Start", lambda: self._dashboard_action(start_tailscale)),
             ("Stop", lambda: self._dashboard_action(stop_tailscale)),
             ("Restart", lambda: self._dashboard_action(restart_tailscale)),
             ("Connect", lambda: self._dashboard_action(connect_tailscale)),
             ("Disconnect", lambda: self._dashboard_action(disconnect_tailscale))),
        ).pack(side="left", fill="both", expand=True, padx=(4, 0))

        quick = ttk.Frame(inner)
        quick.pack(fill="x", pady=6)
        ttk.Button(quick, text="Refresh status", command=self._refresh_dashboard).pack(side="left")
        ttk.Button(quick, text="Open WebUI", command=lambda: webbrowser.open("http://127.0.0.1:3000")).pack(side="left", padx=5)
        ttk.Button(quick, text="Start terminal chat", command=self._launch_chat_terminal).pack(side="left")
        ttk.Button(quick, text="Server log", command=lambda: self._dashboard_tail("server")).pack(side="right")
        ttk.Button(quick, text="Setup log", command=lambda: self._dashboard_tail("setup")).pack(side="right", padx=5)

        models_frame = ttk.LabelFrame(inner, text="Models", padding=6)
        models_frame.pack(fill="both", expand=True, pady=4)
        self.dashboard_models: dict[str, tuple[str, Any]] = {}
        self.dashboard_model_tree = ttk.Treeview(
            models_frame, columns=("state", "quant", "size", "name"), show="headings", height=5
        )
        for key, title, width in (
            ("state", "State", 85), ("quant", "Quant", 80),
            ("size", "Size", 85), ("name", "Model / path", 500),
        ):
            self.dashboard_model_tree.heading(key, text=title)
            self.dashboard_model_tree.column(key, width=width, stretch=key == "name")
        self.dashboard_model_tree.pack(fill="both", expand=True)
        self._populate_dashboard_models()
        model_buttons = ttk.Frame(models_frame)
        model_buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(model_buttons, text="Use selected model", command=self._dashboard_use_model).pack(side="left")
        ttk.Button(model_buttons, text="Rescan disks", command=self._populate_dashboard_models).pack(side="left", padx=5)
        ttk.Button(model_buttons, text="Install/download another…", command=lambda: self.show_step(4)).pack(side="left")
        ttk.Label(model_buttons, text="Context:").pack(side="left", padx=(16, 3))
        self.dashboard_ctx = tk.IntVar(value=int(self.state_data.get("current_ctx", 8192)))
        ttk.Spinbox(
            model_buttons, from_=512, to=262144, increment=512,
            textvariable=self.dashboard_ctx, width=9,
        ).pack(side="left")
        ttk.Button(model_buttons, text="Apply", command=self._dashboard_change_context).pack(side="left", padx=4)

        management = ttk.LabelFrame(inner, text="System and application", padding=6)
        management.pack(fill="x", pady=4)
        ttk.Button(management, text="Optimization settings", command=self._manage_optimizations).pack(side="left")
        ttk.Button(management, text="Re-run / repair setup", command=self._repair).pack(side="left", padx=5)
        ttk.Button(management, text="Start current-boot LLM Mode", command=self._dashboard_enter_llm_mode).pack(side="left")
        ttk.Button(
            management,
            text="Ensure desktop on next boot",
            command=lambda: self._dashboard_action(lambda r: stage_desktop_boot(self.state_data, r)),
        ).pack(side="left", padx=5)
        ttk.Button(management, text="Return to Bazzite desktop mode", command=self._dashboard_desktop_mode).pack(side="right")

        command = f"{sys.executable} -m bc250_llm_mode chat"
        entry = ttk.Entry(inner)
        entry.insert(0, command)
        entry.configure(state="readonly")
        entry.pack(fill="x", pady=(5, 0))
        self.after(50, self._refresh_dashboard)

    def _dashboard_service_card(
        self,
        parent: ttk.Frame,
        title: str,
        status: tk.StringVar,
        actions: tuple[tuple[str, Callable[[], None]], ...],
    ) -> ttk.LabelFrame:
        card = ttk.LabelFrame(parent, text=title, padding=6)
        ttk.Label(card, textvariable=status, wraplength=235).pack(anchor="w", fill="x")
        row = ttk.Frame(card)
        row.pack(fill="x", pady=(5, 0))
        for index, (label, callback) in enumerate(actions):
            ttk.Button(row, text=label, command=callback).grid(
                row=index // 3, column=index % 3, sticky="ew", padx=2, pady=2
            )
        for column in range(3):
            row.columnconfigure(column, weight=1)
        return card

    def _populate_dashboard_models(self) -> None:
        if not hasattr(self, "dashboard_model_tree"):
            return
        for iid in self.dashboard_model_tree.get_children():
            self.dashboard_model_tree.delete(iid)
        self.dashboard_models.clear()
        installed_paths: set[str] = set()
        for index, record in enumerate(self.state_data.get("installed_models", [])):
            path = str(record.get("path", ""))
            installed_paths.add(str(Path(path).expanduser().absolute()))
            iid = f"installed::{index}"
            self.dashboard_models[iid] = ("installed", record)
            marker = "Current" if record.get("id") == self.state_data.get("current_model") else "Installed"
            size = record.get("weights_gib")
            self.dashboard_model_tree.insert(
                "", "end", iid=iid,
                values=(marker, record.get("quant", "?"), f"{float(size):.2f} GiB" if size else "", f"{record.get('display_name') or record.get('id')} — {path}"),
            )
        discovery = discover_local_models(self.state_data)
        for index, model in enumerate(discovery.models):
            if str(Path(model.path).expanduser().absolute()) in installed_paths:
                continue
            iid = f"local::{index}"
            self.dashboard_models[iid] = ("local", model)
            self.dashboard_model_tree.insert(
                "", "end", iid=iid,
                values=("Discovered", model.quant, f"{model.weights_gib:.2f} GiB", f"{model.display_name} — {model.path}"),
            )
        children = self.dashboard_model_tree.get_children()
        if children:
            current = next(
                (iid for iid in children if self.dashboard_model_tree.set(iid, "state") == "Current"),
                children[0],
            )
            self.dashboard_model_tree.selection_set(current)
            self.dashboard_model_tree.focus(current)
        for rejection in discovery.rejected:
            self.emit(f"Local model rejected: {rejection}")

    def _dashboard_action(self, action: Callable[[CommandRunner], Any]) -> None:
        def work() -> None:
            action(self.runner())
            self.save()
        self._work(work, self._refresh_dashboard)

    def _refresh_dashboard(self) -> None:
        if self.busy or not hasattr(self, "dashboard_status_vars"):
            return
        snapshot: dict[str, Any] = {}
        def work() -> None:
            runner = self.runner()
            snapshot["llm"] = service_status(self.state_data, runner)
            if snapshot["llm"]["active"]:
                try:
                    snapshot["health"] = health_check(self.state_data, timeout=3)
                except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as exc:
                    snapshot["health_error"] = str(exc)
            snapshot["webui"] = open_webui_status(self.state_data, runner)
            snapshot["tailscale"] = tailscale_status(runner)
            self.save()
        def done() -> None:
            llm = snapshot["llm"]
            if snapshot.get("health"):
                health = snapshot["health"]
                text = (
                    f"Healthy · {health['model_id']} · ctx {health['n_ctx']} · "
                    f"VRAM {health['vram_used_mib']}/{health['vram_total_mib']} MiB"
                )
            elif llm["active"]:
                text = f"Running but API unavailable · {snapshot.get('health_error', 'unknown error')}"
            else:
                text = f"Stopped · {'enabled' if llm['enabled'] else 'disabled'}"
            self.dashboard_status_vars["llm"].set(text)
            web = snapshot["webui"]
            self.dashboard_status_vars["webui"].set(
                "Not installed" if not web["installed"] else ("Running · port 3000" if web["running"] else "Stopped")
            )
            tail = snapshot["tailscale"]
            if not tail["installed"]:
                text = "Not installed (optional)"
            elif tail["connected"]:
                text = f"Connected · {tail.get('dns_name') or 'tailnet'}"
            else:
                text = f"Daemon {'running' if tail['daemon_active'] else 'stopped'} · {tail['backend_state']}"
            self.dashboard_status_vars["tailscale"].set(text)
        self._work(work, done)

    def _dashboard_use_model(self) -> None:
        selection = self.dashboard_model_tree.selection()
        if not selection:
            messagebox.showinfo("Models", "Select a model first.")
            return
        source, item = self.dashboard_models[selection[0]]
        if source == "installed":
            self._dashboard_action(lambda r: switch_model(self.store, self.state_data, str(item["id"]), r))
        else:
            self._dashboard_action(lambda r: register_and_switch_local(self.store, self.state_data, item.id, r))

    def _dashboard_change_context(self) -> None:
        try:
            ctx = int(self.dashboard_ctx.get())
        except (ValueError, tk.TclError):
            messagebox.showerror("Context", "Enter a valid context size.")
            return
        self._dashboard_action(lambda r: change_context(self.store, self.state_data, ctx, r))

    def _dashboard_tail(self, kind: str) -> None:
        path = (
            str(self.state_data.get("server_log", "/var/log/bc250-llm-server.log"))
            if kind == "server"
            else f"{self.state_data['logs_dir']}/setup.log"
        )
        self._dashboard_action(lambda r: r.run(["tail", "-n", "120", path], check=False))

    def _dashboard_desktop_mode(self) -> None:
        if not messagebox.askyesno(
            "Return to desktop mode",
            "Stop inference, restore Bazzite graphical boot and sleep defaults, and preserve all models?",
        ):
            return
        def action(runner: CommandRunner) -> None:
            switch_to_desktop_mode(self.state_data, runner)
            self.save()
        def done() -> None:
            self._refresh_dashboard()
            messagebox.showinfo("Desktop mode", "Desktop mode is configured. Reboot if the log reports a pending kernel change.")
        self._work(lambda: action(self.runner()), done)

    def _dashboard_enter_llm_mode(self) -> None:
        def action(runner: CommandRunner) -> None:
            apply_llm_mode(self.state_data, runner)
            install_service(self.state_data, runner)
            self.save()
        self._dashboard_action(action)

    def _manage_optimizations(self) -> None:
        self.optimization_return_to_complete = True
        self.show_step(5)

    def _repair(self) -> None:
        self.optimization_return_to_complete = False
        self.state_data.update(setup_complete=False, setup_phase=0)
        self.save()
        self.show_step(0)

    def back(self) -> None:
        if not self.busy:
            if self.current_step == 5 and self.optimization_return_to_complete:
                self.optimization_return_to_complete = False
                self.show_step(10)
            else:
                self.show_step(self.current_step - 1)

    def _work(self, action: Callable[[], None], done: Callable[[], None]) -> None:
        if self.busy:
            return
        self.busy = True
        self.continue_button.configure(state="disabled")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        def target() -> None:
            try:
                action()
                self.events.put(("done", done))
            except Exception as exc:  # noqa: BLE001 - thread boundary must surface every setup failure
                self.events.put(("error", exc))
        threading.Thread(target=target, daemon=True).start()

    def _advance(self) -> None:
        self.save()
        self.show_step(self.current_step + 1)

    def continue_step(self) -> None:
        step = self.current_step
        if step == 0:
            if self.hardware_report and not self.hardware_report.valid:
                messagebox.showerror("Hardware validation failed", "\n".join(self.hardware_report.errors))
                return
            self.state_data["setup_phase"] = max(int(self.state_data.get("setup_phase", 0)), 1)
            self._advance()
        elif step == 1:
            if not acknowledgment_valid(*(v.get() for v in self.ack_vars), self.accept_var.get()):
                return
            acknowledge(self.state_data)
            self._advance()
        elif step == 2:
            mask_desktop = self.mask_desktop.get()
            self._work(lambda: (apply_llm_mode(self.state_data, self.runner(), mask_desktop_services=mask_desktop), self.save()), self._after_llm_mode)
        elif step == 3:
            self._work(lambda: (setup_environment(self.state_data, self.runner()), self.save()), self._advance)
        elif step == 4:
            selected_iid = self.model_tree.selection()[0]
            source, selected = self.model_choices[selected_iid]
            local_data = selected.to_dict() if source == "local" else None
            self.state_data.update(
                selected_model=selected.id,
                selected_source=source,
                selected_local_model=local_data,
                selected_quant=self.quant_var.get(),
                current_ctx=int(self.ctx_var.get()),
                setup_phase=5,
            )
            self._advance()
        elif step == 5:
            try:
                settings = self._collect_optimization_settings()
            except (ValueError, tk.TclError) as exc:
                messagebox.showerror("Invalid optimization settings", str(exc))
                return
            def action() -> None:
                try:
                    runner = self.runner()
                    apply_optimizations(self.state_data, settings, runner)
                    if self.optimization_return_to_complete and self.state_data.get("current_model"):
                        install_service(self.state_data, runner)
                        restart_service(self.state_data, runner)
                        health_check(self.state_data, runner)
                finally:
                    self.save()
            done = self._finish_optimization_management if self.optimization_return_to_complete else self._advance
            self._work(action, done)
        elif step == 6:
            def action() -> None:
                runner = self.runner()
                if self.state_data.get("selected_source") == "local":
                    local = LocalModel.from_dict(self.state_data["selected_local_model"])
                    self.downloaded_path = Path(local.path)
                    self.state_data["download_dir"] = str(self.downloaded_path.parent)
                    self.state_data["setup_phase"] = max(int(self.state_data.get("setup_phase", 0)), 7)
                    runner.emit(f"Download skipped; using existing GGUF {self.downloaded_path}")
                else:
                    model = model_by_id(self.state_data["selected_model"])
                    self.downloaded_path = download_model(
                        self.state_data, model, self.state_data["selected_quant"], runner
                    )
                self.state_data["downloaded_path"] = str(self.downloaded_path)
                self.save()
            self._work(action, self._advance)
        elif step == 7:
            def action() -> None:
                runner = self.runner()
                if self.state_data.get("selected_source") == "local":
                    local = LocalModel.from_dict(self.state_data["selected_local_model"])
                    prepare_local_model(self.state_data, local, runner)
                else:
                    model = model_by_id(self.state_data["selected_model"])
                    downloaded = self.state_data.get("downloaded_path") or self.state_data.get("download_dir")
                    prepare_model(
                        self.state_data, model, self.state_data["selected_quant"], downloaded, runner
                    )
                self.save()
            self._work(action, self._advance)
        elif step == 8:
            def action() -> None:
                runner = self.runner()
                install_service(self.state_data, runner)
                health_check(self.state_data, runner)
                if self.state_data.get("selected_source") != "local":
                    cleanup_conversion_intermediates(
                        self.state_data, model_by_id(self.state_data["selected_model"]), runner
                    )
                self.save()
            self._work(action, self._advance)
        elif step == 9:
            install_webui = self.webui_var.get()
            def action() -> None:
                if install_webui:
                    install_open_webui(self.state_data, self.runner())
                self.state_data["setup_phase"] = 10
                self.save()
            self._work(action, self._finish_setup)
        else:
            self._launch_chat_terminal()

    def _after_llm_mode(self) -> None:
        if self.state_data.get("reboot_required"):
            messagebox.showinfo("Reboot required", "Reboot to activate amdgpu.runpm=0, then re-run BC250 LLM MODE. Your progress is saved.")
            self.continue_button.configure(state="disabled")
        else:
            self._advance()

    def _finish_setup(self) -> None:
        self.state_data.update(setup_complete=True, setup_phase=11)
        self.save()
        self.show_step(10)

    def _finish_optimization_management(self) -> None:
        self.optimization_return_to_complete = False
        self.state_data.update(setup_complete=True, setup_phase=11)
        self.save()
        self.show_step(10)

    def _launch_chat_terminal(self) -> None:
        command = [sys.executable, "-m", "bc250_llm_mode", "chat"]
        terminals = (
            ("konsole", ["konsole", "-e", *command]),
            ("gnome-terminal", ["gnome-terminal", "--", *command]),
            ("x-terminal-emulator", ["x-terminal-emulator", "-e", *command]),
        )
        for executable, argv in terminals:
            if shutil.which(executable):
                subprocess.Popen(argv)
                return
        messagebox.showinfo("Launch chat", "No supported terminal launcher was found. Run:\n" + " ".join(command))


def run_gui(store: StateStore | None = None, management: bool = False) -> None:
    try:
        Wizard(store, management=management).mainloop()
    except tk.TclError as exc:
        raise RuntimeError("A local graphical display is required for the native setup wizard.") from exc
