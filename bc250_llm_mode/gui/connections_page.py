"""Native one-window Connection Assistant over the EXP-2 services."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Mapping

import tkinter as tk
from tkinter import ttk

from ..connection_setup import CLIENT_CARDS, instructions_for
from ..presentation import format_timestamp
from .view_state import Confirmation, Notice


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
    problem_details = {
        "MODEL_ABSENT": "Choose and install a model before connecting another device.",
        "MODEL_STOPPED": "Start the selected model, then run the connection check.",
        "MODEL_IDENTITY_MISMATCH": "Reconcile the running model before sharing it.",
        "GATEWAY_NOT_INSTALLED": "Enable the authenticated gateway for this boot.",
        "GATEWAY_STOPPED": "Start the authenticated gateway for this boot.",
        "GATEWAY_CREDENTIAL_REQUIRED": "Create a credential for this device or app.",
        "TAILSCALE_DISCONNECTED": "Connect this BC-250 to its private tailnet.",
        "SERVE_MAPPING_MISSING": "Publish the reviewed private HTTPS mappings.",
        "PUBLIC_FUNNEL_ENABLED": "Disable public Funnel exposure before continuing.",
        "CLIENT_VERIFICATION_STALE": "Run the guided connection check again.",
        "CLIENT_VERIFICATION_FAILED": "Review the failed check, fix it, and retry.",
        "CLIENT_VERIFICATION_INVALIDATED": "A dependency changed; run the connection check again.",
    }
    problem_code = str(readiness.get("primary_problem_code") or "")
    return ConnectionPageView(
        headline="Ready for another device" if ready else "Finish connection checks",
        detail=(
            "Use the exact values below. The raw model port and /api are not supported."
            if ready else problem_details.get(
                problem_code,
                str(snapshot.get("next_action") or
                    "Start the model, add a client, then run the guided test."),
            )),
        ready=ready,
        model_alias=(
            str(model.get("public_alias")) if model.get("public_alias") else None),
        endpoints=endpoints,
        checks=checks,
        clients=tuple(rows),
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

        self._headline = tk.StringVar(value="Loading connection status…")
        self._detail = tk.StringVar(value="")
        ttk.Label(
            self, textvariable=self._headline,
            font=("TkDefaultFont", 18, "bold")).pack(anchor="w")
        ttk.Label(
            self, textvariable=self._detail, wraplength=760,
        ).pack(anchor="w", fill="x", pady=(2, 8))

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

        checks = ttk.LabelFrame(left, text="Guided checks", padding=7)
        checks.pack(fill="x", pady=(7, 0))
        self._checks_frame = checks

        clients_box = ttk.LabelFrame(left, text="Client credentials", padding=7)
        clients_box.pack(fill="both", expand=True, pady=(7, 0))
        self._tree = ttk.Treeview(
            clients_box, columns=("type", "state", "used"),
            show="tree headings", height=7, selectmode="browse")
        self._tree.heading("#0", text="Label")
        self._tree.heading("type", text="Type")
        self._tree.heading("state", text="State")
        self._tree.heading("used", text="Last use")
        self._tree.column("#0", width=170)
        self._tree.column("type", width=90)
        self._tree.column("state", width=75)
        self._tree.column("used", width=140)
        self._tree.pack(fill="both", expand=True)
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

        add = ttk.LabelFrame(right, text="Add a device or app", padding=7)
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

        secret = ttk.LabelFrame(right, text="One-time API key", padding=7)
        secret.pack(fill="x", pady=(7, 0))
        self._secret = tk.StringVar(value="No new key is being shown.")
        ttk.Entry(secret, textvariable=self._secret, state="readonly").pack(fill="x")
        self._secret_status = tk.StringVar(
            value="Existing keys cannot be revealed; rotate one to get a new key.")
        ttk.Label(secret, textvariable=self._secret_status, wraplength=300).pack(
            anchor="w", fill="x", pady=(3, 5))
        ttk.Button(secret, text="Copy shown key", command=self._copy_secret).pack(anchor="w")

        guides = ttk.LabelFrame(right, text="Client instructions", padding=7)
        guides.pack(fill="x", pady=(7, 0))
        self._guide_kind = tk.StringVar(value="pocketpal")
        ttk.Combobox(
            guides, textvariable=self._guide_kind, state="readonly",
            values=tuple(card.card_id for card in CLIENT_CARDS),
        ).pack(fill="x")
        ttk.Button(
            guides, text="Show exact settings", command=self._show_instructions,
        ).pack(anchor="w", pady=(5, 0))

        danger = ttk.LabelFrame(right, text="Emergency control", padding=7)
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

        self._apply(({}, []))
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
            ),
            self._apply,
        )

    def _apply(self, payload) -> None:
        if self._disposed:
            return
        snapshot, clients = payload
        self._snapshot = dict(snapshot)
        view = build_connections_view(snapshot, clients)
        previous_view = self._view
        self._view = view
        if view == previous_view:
            return
        self._clients = {row["client_id"]: row for row in view.clients}
        self._headline.set(view.headline)
        self._detail.set(view.detail)
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
        for check_id, passed in view.checks:
            ttk.Label(
                self._checks_frame,
                text=f"{'PASS' if passed else 'NEEDS ACTION'} · {check_id}",
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
            self.shell.notice_bar.show_notice(Notice(
                "success", success_title, "Connection state was refreshed."))
            self.refresh()

        self.shell._work(work, done)

    def _add(self) -> None:
        label = self._label.get()
        kind = self._kind.get()
        self._run(
            lambda: self.application.connection_credentials.add_client(
                label=label, client_kind=kind),
            "Client created", reveal=True)
        self._label.set("")

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
        self._run(
            lambda: self.application.connection_probes.run(
                client_id=selected["client_id"], public_alias=alias,
                tailnet_base_url=base),
            "Connection tests completed")

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

    def _show_instructions(self) -> None:
        payload = instructions_for(
            self._guide_kind.get(), urls=self._snapshot.get("urls") or {},
            public_alias=self._view.model_alias if self._view else None)
        self.shell.drawer.show_details(
            f"{payload['card']['title']} settings",
            json.dumps(payload, indent=2))

    def _show_secret(self, secret: str) -> None:
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
        self._label_entry.focus_set()

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
]
