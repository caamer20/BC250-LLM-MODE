"""One installed-and-available model library with explicit draft actions."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Iterable, Mapping

import tkinter as tk
from tkinter import filedialog, ttk

from ..catalog import ADVERTISED_CATALOG, calculate_fit, validation_tier
from ..constants import FAST_VRAM_GIB
from ..appliance_readiness import readiness_from_snapshots
from ..model_recommendation import (
    ModelRecommendationPolicy,
    RecommendationCandidate,
    evidence_is_fresh,
    fit_label,
)
from ..presentation import format_bytes, format_number
from ..progress_projection import format_elapsed, project_operation_progress
from ..ux_guidance import (
    WORKLOAD_PRESETS,
    friendly_state,
    model_install_time_guidance,
    quantization_guidance,
    workload_preset,
)
from .routes import Route
from .view_state import Confirmation, Notice


MODEL_PRESENTATION_STATES = frozenset({
    "AVAILABLE", "DOWNLOADING", "VALIDATING", "INSTALLED", "ACTIVE",
    "VERIFIED", "QUARANTINED", "REMOVING", "RECOVERY_REQUIRED",
})
MODEL_FILTERS = ("Recommended", "Installed", "Long context", "Multi-user", "All")
MAX_MODEL_ROWS = 100


@dataclass(frozen=True)
class ModelActionView:
    code: str
    label: str
    secondary_code: str | None = None
    secondary_label: str | None = None


@dataclass(frozen=True)
class ModelInstallProgressView:
    message: str
    mode: str
    value: float


def _format_progress_bytes(value: int) -> str:
    if value >= 1024**3:
        return f"{value / 1024**3:.2f} GiB"
    if value >= 1024**2:
        return f"{value / 1024**2:.1f} MiB"
    return f"{value / 1024:.1f} KiB"


def build_install_progress_view(
    summary: Any,
    *,
    model_name: str,
    current_step: str | None = None,
) -> ModelInstallProgressView:
    """Render durable acquisition truth without exposing source paths."""
    projection = project_operation_progress(
        summary, current_step=current_step,
    )
    current = max(0, int(getattr(summary, "progress_current", 0) or 0))
    total = int(getattr(summary, "progress_total", 0) or 0)
    unit = str(getattr(summary, "progress_unit", "") or "")
    elapsed = (
        f" · {format_elapsed(projection.elapsed_seconds)}"
        if getattr(summary, "created_at", None) else ""
    )
    problem = (
        f" {projection.problem.title}: {projection.problem.user_message}"
        if projection.problem is not None else ""
    )
    if projection.determinate_fraction is not None:
        bounded = min(current, total)
        percent = projection.determinate_fraction * 100.0
        if unit == "bytes":
            amount = (
                f"{_format_progress_bytes(bounded)} of "
                f"{_format_progress_bytes(total)}"
            )
        else:
            amount = f"{bounded} of {total} {unit}".strip()
        return ModelInstallProgressView(
            f"{projection.phase_label}: {model_name} — {amount} "
            f"({percent:.0f}%){elapsed}.{problem}",
            "determinate",
            percent,
        )
    return ModelInstallProgressView(
        f"{projection.phase_label}: {model_name}{elapsed}. "
        f"Next: {projection.next_checkpoint}.{problem}",
        "indeterminate",
        0.0,
    )


@dataclass(frozen=True)
class ModelItemView:
    key: str
    display_name: str
    family: str
    size_gib: float | None
    state: str
    fit_verdict: str | None
    fit_detail: str
    support_tier: str
    description: str
    source_repo: str | None
    catalog_id: str | None
    alias: str | None
    quant: str | None
    available_quants: tuple[str, ...]
    tags: tuple[str, ...]
    deletion_eligible: bool = False
    deletion_blockers: tuple[str, ...] = ()
    active: bool = False
    running: bool = False
    verified: bool = False
    remote: bool = False
    busy: bool = False
    switching: bool = False
    recommended: bool = False
    recommendation_rank: int | None = None
    recommendation_label: str | None = None
    recommendation_reasons: tuple[str, ...] = ()
    measurement_summary: str = "Not measured on this machine"
    profile_summary: str = ""
    immutable_identity: bool = False
    standard_layout: bool = False
    memory_required_gib: float | None = None

    def __post_init__(self) -> None:
        if self.state not in MODEL_PRESENTATION_STATES:
            raise ValueError(f"unknown model presentation state {self.state!r}")


def model_action(item: ModelItemView) -> ModelActionView:
    if item.busy or item.state in {"DOWNLOADING", "VALIDATING", "REMOVING", "RECOVERY_REQUIRED"}:
        return ModelActionView("activity", "Resolve recovery")
    if item.state == "QUARANTINED" or item.fit_verdict == "NO-FIT":
        return ModelActionView("fit", "View why it cannot start")
    if item.remote:
        return ModelActionView(
            "install-start", "Install, Start and Chat", "install", "Install only")
    if item.active and item.verified:
        return ModelActionView("chat", "Open Chat")
    if item.active and item.running:
        return ModelActionView("checks", "View why it cannot start")
    return ModelActionView(
        "activate", "Switch and Chat" if item.switching else "Start and Chat")


def _measurement_summary(row: Any, *, now=None) -> tuple[bool, str]:
    summary = getattr(row, "benchmark_summary", None)
    if not isinstance(summary, Mapping) or not evidence_is_fresh(
        getattr(row, "last_verified_inference_at", None), now=now,
    ):
        return False, "Not measured on this machine"
    parts = []
    for key, label, suffix in (
        ("tokens_per_second", "speed", " tok/s"),
        ("first_token_seconds", "first token", " s"),
        ("peak_temperature_c", "peak", " °C"),
    ):
        value = summary.get(key)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            parts.append(f"{label} {float(value):.1f}{suffix}")
    return (True, "Measured locally: " + " · ".join(parts)) if parts else (
        False, "Not measured on this machine")


def build_model_items(
    installed: Iterable[Any],
    *,
    context: int,
    slots: int,
    selected_quants: Mapping[str, str] | None = None,
    operations_active: bool = False,
    recovery_required: bool = False,
    inference_verified: bool = False,
    model_running: bool | None = None,
    now=None,
) -> tuple[ModelItemView, ...]:
    """Merge durable installations and curated remote candidates once."""
    selected_quants = selected_quants or {}
    if model_running is None:
        model_running = bool(inference_verified)
    installed_rows = list(installed)
    catalog_by_id = {entry.id: entry for entry in ADVERTISED_CATALOG}
    another_model_active = any(
        bool(getattr(row, "active", False)) for row in installed_rows)
    installed_catalog = {
        str(getattr(row, "catalog_id", "") or ""): row
        for row in installed_rows if getattr(row, "catalog_id", None)
    }
    items: list[ModelItemView] = []
    for row in installed_rows:
        trust = str(getattr(row, "trust_state", "") or "")
        validation = str(getattr(row, "validation_status", "") or "")
        active = bool(getattr(row, "active", False))
        quarantined = trust.upper() == "QUARANTINED" or validation.lower() == "quarantined"
        verified = (
            active and model_running and inference_verified
            and not bool(getattr(row, "fit_verdict", None) == "NO-FIT")
        )
        state = (
            "QUARANTINED" if quarantined else "VERIFIED" if verified
            else "ACTIVE" if active and model_running else "INSTALLED")
        byte_size = getattr(row, "byte_size", None)
        entry = catalog_by_id.get(str(getattr(row, "catalog_id", "") or ""))
        installed_fit = None
        installed_quant = getattr(row, "quant", None)
        if entry is not None and installed_quant in entry.weights_gib_by_quant:
            try:
                installed_fit = calculate_fit(
                    entry, str(installed_quant), context,
                    parallel_slots=slots,
                )
            except (KeyError, ValueError):
                installed_fit = None
        support_tier = validation_tier(entry) if entry is not None else "local"
        format_name = str(getattr(row, "format", "") or "").lower()
        standard_layout = bool(
            format_name == "gguf"
            and trust.upper() == "VERIFIED"
            and validation.lower() in {"verified", "validated"}
            and not quarantined
        )
        immutable_identity = bool(getattr(row, "content_digest", None))
        architecture = str(getattr(row, "architecture", "") or "")
        architecture_compatible = bool(
            entry is not None and architecture == entry.family)
        measured_local, measurement_summary = _measurement_summary(row, now=now)
        items.append(ModelItemView(
            key=f"installed::{row.alias}",
            display_name=str(row.display_name),
            family=str(getattr(row, "architecture", None) or getattr(row, "catalog_id", None) or "custom"),
            size_gib=(float(byte_size) / 1024**3 if byte_size else None),
            state="RECOVERY_REQUIRED" if recovery_required else state,
            fit_verdict=(
                getattr(row, "fit_verdict", None)
                or (installed_fit.verdict if installed_fit is not None else None)
            ),
            fit_detail=str(
                getattr(row, "fit_detail", None)
                or (installed_fit.detail if installed_fit is not None else None)
                or "Fit evidence is unavailable for this custom artifact."
            ),
            support_tier=support_tier,
            description=(
                f"Managed {getattr(row, 'format', None) or 'GGUF'} artifact · "
                f"validation {validation or 'unverified'} · trust {trust or 'unverified'}"
            ),
            source_repo=getattr(row, "source_repo", None),
            catalog_id=getattr(row, "catalog_id", None),
            alias=str(row.alias),
            quant=getattr(row, "quant", None),
            available_quants=tuple(filter(None, (getattr(row, "quant", None),))),
            tags=(),
            deletion_eligible=bool(getattr(row, "deletion_eligible", False)),
            deletion_blockers=tuple(getattr(row, "deletion_blockers", ()) or ()),
            active=active,
            running=active and model_running,
            verified=verified,
            remote=False,
            busy=operations_active,
            switching=another_model_active and not active,
            measurement_summary=measurement_summary,
            profile_summary=f"Selected workload: {context:,} context · {slots} slot(s)",
            immutable_identity=immutable_identity,
            standard_layout=standard_layout and architecture_compatible,
            memory_required_gib=(
                installed_fit.required_gib if installed_fit is not None else None
            ),
        ))
    for entry in ADVERTISED_CATALOG:
        if entry.id in installed_catalog:
            continue
        quants = tuple(entry.allow_globs)
        preferred_quant = selected_quants.get(entry.id)
        quant = (
            preferred_quant if preferred_quant in quants else
            "Q5_K_M" if "Q5_K_M" in quants else quants[0]
        )
        fit_required_gib = None
        try:
            fit = calculate_fit(entry, quant, context, parallel_slots=slots)
            fit_verdict = fit.verdict
            fit_detail = fit.detail
            fit_required_gib = fit.required_gib
        except ValueError as exc:
            # A draft may exceed one catalog entry's trained context limit.
            # That is a per-model NO-FIT result, not a reason to discard the
            # installed library and every other downloadable model.
            fit_verdict = "NO-FIT"
            fit_detail = f"NO-FIT — {exc}"
        items.append(ModelItemView(
            key=f"catalog::{entry.id}",
            display_name=entry.display_name,
            family=entry.family,
            size_gib=entry.weights_gib_by_quant.get(quant),
            state="RECOVERY_REQUIRED" if recovery_required else "AVAILABLE",
            fit_verdict=fit_verdict,
            fit_detail=fit_detail,
            support_tier=validation_tier(entry),
            description=entry.notes,
            source_repo=entry.repo,
            catalog_id=entry.id,
            alias=None,
            quant=quant,
            available_quants=quants,
            tags=entry.task_tags,
            remote=True,
            busy=operations_active,
            switching=another_model_active,
            measurement_summary="Not measured on this machine",
            profile_summary=f"Selected workload: {context:,} context · {slots} slot(s)",
            immutable_identity=False,
            standard_layout=bool(
                not entry.conversion
                and all(
                    "*" not in filename and "?" not in filename
                    for filename in entry.allow_globs.values()
                )
            ),
            memory_required_gib=fit_required_gib,
        ))
    candidates = []
    for item in items:
        candidates.append(RecommendationCandidate(
            key=item.key,
            standard_layout=item.standard_layout,
            immutable_identity=item.immutable_identity,
            fit_verdict=item.fit_verdict,
            support_tier=item.support_tier,
            architecture_compatible=(
                item.standard_layout
                and (item.remote or item.catalog_id in catalog_by_id)
            ),
            inference_verified=item.verified,
            measured_local=item.measurement_summary.startswith("Measured locally:"),
            installed=not item.remote,
            active=item.active,
        ))
    decisions = ModelRecommendationPolicy().evaluate(candidates)
    items = [
        replace(
            item,
            recommended=decisions[item.key].eligible,
            recommendation_rank=decisions[item.key].rank,
            recommendation_label=decisions[item.key].label,
            recommendation_reasons=decisions[item.key].reasons,
        )
        for item in items
    ]
    return tuple(sorted(items, key=lambda item: (item.remote, item.display_name.lower())))


def filter_model_items(
    items: Iterable[ModelItemView], *, query: str = "", category: str = "All"
) -> tuple[ModelItemView, ...]:
    if category not in MODEL_FILTERS:
        raise ValueError(f"unknown model filter {category!r}")
    needle = query.strip().lower()
    result = []
    for item in items:
        text = " ".join((item.display_name, item.family, *item.tags)).lower()
        if needle and needle not in text:
            continue
        if category == "Installed" and item.remote:
            continue
        if category == "Long context" and "long-context" not in item.tags:
            continue
        if category == "Multi-user" and "multi-user" not in item.tags:
            continue
        if category == "Recommended" and not item.recommended:
            continue
        result.append(item)
    if category == "Recommended":
        result.sort(key=lambda item: (
            item.recommendation_rank if item.recommendation_rank is not None else 999,
            item.display_name.casefold(),
        ))
    return tuple(result[:MAX_MODEL_ROWS])


class ModelsPage(ttk.Frame):
    def __init__(self, parent, shell, application, *, context=None) -> None:
        super().__init__(parent)
        self.shell = shell
        self.application = application
        self._disposed = False
        self._all_items: tuple[ModelItemView, ...] = ()
        self._visible: dict[str, ModelItemView] = {}
        self._rendered_visible: tuple[ModelItemView, ...] | None = None
        self._rendered_detail: ModelItemView | None = None
        self._detail_was_rendered = False
        self._selected_key: str | None = None
        self._install_progress_mode: str | None = None
        self._install_starting_name: str | None = None
        self._install_terminal_message: str | None = None
        self._install_terminal_ok = False
        self._available_bytes: int | None = None
        self._quant_choices: dict[str, str] = {}
        self._route_context = dict(context or {})
        runtime = application.runtime_config.current()
        initial_context = int(runtime.get("context") or 8192)
        initial_slots = int(runtime.get("slots") or 1)
        self.context_var = tk.IntVar(value=initial_context)
        self.slots_var = tk.IntVar(value=initial_slots)
        self.search_var = tk.StringVar(value="")
        # All models are visible by default. Recommendation remains a useful
        # filter, but never silently hides a requested/imported model.
        self.filter_var = tk.StringVar(value="All")
        initial_preset = next(
            (
                item.label for item in WORKLOAD_PRESETS
                if item.context == initial_context and item.slots == initial_slots
            ),
            "Custom",
        )
        self.preset_var = tk.StringVar(value=initial_preset)
        self.quant_var = tk.StringVar(value="")
        self.quant_help_var = tk.StringVar(value=quantization_guidance(None))
        self.result_count_var = tk.StringVar(value="Loading models…")
        self._build()
        self.refresh()

    def _build(self) -> None:
        filters = ttk.Frame(self)
        filters.pack(fill="x", pady=(0, 7))
        ttk.Label(filters, text="Search").pack(side="left")
        self.search_entry = ttk.Entry(
            filters, textvariable=self.search_var, width=28
        )
        self.search_entry.pack(side="left", padx=(4, 8))
        combo = ttk.Combobox(filters, values=MODEL_FILTERS, state="readonly", textvariable=self.filter_var, width=15)
        combo.pack(side="left")
        ttk.Label(filters, textvariable=self.result_count_var).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(
            filters, text="Show all", command=self._show_all_models
        ).pack(side="left", padx=(6, 0))
        ttk.Button(filters, text="Import local GGUF…", command=self._import_local).pack(side="right")
        self.search_var.trace_add("write", lambda *_: self._render_list())
        combo.bind("<<ComboboxSelected>>", lambda _event: self._render_list())

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        right = ttk.Frame(body, padding=(9, 0, 0, 0))
        body.add(left, weight=3)
        body.add(right, weight=2)
        ttk.Label(
            left,
            text="Models table · use Up/Down to select and Enter for the primary action",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 3))
        self.tree = ttk.Treeview(
            left, columns=("state", "family", "size", "fit"), show="tree headings", height=15
        )
        self.tree.heading("#0", text="Model")
        for key, title, width in (
            ("state", "State", 105), ("family", "Family", 85),
            ("size", "Size", 70), ("fit", "Fit", 75),
        ):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, stretch=False)
        self.tree.column("#0", width=260)
        self.tree.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        horizontal_scroll = ttk.Scrollbar(
            left, orient="horizontal", command=self.tree.xview
        )
        horizontal_scroll.grid(row=2, column=0, sticky="ew")
        self.tree.configure(
            yscrollcommand=scroll.set, xscrollcommand=horizontal_scroll.set
        )
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._select)
        self.tree.bind("<Up>", self._highlight_previous)
        self.tree.bind("<Down>", self._highlight_next)
        self.tree.bind("<Return>", self._run_highlighted_action)
        self.tree.bind("<KP_Enter>", self._run_highlighted_action)

        self.detail_title = tk.StringVar(value="Select a model")
        self.detail_state = tk.StringVar(value="")
        self.detail_body = tk.StringVar(value="")
        ttk.Label(right, textvariable=self.detail_title, font=("TkDefaultFont", 14, "bold")).pack(anchor="w")
        ttk.Label(right, textvariable=self.detail_state).pack(anchor="w", pady=(2, 5))
        detail_frame = ttk.Frame(right)
        detail_frame.pack(fill="both", expand=True)
        self._detail_text = tk.Text(
            detail_frame, height=10, wrap="word", state="disabled",
        )
        self._detail_text.pack(side="left", fill="both", expand=True)
        detail_scroll = ttk.Scrollbar(
            detail_frame, orient="vertical", command=self._detail_text.yview
        )
        detail_scroll.pack(side="right", fill="y")
        self._detail_text.configure(yscrollcommand=detail_scroll.set)
        draft = ttk.LabelFrame(right, text="Activation draft", padding=6)
        draft.pack(fill="x", pady=8)
        ttk.Label(draft, text="Workload preset").grid(row=0, column=0, sticky="w")
        preset_box = ttk.Combobox(
            draft, state="readonly", textvariable=self.preset_var,
            values=(*tuple(item.label for item in WORKLOAD_PRESETS), "Custom"), width=20,
        )
        preset_box.grid(row=0, column=1, sticky="ew", padx=4)
        preset_box.bind("<<ComboboxSelected>>", self._apply_preset)
        ttk.Label(draft, text="Quality / size").grid(row=1, column=0, sticky="w")
        self.quant_box = ttk.Combobox(draft, state="readonly", textvariable=self.quant_var, width=12)
        self.quant_box.grid(row=1, column=1, sticky="w", padx=4)
        self.quant_box.bind("<<ComboboxSelected>>", self._quant_changed)
        ttk.Label(
            draft, textvariable=self.quant_help_var, wraplength=320,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 4))
        ttk.Label(draft, text="Context per user").grid(row=3, column=0, sticky="w")
        ttk.Spinbox(draft, from_=512, to=262144, increment=512, textvariable=self.context_var, width=12).grid(row=3, column=1, sticky="w", padx=4)
        ttk.Label(draft, text="Concurrent users").grid(row=4, column=0, sticky="w")
        ttk.Spinbox(draft, from_=1, to=8, increment=1, textvariable=self.slots_var, width=12).grid(row=4, column=1, sticky="w", padx=4)
        ttk.Label(
            draft,
            text="More context or users reserve more GPU memory. Recalculate before applying.",
            wraplength=320,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(3, 0))
        ttk.Button(draft, text="Recalculate fit", command=self.refresh).grid(row=6, column=0, columnspan=2, sticky="w", pady=(5, 0))
        draft.columnconfigure(1, weight=1)
        self.action_bar = ttk.Frame(right)
        self.action_bar.pack(fill="x", pady=(4, 0))
        self._primary_action_code: str | None = None
        self._secondary_action_code: str | None = None
        self._primary_action_button = ttk.Button(
            self.action_bar, command=self._run_primary_action,
        )
        self._secondary_action_button = ttk.Button(
            self.action_bar, command=self._run_secondary_action,
        )
        self._apply_draft_button = ttk.Button(
            self.action_bar, text="Apply workload",
            command=lambda: self._run_action("activate"),
        )
        self._remove_button = ttk.Button(
            self.action_bar, text="Remove…", command=self._confirm_remove,
        )
        progress_frame = ttk.LabelFrame(
            right, text="Install / start progress", padding=6
        )
        progress_frame.pack(fill="x", pady=(10, 0))
        self.install_progress_text = tk.StringVar(
            value="No model installation is currently running."
        )
        ttk.Label(
            progress_frame,
            textvariable=self.install_progress_text,
            wraplength=340,
            justify="left",
        ).pack(anchor="w", fill="x")
        self.install_progress = ttk.Progressbar(
            progress_frame, maximum=100, mode="determinate"
        )
        self.install_progress.pack(fill="x", pady=(5, 4))
        ttk.Button(
            progress_frame,
            text="View installation details",
            command=lambda: self.shell.navigate(Route.ACTIVITY),
        ).pack(anchor="e")
        ttk.Label(
            progress_frame,
            text="Activity can safely stop, resume, or recover supported installation work.",
            wraplength=340,
        ).pack(anchor="w", fill="x")

    def _show_all_models(self) -> None:
        self.filter_var.set("All")
        self.search_var.set("")
        self._render_list()

    def _apply_preset(self, _event=None) -> None:
        try:
            preset = workload_preset(self.preset_var.get())
        except ValueError:
            return
        self.context_var.set(preset.context)
        self.slots_var.set(preset.slots)
        self.shell.notice_bar.show_notice(Notice(
            "info", f"{preset.label} workload selected", preset.summary,
        ))
        self.refresh()

    def _sync_preset_label(self, context: int, slots: int) -> None:
        label = next(
            (
                item.label for item in WORKLOAD_PRESETS
                if item.context == context and item.slots == slots
            ),
            "Custom",
        )
        self.preset_var.set(label)

    def _quant_changed(self, _event=None) -> None:
        item = self._selected()
        if item is not None and item.remote and item.catalog_id:
            selected = self.quant_var.get()
            if selected in item.available_quants:
                self._quant_choices[item.catalog_id] = selected
        self.quant_help_var.set(quantization_guidance(self.quant_var.get()))
        self.refresh()

    def mount(self, parent=None):
        del parent
        self.pack(fill="both", expand=True)
        return self

    def enter(self, route_context=None) -> None:
        if route_context:
            self._route_context = dict(route_context)
        self.refresh()

    def refresh(self, snapshot=None) -> None:
        del snapshot
        if self._disposed:
            return
        try:
            context = int(self.context_var.get())
            slots = int(self.slots_var.get())
        except (ValueError, tk.TclError):
            return
        self._sync_preset_label(context, slots)
        def observe():
            install_summary, install_detail = self._observe_install_operation()
            home = self.application.home.snapshot().to_dict()
            connection = self.application.connections.snapshot().to_dict()
            return (
                self.application.model_library.entries(context=context, slots=slots),
                self.application.operation_query.active_summary(),
                home,
                readiness_from_snapshots(
                    home=home, connection=connection,
                    target_journey="native_chat",
                ),
                install_summary,
                install_detail,
            )

        self.shell.request_observation(
            observe,
            lambda result: self._apply_observation(
                result, context=context, slots=slots
            ),
        )

    def _apply_observation(self, result, *, context: int, slots: int) -> None:
        if self._disposed:
            return
        installed, active, home, readiness, install_summary, install_detail = result
        storage = ((home.get("cards") or {}).get("storage") or {})
        available = storage.get("available_bytes")
        next_available = (
            int(available) if isinstance(available, (int, float)) else None
        )
        if next_available != self._available_bytes:
            self._detail_was_rendered = False
        self._available_bytes = next_available
        self._render_observed_install_progress(install_summary, install_detail)
        self._all_items = build_model_items(
            installed, context=context, slots=slots,
            selected_quants=self._quant_choices,
            operations_active=bool(active.active_count),
            recovery_required=bool(active.recovery_required_count),
            inference_verified=(
                bool(readiness.native_chat_ready)
            ),
            model_running=bool(readiness.component("model").process_ready),
        )
        requested = self._route_context.get("model_id")
        if requested:
            for item in self._all_items:
                if item.alias == requested or item.catalog_id == requested:
                    if self._selected_key != item.key:
                        self._rendered_visible = None
                    self._selected_key = item.key
                    break
            self._route_context.pop("model_id", None)
        self._render_list()

    def _install_model_name(self, summary: Any, detail: Any) -> str:
        if str(getattr(summary, "kind", "")) == "MODEL_IMPORT":
            return "local GGUF"
        request = getattr(detail, "request", {}) if detail is not None else {}
        model_id = str(
            request.get("model_id") or request.get("model_alias") or "model"
        )
        installed = next(
            (
                item.display_name for item in self._all_items
                if item.alias == model_id
            ),
            None,
        )
        if installed:
            return installed
        return next(
            (
                entry.display_name for entry in ADVERTISED_CATALOG
                if entry.id == model_id
            ),
            model_id,
        )

    def _observe_install_operation(self):
        operation_page = self.application.operation_query.list(
            scope="active", page_size=20
        )
        install_summary = next(
            (
                item for item in operation_page.items
                if item.kind in {
                    "MODEL_ACQUIRE", "MODEL_IMPORT", "MODEL_ACTIVATE"
                }
            ),
            None,
        )
        install_detail = (
            self.application.operation_query.show(install_summary.operation_id)
            if install_summary is not None
            else None
        )
        return install_summary, install_detail

    def refresh_progress(self) -> None:
        """Poll only durable install/start progress while the action lane is busy."""
        if self._disposed:
            return
        self.shell.request_observation(
            self._observe_install_operation,
            lambda result: self._render_observed_install_progress(*result),
        )

    def _render_observed_install_progress(self, summary: Any, detail: Any) -> None:
        if summary is None:
            if self._install_starting_name:
                self._show_install_progress(ModelInstallProgressView(
                    f"Starting installation: {self._install_starting_name}.",
                    "indeterminate",
                    0.0,
                ))
            elif self._install_terminal_message:
                self._show_install_progress(ModelInstallProgressView(
                    self._install_terminal_message,
                    "determinate",
                    100.0 if self._install_terminal_ok else 0.0,
                ))
            else:
                self._show_install_progress(ModelInstallProgressView(
                    "No model installation is currently running.",
                    "determinate",
                    0.0,
                ))
            return
        self._install_terminal_message = None
        self._show_install_progress(build_install_progress_view(
            summary,
            model_name=self._install_model_name(summary, detail),
            current_step=getattr(detail, "current_step", None),
        ))

    def _show_install_progress(self, view: ModelInstallProgressView) -> None:
        if self._disposed:
            return
        self.install_progress_text.set(view.message)
        if view.mode != self._install_progress_mode:
            self.install_progress.stop()
            self.install_progress.configure(mode=view.mode)
            self._install_progress_mode = view.mode
            if (
                view.mode == "indeterminate"
                and not bool(getattr(self.shell, "reduced_motion", False))
            ):
                self.install_progress.start(12)
        if view.mode == "determinate":
            self.install_progress.configure(value=view.value)

    def _begin_install_progress(self, model_name: str) -> None:
        self._install_starting_name = model_name
        self._install_terminal_message = None
        self._install_terminal_ok = False
        self._show_install_progress(ModelInstallProgressView(
            f"Starting installation: {model_name}.", "indeterminate", 0.0
        ))

    def _finish_install_progress(
        self,
        *,
        model_name: str,
        acquisition: Any,
        activation: Any = None,
        activation_expected: bool = False,
    ) -> None:
        self._install_starting_name = None
        installed = bool(acquisition and acquisition.ok)
        started = bool(activation and activation.ok)
        if installed and not activation_expected:
            message = f"Installed {model_name}. It is ready to start."
        elif installed and started:
            message = f"Installed and started {model_name}."
        elif installed:
            message = (
                f"Installed {model_name}, but starting it needs attention. "
                "Open Activity for details."
            )
        else:
            message = (
                f"Installation of {model_name} stopped safely in "
                f"{getattr(acquisition, 'status', 'UNKNOWN')}. "
                "Open Activity for details."
            )
        self._install_terminal_message = message
        self._install_terminal_ok = installed and (
            not activation_expected or started
        )
        self._render_observed_install_progress(None, None)

    def focus_primary(self) -> None:
        self.search_entry.focus_set()

    def observation_failed(self, _error: BaseException) -> None:
        self.detail_state.set("STALE · model library refresh failed")

    def _render_list(self) -> None:
        visible = filter_model_items(
            self._all_items, query=self.search_var.get(), category=self.filter_var.get()
        )
        self.result_count_var.set(
            f"{len(visible)} shown · {len(self._all_items)} total"
        )
        self._visible = {item.key: item for item in visible}
        if visible == self._rendered_visible:
            return
        self._rendered_visible = visible
        self.tree.delete(*self.tree.get_children())
        for item in visible:
            size = (
                f"{format_number(item.size_gib, decimals=1)} GiB"
                if item.size_gib is not None else "—"
            )
            self.tree.insert(
                "", "end", iid=item.key, text=item.display_name,
                values=(item.state, item.family, size, fit_label(item.fit_verdict)),
            )
        if self._selected_key not in self._visible and visible:
            self._selected_key = visible[0].key
        elif not visible:
            self._selected_key = None
        if self._selected_key is not None:
            self._highlight(self._selected_key)
        else:
            self._render_detail()

    def _select(self, _event=None) -> None:
        selected = self.tree.selection()
        self._selected_key = selected[0] if selected else None
        if self._selected_key is not None:
            self.tree.focus(self._selected_key)
        self._render_detail()

    def _highlight(self, key: str) -> None:
        """Keep the visible row, Tk focus item, and detail pane in sync."""
        if key not in self._visible:
            return
        self._selected_key = key
        self.tree.selection_set(key)
        self.tree.focus(key)
        self.tree.see(key)
        self._render_detail()

    def _move_highlight(self, offset: int) -> str:
        rows = tuple(self.tree.get_children(""))
        if not rows:
            return "break"
        selected = tuple(self.tree.selection())
        current = next((key for key in selected if key in rows), None)
        if current is None:
            focused = self.tree.focus()
            current = focused if focused in rows else None
        if current is None and self._selected_key in rows:
            current = self._selected_key
        if current is None:
            target_index = 0
        else:
            target_index = max(0, min(len(rows) - 1, rows.index(current) + offset))
        self._highlight(rows[target_index])
        return "break"

    def _highlight_previous(self, _event=None) -> str:
        return self._move_highlight(-1)

    def _highlight_next(self, _event=None) -> str:
        return self._move_highlight(1)

    def _run_highlighted_action(self, _event=None) -> str:
        selected = tuple(self.tree.selection())
        key = selected[0] if selected else None
        if key is not None and key in self._visible:
            self._highlight(key)
            # Keyboard activation intentionally follows the same guarded
            # primary path as the visible button.  In particular, remote rows
            # remain install-and-start actions, busy rows open Activity, and a
            # disabled button stays inert.
            self._primary_action_button.invoke()
        return "break"

    def _selected(self) -> ModelItemView | None:
        return self._visible.get(self._selected_key or "")

    def _render_detail(self) -> None:
        item = self._selected()
        if self._detail_was_rendered and item == self._rendered_detail:
            return
        self._detail_was_rendered = True
        self._rendered_detail = item
        for button in (
            self._primary_action_button, self._secondary_action_button,
            self._apply_draft_button, self._remove_button,
        ):
            button.pack_forget()
        self._primary_action_code = None
        self._secondary_action_code = None
        if item is None:
            self.detail_title.set("No models match this view")
            self.detail_state.set("")
            self._set_detail_body("Change the search or filter.")
            return
        self.detail_title.set(item.display_name)
        recommendation = (
            f"{item.recommendation_label} · " if item.recommendation_label else "")
        self.detail_state.set(
            f"{recommendation}{friendly_state(item.state)} · {item.support_tier} support · "
            f"{fit_label(item.fit_verdict)}"
        )
        provenance = item.source_repo or "local managed source"
        identity = (
            "immutable artifact identity verified"
            if item.immutable_identity else
            "artifact identity resolves during installation"
        )
        recommendation_reasons = " · ".join(item.recommendation_reasons)
        model_size = (
            f"about {format_number(item.size_gib, decimals=1)} GiB"
            if item.size_gib is not None else "size not available"
        )
        storage = (
            f"{format_bytes(self._available_bytes)} free now"
            if self._available_bytes is not None else
            "free-space reading unavailable"
        )
        if (
            item.remote and self._available_bytes is not None
            and item.size_gib is not None
        ):
            required = int(item.size_gib * 1024**3) * 3 + (64 << 20)
            storage += f"; preflight requires about {format_bytes(required)} free (download, validation and publication)"
            remaining = self._available_bytes - int(item.size_gib * 1024**3)
            storage += (
                f"; about {format_bytes(remaining)} remains after the model file, "
                "before temporary validation space"
                if remaining >= 0 else
                f"; the model file alone needs about {format_bytes(-remaining)} more"
            )
        if item.memory_required_gib is None:
            memory_headroom = (
                "A numeric fast-memory headroom estimate is not available for this artifact."
            )
        elif item.memory_required_gib <= FAST_VRAM_GIB:
            memory_headroom = (
                f"Approx. {FAST_VRAM_GIB - item.memory_required_gib:.2f} GiB "
                "fast-memory headroom at this workload."
            )
        else:
            memory_headroom = (
                f"This workload exceeds fast memory by about "
                f"{item.memory_required_gib - FAST_VRAM_GIB:.2f} GiB; choose a smaller "
                "quality option, context, or user count."
            )
        install_time = model_install_time_guidance(
            item.size_gib, installed=not item.remote
        )
        self._set_detail_body(
            f"{item.description}\n\n"
            f"WORKLOAD\n{item.profile_summary}\n"
            f"Quality / size: {item.quant or 'not selected'} — {quantization_guidance(item.quant)}\n\n"
            f"MEMORY FIT\n{fit_label(item.fit_verdict)} · {item.fit_detail}\n{memory_headroom}\n"
            "Host RAM is separate from GPU fit. Optional Open WebUI allows up to 2 GiB RAM; the gateway and desktop also need host memory. Stop optional services if host memory is low. These limits are not measured usage.\n\n"
            f"DOWNLOAD & STORAGE\n{model_size} · {storage}. {install_time} "
            "Downloads resume from durable checkpoints; Activity provides safe stop, resume, and recovery.\n\n"
            f"LOCAL PERFORMANCE\n{item.measurement_summary}\n\n"
            f"TRUST\n{provenance} · {identity}\n"
            f"Why recommended: {recommendation_reasons or 'No recommendation claim'}\n"
            f"Removal: {'eligible for quarantine' if item.deletion_eligible else ', '.join(item.deletion_blockers) or 'not applicable'}"
        )
        self.quant_box.configure(values=item.available_quants)
        self.quant_var.set(item.quant or (item.available_quants[0] if item.available_quants else ""))
        self.quant_help_var.set(quantization_guidance(self.quant_var.get()))
        action = model_action(item)
        self._primary_action_code = action.code
        self._primary_action_button.configure(text=action.label)
        self._primary_action_button.pack(side="left")
        if action.secondary_code and action.secondary_label:
            self._secondary_action_code = action.secondary_code
            self._secondary_action_button.configure(text=action.secondary_label)
            self._secondary_action_button.pack(side="left", padx=5)
        if not item.remote and item.state not in {"QUARANTINED", "RECOVERY_REQUIRED"}:
            self._apply_draft_button.pack(side="left", padx=5)
        if not item.remote:
            self._remove_button.pack(side="right")

    def _set_detail_body(self, value: str) -> None:
        self.detail_body.set(value)
        self._detail_text.configure(state="normal")
        self._detail_text.delete("1.0", "end")
        self._detail_text.insert("1.0", value)
        self._detail_text.configure(state="disabled")

    def _run_primary_action(self) -> None:
        item = self._selected()
        if item is not None:
            # Recompute from the selected read model so a stale button code can
            # never bypass a newly-busy or recovery-required state.
            self._run_action(model_action(item).code)

    def _run_secondary_action(self) -> None:
        if self._secondary_action_code is not None:
            self._run_action(self._secondary_action_code)

    def _run_action(self, code: str) -> None:
        item = self._selected()
        if item is None:
            return
        if code == "activity":
            self.shell.navigate(Route.ACTIVITY)
            return
        if code == "fit":
            self.shell.notice_bar.show_notice(Notice(
                "warning", "This model cannot start with the selected workload",
                item.fit_detail or "Fit evidence is missing. Review the model details.",
                dismissible=False,
            ))
            return
        if code == "chat":
            self.shell.navigate(Route.CHAT)
            return
        if code == "checks":
            self.shell.navigate(Route.SYSTEM)
            return
        quant = self.quant_var.get()
        try:
            context = int(self.context_var.get())
            slots = int(self.slots_var.get())
        except (ValueError, tk.TclError):
            self.shell.notice_bar.show_notice(Notice(
                "error", "Activation draft is not valid",
                "Context must be 512–262144 and user slots must be 1–8.",
                dismissible=False,
            ))
            return
        result_box: dict[str, Any] = {}
        if code in {"install", "install-start"}:
            self._begin_install_progress(item.display_name)

        def action_fn() -> None:
            alias = item.alias
            if code in {"install", "install-start"}:
                outcome = self.application.model_acquisition.acquire_catalog(
                    str(item.catalog_id), quant, requested_by="gui"
                )
                result_box["acquisition"] = outcome
                self.shell.track_operation_id(outcome.operation_id)
                if not outcome.ok or code == "install":
                    return
                alias = str(item.catalog_id)
            if code in {"activate", "install-start"}:
                payload = {
                    "model_alias": alias,
                    "context_per_slot": context,
                    "parallel_slots": slots,
                    "requested_by": "gui",
                }
                activation = self.application.activation.activate(payload)
                result_box["activation"] = activation
                self.shell.track_operation_id(activation.operation_id)

        def done() -> None:
            outcome = result_box.get("activation") or result_box.get("acquisition")
            acquisition = result_box.get("acquisition")
            if acquisition is not None:
                self._finish_install_progress(
                    model_name=item.display_name,
                    acquisition=acquisition,
                    activation=result_box.get("activation")
                    if code == "install-start"
                    else None,
                    activation_expected=code == "install-start",
                )
            ok = bool(outcome and outcome.ok)
            self.shell.notice_bar.show_notice(Notice(
                "success" if ok else "error",
                "Model ready" if ok else "Model action needs attention",
                (
                    "The requested operation completed and durable state was refreshed."
                    if ok else
                    f"The operation ended in {getattr(outcome, 'status', 'UNKNOWN')}. Open Activity for details."
                ),
                dismissible=ok,
            ))
            if ok and code in {"activate", "install-start"}:
                self.shell.navigate(Route.CHAT)
                return
            self.refresh()

        self.shell._work(action_fn, done)

    def _import_local(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose a standard-layout GGUF", filetypes=(("GGUF models", "*.gguf"),)
        )
        if not path:
            return
        result_box: dict[str, Any] = {}
        self._begin_install_progress("local GGUF")

        def action_fn() -> None:
            outcome = self.application.model_acquisition.import_local(path, requested_by="gui")
            result_box["outcome"] = outcome
            self.shell.track_operation_id(outcome.operation_id)

        def done() -> None:
            outcome = result_box.get("outcome")
            self._finish_install_progress(
                model_name="local GGUF", acquisition=outcome
            )
            self.shell.notice_bar.show_notice(Notice(
                "success" if outcome and outcome.ok else "error",
                "Model imported" if outcome and outcome.ok else "Import needs attention",
                "The original file was not changed. The managed library has been refreshed.",
                dismissible=bool(outcome and outcome.ok),
            ))
            self.refresh()

        self.shell._work(action_fn, done)

    def _confirm_remove(self) -> None:
        item = self._selected()
        if item is None or item.alias is None:
            return
        plan = self.application.model_remove.dry_run(item.alias)
        impact = plan.get("impact") or {}
        byte_count = int(impact.get("bytes_to_quarantine") or 0)
        blockers = plan.get("blockers") or []
        consequence = (
            f"Remove alias {item.alias!r} and move {byte_count / 1024**3:.2f} GiB of managed data to quarantine."
            if not blockers else
            f"Removal is currently blocked: {', '.join(map(str, blockers))}."
        )
        confirmation = Confirmation(
            "Remove managed model", consequence,
            "Quarantined managed bytes remain available for bounded recovery; external source files are untouched.",
            "Remove", destructive=True,
        )
        if blockers:
            self.shell.notice_bar.show_notice(Notice(
                "warning", "Model cannot be removed", consequence, dismissible=False
            ))
            return
        self.shell.drawer.show_confirmation(
            confirmation, lambda: self._remove_confirmed(item.alias)
        )

    def _remove_confirmed(self, alias: str) -> None:
        result_box: dict[str, Any] = {}

        def action_fn() -> None:
            outcome = self.application.model_remove.remove(alias, requested_by="gui")
            result_box["outcome"] = outcome
            self.shell.track_operation_id(outcome.operation_id)

        def done() -> None:
            outcome = result_box.get("outcome")
            self.shell.notice_bar.show_notice(Notice(
                "success" if outcome and outcome.ok else "error",
                "Model removed safely" if outcome and outcome.ok else "Removal needs attention",
                "Managed data was quarantined; no external source was deleted."
                if outcome and outcome.ok else
                f"Removal ended in {getattr(outcome, 'status', 'UNKNOWN')}. Open Activity for details.",
                dismissible=bool(outcome and outcome.ok),
            ))
            self.refresh()

        self.shell._work(action_fn, done)

    def leave(self) -> None:
        return None

    def dispose(self) -> None:
        self._disposed = True
        self.install_progress.stop()
        self._all_items = ()
        self._visible = {}


__all__ = [
    "MAX_MODEL_ROWS", "MODEL_FILTERS", "MODEL_PRESENTATION_STATES", "ModelActionView",
    "ModelInstallProgressView", "ModelItemView", "ModelsPage",
    "build_install_progress_view", "build_model_items", "filter_model_items",
    "model_action",
]
