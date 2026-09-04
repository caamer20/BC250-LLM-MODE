"""Staged Basic/Advanced settings with preview, apply, and discard."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import tkinter as tk
from tkinter import ttk

from ..message_catalog import safe_exception_message
from .view_state import Notice


class SettingsPage(ttk.Frame):
    def __init__(self, parent, shell, application) -> None:
        super().__init__(parent)
        self.shell = shell
        self.application = application
        self._disposed = False
        self._baseline: dict[str, Any] = {}
        self._preference_baseline: dict[str, Any] = {}
        self._build()
        self.discard()

    def _build(self) -> None:
        ttk.Label(
            self,
            text="Changes remain a draft until Apply. Context, slots, and cache choices are fit-checked together.",
            wraplength=720,
        ).pack(anchor="w", fill="x", pady=(0, 7))
        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill="both", expand=True)
        basic = ttk.Frame(self._notebook, padding=8)
        advanced = ttk.Frame(self._notebook, padding=8)
        privacy = ttk.Frame(self._notebook, padding=8)
        self._notebook.add(basic, text="Basic")
        self._notebook.add(advanced, text="Advanced")
        self._notebook.add(privacy, text="Privacy")

        self.context_var = tk.IntVar(value=8192)
        self.slots_var = tk.IntVar(value=1)
        self.kv_var = tk.StringVar(value="q8_0")
        self.flash_var = tk.StringVar(value="auto")
        basic_widgets = (
            ("Context per user", ttk.Spinbox(basic, from_=512, to=262144, increment=512, textvariable=self.context_var, width=12)),
            ("Concurrent user slots", ttk.Spinbox(basic, from_=1, to=8, increment=1, textvariable=self.slots_var, width=12)),
            ("KV cache", ttk.Combobox(basic, values=("q8_0", "q4_0"), state="readonly", textvariable=self.kv_var, width=12)),
            ("Flash attention", ttk.Combobox(basic, values=("auto", "on", "off"), state="readonly", textvariable=self.flash_var, width=12)),
        )
        self.context_input = basic_widgets[0][1]
        for row, (label, widget) in enumerate(basic_widgets):
            ttk.Label(basic, text=label).grid(row=row, column=0, sticky="w", pady=4)
            widget.grid(row=row, column=1, sticky="w", padx=8, pady=4)

        preference_row = 4
        ttk.Separator(basic, orient="horizontal").grid(
            row=preference_row, column=0, columnspan=2, sticky="ew", pady=8
        )
        self.appearance_var = tk.StringVar(value="system")
        self.scale_var = tk.IntVar(value=100)
        self.reduced_motion_var = tk.BooleanVar(value=False)
        self.notifications_var = tk.BooleanVar(value=False)
        ttk.Label(basic, text="Appearance").grid(
            row=preference_row + 1, column=0, sticky="w", pady=4
        )
        ttk.Combobox(
            basic, values=("system (light fallback)", "light", "dark"), state="readonly",
            textvariable=self.appearance_var, width=24,
        ).grid(row=preference_row + 1, column=1, sticky="w", padx=8, pady=4)
        ttk.Label(basic, text="Interface scale").grid(
            row=preference_row + 2, column=0, sticky="w", pady=4
        )
        ttk.Combobox(
            basic, values=(100, 125, 150, 175, 200), state="readonly",
            textvariable=self.scale_var, width=12,
        ).grid(row=preference_row + 2, column=1, sticky="w", padx=8, pady=4)
        ttk.Checkbutton(
            basic, text="Reduce motion and progress animation",
            variable=self.reduced_motion_var,
        ).grid(row=preference_row + 3, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Checkbutton(
            basic, text="Show local maintenance notifications",
            variable=self.notifications_var,
        ).grid(row=preference_row + 4, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Label(
            basic,
            text=f"Model folder: {self.application.paths.models_dir}",
            wraplength=520,
        ).grid(row=preference_row + 5, column=0, columnspan=2, sticky="w", pady=4)

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

        self._build_privacy(privacy)

        self.preview_var = tk.StringVar(value="No pending preview")
        ttk.Label(self, textvariable=self.preview_var, wraplength=720).pack(anchor="w", fill="x", pady=7)
        actions = ttk.Frame(self)
        actions.pack(fill="x")
        ttk.Button(actions, text="Preview", command=self.preview).pack(side="left")
        ttk.Button(actions, text="Apply", command=self.apply).pack(side="left", padx=5)
        ttk.Button(actions, text="Discard changes", command=self.discard).pack(side="left")

    def _build_privacy(self, parent) -> None:
        snapshot = self.application.privacy.snapshot()
        ttk.Label(
            parent, text=snapshot.telemetry,
            font=("TkDefaultFont", 11, "bold"), wraplength=720,
        ).pack(anchor="w", fill="x")
        ttk.Label(
            parent, text=snapshot.network_summary, wraplength=720,
        ).pack(anchor="w", fill="x", pady=(3, 7))
        self._privacy_items = {item.item_id: item for item in snapshot.items}
        self._privacy_tree = ttk.Treeview(
            parent, columns=("retention", "network"), show="tree headings",
            selectmode="browse", height=8,
        )
        self._privacy_tree.heading("#0", text="Data")
        self._privacy_tree.heading("retention", text="Retention")
        self._privacy_tree.heading("network", text="Leaves this machine")
        self._privacy_tree.column("#0", width=190)
        self._privacy_tree.column("retention", width=260)
        self._privacy_tree.column("network", width=260)
        for item in snapshot.items:
            self._privacy_tree.insert(
                "", "end", iid=item.item_id, text=item.label,
                values=(item.retention, item.leaves_machine),
            )
        self._privacy_tree.pack(fill="both", expand=True)
        self._privacy_tree.bind(
            "<<TreeviewSelect>>", lambda _event: self._privacy_selected()
        )
        self._privacy_detail = tk.StringVar(value="Select a data category.")
        ttk.Label(
            parent, textvariable=self._privacy_detail, wraplength=720,
            justify="left",
        ).pack(anchor="w", fill="x", pady=(5, 3))
        self._privacy_manage = ttk.Button(
            parent, text="Open management page", command=self._manage_privacy,
            state="disabled",
        )
        self._privacy_manage.pack(anchor="w")
        if snapshot.items:
            self._privacy_tree.selection_set(snapshot.items[0].item_id)
            self._privacy_selected()

    def _privacy_selected(self) -> None:
        selected = self._privacy_tree.selection()
        item = self._privacy_items.get(str(selected[0])) if selected else None
        if item is None:
            self._privacy_detail.set("Select a data category.")
            self._privacy_manage.configure(state="disabled")
            return
        self._privacy_detail.set(
            f"What: {item.contents}\nLocation: {item.location}\n"
            f"Retention: {item.retention}\nNetwork: {item.leaves_machine}"
        )
        self._privacy_manage.configure(
            text=item.manage_label or "No in-app management action",
            state="normal" if item.manage_route else "disabled",
        )

    def _manage_privacy(self) -> None:
        selected = self._privacy_tree.selection()
        item = self._privacy_items.get(str(selected[0])) if selected else None
        if item is not None and item.manage_route:
            self.shell.navigate(item.manage_route, dict(item.manage_context))

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

    def _preference_values(self) -> dict[str, Any]:
        return self.application.preferences.validate({
            "appearance": self.appearance_var.get().split(" ")[0],
            "ui_scale_percent": int(self.scale_var.get()),
            "reduced_motion": bool(self.reduced_motion_var.get()),
            "notifications_enabled": bool(self.notifications_var.get()),
        })

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
            message = safe_exception_message(exc, code="SETTINGS_INVALID")
            self.preview_var.set(f"{message.title}. {message.body}")

    def apply(self) -> None:
        try:
            context = int(self.context_var.get())
            slots = int(self.slots_var.get())
            values = self._values()
            preferences = self._preference_values()
        except (ValueError, tk.TclError) as exc:
            message = safe_exception_message(exc, code="SETTINGS_INVALID")
            self.shell.notice_bar.show_notice(Notice(
                message.level, message.title, message.body, dismissible=False
            ))
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
            if outcome.ok:
                result_box["preferences"] = self.application.preferences.apply(
                    preferences
                )

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
                preferences = result_box.get("preferences") or {}
                self.shell.apply_preferences(preferences)
                self.discard()

        self.shell._work(action, done)

    def discard(self) -> None:
        state = self.application.read_model()
        current = self.application.runtime_config.current()
        settings = self.application.optimizations.normalized(state.get("optimizations"))
        preferences = self.application.preferences.current()
        self._baseline = deepcopy(settings)
        self._preference_baseline = deepcopy(preferences)
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
        self.appearance_var.set("system (light fallback)" if preferences["appearance"] == "system" else str(preferences["appearance"]))
        self.scale_var.set(int(preferences["ui_scale_percent"]))
        self.reduced_motion_var.set(bool(preferences["reduced_motion"]))
        self.notifications_var.set(bool(preferences["notifications_enabled"]))
        self.preview_var.set("No pending preview")

    def mount(self, parent=None):
        del parent
        self.pack(fill="both", expand=True)
        return self

    def enter(self, route_context=None) -> None:
        context = route_context if isinstance(route_context, dict) else {}
        section = str(context.get("section") or "basic")
        self._notebook.select(2 if section == "privacy" else 0)

    def refresh(self, snapshot=None) -> None:
        del snapshot

    def focus_primary(self) -> None:
        self.context_input.focus_set()

    def leave(self) -> None:
        return None

    def dispose(self) -> None:
        self._disposed = True


__all__ = ["SettingsPage"]
