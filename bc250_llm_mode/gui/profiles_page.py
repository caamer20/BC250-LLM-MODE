"""One-window workload Profiles and Performance Coach page."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import tkinter as tk
from tkinter import ttk

from ..presentation import format_number, format_tokens
from .view_state import Confirmation, Notice
from .widgets import VerticalScrollFrame


MAX_VISIBLE_PROFILES = 37
MAX_VISIBLE_SUGGESTIONS = 3


@dataclass(frozen=True)
class ProfilesPageView:
    profiles: tuple[dict[str, Any], ...]
    preview: dict[str, Any] | None
    suggestions: tuple[dict[str, Any], ...]


def build_profiles_view(
    profiles,
    preview: Mapping[str, Any] | None,
    suggestions,
) -> ProfilesPageView:
    bounded_profiles = tuple(
        dict(item) for item in profiles[:MAX_VISIBLE_PROFILES]
        if isinstance(item, Mapping)
    )
    bounded_suggestions = tuple(
        dict(item) for item in suggestions[:MAX_VISIBLE_SUGGESTIONS]
        if isinstance(item, Mapping)
    )
    return ProfilesPageView(
        bounded_profiles,
        dict(preview) if isinstance(preview, Mapping) else None,
        bounded_suggestions,
    )


class ProfilesPage(ttk.Frame):
    def __init__(self, parent, shell, application) -> None:
        super().__init__(parent)
        self.shell = shell
        self.application = application
        self._disposed = False
        self._profiles: dict[str, dict[str, Any]] = {}
        self._preview: dict[str, Any] | None = None

        ttk.Label(
            self,
            text="Choose an outcome; the app resolves safe settings for this model.",
            font=("TkDefaultFont", 15, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            self,
            text=(
                "Every preview is read-only. Apply and calibration use durable "
                "operations with known-good restoration."
            ),
            wraplength=780,
        ).pack(anchor="w", pady=(2, 8))

        split = ttk.Frame(self)
        split.pack(fill="both", expand=True)
        left_scroll = VerticalScrollFrame(split)
        right_scroll = VerticalScrollFrame(split)
        left, right = left_scroll.inner, right_scroll.inner
        split.columnconfigure(0, weight=1)
        split.rowconfigure(0, weight=1)
        split.rowconfigure(1, weight=1)
        left_scroll.grid(row=0, column=0, sticky="nsew")
        right_scroll.grid(row=1, column=0, sticky="nsew")

        self._tree = ttk.Treeview(
            left,
            columns=("goal", "evidence"),
            show="tree headings",
            selectmode="extended",
            height=10,
        )
        self._tree.heading("#0", text="Profile")
        self._tree.heading("goal", text="Goal")
        self._tree.heading("evidence", text="Evidence")
        self._tree.column("#0", width=145)
        self._tree.column("goal", width=105)
        self._tree.column("evidence", width=105)
        self._tree.pack(fill="both", expand=True)
        self._tree.bind("<<TreeviewSelect>>", lambda _event: self.refresh())
        row = ttk.Frame(left)
        row.pack(fill="x", pady=(5, 0))
        ttk.Button(row, text="Compare selected", command=self._compare).pack(side="left")
        ttk.Button(row, text="Delete custom", command=self._delete).pack(side="left", padx=4)
        ttk.Button(row, text="Load custom", command=self._load_custom).pack(side="left")

        custom = ttk.LabelFrame(left, text="New custom profile", padding=7)
        custom.pack(fill="x", pady=(7, 0))
        self._profile_name = tk.StringVar(value="My workload")
        self._context = tk.IntVar(value=8192)
        self._slots = tk.IntVar(value=1)
        self._kv = tk.StringVar(value="q8_0")
        self._batch = tk.IntVar(value=1024)
        self._ubatch = tk.IntVar(value=256)
        self._flash = tk.StringVar(value="auto")
        self._preset = tk.StringVar(value="balanced")
        self._thermal = tk.StringVar(value="standard")
        self._idle = tk.StringVar(value="KEEP_LOADED")
        self._stop_after = tk.IntVar(value=30)
        for label, variable, widget, options in (
            ("Name", self._profile_name, "entry", ()),
            ("Context per user", self._context, "spin", (512, 262144, 512)),
            ("Concurrent user slots", self._slots, "spin", (1, 8, 1)),
            ("KV cache", self._kv, "choice", ("q8_0", "q4_0")),
            ("Batch", self._batch, "spin", (128, 2048, 128)),
            ("Micro-batch", self._ubatch, "spin", (64, 512, 64)),
            ("Flash attention", self._flash, "choice", ("auto", "on", "off")),
            ("Tuning goal", self._preset, "choice", ("custom", "cool-quiet", "balanced", "maximum")),
            ("Thermal policy", self._thermal, "choice", ("standard", "cool", "throughput-guarded")),
            ("Idle behavior", self._idle, "choice", ("KEEP_LOADED", "STOP_AFTER", "STOP_ON_DESKTOP")),
            ("Stop after minutes", self._stop_after, "spin", (5, 240, 5)),
        ):
            line = ttk.Frame(custom)
            line.pack(fill="x", pady=2)
            ttk.Label(line, text=label, width=17).pack(side="left")
            if widget == "choice":
                control = ttk.Combobox(
                    line, textvariable=variable, values=options,
                    state="readonly", width=18,
                )
            elif widget == "spin":
                control = ttk.Spinbox(
                    line, textvariable=variable, from_=options[0],
                    to=options[1], increment=options[2], width=18,
                )
            else:
                control = ttk.Entry(line, textvariable=variable, width=20)
            control.pack(side="left", fill="x", expand=True)
        editor_actions = ttk.Frame(custom)
        editor_actions.pack(fill="x", pady=(4, 0))
        ttk.Button(editor_actions, text="Create", command=self._create).pack(side="left")
        ttk.Button(
            editor_actions, text="Save selected", command=self._edit
        ).pack(side="left", padx=4)

        summary = ttk.LabelFrame(right, text="Resolved preview", padding=8)
        summary.pack(fill="x")
        self._headline = tk.StringVar(value="Select one profile")
        self._detail = tk.StringVar(value="")
        self._fit = tk.StringVar(value="")
        self._evidence = tk.StringVar(value="")
        ttk.Label(summary, textvariable=self._headline, font=("TkDefaultFont", 13, "bold")).pack(anchor="w")
        ttk.Label(summary, textvariable=self._detail, wraplength=480).pack(anchor="w", fill="x")
        ttk.Label(summary, textvariable=self._fit).pack(anchor="w", pady=(4, 0))
        ttk.Label(summary, textvariable=self._evidence).pack(anchor="w")
        actions = ttk.Frame(summary)
        actions.pack(fill="x", pady=(6, 0))
        self._apply_button = ttk.Button(actions, text="Apply profile", command=self._confirm_apply)
        self._apply_button.pack(side="left")
        self._calibrate_button = ttk.Button(actions, text="Calibrate", command=self._confirm_calibrate)
        self._calibrate_button.pack(side="left", padx=5)
        ttk.Button(actions, text="Technical details", command=self._details).pack(side="left")

        coach = ttk.LabelFrame(right, text="Performance Coach", padding=8)
        coach.pack(fill="both", expand=True, pady=(7, 0))
        self._coach = coach
        self._render_coach(())

        self.refresh()

    def mount(self, parent=None):
        del parent
        self.pack(fill="both", expand=True)
        return self

    def enter(self, route_context=None) -> None:
        del route_context
        self.refresh()

    def _selected_ids(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self._tree.selection()[:3])

    def _selected_one(self) -> str | None:
        selected = self._selected_ids()
        return selected[0] if selected else None

    def refresh(self, payload=None) -> None:
        if self._disposed:
            return
        if payload is not None:
            self._apply(payload)
            return
        selected = self._selected_one() or "builtin-interactive"

        def observe():
            profiles = self.application.workload_profiles.list()
            valid_ids = {item["profile_id"] for item in profiles}
            profile_id = selected if selected in valid_ids else "builtin-interactive"
            preview = self.application.workload_profiles.preview(profile_id)
            suggestions = self.application.performance_coach.suggestions(
                profile_id=profile_id
            )
            return profiles, preview, suggestions

        self.shell.request_observation(observe, self._apply)

    def _apply(self, payload) -> None:
        if self._disposed:
            return
        view = build_profiles_view(*payload)
        previous = self._selected_ids()
        self._profiles = {item["profile_id"]: item for item in view.profiles}
        for item in self._tree.get_children():
            self._tree.delete(item)
        for item in view.profiles:
            self._tree.insert(
                "", "end", iid=item["profile_id"], text=item["name"],
                values=(item["purpose"], item["evidence_class"]),
            )
        for item in previous:
            if item in self._profiles:
                self._tree.selection_add(item)
        self._preview = view.preview
        if view.preview:
            item = view.preview
            self._headline.set(str(item["profile"]["name"]))
            self._detail.set(
                f"{item['model_alias']} · {format_tokens(item['context_per_slot'])} "
                f"per user × {format_number(item['slots'])} concurrent slot(s)"
            )
            self._fit.set(
                f"{item['fit_verdict']} · "
                f"{format_number(item['required_gib'], decimals=2)} of 12 GiB · "
                f"{format_number(item['headroom_gib'], decimals=2)} GiB headroom"
            )
            self._evidence.set(
                f"{item['evidence_class']} · thermal {item['thermal_readiness']} · "
                f"rollback {'ready' if item['rollback_available'] else 'not yet recorded'}"
            )
            state = "normal" if item["ready_to_apply"] else "disabled"
            self._apply_button.configure(state=state)
            self._calibrate_button.configure(state=state)
        self._render_coach(view.suggestions)

    def _render_coach(self, suggestions) -> None:
        for child in self._coach.winfo_children():
            child.destroy()
        if not suggestions:
            ttk.Label(
                self._coach,
                text="No evidence-bound change is suggested for this preview.",
                wraplength=470,
            ).pack(anchor="w")
            return
        for item in suggestions[:MAX_VISIBLE_SUGGESTIONS]:
            card = ttk.Frame(self._coach, padding=(0, 3))
            card.pack(fill="x")
            ttk.Label(
                card, text=str(item["code"]).replace("_", " ").title(),
                font=("TkDefaultFont", 10, "bold"),
            ).pack(anchor="w")
            ttk.Label(card, text=item["benefit"], wraplength=470).pack(anchor="w")
            ttk.Label(
                card,
                text=f"Tradeoff: {item['tradeoff']} · {item['confidence']}",
                wraplength=470,
            ).pack(anchor="w")

    def _run(self, action, title: str) -> None:
        box: dict[str, Any] = {}

        def work() -> None:
            box["result"] = action()

        def done() -> None:
            result = box.get("result")
            operation_id = getattr(result, "operation_id", None)
            self.shell.track_operation_id(operation_id)
            ok = bool(getattr(result, "ok", True))
            self.shell.notice_bar.show_notice(Notice(
                "success" if ok else "error",
                title if ok else f"{title} needs attention",
                (
                    "The durable result is available in Activity. No winner was auto-applied."
                    if ok and "Calibration" in title
                    else "The profile view was refreshed."
                    if ok
                    else f"The durable operation ended in {getattr(result, 'status', 'UNKNOWN')}. Open Activity for details."
                ),
                dismissible=ok,
            ))
            self.refresh()

        self.shell._work(work, done)

    def _confirm_apply(self) -> None:
        preview = self._preview
        if not preview or not preview.get("ready_to_apply"):
            return
        tight = bool(preview["tight_confirmation_required"])
        self.shell.drawer.show_confirmation(Confirmation(
            "Apply this workload profile?",
            "The one model server will restart with this exact preview.",
            "A failed verification restores the previous known-good runtime.",
            "Apply profile",
            typed_phrase="TIGHT" if tight else None,
        ), lambda: self._run(
            lambda: self.application.workload_profile_commands.apply(
                preview["profile_id"],
                model_alias=preview["model_alias"],
                expected_profile_revision=int(preview["profile_revision"]),
                preview_fingerprint=str(preview["profile_fingerprint"]),
                accept_tight=tight,
                requested_by="gui",
            ),
            "Profile apply completed",
        ))

    def _confirm_calibrate(self) -> None:
        preview = self._preview
        if not preview or not preview.get("ready_to_apply"):
            return
        tight = bool(preview["tight_confirmation_required"])
        self.shell.drawer.show_confirmation(Confirmation(
            "Run local calibration?",
            "The GPU will run up to three fixed-prompt trials and may become hot.",
            "Each trial restores the exact baseline; the winner is only proposed.",
            "Start calibration",
            typed_phrase="TIGHT" if tight else None,
        ), lambda: self._run(
            lambda: self.application.calibration.calibrate(
                profile_id=preview["profile_id"],
                expected_profile_revision=int(preview["profile_revision"]),
                model_alias=str(preview["model_alias"]),
                accept_tight=tight,
                requested_by="gui",
            ),
            "Calibration completed",
        ))

    def _compare(self) -> None:
        selected = self._selected_ids()
        if not selected:
            return

        def show(previews) -> None:
            lines = [
                f"{item['profile']['name']}: {item['fit_verdict']} · "
                f"{format_tokens(item['context_per_slot'])} × "
                f"{format_number(item['slots'])} · "
                f"{format_number(item['required_gib'], decimals=2)} GiB · "
                f"{item['evidence_class']}"
                for item in previews
            ]
            self.shell.drawer.show_details("Profile comparison", "\n".join(lines))

        self.shell.request_observation(
            lambda: self.application.workload_profiles.compare(selected), show
        )

    def _editor_values(self) -> dict[str, Any]:
        idle_policy = str(self._idle.get())
        return {
            "name": str(self._profile_name.get()),
            "context_per_slot": int(self._context.get()),
            "slots": int(self._slots.get()),
            "kv_cache_type": str(self._kv.get()),
            "batch_size": int(self._batch.get()),
            "ubatch_size": int(self._ubatch.get()),
            "flash_attention": str(self._flash.get()),
            "optimization_preset_id": str(self._preset.get()),
            "thermal_policy": str(self._thermal.get()),
            "idle_policy": idle_policy,
            "stop_after_minutes": (
                int(self._stop_after.get()) if idle_policy == "STOP_AFTER" else None
            ),
        }

    def _create(self) -> None:
        self._run(
            lambda: self.application.workload_profile_commands.create(
                **self._editor_values(),
            ),
            "Custom profile created",
        )

    def _load_custom(self) -> None:
        row = self._profiles.get(self._selected_one() or "")
        if not row or row.get("owner") != "user":
            return
        self._profile_name.set(row["name"])
        self._context.set(int(row["context_per_slot"]))
        self._slots.set(int(row["slots"]))
        self._kv.set(row["kv_cache_type"])
        self._batch.set(int(row["batch_size"]))
        self._ubatch.set(int(row["ubatch_size"]))
        self._flash.set(row["flash_attention"])
        self._preset.set(row["optimization_preset_id"])
        self._thermal.set(row["thermal_policy"])
        self._idle.set(row["idle_policy"])
        self._stop_after.set(int(row.get("stop_after_minutes") or 30))

    def _edit(self) -> None:
        profile_id = self._selected_one()
        row = self._profiles.get(profile_id or "")
        if not row or row.get("owner") != "user":
            return
        self._run(
            lambda: self.application.workload_profile_commands.edit(
                profile_id,
                expected_revision=int(row["revision"]),
                **self._editor_values(),
            ),
            "Custom profile saved",
        )

    def _delete(self) -> None:
        profile_id = self._selected_one()
        row = self._profiles.get(profile_id or "")
        if not row or row.get("owner") != "user":
            return
        self.shell.drawer.show_confirmation(Confirmation(
            "Delete this custom profile?",
            "It will no longer be available for a new preview or apply.",
            "Running and historical profile identity remains intact.",
            "Delete profile",
            destructive=True,
        ), lambda: self._run(
            lambda: self.application.workload_profile_commands.delete(
                profile_id, expected_revision=int(row["revision"])
            ),
            "Custom profile deleted",
        ))

    def _details(self) -> None:
        if self._preview:
            import json

            self.shell.drawer.show_details(
                "Exact profile preview", json.dumps(self._preview, indent=2)
            )

    def focus_primary(self) -> None:
        self._tree.focus_set()

    def observation_failed(self, _error: BaseException) -> None:
        self._detail.set("Profile preview could not be refreshed; no ready state was inferred.")

    def dispose(self) -> None:
        self._disposed = True


__all__ = ["ProfilesPage", "ProfilesPageView", "build_profiles_view"]
