"""Model-selection and optimization form screens."""

from __future__ import annotations

import tkinter as tk

from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any

from ..catalog import CATALOG, calculate_fit
from ..chat import benchmark
from ..local_models import (
    LocalModel,
    discover_local_models,
    fit_entry_for_local,
    selected_fit_entry,
)
from ..model_manager import (
    change_context,
    register_and_switch_local,
    switch_model,
)
from ..openwebui import (
    install_open_webui,
    open_webui_status,
    restart_open_webui,
    start_open_webui,
    stop_open_webui,
)
from ..optimize import (
    DEFAULT_OPTIMIZATIONS,
    TRIMMABLE_SERVICES,
    apply_optimizations,
    kv_scale_for_settings,
    normalized_settings,
    validate_settings,
)


def fit_message(model, quant: str, ctx: int, *, slots: int) -> tuple[str, bool]:
    """Pure VRAM-fit verdict: (message, may_continue).

    Preserves the wizard's exact wording, including the Q4-KV fallback
    suggestion shown when the selected cache type does not fit.
    """
    fit = calculate_fit(model, quant, ctx, parallel_slots=slots)
    concurrency = f" · {ctx:,} tokens per user across {slots} slots"
    if fit.verdict != "NO-FIT":
        return fit.detail + concurrency, True
    q4_fit = calculate_fit(model, quant, ctx, kv_scale=0.5, parallel_slots=slots)
    if q4_fit.verdict != "NO-FIT":
        return f"Q8 NO-FIT; Q4 KV can fit at ~{q4_fit.required_gib:.2f} GiB{concurrency}", True
    return fit.detail + concurrency, False


def optimization_settings_from_values(
    state: dict[str, Any], values: dict[str, Any]
) -> dict[str, Any]:
    """Validate raw form values and re-check the selected model's VRAM fit."""
    checked = validate_settings(values)
    model = selected_fit_entry(state)
    fit = calculate_fit(
        model,
        str(state["selected_quant"]),
        int(state["current_ctx"]),
        kv_scale=kv_scale_for_settings(checked),
        parallel_slots=int(checked["parallel_slots"]),
    )
    if fit.verdict == "NO-FIT":
        raise ValueError(f"Selected runtime settings do not fit: {fit.detail}")
    return checked
from ..server import (
    health_check,
    install_service,
    restart_and_wait,
    restart_service,
    service_status,
    start_service,
    stop_service,
)
from ..tailscale import (
    connect_tailscale,
    disconnect_tailscale,
    restart_tailscale,
    start_tailscale,
    stop_tailscale,
    tailscale_status,
)


class FormsMixin:

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
            self.commit_narrow()
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
            message, can_continue = fit_message(model, self.quant_var.get(), ctx, slots=slots)
            self.fit_label.configure(text=message)
            self.continue_button.configure(state="normal" if can_continue else "disabled")
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
        profile = getattr(getattr(self.application, "platform", None), "profile", None)
        self.gpu_tuning_available = bool(
            profile is None or profile.supports_gpu_tuning
        )
        if not self.gpu_tuning_available:
            settings["gpu_tuning_enabled"] = False
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

        gpu_title = (
            "Cyan GPU governor (host change; reversible)"
            if self.gpu_tuning_available
            else "GPU governor tuning (unavailable on this host)"
        )
        gpu = ttk.LabelFrame(inner, text=gpu_title, padding=7)
        gpu.pack(fill="x", pady=4)
        self.opt_gpu = tk.BooleanVar(value=bool(settings["gpu_tuning_enabled"]))
        gpu_check = ttk.Checkbutton(
            gpu, text="Tune frequency range and thermal limits", variable=self.opt_gpu
        )
        if not self.gpu_tuning_available:
            gpu_check.configure(state="disabled")
        gpu_check.pack(anchor="w")
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
        gpu_note = (
            "Recommended: 500–1850 MHz, throttle 85°C, recover 75°C. "
            "Values above 1850 MHz are experimental."
            if self.gpu_tuning_available
            else "The Cyan governor was not detected. Runtime tuning and "
                 "thermal emergency-stop protection remain available; clock changes are disabled."
        )
        ttk.Label(gpu, text=gpu_note).pack(anchor="w")

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
        self.opt_gpu.set(bool(getattr(self, "gpu_tuning_available", True)))
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
            "gpu_tuning_enabled": (
                self.opt_gpu.get()
                if getattr(self, "gpu_tuning_available", True)
                else False
            ),
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
        return optimization_settings_from_values(self.state_data, settings)
