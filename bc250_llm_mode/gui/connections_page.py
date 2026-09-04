"""Native one-window Connection Assistant over the EXP-2 services."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping

import tkinter as tk
from tkinter import ttk

from ..client_compatibility import (
    CLIENT_COMPATIBILITY_SCHEMA_VERSION,
    capability_display_rows,
)
from ..connection_setup import CLIENT_CARDS, instructions_for
from ..presentation import format_timestamp
from ..problem_details import UNKNOWN_PROBLEM, problem_detail
from ..progress_projection import format_elapsed, project_operation_progress
from ..ux_guidance import (
    ConnectionDoctorView,
    connection_doctor,
    http_status_guidance,
)
from .routes import Route
from .view_state import Confirmation, Notice
from .widgets import VerticalScrollFrame


MAX_VISIBLE_CLIENTS = 32
SECRET_REVEAL_SECONDS = 30.0


@dataclass(frozen=True)
class ConnectionPageView:
    headline: str
    detail: str
    ready: bool
    model_alias: str | None
    endpoints: tuple[tuple[str, str], ...]
    checks: tuple[tuple[str, bool], ...]
    clients: tuple[dict[str, Any], ...]
    doctor: ConnectionDoctorView


def connection_action_notice(result: Any, success_title: str) -> Notice:
    """Translate a command outcome without ever painting failure as success."""
    status = getattr(result, "status", None)
    if status is None or getattr(result, "ok", True):
        return Notice(
            "success", success_title, "Connection state was refreshed.")
    detail = getattr(result, "detail", {})
    if not isinstance(detail, Mapping):
        detail = {}
    code = str(detail.get("reason_code") or status)
    status_code = detail.get("status_code") or detail.get("http_status")
    if isinstance(status_code, int):
        guidance = http_status_guidance(status_code, problem_code=code)
        title = guidance.title
        message = f"{guidance.explanation} {guidance.action}"
    else:
        problem = problem_detail(code)
        title = (
            problem.title if problem != UNKNOWN_PROBLEM
            else "Connection check stopped safely"
        )
        action = str(detail.get("safe_action") or problem.user_message)
        message = (
            f"{problem.user_message} {action}"
            if action != problem.user_message else problem.user_message
        )
    level = "warning" if status in {"BLOCKED", "BUSY", "CANCELLED"} else "error"
    return Notice(
        level, title, message,
        action_label="Review Connections", action_route=Route.CONNECTIONS.value,
        details=f"Stable connection reason: {code}",
    )


def build_connections_view(
    snapshot: Mapping[str, Any], clients: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...]
) -> ConnectionPageView:
    readiness = (
        snapshot.get("readiness")
        if isinstance(snapshot.get("readiness"), Mapping) else {}
    )
    ready = bool(
        readiness.get("remote_client_ready")
        if readiness else snapshot.get("ready")
    )
    model = snapshot.get("model") if isinstance(snapshot.get("model"), Mapping) else {}
    urls = snapshot.get("urls") if isinstance(snapshot.get("urls"), Mapping) else {}
    checks_source = snapshot.get("checks")
    checks = tuple(
        (str(item.get("id") or "unknown"), bool(item.get("passed")))
        for item in (checks_source if isinstance(checks_source, (list, tuple)) else ())[:12]
        if isinstance(item, Mapping)
    )
    endpoints = tuple(
        (label, value)
        for label, key in (
            ("Open WebUI", "webui_url"),
            ("OpenAI Base URL", "base_url"),
            ("Models", "models_url"),
            ("Chat Completions", "chat_completions_url"),
        )
        if isinstance((value := urls.get(key)), str) and value
    )
    rows = []
    for item in clients[:MAX_VISIBLE_CLIENTS]:
        if not isinstance(item, Mapping):
            continue
        rows.append({
            "client_id": str(item.get("client_id") or ""),
            "label": str(item.get("label") or "Unnamed client")[:80],
            "client_kind": str(item.get("client_kind") or "unknown"),
            "revoked": bool(item.get("revoked_at")),
            "revision": int(item.get("revision") or 0),
            "last_used_at": item.get("last_used_at"),
            "last_endpoint_class": item.get("last_endpoint_class"),
        })
    problem_code = str(readiness.get("primary_problem_code") or "")
    doctor = connection_doctor(snapshot)
    return ConnectionPageView(
        headline="Ready for another device" if ready else "Finish connection checks",
        detail=(
            "Use the exact values below. The raw model port and /api are not supported."
            if ready else (
                problem_detail(problem_code).user_message
                if problem_code
                else str(snapshot.get("next_action") or
                         "Start the model, add a client, then run the guided test.")
            )),
        ready=ready,
        model_alias=(
            str(model.get("public_alias")) if model.get("public_alias") else None),
        endpoints=endpoints,
        checks=checks,
        clients=tuple(rows),
        doctor=doctor,
    )


class ConnectionsPage(ttk.Frame):
    """Exact endpoints, bounded clients, tests, and one-time secret reveal."""

    def __init__(self, parent, shell, application) -> None:
        super().__init__(parent)
        self.shell = shell
        self.application = application
        self._disposed = False
        self._view: ConnectionPageView | None = None
        self._snapshot: dict[str, Any] = {}
        self._clients: dict[str, dict[str, Any]] = {}
        self._secret_deadline: float | None = None
        self._copied_secret = False
        self._legacy: dict[str, Any] = {}

        self._headline = tk.StringVar(value="Loading connection status…")
        self._detail = tk.StringVar(value="")
        ttk.Label(
            self, textvariable=self._headline,
            font=("TkDefaultFont", 18, "bold")).pack(anchor="w")
        ttk.Label(
            self, textvariable=self._detail, wraplength=760,
        ).pack(anchor="w", fill="x", pady=(2, 8))

        doctor_box = ttk.LabelFrame(self, text="Connection Doctor", padding=7)
        doctor_box.pack(fill="x", pady=(0, 7))
        self._doctor_summary = tk.StringVar(
            value="Checking the model, private API, client key, and private network…"
        )
        ttk.Label(
            doctor_box, textvariable=self._doctor_summary,
            wraplength=700, justify="left",
        ).pack(side="left", fill="x", expand=True)
        self._doctor_button = ttk.Button(
            doctor_box, text="Refresh diagnosis", command=self._doctor_action,
        )
        self._doctor_button.pack(side="right", padx=(8, 0))
        self._doctor_route: str | None = None

        split = ttk.Panedwindow(self, orient="horizontal")
        split.pack(fill="both", expand=True)
        left = ttk.Frame(split, padding=(0, 0, 6, 0))
        right = ttk.Frame(split, padding=(6, 0, 0, 0))
        split.add(left, weight=3)
        split.add(right, weight=2)

        endpoints = ttk.LabelFrame(left, text="Exact connection values", padding=7)
        endpoints.pack(fill="x")
        self._endpoint_frame = endpoints
        self._model_value = tk.StringVar(value="Model: waiting for a live alias")
        ttk.Label(endpoints, textvariable=self._model_value).pack(anchor="w", pady=(3, 0))
        self._endpoint_rows = ttk.Frame(endpoints)
        self._endpoint_rows.pack(fill="x")

        compatibility = ttk.LabelFrame(
            left,
            text=f"Supported model API · contract v{CLIENT_COMPATIBILITY_SCHEMA_VERSION}",
            padding=7,
        )
        compatibility.pack(fill="x", pady=(7, 0))
        ttk.Label(
            compatibility,
            text=(
                "Enter the displayed Base URL ending once in /v1. Open WebUI "
                "/api routes are browser-only; apps requiring embeddings, "
                "legacy completions, or Responses are not compatible yet."
            ),
            wraplength=500,
        ).pack(anchor="w", fill="x", pady=(0, 3))
        for endpoint, status, summary in capability_display_rows():
            ttk.Label(
                compatibility,
                text=f"{status.upper()} · {endpoint} — {summary}",
                wraplength=500,
            ).pack(anchor="w", fill="x")

        checks = ttk.LabelFrame(left, text="Guided checks", padding=7)
        checks.pack(fill="x", pady=(7, 0))
        self._checks_frame = checks

        clients_box = ttk.LabelFrame(left, text="Client credentials", padding=7)
        clients_box.pack(fill="both", expand=True, pady=(7, 0))
        ttk.Label(
            clients_box,
            text="Client access table · select a row to repeat its state below",
        ).pack(anchor="w", fill="x", pady=(0, 3))
        client_table = ttk.Frame(clients_box)
        client_table.pack(fill="both", expand=True)
        self._tree = ttk.Treeview(
            client_table, columns=("type", "state", "used"),
            show="tree headings", height=7, selectmode="browse")
        self._tree.heading("#0", text="Label")
        self._tree.heading("type", text="Type")
        self._tree.heading("state", text="State")
        self._tree.heading("used", text="Last use")
        self._tree.column("#0", width=170)
        self._tree.column("type", width=90)
        self._tree.column("state", width=75)
        self._tree.column("used", width=140)
        self._tree.grid(row=0, column=0, sticky="nsew")
        client_vertical = ttk.Scrollbar(
            client_table, orient="vertical", command=self._tree.yview
        )
        client_vertical.grid(row=0, column=1, sticky="ns")
        client_horizontal = ttk.Scrollbar(
            client_table, orient="horizontal", command=self._tree.xview
        )
        client_horizontal.grid(row=1, column=0, sticky="ew")
        self._tree.configure(
            yscrollcommand=client_vertical.set,
            xscrollcommand=client_horizontal.set,
        )
        client_table.rowconfigure(0, weight=1)
        client_table.columnconfigure(0, weight=1)
        self._tree.bind(
            "<<TreeviewSelect>>", lambda _event: self._client_selected()
        )
        self._client_detail = tk.StringVar(value="Select a client for details.")
        ttk.Label(
            clients_box, textvariable=self._client_detail, wraplength=520,
        ).pack(anchor="w", fill="x", pady=(4, 0))
        actions = ttk.Frame(clients_box)
        actions.pack(fill="x", pady=(5, 0))
        ttk.Button(actions, text="Rotate selected", command=self._rotate).pack(side="left")
        ttk.Button(actions, text="Revoke selected", command=self._confirm_revoke).pack(side="left", padx=4)
        ttk.Button(actions, text="Test selected", command=self._test).pack(side="left")

        self._mode_tabs = ttk.Notebook(right)
        self._mode_tabs.pack(fill="both", expand=True)
        self._connect_tab = ttk.Frame(self._mode_tabs, padding=6)
        self._manage_tab = ttk.Frame(self._mode_tabs, padding=6)
        self._mode_tabs.add(self._connect_tab, text="Connect a device")
        self._mode_tabs.add(self._manage_tab, text="Manage access")
        connect_scroll = VerticalScrollFrame(self._connect_tab)
        connect_scroll.pack(fill="both", expand=True)
        connect_body = connect_scroll.inner
        manage_scroll = VerticalScrollFrame(self._manage_tab)
        manage_scroll.pack(fill="both", expand=True)
        manage_body = manage_scroll.inner

        guided = ttk.LabelFrame(connect_body, text="What do you want to connect?", padding=7)
        guided.pack(fill="x")
        self._setup_progress = tk.StringVar(
            value="No guided connection setup is currently running."
        )
        ttk.Label(
            guided, textvariable=self._setup_progress, wraplength=300,
        ).pack(anchor="w", fill="x", pady=(0, 5))
        ttk.Button(
            guided, text="View connection activity",
            command=lambda: self.shell.navigate(Route.ACTIVITY),
        ).pack(anchor="w", pady=(0, 5))
        self._guided_primary = None
        for title, intent in (
            ("Open WebUI on this BC250", "OPENWEBUI"),
            ("Phone or tablet app", "PHONE_TABLET"),
            ("Desktop OpenAI-compatible app", "DESKTOP_APP"),
            ("Developer or curl client", "DEVELOPER"),
        ):
            button = ttk.Button(
                guided, text=title,
                command=lambda selected=intent: self._guided_setup(selected),
            )
            button.pack(fill="x", pady=2)
            if self._guided_primary is None:
                self._guided_primary = button
        ttk.Label(
            guided,
            text=("The assistant starts only this boot, keeps the raw model port "
                  "private, and verifies blocked plus authorized requests."),
            wraplength=300,
        ).pack(anchor="w", fill="x", pady=(4, 0))

        troubleshooting = ttk.LabelFrame(
            connect_body, text="If another app reports an error", padding=7
        )
        troubleshooting.pack(fill="x", pady=(7, 0))
        ttk.Label(
            troubleshooting,
            text=(
                "401 · replace the API key.  403 · check key permissions and the /v1 Base URL.  "
                "502 · start and verify the selected model. Run the selected client's test after each fix."
            ),
            wraplength=300, justify="left",
        ).pack(anchor="w", fill="x")

        add = ttk.LabelFrame(manage_body, text="Advanced: create a client only", padding=7)
        add.pack(fill="x")
        self._label = tk.StringVar(value="")
        self._kind = tk.StringVar(value="pocketpal")
        ttk.Label(add, text="Name (for example, Cameron's phone)").pack(anchor="w")
        self._label_entry = ttk.Entry(add, textvariable=self._label)
        self._label_entry.pack(fill="x", pady=(2, 5))
        ttk.Label(add, text="Client type").pack(anchor="w")
        ttk.Combobox(
            add, textvariable=self._kind, state="readonly",
            values=("pocketpal", "openwebui", "openai", "curl", "sse"),
        ).pack(fill="x", pady=(2, 6))
        ttk.Button(add, text="Create client", command=self._add).pack(anchor="w")

        secret = ttk.LabelFrame(connect_body, text="One-time API key", padding=7)
        secret.pack(fill="x", pady=(7, 0))
        self._secret = tk.StringVar(value="No new key is being shown.")
        ttk.Entry(secret, textvariable=self._secret, state="readonly").pack(fill="x")
        self._secret_status = tk.StringVar(
            value="Existing keys cannot be revealed; rotate one to get a new key.")
        ttk.Label(secret, textvariable=self._secret_status, wraplength=300).pack(
            anchor="w", fill="x", pady=(3, 5))
        ttk.Button(secret, text="Copy shown key", command=self._copy_secret).pack(anchor="w")

        guides = ttk.LabelFrame(connect_body, text="Client instructions", padding=7)
        guides.pack(fill="x", pady=(7, 0))
        self._guide_kind = tk.StringVar(value="pocketpal")
        ttk.Combobox(
            guides, textvariable=self._guide_kind, state="readonly",
            values=tuple(card.card_id for card in CLIENT_CARDS),
        ).pack(fill="x")
        ttk.Button(
            guides, text="Show exact settings", command=self._show_instructions,
        ).pack(anchor="w", pady=(5, 0))
        ttk.Button(
            guides, text="Copy safe settings (no key)",
            command=self._copy_safe_settings,
        ).pack(anchor="w", pady=(4, 0))

        danger = ttk.LabelFrame(manage_body, text="Emergency control", padding=7)
        danger.pack(fill="x", pady=(7, 0))
        ttk.Label(
            danger,
            text="Disable every remote API credential even if the model server is down.",
            wraplength=300,
        ).pack(anchor="w")
        ttk.Button(
            danger, text="Disable all remote API access",
            command=self._confirm_disable_all,
        ).pack(anchor="w", pady=(5, 0))

        migration = ttk.LabelFrame(manage_body, text="Legacy shared key", padding=7)
        migration.pack(fill="x", pady=(7, 0))
        self._legacy_status = tk.StringVar(
            value="Checking whether separate replacement keys are ready…")
        ttk.Label(
            migration, textvariable=self._legacy_status, wraplength=300,
        ).pack(anchor="w", fill="x")
        ttk.Button(
            migration, text="Retire replaced shared key",
            command=self._confirm_retire_legacy,
        ).pack(anchor="w", pady=(5, 0))

        self._apply(({}, [], {}, None, None))
        self.refresh()

    def mount(self, parent=None):
        del parent
        self.pack(fill="both", expand=True)
        return self

    def enter(self, route_context=None) -> None:
        del route_context
        self.refresh()

    def refresh(self, payload=None) -> None:
        if self._disposed:
            return
        self._expire_secret()
        if payload is not None:
            self._apply(payload)
            return
        self.shell.request_observation(
            lambda: (
                self.application.connections.snapshot().to_dict(),
                self.application.connection_credentials.list_clients(),
                self.application.integration_setup.legacy_status(),
                *self._observe_setup_operation(),
            ),
            self._apply,
        )

    def _apply(self, payload) -> None:
        if self._disposed:
            return
        snapshot, clients, *extra = payload
        self._legacy = dict(extra[0]) if extra and isinstance(extra[0], Mapping) else {}
        setup_summary = extra[1] if len(extra) > 1 else None
        setup_detail = extra[2] if len(extra) > 2 else None
        self._render_setup_progress(setup_summary, setup_detail)
        self._snapshot = dict(snapshot)
        view = build_connections_view(snapshot, clients)
        previous_view = self._view
        self._view = view
        if view == previous_view:
            return
        self._clients = {row["client_id"]: row for row in view.clients}
        self._headline.set(view.headline)
        self._detail.set(view.detail)
        doctor = view.doctor
        count = (
            f"{doctor.passed_count} of {doctor.total_count} checks passed. "
            if doctor.total_count else ""
        )
        self._doctor_summary.set(
            f"{doctor.headline}. {count}{doctor.explanation}"
        )
        self._doctor_route = doctor.next_action_route
        self._doctor_button.configure(
            text=doctor.next_action_label or "Refresh diagnosis"
        )
        self._model_value.set(
            f"Model: {view.model_alias}" if view.model_alias
            else "Model: no safe live alias observed")
        for child in self._endpoint_rows.winfo_children():
            child.destroy()
        # Recreate a compact endpoint grid; values are read-only and copied
        # only by explicit user action.
        for row, (label, value) in enumerate(view.endpoints):
            line = ttk.Frame(self._endpoint_rows)
            line.pack(fill="x", pady=2)
            ttk.Label(line, text=f"{label}:", width=18).pack(side="left")
            var = tk.StringVar(value=value)
            ttk.Entry(line, textvariable=var, state="readonly").pack(
                side="left", fill="x", expand=True)
            ttk.Button(
                line, text="Copy",
                command=lambda exact=value: self._copy_value(exact),
            ).pack(side="right", padx=(4, 0))
        for child in self._checks_frame.winfo_children():
            child.destroy()
        doctor_steps = {step.check_id: step for step in view.doctor.steps}
        for check_id, passed in view.checks:
            step = doctor_steps.get(check_id)
            ttk.Label(
                self._checks_frame,
                text=(
                    f"{'Ready' if passed else 'Needs action'} · "
                    f"{step.label if step is not None else check_id}"
                ),
            ).pack(anchor="w")
        for item in self._tree.get_children():
            self._tree.delete(item)
        for row in view.clients:
            used = (
                format_timestamp(str(row["last_used_at"]))
                if row.get("last_used_at") else "Never"
            )
            self._tree.insert(
                "", "end", iid=row["client_id"], text=row["label"],
                values=(row["client_kind"],
                        "Revoked" if row["revoked"] else "Active", used))
        if view.clients:
            self._tree.selection_set(view.clients[0]["client_id"])
        self._client_selected()
        if not self._legacy.get("legacy_present"):
            self._legacy_status.set("No active legacy shared key remains.")
        elif self._legacy.get("revoke_recommended"):
            self._legacy_status.set(
                "Separate Open WebUI and external-app keys are verified. "
                "The exposed legacy shared key can now be retired.")
        else:
            self._legacy_status.set(
                "Keep the legacy key for now. Verify separate Open WebUI and "
                "external-app replacements first.")

    def _client_selected(self) -> None:
        selected = self._selected()
        if selected is None:
            self._client_detail.set("Select a client for details.")
            return
        last_use = (
            format_timestamp(str(selected["last_used_at"]))
            if selected.get("last_used_at") else "never"
        )
        state = "revoked" if selected["revoked"] else "active"
        endpoint = selected.get("last_endpoint_class") or "no endpoint recorded"
        self._client_detail.set(
            f"{selected['label']}: {selected['client_kind']} · {state} · "
            f"last used {last_use} · {endpoint}."
        )

    def _selected(self) -> dict[str, Any] | None:
        selection = self._tree.selection()
        key = selection[0] if selection else self._tree.focus()
        return self._clients.get(str(key))

    def _run(self, action, success_title: str, *, reveal: bool = False) -> None:
        box: dict[str, Any] = {}

        def work() -> None:
            box["result"] = action()

        def done() -> None:
            result = box.get("result")
            if reveal and getattr(result, "secret", None):
                self._show_secret(result.secret)
            self.shell.notice_bar.show_notice(
                connection_action_notice(result, success_title))
            self.refresh()

        self.shell._work(work, done)

    def _observe_setup_operation(self):
        page = self.application.operation_query.list(
            scope="active", kind="INTEGRATION_SETUP", page_size=4,
        )
        summary = page.items[0] if page.items else None
        detail = (
            self.application.operation_query.show(summary.operation_id)
            if summary is not None else None
        )
        return summary, detail

    def refresh_progress(self) -> None:
        """Use the shared coordinator while guided setup owns the action lane."""
        if self._disposed:
            return
        self.shell.request_observation(
            self._observe_setup_operation,
            lambda result: self._render_setup_progress(*result),
        )

    def _render_setup_progress(self, summary: Any, detail: Any) -> None:
        if summary is None:
            self._setup_progress.set(
                "No guided connection setup is currently running."
            )
            return
        projection = project_operation_progress(
            summary, current_step=getattr(detail, "current_step", None),
        )
        text = (
            f"{projection.phase_label} · "
            f"{format_elapsed(projection.elapsed_seconds)}. "
            f"Next: {projection.next_checkpoint}."
        )
        if projection.problem is not None:
            text += (
                f" {projection.problem.title}: "
                f"{projection.problem.user_message}"
            )
        self._setup_progress.set(text)

    def _add(self) -> None:
        label = self._label.get()
        kind = self._kind.get()
        self._run(
            lambda: self.application.connection_credentials.add_client(
                label=label, client_kind=kind),
            "Client created", reveal=True)
        self._label.set("")

    def _guided_setup(self, intent: str) -> None:
        defaults = {
            "OPENWEBUI": "Open WebUI",
            "PHONE_TABLET": "My phone",
            "DESKTOP_APP": "Desktop app",
            "DEVELOPER": "Developer client",
        }
        label = self._label.get().strip() or defaults[intent]

        def action():
            return self.application.integration_setup.start(
                intent=intent, label=label, require_tailnet=True,
                requested_by="gui")

        self._run(
            action,
            "Connection ready",
            reveal=intent != "OPENWEBUI",
        )

    def _rotate(self) -> None:
        selected = self._selected()
        if not selected or selected["revoked"]:
            self._selection_notice()
            return
        self._run(
            lambda: self.application.connection_credentials.rotate_client(
                selected["client_id"],
                expected_revision=int(selected["revision"])),
            "Client key rotated", reveal=True)

    def _confirm_revoke(self) -> None:
        selected = self._selected()
        if not selected or selected["revoked"]:
            self._selection_notice()
            return
        self.shell.drawer.show_confirmation(Confirmation(
            "Revoke this client?",
            f"{selected['label']} will immediately lose model API access.",
            "Other client credentials remain unchanged. A revoked key cannot be restored.",
            "Revoke client",
        ), lambda: self._run(
            lambda: self.application.connection_credentials.revoke_client(
                selected["client_id"],
                expected_revision=int(selected["revision"])),
            "Client revoked"))

    def _test(self) -> None:
        selected = self._selected()
        alias = self._view.model_alias if self._view else None
        if not selected or selected["revoked"] or not alias:
            self._selection_notice()
            return
        base = (self._snapshot.get("urls") or {}).get("base_url")
        self.shell.drawer.show_confirmation(Confirmation(
            "Test this client connection?",
            f"Verify {selected['label']} against {base or 'the local gateway'} using model {alias}. The check covers authentication rejection, model identity and a synthetic streamed response.",
            "No saved conversation is sent. This appliance-side check does not qualify a phone app or a second-device journey.",
            "Run connection check"), lambda: self._run(
            lambda: self.application.connection_probes.run(
                client_id=selected["client_id"], public_alias=alias,
                tailnet_base_url=base),
            "Connection tests completed"))

    def _confirm_disable_all(self) -> None:
        access = self.application.connection_credentials.access_state()
        self.shell.drawer.show_confirmation(Confirmation(
            "Disable all remote API access?",
            "Every active client key will be revoked immediately.",
            "The model and Open WebUI data remain installed. Create new clients later to recover.",
            "Disable all", typed_phrase="DISABLE",
        ), lambda: self._run(
            lambda: self.application.connection_credentials.disable_all(
                expected_revision=int(access["revision"])),
            "Remote API access disabled"))

    def _confirm_retire_legacy(self) -> None:
        if not self._legacy.get("legacy_present"):
            self.shell.notice_bar.show_notice(Notice(
                "info", "No legacy key", "No active legacy shared key remains."))
            return
        if not self._legacy.get("revoke_recommended"):
            self.shell.notice_bar.show_notice(Notice(
                "warning", "Replacement checks required",
                "Verify separate Open WebUI and external-app keys before retiring the shared key."))
            return
        self.shell.drawer.show_confirmation(Confirmation(
            "Retire the replaced shared key?",
            "Apps still using the old shared API key will immediately lose access.",
            "Verified named Open WebUI and external-app keys remain active.",
            "Retire shared key", typed_phrase="REVOKE LEGACY",
        ), lambda: self._run(
            lambda: self.application.integration_setup.retire_legacy(
                confirmation="REVOKE LEGACY"),
            "Legacy shared key retired"))

    def _show_instructions(self) -> None:
        payload = instructions_for(
            self._guide_kind.get(), urls=self._snapshot.get("urls") or {},
            public_alias=self._view.model_alias if self._view else None)
        values = payload.get("values") or {}
        rows = [f"{label}: {value or 'Not ready'}" for label, value in values.items()]
        notes = payload.get("card", {}).get("notes") or []
        text = "\n".join((
            "Use these values exactly:",
            *rows,
            "",
            *(str(note) for note in notes),
            "",
            "A 401 means the key is missing or rejected. A 403 usually means "
            "the key lacks permission or the client is using the wrong API path. "
            "A 502 means the private address is reachable but the model backend needs attention.",
        ))
        self.shell.drawer.show_details(
            f"{payload['card']['title']} settings", text)

    def _copy_safe_settings(self) -> None:
        payload = instructions_for(
            self._guide_kind.get(), urls=self._snapshot.get("urls") or {},
            public_alias=self._view.model_alias if self._view else None,
        )
        values = payload.get("values") or {}
        safe = {
            key: value for key, value in values.items()
            if key not in {"API Key", "Authorization", "api_key"}
        }
        if not payload.get("available"):
            self.shell.notice_bar.show_notice(Notice(
                "warning", "Connection settings are not ready",
                str(payload.get("unavailable_reason") or
                    "Start a model and private sharing first."),
                action_label="Review Models", action_route=Route.MODELS.value,
                dismissible=False,
            ))
            return
        text = "\n".join(f"{label}: {value}" for label, value in safe.items())
        self._copy_value(text)

    def _doctor_action(self) -> None:
        route = self._doctor_route
        if route and route != Route.CONNECTIONS.value:
            self.shell.navigate(route)
            return
        self._mode_tabs.select(self._connect_tab)
        selected = self._selected()
        if selected and not selected["revoked"] and self._view and self._view.model_alias:
            self._test()
        else:
            if self._guided_primary is not None:
                self._guided_primary.focus_set()
            self.refresh()

    def _show_secret(self, secret: str) -> None:
        self._mode_tabs.select(self._connect_tab)
        self._secret.set(secret)
        self._secret_deadline = time.monotonic() + SECRET_REVEAL_SECONDS
        self._secret_status.set(
            "Shown once for 30 seconds. Save it now; existing keys cannot be revealed again.")

    def _expire_secret(self) -> None:
        if self._secret_deadline is None or time.monotonic() < self._secret_deadline:
            return
        self._clear_secret()

    def _clear_secret(self) -> None:
        self._secret.set("No new key is being shown.")
        self._secret_deadline = None
        self._secret_status.set(
            "Existing keys cannot be revealed; rotate one to get a new key.")
        if self._copied_secret:
            try:
                self.clipboard_clear()
            except Exception:
                pass
        self._copied_secret = False

    def _copy_secret(self) -> None:
        if self._secret_deadline is None:
            return
        self._copy_value(self._secret.get())
        self._copied_secret = True
        self._secret_status.set("API key copied; clipboard will clear when this reveal expires.")

    def _copy_value(self, value: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(value)
        self.shell.notice_bar.show_notice(Notice(
            "success", "Copied", "The selected connection value was copied."))

    def _selection_notice(self) -> None:
        self.shell.notice_bar.show_notice(Notice(
            "warning", "Select an active client",
            "Select a client and make sure a live public model alias is available."))

    def focus_primary(self) -> None:
        self._doctor_button.focus_set()

    def observation_failed(self, _error: BaseException) -> None:
        self._detail.set(
            "Connection status could not be refreshed; no ready state was inferred.")

    def leave(self) -> None:
        self._clear_secret()

    def dispose(self) -> None:
        self.leave()
        self._disposed = True


__all__ = [
    "ConnectionPageView", "ConnectionsPage", "build_connections_view",
    "connection_action_notice",
]
