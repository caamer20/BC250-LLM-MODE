"""Staged Basic/Advanced settings with preview, apply, and discard."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import tkinter as tk
from tkinter import ttk

from .view_state import Notice


class SettingsPage(ttk.Frame):
    def __init__(self, parent, shell, application) -> None:
        super().__init__(parent)
        self.shell = shell
        self.application = application
        self._disposed = False
        self._baseline: dict[str, Any] = {}
        self._build()
        self.discard()

    def _build(self) -> None:
        ttk.Label(
            self,
            text="Changes remain a draft until Apply. Context, slots, and cache choices are fit-checked together.",
            wraplength=720,
        ).pack(anchor="w", fill="x", pady=(0, 7))
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)
        basic = ttk.Frame(notebook, padding=8)
        advanced = ttk.Frame(notebook, padding=8)
        notebook.add(basic, text="Basic")
        notebook.add(advanced, text="Advanced")

        self.context_var = tk.IntVar(value=8192)
        self.slots_var = tk.IntVar(value=1)
        self.kv_var = tk.StringVar(value="q8_0")
        self.flash_var = tk.StringVar(value="auto")
        for row, (label, widget) in enumerate((
            ("Context per user", ttk.Spinbox(basic, from_=512, to=262144, increment=512, textvariable=self.context_var, width=12)),
            ("Concurrent user slots", ttk.Spinbox(basic, from_=1, to=8, increment=1, textvariable=self.slots_var, width=12)),
            ("KV cache", ttk.Combobox(basic, values=("q8_0", "q4_0"), state="readonly", textvariable=self.kv_var, width=12)),
            ("Flash attention", ttk.Combobox(basic, values=("auto", "on", "off"), state="readonly", textvariable=self.flash_var, width=12)),
        )):
            ttk.Label(basic, text=label).grid(row=row, column=0, sticky="w", pady=4)
            widget.grid(row=row, column=1, sticky="w", padx=8, pady=4)

        self.batch_var = tk.IntVar(value=512)
        self.ubatch_var = tk.IntVar(value=128)
        self.gpu_var = tk.BooleanVar(value=False)
        self.gpu_min_var = tk.IntVar(value=500)
        self.gpu_max_var = tk.IntVar(value=1850)
        self.throttle_var = tk.IntVar(value=85)
        self.recovery_var = tk.IntVar(value=75)
        self.safeguards_var = tk.BooleanVar(value=True)
        rows = (
            ("Batch", self.batch_var, 128, 2048, 64),
            ("Micro-batch", self.ubatch_var, 64, 512, 64),
            ("GPU minimum MHz", self.gpu_min_var, 500, 1200, 25),
            ("GPU maximum MHz", self.gpu_max_var, 1500, 2000, 25),
            ("Thermal throttle °C", self.throttle_var, 75, 90, 1),
            ("Thermal recovery °C", self.recovery_var, 60, 85, 1),
        )
        ttk.Checkbutton(advanced, text="Enable capability-gated GPU tuning", variable=self.gpu_var).grid(row=0, column=0, columnspan=2, sticky="w")
        for index, (label, variable, low, high, step) in enumerate(rows, 1):
            ttk.Label(advanced, text=label).grid(row=index, column=0, sticky="w", pady=3)
            ttk.Spinbox(advanced, from_=low, to=high, increment=step, textvariable=variable, width=10).grid(row=index, column=1, sticky="w", padx=8)
        ttk.Checkbutton(advanced, text="Restart-loop and log safeguards", variable=self.safeguards_var).grid(row=len(rows) + 1, column=0, columnspan=2, sticky="w")
        if not self.application.platform.profile.supports_gpu_tuning:
            self.gpu_var.set(False)

        self.preview_var = tk.StringVar(value="No pending preview")
        ttk.Label(self, textvariable=self.preview_var, wraplength=720).pack(anchor="w", fill="x", pady=7)
        actions = ttk.Frame(self)
        actions.pack(fill="x")
        ttk.Button(actions, text="Preview", command=self.preview).pack(side="left")
        ttk.Button(actions, text="Apply", command=self.apply).pack(side="left", padx=5)
        ttk.Button(actions, text="Discard changes", command=self.discard).pack(side="left")

    def _values(self) -> dict[str, Any]:
        current = deepcopy(self._baseline)
        current.update({
            "runtime_enabled": True,
            "flash_attention": self.flash_var.get(),
            "kv_cache_type": self.kv_var.get(),
            "batch_size": int(self.batch_var.get()),
            "ubatch_size": int(self.ubatch_var.get()),
            "parallel_slots": int(self.slots_var.get()),
            "gpu_tuning_enabled": bool(self.gpu_var.get()) and self.application.platform.profile.supports_gpu_tuning,
            "gpu_min_mhz": int(self.gpu_min_var.get()),
            "gpu_max_mhz": int(self.gpu_max_var.get()),
            "thermal_throttle_c": int(self.throttle_var.get()),
            "thermal_recovery_c": int(self.recovery_var.get()),
            "safeguards_enabled": bool(self.safeguards_var.get()),
        })
        return current

    def preview(self) -> None:
        try:
            state = self.application.read_model()
            state["current_ctx"] = int(self.context_var.get())
            values = self.application.optimizations.validate_selection(state, self._values())
            current = self.application.runtime_config.current()
            projection = self.application.runtime_config.preview({
                "model_alias": current.get("model_alias"),
                "context": int(self.context_var.get()),
                "slots": int(self.slots_var.get()),
                "optimizations_patch": values,
            })
            fit = projection.get("fit") or {}
            restart = "restart required" if projection.get("restart_required") else "no restart"
            host = " · host tuning changes" if projection.get("host_tuning_changes") else ""
            self.preview_var.set(f"{fit.get('verdict', 'UNKNOWN')} · {fit.get('detail', '')} · {restart}{host}")
        except Exception as exc:
            self.preview_var.set(f"Draft rejected: {exc}")

    def apply(self) -> None:
        try:
            context = int(self.context_var.get())
            slots = int(self.slots_var.get())
            values = self._values()
        except (ValueError, tk.TclError) as exc:
            self.shell.notice_bar.show_notice(Notice("error", "Settings are invalid", str(exc), dismissible=False))
            return
        result_box: dict[str, Any] = {}

        def action() -> None:
            state = self.application.read_model()
            state["current_ctx"] = context
            checked = self.application.optimizations.validate_selection(state, values)
            self.application.optimizations.apply(state, checked, self.shell.runner())
            current = self.application.runtime_config.current()
            outcome = self.application.activation.activate({
                "model_alias": current.get("model_alias"),
                "context_per_slot": context,
                "parallel_slots": slots,
                "requested_by": "gui",
            })
            self.shell.track_operation_id(outcome.operation_id)
            result_box["outcome"] = outcome

        def done() -> None:
            outcome = result_box.get("outcome")
            ok = bool(outcome and outcome.ok)
            self.shell.notice_bar.show_notice(Notice(
                "success" if ok else "error",
                "Settings applied and verified" if ok else "Settings need attention",
                "The model was restarted with the verified draft."
                if ok else f"Activation ended in {getattr(outcome, 'status', 'UNKNOWN')}. Open Activity for details.",
                dismissible=ok,
            ))
            if ok:
                self.discard()

        self.shell._work(action, done)

    def discard(self) -> None:
        state = self.application.read_model()
        current = self.application.runtime_config.current()
        settings = self.application.optimizations.normalized(state.get("optimizations"))
        self._baseline = deepcopy(settings)
        self.context_var.set(int(current.get("context") or 8192))
        self.slots_var.set(int(current.get("slots") or 1))
        self.kv_var.set(str(settings["kv_cache_type"]))
        self.flash_var.set(str(settings["flash_attention"]))
        self.batch_var.set(int(settings["batch_size"]))
        self.ubatch_var.set(int(settings["ubatch_size"]))
        self.gpu_var.set(bool(settings["gpu_tuning_enabled"]) and self.application.platform.profile.supports_gpu_tuning)
        self.gpu_min_var.set(int(settings["gpu_min_mhz"]))
        self.gpu_max_var.set(int(settings["gpu_max_mhz"]))
        self.throttle_var.set(int(settings["thermal_throttle_c"]))
        self.recovery_var.set(int(settings["thermal_recovery_c"]))
        self.safeguards_var.set(bool(settings["safeguards_enabled"]))
        self.preview_var.set("No pending preview")

    def mount(self, parent=None):
        del parent
        self.pack(fill="both", expand=True)
        return self

    def enter(self, route_context=None) -> None:
        del route_context

    def refresh(self, snapshot=None) -> None:
        del snapshot

    def leave(self) -> None:
        return None

    def dispose(self) -> None:
        self._disposed = True


__all__ = ["SettingsPage"]
