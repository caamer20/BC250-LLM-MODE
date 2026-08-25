"""Operations dashboard: services, models, catalog browser, benchmarks."""

from __future__ import annotations

import json

import shutil
import sys

import tkinter as tk
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from ..catalog import catalog_rows, model_by_id
from ..chat import benchmark
from ..desktop import switch_to_desktop_mode
from ..hardware import detect_hardware
from ..llmmode import apply_llm_mode, stage_desktop_boot
from ..local_models import (
    LocalModel,
    discover_local_models,
    fit_entry_for_local,
    selected_fit_entry,
)
from ..logging_utils import CommandRunner
from ..memory_profile import analyze_memory_profile
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
from ..server import (
    health_check,
    install_service,
    restart_and_wait,
    restart_service,
    service_status,
    start_service,
    stop_service,
)
from ..sharing import https_sharing_status, start_https_sharing, stop_https_sharing
from ..tailscale import (
    connect_tailscale,
    disconnect_tailscale,
    restart_tailscale,
    start_tailscale,
    stop_tailscale,
    tailscale_status,
)


def llamacpp_button_states(report: dict) -> dict[str, str]:
    """§15.4 gating: buttons reflect durable truth, never optimism.

    - recovery barrier present  -> both disabled;
    - any active runtime op     -> both disabled (foreground only);
    - otherwise Update enabled; Rollback only with a VERIFIED retained
      target.
    """
    barrier = bool(report.get("recovery_barrier"))
    active = bool(report.get("active_operation"))
    rollback_ok = bool(report.get("rollback_available"))
    blocked = barrier or active
    return {
        "update": "disabled" if blocked else "normal",
        "rollback": "normal" if (rollback_ok and not blocked) else "disabled",
    }


def llamacpp_card_text(report: dict) -> str:
    """Single source for the card's human text (pure)."""
    promoted = report.get("promoted") or {}
    short = promoted.get("short")
    barrier = report.get("recovery_barrier")
    if barrier:
        text = f"RECOVERY REQUIRED (operation {barrier['operation_id'][:12]})"
    elif short:
        text = f"promoted build {short}"
        if report.get("rollback_available"):
            text += "; prior build retained for rollback"
    else:
        text = "not recorded yet; run setup or update"
    op = report.get("active_operation")
    if op:
        text += (
            f" | {op['type']} {op['state']}"
            + (f" {op['phase']} {op['current']}/{op['total']}"
               if op.get("total") else "")
            + " (foreground only)"
        )
    return f"llama.cpp: {text}"



class DashboardMixin:

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
            "sharing": tk.StringVar(value="Checking…"),
            "memory": tk.StringVar(value="Checking…"),
        }
        services = ttk.Frame(inner)
        services.pack(fill="x")
        self._dashboard_service_card(
            services, "LLM server", self.dashboard_status_vars["llm"],
            (("Start current", lambda: self._dashboard_action(lambda r: start_service(self.state_data, r))),
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

        sharing = ttk.Frame(inner)
        sharing.pack(fill="x", pady=(5, 0))
        self._dashboard_service_card(
            sharing,
            "Tailnet HTTPS — Open WebUI :8443 · Model API :10000",
            self.dashboard_status_vars["sharing"],
            (
                ("Enable", lambda: self._dashboard_action(lambda r: start_https_sharing(self.state_data, r))),
                ("Disable", lambda: self._dashboard_action(lambda r: stop_https_sharing(self.state_data, r))),
            ),
        ).pack(side="left", fill="both", expand=True, padx=(0, 4))
        self._dashboard_service_card(
            sharing,
            "BC-250 memory profile",
            self.dashboard_status_vars["memory"],
            (),
        ).pack(side="left", fill="both", expand=True, padx=(4, 0))

        quick = ttk.Frame(inner)
        quick.pack(fill="x", pady=6)
        ttk.Button(quick, text="Refresh status", command=self._refresh_dashboard).pack(side="left")
        ttk.Button(quick, text="Open WebUI", command=lambda: webbrowser.open("http://127.0.0.1:3000")).pack(side="left", padx=5)
        ttk.Button(quick, text="Open HTTPS WebUI", command=self._open_shared_webui).pack(side="left")
        ttk.Button(quick, text="Start terminal chat", command=self._launch_chat_terminal).pack(side="left")
        ttk.Button(quick, text="Benchmark", command=self._dashboard_benchmark).pack(side="left", padx=5)

        llama_frame = ttk.LabelFrame(inner, text="llama.cpp runtime", padding=6)
        llama_frame.pack(fill="x", pady=4)
        self.llamacpp_status_var = tk.StringVar(value="")
        ttk.Label(llama_frame, textvariable=self.llamacpp_status_var).pack(anchor="w")
        llama_buttons = ttk.Frame(llama_frame)
        llama_buttons.pack(fill="x", pady=(4, 0))
        ttk.Button(
            llama_buttons, text="Refresh status", command=self._dashboard_llamacpp_status
        ).pack(side="left")
        self.llamacpp_update_btn = ttk.Button(
            llama_buttons, text="Update to pinned release",
            command=self._dashboard_llamacpp_update,
        )
        self.llamacpp_update_btn.pack(side="left", padx=5)
        self.llamacpp_rollback_btn = ttk.Button(
            llama_buttons, text="Roll back", command=self._dashboard_llamacpp_rollback,
            state="disabled",
        )
        self.llamacpp_rollback_btn.pack(side="left")
        self._refresh_llamacpp_card()
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
        self.dashboard_model_tree.bind("<Double-1>", lambda _event: self._dashboard_use_model())
        self._populate_dashboard_models()
        model_buttons = ttk.Frame(models_frame)
        model_buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(
            model_buttons, text="Start selected model", command=self._dashboard_use_model
        ).pack(side="left")
        ttk.Button(model_buttons, text="Rescan disks", command=self._populate_dashboard_models).pack(side="left", padx=5)
        ttk.Button(model_buttons, text="Install/download another…", command=lambda: self.show_step(4)).pack(side="left")
        ttk.Label(model_buttons, text="Context:").pack(side="left", padx=(16, 3))
        self.dashboard_ctx = tk.IntVar(value=int(self.state_data.get("current_ctx", 8192)))
        ttk.Spinbox(
            model_buttons, from_=512, to=262144, increment=512,
            textvariable=self.dashboard_ctx, width=9,
        ).pack(side="left")
        ttk.Button(model_buttons, text="Apply", command=self._dashboard_change_context).pack(side="left", padx=4)
        ttk.Label(
            models_frame,
            text=(
                "Highlight a model and choose Start selected model (or double-click it). "
                "LLM server → Start current resumes the already-selected model."
            ),
        ).pack(anchor="w", pady=(5, 0))

        catalog_frame = ttk.LabelFrame(
            inner, text="Catalog browser — search, fit check, and install", padding=6
        )
        catalog_frame.pack(fill="x", pady=4)
        search_row = ttk.Frame(catalog_frame)
        search_row.pack(fill="x")
        self.catalog_search_var = tk.StringVar()
        search_entry = ttk.Entry(search_row, textvariable=self.catalog_search_var)
        search_entry.pack(side="left", fill="x", expand=True)
        search_entry.bind("<Return>", lambda _event: self._refresh_catalog_browser())
        ttk.Button(search_row, text="Search", command=self._refresh_catalog_browser).pack(side="left", padx=4)
        self.dashboard_catalog: dict[str, tuple[str, str | None]] = {}
        self.dashboard_catalog_tree = ttk.Treeview(
            catalog_frame, columns=("quant", "fit", "name"), show="headings", height=6
        )
        for key, title, width in (
            ("quant", "Quant", 85), ("fit", "Fit at current context", 230), ("name", "Model / tags", 450),
        ):
            self.dashboard_catalog_tree.heading(key, text=title)
            self.dashboard_catalog_tree.column(key, width=width, stretch=key == "name")
        self.dashboard_catalog_tree.pack(fill="both", expand=True, pady=(5, 0))
        self.dashboard_catalog_tree.bind("<Double-1>", lambda _event: self._dashboard_install_catalog_model())
        catalog_buttons = ttk.Frame(catalog_frame)
        catalog_buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(
            catalog_buttons, text="Install/download selected", command=self._dashboard_install_catalog_model
        ).pack(side="left")
        ttk.Label(
            catalog_buttons,
            text="The best-fitting quantization is preselected; installing activates the model.",
        ).pack(side="left", padx=6)
        self._refresh_catalog_browser()

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

        command = "python -m bc250_llm_mode chat"
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
            self.commit_narrow()
        self._work(work, self._refresh_dashboard)

    def _refresh_dashboard(self) -> None:
        if self.busy or not hasattr(self, "dashboard_status_vars"):
            return
        snapshot: dict[str, Any] = {}
        def work() -> None:
            # Status refresh is a pure query: probes may annotate the local
            # draft but NEVER persist anything (no revision bump).
            runner = self.runner()
            snapshot["llm"] = service_status(self.state_data, runner)
            if snapshot["llm"]["active"]:
                try:
                    snapshot["health"] = health_check(self.state_data, timeout=3)
                except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as exc:
                    snapshot["health_error"] = str(exc)
            snapshot["webui"] = open_webui_status(self.state_data, runner)
            snapshot["tailscale"] = tailscale_status(runner)
            snapshot["sharing"] = https_sharing_status(self.state_data, runner)
            snapshot["memory"] = analyze_memory_profile(
                detect_hardware(self.state_data["models_dir"], check_vulkan=False)
            ).to_dict()
            try:
                from ..thermals import read_gpu_temperature

                snapshot["gpu_temp_c"] = read_gpu_temperature()
            except (OSError, RuntimeError, ValueError):
                snapshot["gpu_temp_c"] = None
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
            shared = snapshot["sharing"]
            if not shared.get("available"):
                text = shared.get("status", "Unavailable")
            elif shared.get("public_funnel"):
                text = "Unsafe public Funnel detected — disable and re-enable sharing"
            elif shared.get("enabled"):
                text = f"Ready · UI {shared.get('webui_url')} · API {shared.get('api_base_url')}"
            else:
                text = "Disabled or incomplete"
            self.dashboard_status_vars["sharing"].set(text)
            memory = snapshot["memory"]
            prefix = "Supported" if memory["supported"] else memory["risk"].title()
            temp = snapshot.get("gpu_temp_c")
            temp_text = f" · GPU {temp:.0f}°C" if temp is not None else ""
            self.dashboard_status_vars["memory"].set(
                f"{prefix} · {memory['estimated_bios_split']} · "
                f"host usable {memory['detected_host_usable_gib']:.2f} GiB{temp_text} · live resize unavailable"
            )
        self._work(work, done)

    def _poll_dashboard(self) -> None:
        """Keep external CLI/systemd changes visible without blocking tkinter."""
        if self.current_step == 10 and not self.busy and hasattr(self, "dashboard_status_vars"):
            self._refresh_dashboard()
        self.after(5000, self._poll_dashboard)

    def _open_shared_webui(self) -> None:
        url = self.state_data.get("https_webui_url")
        if url:
            webbrowser.open(str(url))
        else:
            messagebox.showinfo("Tailnet HTTPS", "Enable Tailnet HTTPS sharing first.")

    def _dashboard_use_model(self) -> None:
        selection = self.dashboard_model_tree.selection()
        if not selection:
            messagebox.showinfo("Models", "Select a model first.")
            return
        source, item = self.dashboard_models[selection[0]]
        if source == "installed":
            self._dashboard_action(lambda r: switch_model(self.application, self.state_data, str(item["id"]), r))
        else:
            self._dashboard_action(lambda r: register_and_switch_local(self.application, self.state_data, item.id, r))

    def _refresh_catalog_browser(self) -> None:
        if not hasattr(self, "dashboard_catalog_tree"):
            return
        for iid in self.dashboard_catalog_tree.get_children():
            self.dashboard_catalog_tree.delete(iid)
        self.dashboard_catalog.clear()
        try:
            ctx = int(self.dashboard_ctx.get())
        except (ValueError, tk.TclError, AttributeError):
            ctx = int(self.state_data.get("current_ctx", 8192))
        slots = int(self.state_data.get("optimizations", {}).get("parallel_slots", 4))
        rows = catalog_rows(
            self.catalog_search_var.get(),
            ctx_tokens=ctx,
            kv_scale=kv_scale_for_settings(self.state_data.get("optimizations")),
            parallel_slots=slots,
        )
        top_pick = next(
            (row["id"] for row in rows if row["verdict"] == "FITS" and row["recommended_quant"]),
            None,
        )
        for index, row in enumerate(rows):
            iid = f"catalog::{index}"
            self.dashboard_catalog[iid] = (str(row["id"]), row["recommended_quant"])
            fit_text = row["verdict"]
            if row["required_gib"] is not None:
                fit_text += f" · {row['required_gib']:.2f} GiB"
            star = "★ " if row["id"] == top_pick else ""
            self.dashboard_catalog_tree.insert(
                "", "end", iid=iid,
                values=(
                    row["recommended_quant"] or "—",
                    fit_text,
                    f"{star}{row['display_name']} — {', '.join(row['task_tags'])}",
                ),
            )

    def _dashboard_install_catalog_model(self) -> None:
        selection = self.dashboard_catalog_tree.selection()
        if not selection:
            messagebox.showinfo("Catalog browser", "Select a catalog model first.")
            return
        model_id, quant = self.dashboard_catalog[selection[0]]
        if quant is None:
            messagebox.showwarning(
                "Catalog browser", "That model has no quantization that fits the current context."
            )
            return
        model = model_by_id(model_id)
        if not messagebox.askyesno(
            "Install model",
            f"Acquire and activate {model.display_name} ({quant})?\n"
            "The model downloads into app-managed storage; the server "
            "restarts when activation completes.",
        ):
            return

        def action() -> None:
            # U1.1 §8.4: catalog install = durable acquire, then a separate
            # durable activation. Distinct outcomes render distinct messages.
            outcome = self.application.model_acquisition.acquire_catalog(
                str(model.id), str(quant), requested_by="dashboard"
            )
            if outcome.status == "BUSY":
                messagebox.showinfo(
                    "Install model",
                    "Another model operation is already running; try again "
                    "when it finishes.",
                )
                return
            if outcome.status == "RECOVERY_REQUIRED":
                messagebox.showerror(
                    "Install model",
                    f"Operation {outcome.operation_id} needs recovery before "
                    "continuing. Run repair from the maintenance menu.",
                )
                return
            if not outcome.ok:
                messagebox.showerror(
                    "Install model",
                    f"Acquisition ended with {outcome.status} "
                    f"(operation {outcome.operation_id}).",
                )
                return
            alias = outcome.detail.get("alias") or str(model.id)
            if self.state_data.get("setup_complete"):
                # Durable activation through the ONE composed command.
                switch_model(
                    self.application,
                    self.state_data,
                    alias,
                    self.runner(),
                )
                self.state_data.update(self.application.read_model())
            else:
                self.state_data.update(self.application.read_model())

        self._work(action, self._populate_dashboard_models)

    def _dashboard_benchmark(self) -> None:
        result_box: dict[str, Any] = {}

        def work() -> None:
            result_box["result"] = benchmark(self.state_data)

        def done() -> None:
            result = result_box.get("result")
            if not isinstance(result, dict):
                return
            speed = result.get("predicted_per_second")
            if speed:
                messagebox.showinfo(
                    "Benchmark",
                    f"{result.get('model')}\n{float(speed):.1f} tokens/second generation\n"
                    f"prompt processing: {result.get('prompt_per_second') or '?'} tokens/second",
                )
            else:
                messagebox.showinfo("Benchmark", "Benchmark completed without timing data.")

        self._work(work, done)

    def _refresh_llamacpp_card(self) -> None:
        try:
            report = self.application.runtime_lifecycle.status()
        except (OSError, RuntimeError, ValueError) as exc:
            self.llamacpp_status_var.set(f"Status unavailable: {exc}")
            return
        self.llamacpp_status_var.set(llamacpp_card_text(report))
        states = llamacpp_button_states(report)
        if hasattr(self, "llamacpp_rollback_btn"):
            self.llamacpp_rollback_btn.configure(state=states["rollback"])
        if hasattr(self, "llamacpp_update_btn"):
            self.llamacpp_update_btn.configure(state=states["update"])

    def _dashboard_llamacpp_status(self) -> None:
        self._refresh_llamacpp_card()

    def _dashboard_llamacpp_update(self) -> None:
        if not messagebox.askyesno(
            "llama.cpp update",
            "Build the pinned known-good release as an immutable, "
            "verified runtime?\n\n"
            "Runs in the FOREGROUND: keep this window open. Closing it "
            "pauses the operation safely for resume (background workers "
            "arrive in a later release).\n"
            "The server restarts after an atomic, verified switch; any "
            "unproven failure restores the previous build.",
        ):
            return

        def action() -> dict:
            return self.application.runtime_lifecycle.update(
                requested_by="gui"
            ).to_dict()

        def done() -> None:
            outcome = self._last_outcome.get("runtime", {})
            status = outcome.get("status")
            if status == "SUCCEEDED" and outcome.get("already_active"):
                messagebox.showinfo("llama.cpp", "Already on the requested "
                                    "verified build — nothing to do.")
            elif status == "SUCCEEDED":
                messagebox.showinfo("llama.cpp", "Runtime updated and "
                                    "live-verified.")
            elif status == "RECOVERY_REQUIRED":
                messagebox.showerror(
                    "llama.cpp",
                    "The operation needs manual recovery. Operation id:\n"
                    f"{outcome.get('operation_id')}\n"
                    "Every retained tree was preserved.",
                )
            elif status == "BUSY":
                messagebox.showinfo("llama.cpp",
                                    "Another runtime operation is active.")
            else:
                messagebox.showwarning("llama.cpp",
                                       f"Update ended with {status}.")
            self._refresh_llamacpp_card()

        def work_and_capture() -> dict:
            result = action()
            self._last_outcome["runtime"] = result
            return result

        self._work(work_and_capture, done)

    def _dashboard_llamacpp_rollback(self) -> None:
        status_report = self.application.runtime_lifecycle.status()
        target = (status_report.get("rollback") or {}).get("short")
        if not target:
            messagebox.showinfo("llama.cpp rollback",
                                "No verified prior runtime is retained yet.")
            return
        if not messagebox.askyesno(
            "llama.cpp rollback",
            f"Restore retained build {target} and restart the "
            "server?\n\nForeground operation; closing this window pauses "
            "it safely for resume.",
        ):
            return

        def work() -> dict:
            result = self.application.runtime_lifecycle.rollback(
                requested_by="gui"
            ).to_dict()
            self._last_outcome["runtime"] = result
            return result

        def done() -> None:
            outcome = self._last_outcome.get("runtime", {})
            status = outcome.get("status")
            if status == "SUCCEEDED":
                messagebox.showinfo("llama.cpp", "Rollback complete and "
                                    "live-verified.")
            elif status == "RECOVERY_REQUIRED":
                messagebox.showerror(
                    "llama.cpp",
                    "Rollback could not be proven safe; repair is required."
                    f"\nOperation {outcome.get('operation_id')}",
                )
            elif status == "BUSY":
                messagebox.showinfo("llama.cpp",
                                    "Another runtime operation is active.")
            else:
                messagebox.showwarning("llama.cpp",
                                       f"Rollback ended with {status}.")
            self._refresh_llamacpp_card()

        self._work(work, done)

    def _dashboard_change_context(self) -> None:
        try:
            ctx = int(self.dashboard_ctx.get())
        except (ValueError, tk.TclError):
            messagebox.showerror("Context", "Enter a valid context size.")
            return
        self._dashboard_action(lambda r: change_context(self.application, self.state_data, ctx, r))

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
            self.application.host_mode.return_to_desktop(self.state_data, runner)
        def done() -> None:
            self._refresh_dashboard()
            messagebox.showinfo("Desktop mode", "Desktop mode is configured. Reboot if the log reports a pending kernel change.")
        self._work(lambda: action(self.runner()), done)

    def _dashboard_enter_llm_mode(self) -> None:
        def action(runner: CommandRunner) -> None:
            from ..server import install_service

            self.application.host_mode.enter_llm_mode(
                self.state_data, runner, install_service_fn=install_service, install=True
            )
        self._dashboard_action(action)

    def _launch_chat_terminal(self) -> None:
        self.application.open_chat_terminal()
