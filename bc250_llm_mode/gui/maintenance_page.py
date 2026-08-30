"""One-window prioritized Maintenance inbox and notification preferences."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

import tkinter as tk
from tkinter import ttk

from ..maintenance_center import MAX_MAINTENANCE_ITEMS, PRIORITY_POLICY
from ..notifications import CATEGORIES, MASTER
from .routes import Route
from .view_state import Notice


@dataclass(frozen=True)
class MaintenancePageView:
    generated_at: str | None
    items: tuple[dict[str, Any], ...]
    sources: tuple[dict[str, Any], ...]
    notification_status: dict[str, Any]


def build_maintenance_view(
    snapshot: Mapping[str, Any], notification_status: Mapping[str, Any]
) -> MaintenancePageView:
    items = tuple(
        dict(item) for item in (snapshot.get("items") or ())[:MAX_MAINTENANCE_ITEMS]
        if isinstance(item, Mapping)
        and str(item.get("category")) in PRIORITY_POLICY
    )
    items = tuple(sorted(items, key=lambda item: (
        int(item.get("priority") or 99), str(item.get("code") or "")
    )))
    sources = tuple(
        dict(item) for item in (snapshot.get("sources") or ())[:16]
        if isinstance(item, Mapping)
    )
    return MaintenancePageView(
        str(snapshot.get("generated_at")) if snapshot.get("generated_at") else None,
        items,
        sources,
        dict(notification_status),
    )


def _age(value: Any) -> str:
    if value is None:
        return "not checked"
    seconds = max(0, int(value))
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        return f"{seconds // 3600} hr"
    return f"{seconds // 86400} day(s)"


_CATEGORY_LABELS = {
    "OPERATION_SUCCESS": "Long operation completed",
    "OPERATION_FAILURE": "Operation needs attention",
    "THERMAL_WARNING": "Thermal warning",
    "THERMAL_STOP": "Thermal safety stop",
    "STORAGE_CRITICAL": "Critical storage",
    "BACKUP_FAILURE": "Backup/restore failure",
    "BACKUP_STALE": "Backup stale",
    "REMOTE_SAFETY_DISABLE": "Remote access safety disable",
    "APPLICATION_UPDATE": "Verified application update",
}


class MaintenancePage(ttk.Frame):
    def __init__(self, parent, shell, application) -> None:
        super().__init__(parent)
        self.shell = shell
        self.application = application
        self._disposed = False
        self._view: MaintenancePageView | None = None
        self._items: dict[str, dict[str, Any]] = {}
        self._notification_revisions: dict[str, int] = {}

        ttk.Label(
            self, text="Maintenance", font=("TkDefaultFont", 15, "bold")
        ).pack(anchor="w")
        ttk.Label(
            self,
            text=(
                "The five highest-priority items are shown first. Status refresh "
                "uses cached/durable evidence; Run full check is explicit."
            ),
            wraplength=780,
        ).pack(anchor="w", fill="x", pady=(2, 7))
        action_row = ttk.Frame(self)
        action_row.pack(fill="x", pady=(0, 6))
        self._check_button = ttk.Button(
            action_row, text="Run full check", command=self._run_check
        )
        self._check_button.pack(side="left")
        ttk.Button(
            action_row, text="Preview storage cleanup", command=self._cleanup_preview
        ).pack(side="left", padx=5)
        ttk.Button(action_row, text="Refresh", command=self.refresh).pack(side="left")

        split = ttk.Panedwindow(self, orient="horizontal")
        split.pack(fill="both", expand=True)
        left = ttk.Frame(split, padding=(0, 0, 6, 0))
        right = ttk.Frame(split, padding=(6, 0, 0, 0))
        split.add(left, weight=3)
        split.add(right, weight=2)
        self._tree = ttk.Treeview(
            left,
            columns=("priority", "freshness", "age", "resource"),
            show="tree headings",
            selectmode="browse",
            height=8,
        )
        self._tree.heading("#0", text="Needs attention")
        self._tree.heading("priority", text="Priority")
        self._tree.heading("freshness", text="Evidence")
        self._tree.heading("age", text="Age")
        self._tree.heading("resource", text="Resource")
        self._tree.column("#0", width=245)
        self._tree.column("priority", width=70)
        self._tree.column("freshness", width=90)
        self._tree.column("age", width=70)
        self._tree.column("resource", width=120)
        self._tree.pack(fill="both", expand=True)
        self._tree.bind("<<TreeviewSelect>>", lambda _event: self._show_selected())

        detail = ttk.LabelFrame(right, text="Selected item", padding=8)
        detail.pack(fill="x")
        self._title = tk.StringVar(value="Select an item")
        self._impact = tk.StringVar(value="")
        self._evidence = tk.StringVar(value="")
        self._dismissibility = tk.StringVar(value="")
        ttk.Label(
            detail, textvariable=self._title, font=("TkDefaultFont", 11, "bold")
        ).pack(anchor="w")
        ttk.Label(
            detail, textvariable=self._impact, wraplength=330
        ).pack(anchor="w", fill="x", pady=(2, 4))
        ttk.Label(detail, textvariable=self._evidence, wraplength=330).pack(anchor="w")
        ttk.Label(detail, textvariable=self._dismissibility, wraplength=330).pack(anchor="w")
        selected_actions = ttk.Frame(detail)
        selected_actions.pack(fill="x", pady=(6, 0))
        self._primary = ttk.Button(
            selected_actions, text="Open recommended action", command=self._run_primary
        )
        self._primary.pack(side="left")
        ttk.Button(
            selected_actions, text="Details", command=self._show_details
        ).pack(side="left", padx=5)

        notices = ttk.LabelFrame(right, text="Local notifications", padding=8)
        notices.pack(fill="both", expand=True, pady=(7, 0))
        self._notification_vars = {
            MASTER: tk.BooleanVar(value=False),
            **{category: tk.BooleanVar(value=False) for category in CATEGORIES},
        }
        ttk.Checkbutton(
            notices, text="Enable local notifications",
            variable=self._notification_vars[MASTER],
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        for index, category in enumerate(CATEGORIES):
            ttk.Checkbutton(
                notices,
                text=_CATEGORY_LABELS[category],
                variable=self._notification_vars[category],
            ).grid(
                row=1 + index // 2, column=index % 2,
                sticky="w", padx=(0, 7), pady=1,
            )
        notice_actions = ttk.Frame(notices)
        notice_actions.grid(
            row=1 + (len(CATEGORIES) + 1) // 2,
            column=0, columnspan=2, sticky="ew", pady=(6, 0),
        )
        ttk.Button(
            notice_actions, text="Apply notification choices",
            command=self._apply_notifications,
        ).pack(side="left")
        ttk.Button(
            notice_actions, text="Test notification", command=self._test_notification
        ).pack(side="left", padx=5)
        notices.columnconfigure(0, weight=1)
        notices.columnconfigure(1, weight=1)
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
        if payload is not None:
            self._apply(payload)
            return
        self.shell.request_observation(
            lambda: (
                self.application.maintenance_snapshot.snapshot().to_dict(),
                self.application.notification_preferences.status(),
            ),
            self._apply,
        )

    def _apply(self, payload) -> None:
        if self._disposed:
            return
        view = build_maintenance_view(*payload)
        self._view = view
        selected = self._tree.selection()
        selected_code = selected[0] if selected else None
        for row in self._tree.get_children():
            self._tree.delete(row)
        self._items = {}
        for item in view.items:
            code = str(item["code"])
            self._items[code] = item
            self._tree.insert(
                "", "end", iid=code, text=str(item["title"]),
                values=(
                    item["priority"], item["evidence_freshness"],
                    _age(item.get("evidence_age_seconds")), item["resource"],
                ),
            )
        if selected_code in self._items:
            self._tree.selection_set(selected_code)
        elif view.items:
            self._tree.selection_set(str(view.items[0]["code"]))
        self._show_selected()

        status = view.notification_status
        self._notification_vars[MASTER].set(bool(status.get("master_enabled")))
        self._notification_revisions = {
            MASTER: int(status.get("master_revision") or 0)
        }
        categories = status.get("categories") or {}
        for category in CATEGORIES:
            row = categories.get(category) or {}
            self._notification_vars[category].set(bool(row.get("enabled")))
            self._notification_revisions[category] = int(row.get("revision") or 0)

    def _selected(self) -> dict[str, Any] | None:
        rows = self._tree.selection()
        return self._items.get(str(rows[0])) if rows else None

    def _show_selected(self) -> None:
        item = self._selected()
        if item is None:
            self._title.set("No maintenance items")
            self._impact.set("")
            self._evidence.set("")
            self._dismissibility.set("")
            self._primary.configure(state="disabled")
            return
        self._primary.configure(state="normal")
        self._title.set(str(item["title"]))
        self._impact.set(str(item["impact"]))
        self._evidence.set(
            f"{item['evidence_freshness']} evidence · {_age(item.get('evidence_age_seconds'))}"
        )
        self._dismissibility.set(
            "Safety/integrity item — cannot be dismissed."
            if not item.get("dismissible")
            else "Informational item; addressing the cause removes it."
        )

    def _run_primary(self) -> None:
        item = self._selected()
        if item is None:
            return
        category = str(item.get("category"))
        routes = {
            "SAFETY": Route.SYSTEM,
            "RECOVERY": Route.ACTIVITY,
            "SECURITY": Route.CONNECTIONS,
            "INTEGRITY": Route.MODELS,
            "OPERATION": Route.ACTIVITY,
        }
        if category in routes:
            self.shell.navigate(routes[category])
        elif category == "STORAGE":
            self._cleanup_preview()
        elif category == "INFORMATION":
            self._run_check()
        else:
            self._show_details()

    def _show_details(self) -> None:
        item = self._selected()
        if item is None:
            return
        self.shell.drawer.show_details(
            "Maintenance evidence",
            json.dumps(item, indent=2, sort_keys=True),
        )

    def _run_check(self) -> None:
        result_box: dict[str, Any] = {}

        def action() -> None:
            result_box["result"] = self.application.maintenance_checks.run()

        def done() -> None:
            result = result_box.get("result") or {}
            completed = result.get("sources_completed") or {}
            failed = [name for name, ok in completed.items() if not ok and name != "application_update"]
            self.shell.notice_bar.show_notice(Notice(
                "warning" if failed else "success",
                "Maintenance check completed",
                "Some evidence sources were unavailable; they remain labeled stale."
                if failed else "Local checks completed and the inbox was refreshed.",
                dismissible=not bool(failed),
            ))
            self.refresh()

        self.shell._work(action, done)

    def _cleanup_preview(self) -> None:
        def apply(report) -> None:
            self.shell.drawer.show_details(
                "Storage cleanup preview",
                json.dumps(report, indent=2, sort_keys=True)[:8192],
            )

        self.shell.request_observation(
            self.application.storage_capacity.dry_run_cleanup,
            apply,
        )

    def _apply_notifications(self) -> None:
        changes = {
            category: bool(variable.get())
            for category, variable in self._notification_vars.items()
        }
        revisions = dict(self._notification_revisions)
        result_box: dict[str, Any] = {}

        def action() -> None:
            result_box["result"] = self.application.notification_preferences.apply(
                changes, expected_revisions=revisions
            )

        def done() -> None:
            self.shell.notice_bar.show_notice(Notice(
                "success", "Notification choices saved",
                "Only the selected fixed, privacy-safe categories may notify locally.",
            ))
            self.refresh()

        self.shell._work(action, done)

    def _test_notification(self) -> None:
        result_box: dict[str, Any] = {}

        def action() -> None:
            result_box["result"] = self.application.notifications.test()

        def done() -> None:
            result = result_box.get("result")
            delivered = bool(result and result.delivered)
            self.shell.notice_bar.show_notice(Notice(
                "success" if delivered else "warning",
                "Test notification sent" if delivered else "Notification unavailable",
                "The desktop accepted the fixed test notice."
                if delivered else "Enable notifications and verify the current desktop session supports notify-send.",
                dismissible=delivered,
            ))

        self.shell._work(action, done)

    def observation_failed(self, _error: BaseException) -> None:
        self.shell.notice_bar.show_notice(Notice(
            "warning", "Maintenance status is stale",
            "The last bounded evidence remains visible; run the full check when ready.",
            dismissible=False,
        ))

    def focus_primary(self) -> None:
        self._check_button.focus_set()

    def leave(self) -> None:
        return None

    def dispose(self) -> None:
        self._disposed = True


__all__ = ["MaintenancePage", "MaintenancePageView", "build_maintenance_view"]
