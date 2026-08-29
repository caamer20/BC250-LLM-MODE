"""Five-chapter first-run setup over the canonical durable workflow."""

from __future__ import annotations

import json
import sys

import tkinter as tk

from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from typing import Any, Mapping

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
from .routes import Route, SETUP_CHAPTERS, setup_chapter_for
from .view_state import Notice


SETUP_STATUS_STATES = frozenset({"ready", "info", "warning", "blocked"})
LEGACY_STEP_CHAPTERS = (0, 1, 2, 2, 3, 3, 4, 4, 4, 4, 4)
_STAGE_INDEX = {
    stage: index for index, stage in enumerate((
        "WELCOME", "SAFETY_ACKNOWLEDGED", "HARDWARE_VALIDATED",
        "TKINTER_READY", "LLM_MODE_CONFIGURED", "RUNTIME_READY",
        "MODEL_SELECTED", "MODEL_PREPARED", "PROFILE_APPLIED",
        "SERVICE_INSTALLED", "OPTIONALS_CONFIGURED", "VERIFIED", "COMPLETE",
    ))
}
_STAGE_RESUME_STEP = {
    "WELCOME": 0,
    "SAFETY_ACKNOWLEDGED": 0,
    "HARDWARE_VALIDATED": 1,
    "TKINTER_READY": 2,
    "LLM_MODE_CONFIGURED": 3,
    "RUNTIME_READY": 4,
    "MODEL_SELECTED": 5,
    "MODEL_PREPARED": 8,
    "PROFILE_APPLIED": 8,
    "SERVICE_INSTALLED": 9,
    "OPTIONALS_CONFIGURED": 9,
    "VERIFIED": 10,
    "COMPLETE": 10,
}


@dataclass(frozen=True)
class SetupStatusRow:
    label: str
    value: str
    state: str = "info"
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.label.strip() or not self.value.strip():
            raise ValueError("setup status rows require a label and value")
        if self.state not in SETUP_STATUS_STATES:
            raise ValueError(f"unknown setup status {self.state!r}")


@dataclass(frozen=True)
class SetupResumeView:
    stage: str
    visible_chapter: int
    durable_chapter: int
    resume_step: int
    progress_value: int
    status: str


@dataclass(frozen=True)
class WorkloadGoal:
    goal_id: str
    label: str
    description: str
    context: int | None
    slots: int | None
    preferred_tags: tuple[str, ...]


WORKLOAD_GOALS = (
    WorkloadGoal(
        "everyday", "Everyday assistant", "Balanced quality, speed, and memory use.",
        8192, 1, ("chat", "general"),
    ),
    WorkloadGoal(
        "long", "Long documents", "Prefer low KV growth and a larger context window.",
        32768, 1, ("long-context", "fast"),
    ),
    WorkloadGoal(
        "several", "Several users", "Prefer a smaller model and four concurrent slots.",
        8192, 4, ("multi-user", "fast"),
    ),
    WorkloadGoal(
        "quality", "Best quality on this card", "Prefer a larger fitting model with conservative context.",
        8192, 1, ("reasoning", "general"),
    ),
    WorkloadGoal(
        "advanced", "Advanced / custom", "Keep expert context and slot controls unchanged.",
        None, None, (),
    ),
)
_WORKLOAD_BY_ID = {item.goal_id: item for item in WORKLOAD_GOALS}


def workload_goal(goal_id: str) -> WorkloadGoal:
    try:
        return _WORKLOAD_BY_ID[goal_id]
    except KeyError:
        raise ValueError(f"unknown workload goal {goal_id!r}") from None


def setup_resume_view(
    stage: str,
    *,
    visible_step: int,
    legacy_phase: int = 0,
    reboot_required: bool = False,
    active_operation: Mapping[str, Any] | None = None,
) -> SetupResumeView:
    """Return honest chapter progress without treating widgets as evidence."""
    durable_chapter = setup_chapter_for(stage)
    try:
        visible_chapter = LEGACY_STEP_CHAPTERS[visible_step]
        resume_step = _STAGE_RESUME_STEP[stage]
    except (IndexError, KeyError):
        raise ValueError("unknown setup step or stage") from None
    # WELCOME has no durable substage for the read-only machine screen. Its
    # legacy phase may only resume the still-unacknowledged Safety screen.
    if stage == "WELCOME" and int(legacy_phase) >= 1:
        resume_step = 1
    operation = dict(active_operation or {})
    if reboot_required:
        status = "Restart required · progress is saved"
    elif operation.get("recovery_required_count"):
        status = "Recovery required · review Activity"
    elif operation.get("paused_count"):
        status = "Setup work paused · progress is saved"
    elif operation.get("active_count"):
        status = "Setup work is continuing"
    else:
        status = f"Saved stage: {stage.replace('_', ' ').title()}"
    return SetupResumeView(
        stage=stage,
        visible_chapter=visible_chapter,
        durable_chapter=durable_chapter,
        resume_step=resume_step,
        progress_value=durable_chapter + 1,
        status=status,
    )


def hardware_status_rows(
    report: Any, memory_profile: Any, platform_status: Mapping[str, Any]
) -> tuple[SetupStatusRow, ...]:
    """Build concise machine-check rows; raw paths stay in technical detail."""
    integration = str(platform_status.get("integration_tier") or "unknown")
    platform_state = "ready" if integration == "native" else (
        "warning" if integration == "compatible-unqualified" else "blocked"
    )
    gpu_state = "ready" if report.is_bc250 else (
        "warning" if report.gpu_path else "blocked"
    )
    memory_state = "ready" if memory_profile.supported else (
        "blocked" if memory_profile.risk == "critical" else "warning"
    )
    vulkan_state = "ready" if report.vulkan_device else "warning"
    disk_state = "ready" if report.disk_free_gib >= 20 else "blocked"
    rows = (
        SetupStatusRow(
            "Host platform",
            str(platform_status.get("distro_name") or platform_status.get("distro_id") or "Unknown Linux"),
            platform_state,
            integration.replace("-", " "),
        ),
        SetupStatusRow(
            "Compute device",
            "AMD BC-250 / GFX1013" if report.is_bc250 else (
                "AMD GPU detected" if report.gpu_path else "Not detected"
            ),
            gpu_state,
            "Detected by PCI vendor 0x1002; DRM card numbers are never cached.",
        ),
        SetupStatusRow(
            "Fast GPU memory",
            f"{report.vram_total_mib / 1024:.1f} GiB detected · 12 GiB target",
            memory_state,
            memory_profile.estimated_bios_split,
        ),
        SetupStatusRow(
            "Host memory",
            f"{report.host_ram_mib / 1024:.1f} GiB total · {report.host_available_mib / 1024:.1f} GiB available",
            "ready" if report.host_ram_mib >= 4096 else (
                "warning" if report.host_ram_mib >= 3072 else "blocked"
            ),
            "Large host allocations and --no-mmap remain disabled.",
        ),
        SetupStatusRow(
            "Model storage",
            f"{report.disk_free_gib:.1f} GiB free",
            disk_state,
            "At least 20 GiB is required for setup and model staging.",
        ),
        SetupStatusRow(
            "Vulkan",
            report.vulkan_device or "Not confirmed yet",
            vulkan_state,
            "The environment chapter performs the authoritative Vulkan smoke test.",
        ),
        SetupStatusRow(
            "UMA profile",
            memory_profile.profile.replace("-", " "),
            memory_state,
            memory_profile.recommendation,
        ),
    )
    return rows


def hardware_technical_detail(
    report: Any, memory_profile: Any, platform_status: Mapping[str, Any]
) -> str:
    payload = {
        "hardware": report.to_dict(),
        "memory_profile": memory_profile.to_dict(),
        "platform": dict(platform_status),
    }
    encoded = json.dumps(payload, indent=2, sort_keys=True)
    return encoded[:8192]


class SetupPageMixin:

    def _show_setup_notice(
        self, level: str, title: str, message: str, *, dismissible: bool = False
    ) -> None:
        self.notice_bar.show_notice(Notice(
            level, title, message, dismissible=dismissible
        ))

    def _record_setup_stage(
        self, expected: str, next_stage: str, evidence: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Advance one canonical stage, or accept an already-later stage."""
        service = self.application.setup
        if service is None:
            return {"stage": next_stage}
        current = service.current_workflow()
        stage = str(current["stage"])
        if stage == expected:
            current = service.advance(expected, next_stage, evidence=dict(evidence or {}))
        elif _STAGE_INDEX.get(stage, -1) < _STAGE_INDEX[next_stage]:
            raise RuntimeError(
                f"Setup cannot record {next_stage}: durable stage is {stage}, expected {expected}."
            )
        self.state_data.update(
            setup_stage=str(current["stage"]), setup_phase=int(current["phase"])
        )
        self._synced.update(
            setup_stage=str(current["stage"]), setup_phase=int(current["phase"])
        )
        return current

    def _record_machine_check(self) -> None:
        """Bridge the acknowledged read-only check to Tk-ready truth."""
        service = self.application.setup
        if service is None:
            self.commit_narrow()
            return
        current = str(service.current_workflow()["stage"])
        if current == "SAFETY_ACKNOWLEDGED":
            report = self.hardware_report or detect_hardware(
                self.state_data["models_dir"]
            )
            if not report.valid:
                raise RuntimeError("Hardware validation no longer passes.")
            service.record_hardware_validation(evidence={
                "is_bc250": report.is_bc250,
                "vram_total_mib": report.vram_total_mib,
                "host_ram_mib": report.host_ram_mib,
                "disk_free_gib": report.disk_free_gib,
            })
            current = "HARDWARE_VALIDATED"
        if current == "HARDWARE_VALIDATED":
            service.advance(
                "HARDWARE_VALIDATED", "TKINTER_READY",
                evidence={"native_gui_running": True},
            )
        workflow = service.current_workflow()
        self.state_data.update(
            setup_stage=str(workflow["stage"]), setup_phase=int(workflow["phase"])
        )
        self._synced.update(
            setup_stage=str(workflow["stage"]), setup_phase=int(workflow["phase"])
        )

    def _hardware(self) -> None:
        self._body_label(
            "This read-only check confirms the machine, memory split, host platform, "
            "storage, and Vulkan visibility before any system change is offered."
        )
        self.hardware_report = detect_hardware(self.state_data["models_dir"])
        memory = analyze_memory_profile(self.hardware_report)
        platform = self.application.platform.status()
        rows = hardware_status_rows(self.hardware_report, memory, platform)
        table = ttk.Frame(self.content)
        table.pack(fill="both", expand=True, pady=(4, 6))
        symbols = {"ready": "Ready", "info": "Info", "warning": "Check", "blocked": "Blocked"}
        for index, row in enumerate(rows):
            item = ttk.Frame(table, padding=(6, 5))
            item.grid(row=index, column=0, sticky="ew")
            item.columnconfigure(1, weight=1)
            ttk.Label(item, text=row.label, width=19).grid(row=0, column=0, sticky="nw")
            ttk.Label(item, text=row.value).grid(row=0, column=1, sticky="w")
            ttk.Label(item, text=symbols[row.state], width=9).grid(row=0, column=2, sticky="e")
            if row.detail:
                ttk.Label(item, text=row.detail, wraplength=620).grid(
                    row=1, column=1, columnspan=2, sticky="w", pady=(2, 0)
                )
        table.columnconfigure(0, weight=1)
        detail = hardware_technical_detail(self.hardware_report, memory, platform)
        ttk.Button(
            self.content,
            text="Technical details",
            command=lambda: self.drawer.show_details("Machine-check technical details", detail),
        ).pack(anchor="e")
        messages = [*self.hardware_report.errors, *self.hardware_report.warnings]
        if messages:
            self._show_setup_notice(
                "error" if self.hardware_report.errors else "warning",
                "Machine check needs attention" if self.hardware_report.errors else "Machine check completed with notes",
                " ".join(messages),
                dismissible=not bool(self.hardware_report.errors),
            )
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
        ttk.Label(
            self.content,
            text="This acknowledgment is saved only in the local BC250 LLM MODE profile. "
                 "Cooling, the firmware CU setting, and the 12/4 UMA allocation remain your responsibility.",
            wraplength=760,
        ).pack(anchor="w", fill="x", pady=(0, 4))
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
        profile = self.application.platform.profile
        review = ttk.LabelFrame(self.content, text="Review before applying", padding=7)
        review.pack(fill="x", pady=5)
        for label, value in (
            ("Host adapter", f"{profile.label} · {profile.integration_tier.value}"),
            ("Authorization", "Administrator approval is required for systemd and udev changes"),
            ("Current boot", "Sleep is runtime-masked and the AMD GPU is pinned awake"),
            ("Next boot", "Graphical desktop; model auto-start remains off"),
            ("Desktop trimming", "Off unless you select the option above"),
        ):
            ttk.Label(review, text=f"{label}: {value}", wraplength=720).pack(
                anchor="w", fill="x", pady=2
            )
        if not profile.supports_current_boot_llm_mode:
            self._show_setup_notice(
                "error", "LLM Mode is unavailable on this host",
                "; ".join(profile.blockers) or "The required systemd/udev capabilities were not detected.",
                dismissible=False,
            )
            self.continue_button.configure(state="disabled")

    def _environment(self) -> None:
        self._body_label(
            "Creates/reuses the llm distrobox, builds llama.cpp with Vulkan, creates the Hugging Face venv, and tests Vulkan visibility. This can take a while."
        )
        plan = self.application.platform.profile.runtime_host_plan()
        review = ttk.LabelFrame(self.content, text="Runtime plan", padding=7)
        review.pack(fill="x", pady=5)
        ttk.Label(
            review,
            text=(
                f"Host packages: {plan.guidance}\n"
                "Guest: reusable Fedora Distrobox named llm\n"
                "Runtime: pinned llama.cpp Vulkan build with a retained known-good rollback target\n"
                "Verification: Vulkan device discovery and allocation smoke test"
            ),
            wraplength=720,
            justify="left",
        ).pack(anchor="w", fill="x")

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
        review = ttk.LabelFrame(self.content, text="Install and start plan", padding=7)
        review.pack(fill="x", pady=7)
        selected_name = (
            LocalModel.from_dict(self.state_data["selected_local_model"]).display_name
            if self.state_data.get("selected_source") == "local"
            else model_by_id(self.state_data["selected_model"]).display_name
        )
        for item in (
            f"Model: {selected_name} · {self.state_data.get('selected_quant')}",
            f"Context: {int(self.state_data.get('current_ctx', 8192)):,} tokens",
            "Operations: acquire/import → validate → activate → health and inference verification",
            "Safety: standard per-tensor GGUF only; mmap remains enabled; one server owner",
        ):
            ttk.Label(review, text=item, wraplength=720).pack(anchor="w", fill="x", pady=2)
        self.continue_button.configure(text="Install selected model")

    def _prepare(self) -> None:
        self._body_label("Verifies GGUF metadata and tensor blocks, then applies only guarded, known-safe repairs. Conversion never reads a full model into host RAM.")
        self.continue_button.configure(text="Validate model")

    def _server(self) -> None:
        self._body_label("Installs the single-owner systemd service, starts it, waits up to 120 seconds, and displays server-log guidance on failure.")
        self.continue_button.configure(text="Start selected model")

    def _webui(self) -> None:
        self.webui_var = tk.BooleanVar(value=bool(self.state_data.get("openwebui_installed")))
        ttk.Checkbutton(self.content, text="Install optional Open WebUI on host port 3000", variable=self.webui_var).pack(anchor="w", pady=10)
        self._body_label("If a model is absent from its selector, log in as admin and create a Workspace model pinned to the base model id.")
        self.continue_button.configure(text="Verify and finish")

    def _setup_ready(self) -> None:
        self.back_button.configure(state="disabled")
        self.continue_button.configure(text="Start chatting in the terminal", state="normal")
        ttk.Label(
            self.content,
            text="Ready",
            font=("TkDefaultFont", 18, "bold"),
        ).pack(anchor="w", pady=(8, 4))
        ttk.Label(
            self.content,
            text=(
                f"{self.state_data.get('current_model') or 'The selected model'} is installed, "
                f"active, and verified at {int(self.state_data.get('current_ctx', 8192)):,} tokens."
            ),
            wraplength=720,
        ).pack(anchor="w", fill="x")
        ttk.Label(
            self.content,
            text="A restart returns to the graphical desktop and does not auto-start the model.",
        ).pack(anchor="w", pady=(4, 12))
        actions = ttk.Frame(self.content)
        actions.pack(fill="x")
        ttk.Button(
            actions, text="Start chatting in the terminal", command=self._launch_chat_terminal
        ).pack(side="left")
        if self.state_data.get("openwebui_installed"):
            ttk.Button(
                actions, text="Open Open WebUI",
                command=lambda: __import__("webbrowser").open("http://127.0.0.1:3000"),
            ).pack(side="left", padx=6)
        ttk.Button(
            actions, text="View system details", command=lambda: self.navigate(Route.SYSTEM)
        ).pack(side="left")

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
                self._show_setup_notice(
                    "error", "Hardware validation failed",
                    " ".join(self.hardware_report.errors), dismissible=False,
                )
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
                self._record_machine_check()
            else:
                self.commit_narrow()
            self.show_step(2)
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
                self.track_operation_id(outcome.operation_id)
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
                self._after_environment,
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
            self.commit_narrow()
            self._record_setup_stage(
                "RUNTIME_READY", "MODEL_SELECTED",
                {"model_id": selected.id, "source": source},
            )
            self.show_step(5)
        elif step == 5:
            try:
                settings = self._collect_optimization_settings()
            except (ValueError, tk.TclError) as exc:
                self._show_setup_notice(
                    "error", "Optimization settings are not valid", str(exc),
                    dismissible=False,
                )
                return
            def action() -> None:
                try:
                    runner = self.runner()
                    self.application.optimizations.apply(
                        self.state_data, settings, runner
                    )
                    if self.optimization_return_to_complete and self.state_data.get("current_model"):
                        current = self.application.runtime_config.current()
                        outcome = self.application.activation.activate({
                            "model_alias": current.get("model_alias"),
                            "context_per_slot": current.get("context"),
                            "parallel_slots": current.get("slots"),
                            "requested_by": "gui",
                        })
                        self.track_operation_id(outcome.operation_id)
                        if not outcome.ok:
                            raise RuntimeError(
                                f"Optimization activation ended in {outcome.status}."
                            )
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
                self.track_operation_id(outcome.operation_id)
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
            self._work(action, self._after_prepare)
        elif step == 8:
            def action() -> None:
                outcome = self.application.activation.activate({
                    "model_alias": str(
                        self.state_data.get("installed_alias")
                        or self.state_data.get("selected_model")
                    ),
                    "context_per_slot": int(self.state_data.get("current_ctx", 8192)),
                    "parallel_slots": int(
                        self.application.optimizations.normalized(
                            self.state_data.get("optimizations")
                        )["parallel_slots"]
                    ),
                    "requested_by": "setup",
                })
                self.track_operation_id(outcome.operation_id)
                if not outcome.ok:
                    raise RuntimeError(
                        f"Activation ended in {outcome.status}; operation {outcome.operation_id}."
                    )
                self.state_data.update(self.application.read_model())
            self._work(action, self._after_server)
        elif step == 9:
            install_webui = self.webui_var.get()
            def action() -> None:
                if install_webui:
                    self.application.openwebui.install(
                        self.state_data, self.runner()
                    )
                self.state_data["setup_phase"] = 10
                self.commit_narrow()
            self._work(action, self._after_optionals)
        else:
            self._launch_chat_terminal()

    def _after_llm_mode(self) -> None:
        self._record_setup_stage(
            "TKINTER_READY", "LLM_MODE_CONFIGURED",
            {
                "desktop_next_boot": True,
                "model_autostart": False,
                "reboot_required": bool(self.state_data.get("reboot_required")),
            },
        )
        if self.state_data.get("reboot_required"):
            self._show_setup_notice(
                "warning", "Restart required · progress is saved",
                "Restart to activate amdgpu.runpm=0, then relaunch BC250 LLM MODE. "
                "The next boot remains graphical and the model will not auto-start.",
                dismissible=False,
            )
            self.continue_button.configure(state="disabled")
        else:
            self._advance()

    def _after_environment(self) -> None:
        self._record_setup_stage(
            "LLM_MODE_CONFIGURED", "RUNTIME_READY",
            {"vulkan_smoke_test": "passed", "runtime": "pinned"},
        )
        self.show_step(4)

    def _after_prepare(self) -> None:
        self._record_setup_stage(
            "MODEL_SELECTED", "MODEL_PREPARED",
            {"managed_model": self.state_data.get("installed_alias")},
        )
        self.show_step(8)

    def _after_server(self) -> None:
        self._record_setup_stage(
            "MODEL_PREPARED", "PROFILE_APPLIED",
            {"profile": "selected setup profile"},
        )
        self._record_setup_stage(
            "PROFILE_APPLIED", "SERVICE_INSTALLED",
            {"single_owner": True, "enabled_at_boot": False},
        )
        self.show_step(9)

    def _after_optionals(self) -> None:
        self._record_setup_stage(
            "SERVICE_INSTALLED", "OPTIONALS_CONFIGURED",
            {"openwebui": bool(self.webui_var.get())},
        )
        self._record_setup_stage(
            "OPTIONALS_CONFIGURED", "VERIFIED",
            {"health": "passed", "inference_endpoint": "loopback"},
        )
        self._finish_setup()

    def _finish_setup(self) -> None:
        if self.application.setup is not None:
            self.application.setup.mark_setup_complete()
            self.refresh_snapshot()
        else:
            self.state_data.update(setup_complete=True, setup_phase=11)
            self.commit_narrow()
        self._show_setup_ready = True
        self.show_step(10)

    def _finish_optimization_management(self) -> None:
        self.optimization_return_to_complete = False
        self.state_data.update(setup_complete=True, setup_phase=11)
        self.commit_narrow()
        self.show_step(10)
