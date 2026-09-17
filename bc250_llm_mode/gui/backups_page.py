"""Native backup creation, byte verification and explicit restore preview."""
from __future__ import annotations

import time
import uuid
import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path

from .view_state import Confirmation, Notice


class BackupsPage(ttk.Frame):
    def __init__(self, parent, shell, application):
        super().__init__(parent)
        self.shell, self.application = shell, application
        self._disposed = False
        self._offset = 0
        self._rows = {}
        ttk.Label(self, text="Protect settings and recover safely", font=("TkDefaultFont", 15, "bold")).pack(anchor="w")
        ttk.Label(self, text="Backups contain the application database. Model files, runtime files and conversations are kept locally during restore. Secret files are never placed in the archive.", wraplength=680).pack(anchor="w", fill="x", pady=8)
        actions = ttk.Frame(self)
        actions.pack(fill="x")
        self.create_button = ttk.Button(actions, text="Create backup", command=self._create)
        self.create_button.pack(side="left")
        ttk.Button(actions, text="Verify selected", command=self._verify).pack(side="left", padx=6)
        ttk.Button(actions, text="Preview restore…", command=self._preview).pack(side="left")
        portable = ttk.Frame(self)
        portable.pack(fill="x", pady=(8, 0))
        ttk.Button(portable, text="Export conversations and settings…", command=self._portable_export).pack(side="left")
        ttk.Button(portable, text="Import portable backup…", command=self._portable_import).pack(side="left", padx=6)
        ttk.Label(self, text="Portable backups can protect conversations and drafts on another disk. They import as new conversations and do not restore machine services or credentials.", wraplength=680).pack(anchor="w", pady=5)
        self.status = tk.StringVar(value="Reading local backups…")
        ttk.Label(self, textvariable=self.status, wraplength=680).pack(anchor="w", pady=8)
        self.tree = ttk.Treeview(self, columns=("created", "size", "verification"), show="headings", selectmode="browse", height=12)
        for key, title in (("created", "Created"), ("size", "Archive size"), ("verification", "Recorded verification")):
            self.tree.heading(key, text=title)
        self.tree.pack(fill="both", expand=True)
        pages = ttk.Frame(self)
        pages.pack(fill="x", pady=6)
        ttk.Button(pages, text="Previous", command=lambda: self._page(-1)).pack(side="left")
        ttk.Button(pages, text="Next", command=lambda: self._page(1)).pack(side="left", padx=6)

    def mount(self):
        self.pack(fill="both", expand=True)

    def enter(self, context=None):
        self.refresh()

    def _page(self, change):
        self._offset = max(0, self._offset + change * 50)
        self.refresh()

    def refresh(self):
        offset = self._offset
        self.shell.request_observation(
            lambda: self.application.backup.list_backups(limit=50, offset=offset), self._apply)

    def _apply(self, rows):
        if self._disposed:
            return
        self._rows = {row["backup_id"]: row for row in rows}
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            self.tree.insert("", "end", iid=row["backup_id"], values=(row["created_at"], f'{row["bytes_total"] / 1024**2:.1f} MiB', row["verification_state"]))
        if selected and selected[0] in self._rows:
            self.tree.selection_set(selected[0])
        self.status.set(f"Showing {len(rows)} backup(s). Verify checks the current archive bytes; a recorded result alone does not qualify a restore.")

    def _selected(self):
        selection = self.tree.selection()
        if not selection:
            self.status.set("Select a backup first.")
            return None
        return selection[0]

    def _portable_run(self, work, done):
        if self.shell.busy:
            return
        result = {}
        def action():
            try:
                result["value"] = work()
            except Exception:
                result["failed"] = True
        def complete():
            if self._disposed:
                return
            if result.get("failed"):
                self.status.set("Portable backup could not finish. Check the selected file, available storage, and the 200-conversation/128 MiB limits. Existing conversations were not replaced; keep the original archive and retry after correcting the issue.")
            else:
                done(result["value"])
        self.shell._work(action, complete)

    @staticmethod
    def _preference_choices(body, available):
        labels = {"appearance": "Appearance", "ui_scale_percent": "Interface scale", "reduced_motion": "Reduced motion"}
        choices = {}
        for key, label in labels.items():
            if key in available:
                choices[key] = tk.BooleanVar(value=False)
                ttk.Checkbutton(body, text=label, variable=choices[key]).pack(anchor="w")
        return choices

    def _portable_export(self):
        body = self.shell.drawer.show_form("Export a portable backup")
        ttk.Label(body, text="Includes all saved conversations, drafts and their instructions. The file contains readable private text; choose a location you trust, preferably on another disk. Current model services and credentials are excluded.", wraplength=760).pack(anchor="w")
        ttk.Label(body, text="Also include these optional settings:").pack(anchor="w", pady=(6, 0))
        choices = self._preference_choices(body, ("appearance", "ui_scale_percent", "reduced_motion"))
        templates = tk.BooleanVar(value=False)
        ttk.Checkbutton(body, text="Reusable prompt templates", variable=templates).pack(anchor="w")
        def choose():
            path = filedialog.asksaveasfilename(parent=self, title="Save private portable backup",
                defaultextension=".tar", initialfile=time.strftime("bc250-conversations-%Y%m%d.tar"),
                filetypes=(("Portable backup", "*.tar"),))
            if not path:
                return
            selected = tuple(key for key, value in choices.items() if value.get())
            include_templates = bool(templates.get())
            def complete(preview):
                self.shell.drawer.clear()
                self.status.set(f"Portable backup verified and saved: {len(preview.conversations)} conversations, {len(preview.templates)} templates, {len(preview.preferences)} selected display settings. Keep this file to recover on another installation.")
            self._portable_run(lambda: self.application.portable_backup.export(Path(path),
                preference_keys=selected, include_templates=include_templates), complete)
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=5)
        ttk.Button(actions, text="Cancel", command=self.shell.drawer.clear).pack(side="right")
        ttk.Button(actions, text="Choose destination…", command=choose).pack(side="right", padx=5)

    def _portable_import(self):
        path = filedialog.askopenfilename(parent=self, title="Inspect portable backup", filetypes=(("Portable backup", "*.tar"),))
        if path:
            self._portable_run(lambda: (self.application.portable_backup.inspect(Path(path)), self.application.preferences.current()),
                lambda result: self._portable_preview(Path(path), *result))

    def _portable_preview(self, path, preview, baseline):
        body = self.shell.drawer.show_form("Verified portable backup contents")
        drafts = sum(row["has_draft"] for row in preview.conversations)
        ttk.Label(body, text=f"{len(preview.conversations)} conversations · {drafts} drafts · {preview.size_bytes / 1024**2:.1f} MiB. Import creates new conversations; existing conversations remain unchanged. Repeating this exact import resumes it without duplicates.", wraplength=760).pack(anchor="w")
        frame = ttk.Frame(body)
        frame.pack(fill="both", expand=True)
        contents = tk.Text(frame, height=5, wrap="word")
        contents.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=contents.yview)
        scroll.pack(side="right", fill="y")
        contents.configure(yscrollcommand=scroll.set)
        for row in preview.conversations:
            contents.insert("end", f"{row['title']} · {row['message_count']} messages" + (" · draft" if row["has_draft"] else "") + "\n")
        contents.configure(state="disabled")
        ttk.Label(body, text="Optional settings to import:").pack(anchor="w")
        choices = self._preference_choices(body, preview.preferences)
        templates = tk.BooleanVar(value=False)
        if preview.templates:
            ttk.Checkbutton(body, text=f"Import {len(preview.templates)} prompt templates (keep existing versions)", variable=templates).pack(anchor="w")
        def run():
            selected = tuple(key for key, variable in choices.items() if variable.get())
            include_templates = bool(templates.get())
            def work():
                result = self.application.portable_backup.import_archive(path, preview.digest,
                    preference_keys=selected, include_templates=include_templates, expected_preferences=baseline)
                return result, self.application.preferences.current()
            def done(value):
                result, preferences = value
                self.shell.drawer.clear()
                if result.preferences_applied:
                    self.shell.apply_preferences(preferences)
                message = f"Imported {result.imported} conversations; {result.already_present} already present. "
                if result.complete:
                    message += "Portable import complete. Open Chat to find the imported conversations."
                else:
                    message += f"{result.remaining} conversations and/or optional settings still need attention. Free storage or resolve the settings/template conflict, then inspect and retry this same archive safely."
                self.status.set(message)
            self._portable_run(work, done)
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=5)
        ttk.Button(actions, text="Cancel", command=self.shell.drawer.clear).pack(side="right")
        ttk.Button(actions, text="Import as new conversations", command=run).pack(side="right", padx=5)

    def _run(self, work, done):
        box = {}
        def action():
            box["result"] = work()
        self.shell._work(action, lambda: done(box["result"]))

    def _create(self):
        label = time.strftime("backup-%Y%m%d-%H%M%S-", time.gmtime()) + uuid.uuid4().hex[:8] + ".tar"
        self._run(lambda: self.application.backup.create_backup(label, requested_by="gui"), self._outcome)

    def _outcome(self, result):
        self.shell.track_operation_id(result.operation_id)
        self.shell.notice_bar.show_notice(Notice(
            "success" if result.ok else "warning", "Backup action completed" if result.ok else "Backup action needs attention",
            ("Restore completed. Reopen the application to refresh all views and log files; the model remains stopped. Prior profile: " + str(result.detail.get("retained_prior_profile", "see Activity"))) if result.status == "RESTORED" else
            "Backup archive created and verified." if result.status == "CREATED" else
            "No successful restore was inferred. Review the operation in Activity.",
            action_label="View Activity", action_route="activity", details=result.status,
            dismissible=result.status != "RESTORED"))
        self.refresh()

    def _verify(self):
        selected = self._selected()
        if selected:
            self._run(lambda: self.application.backup.verify_backup(selected),
                      lambda result: self.status.set("Archive bytes verified." if result.get("valid") else "Archive verification failed. This backup cannot be restored; keep it for investigation."))

    def _preview(self):
        selected = self._selected()
        if selected:
            self._run(lambda: self.application.backup.restore_inspect(selected), self._confirm)

    def _confirm(self, preview):
        if not preview.get("restorable"):
            self.status.set("Restore blocked: " + ", ".join(preview.get("blockers", [])))
            return
        self.status.set(f"Verified identity: {preview['confirmation_digest']}. Required free space: {(preview.get('required_space_bytes') or 0) / 1024**3:.2f} GiB. Prior profile retained beside the active profile in {preview.get('retained_prior_parent', 'its existing parent directory')}.")
        self.shell.drawer.show_confirmation(Confirmation(
            "Restore this backup?", "This replaces saved configuration and stops the model. Current local files, credentials, revocations and thermal safety are preserved.",
            "The prior profile is retained. Reopen the app after restore. A verification failure restores the prior profile or asks for Repair.",
            "Restore backup", destructive=True, typed_phrase="RESTORE"),
            lambda: self._run(lambda: self.application.backup.restore_start(
                preview["backup_id"], preview["confirmation_digest"], requested_by="gui"), self._outcome))

    def observation_failed(self, error):
        self.status.set("Backups could not be read. Refresh or open Activity for recovery status.")

    def focus_primary(self):
        self.create_button.focus_set()

    def leave(self):
        pass

    def dispose(self):
        self._disposed = True
        self.destroy()
