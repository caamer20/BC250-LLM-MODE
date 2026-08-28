"""Setup wizard screens and step transitions."""

from __future__ import annotations

import json
import sys

import tkinter as tk

from pathlib import Path
from tkinter import messagebox, ttk

from ..catalog import model_by_id
from ..disclaimer import DISCLAIMER_TEXT, acknowledge, acknowledgment_valid
from ..hardware import detect_hardware
from ..local_models import (
    LocalModel,
    discover_local_models,
    fit_entry_for_local,
    selected_fit_entry,
)
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
from ..tailscale import (
    connect_tailscale,
    disconnect_tailscale,
    restart_tailscale,
    start_tailscale,
    stop_tailscale,
    tailscale_status,
)


class StepsMixin:

    def _hardware(self) -> None:
        self._body_label("The GPU is rediscovered by PCI vendor ID on every run; card0/card1 is never assumed.")
        self.hardware_report = detect_hardware(self.state_data["models_dir"])
        report = tk.Text(self.content, height=13, wrap="word")
        report.insert("1.0", json.dumps({
            "hardware": self.hardware_report.to_dict(),
            "memory_profile": analyze_memory_profile(self.hardware_report).to_dict(),
        }, indent=2))
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
            "The model service remains disabled for boot: restarting always returns to the normal host desktop with no LLM running."
        )
        self.mask_desktop = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self.content, text="Also mask heavy desktop/display-manager services (optional)", variable=self.mask_desktop
        ).pack(anchor="w", pady=8)

    def _environment(self) -> None:
        self._body_label(
            "Creates/reuses the llm distrobox, builds llama.cpp with Vulkan, creates the Hugging Face venv, and tests Vulkan visibility. This can take a while."
        )

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

    def _manage_optimizations(self) -> None:
        self.optimization_return_to_complete = True
        self.show_step(5)

    def _repair(self) -> None:
        self.optimization_return_to_complete = False
        if self.application.setup is not None:
            reset = self.application.setup.reset_for_repair("wizard repair")
            self.state_data.update(
                setup_complete=False, setup_phase=reset["phase"]
            )
        else:
            self.state_data.update(setup_complete=False, setup_phase=0)
        self.show_step(0)

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
            # Safety acknowledgement is a SetupService command: it advances
            # the canonical stage and never participates in repair rewinds.
            if self.application.setup is not None:
                self.application.setup.acknowledge_safety()
                self._synced.update(
                    disclaimer_ack=True,
                    setup_stage=self.application.setup.current_workflow()["stage"],
                )
            else:
                self.commit_narrow()
            self._advance()
        elif step == 2:
            mask_desktop = self.mask_desktop.get()
            self._work(
                lambda: self.application.host_mode.enter_llm_mode(
                    self.state_data,
                    self.runner(),
                    mask_desktop_services=mask_desktop,
                ),
                self._after_llm_mode,
            )
        elif step == 3:
            def provision_and_install_runtime() -> dict:
                # §15.5: provisioning stays synchronous; the FIRST runtime
                # comes from durable RUNTIME_UPDATE v1 at the shipped pin.
                application = self.application
                result = application.component.provision_environment(
                    self.state_data, self.runner()
                )
                outcome = application.runtime_lifecycle.update(
                    requested_by="setup"
                )
                result["runtime_operation"] = outcome.to_dict()
                if not outcome.ok:
                    raise RuntimeError(
                        "runtime install failed "
                        f"({outcome.status}); operation {outcome.operation_id}"
                    )
                self.state_data.update(application.read_model())
                return result

            self._work(
                lambda: (provision_and_install_runtime(), self.commit_narrow()),
                self._advance,
            )
        elif step == 4:
            selected_iid = self.model_tree.selection()[0]
            source, selected = self.model_choices[selected_iid]
            local_data = selected.to_dict() if source == "local" else None
            self.state_data["pending_activation_previous"] = {
                "current_model": self.state_data.get("current_model"),
                "current_ctx": self.state_data.get("current_ctx", 8192),
            }
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
                    self.commit_narrow()
            done = self._finish_optimization_management if self.optimization_return_to_complete else self._advance
            self._work(action, done)
        elif step == 6:
            def action() -> None:
                runner = self.runner()
                # U1.1 §8.3: ONE durable acquisition/import operation replaces
                # the mutable download handoff. Foreground only until U1.3.
                acquisition = self.application.model_acquisition
                if self.state_data.get("selected_source") == "local":
                    local = LocalModel.from_dict(self.state_data["selected_local_model"])
                    outcome = acquisition.import_local(
                        str(local.path), requested_by="wizard"
                    )
                    runner.emit(
                        "Importing a managed copy of your model; the original "
                        "file stays unchanged."
                    )
                else:
                    model = model_by_id(self.state_data["selected_model"])
                    outcome = acquisition.acquire_catalog(
                        str(model.id),
                        str(self.state_data["selected_quant"]),
                        requested_by="wizard",
                    )
                    runner.emit(f"Downloading {model.display_name} into managed storage")
                if not outcome.ok:
                    raise RuntimeError(
                        f"Acquisition failed: {outcome.status} "
                        f"(operation {outcome.operation_id})"
                    )
                self.state_data["installed_alias"] = (
                    outcome.detail.get("alias") or self.state_data.get("selected_model")
                )
                self.commit_narrow()
            self._work(action, self._advance)
        elif step == 7:
            def action() -> None:
                runner = self.runner()
                # Durable validation/install outcome: query installation
                # truth instead of trusting prior method returns.
                alias = self.state_data.get("installed_alias")
                model_view = self.application.read_model()
                installed = {
                    item.get("id") for item in model_view.get("installed_models", [])
                }
                if alias not in installed:
                    raise RuntimeError(
                        "The model is not recorded as installed; reopen the "
                        f"wizard to resume operation {alias!r}."
                    )
                self.state_data["setup_phase"] = max(
                    int(self.state_data.get("setup_phase", 0)), 7
                )
                del runner
                self.commit_narrow()
            self._work(action, self._advance)
        elif step == 8:
            def action() -> None:
                runner = self.runner()
                install_service(self.state_data, runner, enable_and_start=False)
                switch_model(
                    self.application,
                    self.state_data,
                    str(self.state_data.get("installed_alias")
                        or self.state_data.get("selected_model")),
                    runner,
                )
                self.commit_narrow()
            self._work(action, self._advance)
        elif step == 9:
            install_webui = self.webui_var.get()
            def action() -> None:
                if install_webui:
                    install_open_webui(self.state_data, self.runner())
                self.state_data["setup_phase"] = 10
                self.commit_narrow()
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
        if self.application.setup is not None:
            self.application.setup.mark_setup_complete()
            self._synced.update(
                setup_complete=True,
                setup_stage=self.application.setup.current_workflow()["stage"],
            )
        else:
            self.commit_narrow()
        self.show_step(10)

    def _finish_optimization_management(self) -> None:
        self.optimization_return_to_complete = False
        self.state_data.update(setup_complete=True, setup_phase=11)
        self.commit_narrow()
        self.show_step(10)
