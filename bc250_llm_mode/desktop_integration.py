"""Verified user-local desktop launcher installation (EXP-1)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from .fsops import atomic_write_bytes, atomic_write_text, ensure_private_dir
from .paths import AppPaths

DESKTOP_FILENAME = "bc250-llm-mode.desktop"
LAUNCHER_FILENAME = "bc250-llm-mode"
ICON_FILENAME = "bc250-llm-mode.svg"
RECEIPT_FILENAME = "desktop-integration.json"
RECEIPT_SCHEMA_VERSION = 1


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _desktop_quote(path: Path) -> str:
    value = str(path)
    if any(char in value for char in ("\n", "\r", "%", "\x00")):
        raise ValueError("desktop launcher path contains an unsafe character")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_desktop_entry(*, launcher: Path, icon: Path) -> str:
    return "\n".join((
        "[Desktop Entry]",
        "Type=Application",
        "Version=1.0",
        "Name=BC250 LLM MODE",
        "Comment=Manage local LLM inference on the AMD BC-250",
        f"Exec={_desktop_quote(launcher)}",
        f"Icon={icon}",
        "Terminal=false",
        "StartupNotify=true",
        "Categories=Utility;System;Development;",
        "Actions=TerminalChat;",
        "",
        "[Desktop Action TerminalChat]",
        "Name=Open terminal chat",
        f"Exec={_desktop_quote(launcher)} chat",
        "Terminal=true",
        "",
    ))


def render_launcher(*, python: Path) -> str:
    value = str(python)
    if not value.startswith("/") or any(c in value for c in ("\n", "\r", "\x00", "'")):
        raise ValueError("launcher requires a safe absolute Python path")
    return "#!/bin/sh\nexec '" + value + "' -m bc250_llm_mode \"$@\"\n"


@dataclass(frozen=True)
class DesktopTargets:
    launcher: Path
    desktop_entry: Path
    icon: Path
    receipt: Path


class DesktopIntegrationService:
    def __init__(
        self,
        paths: AppPaths,
        *,
        environ: dict[str, str] | None = None,
        python_executable: str | Path | None = None,
    ) -> None:
        self.paths = paths
        self.environ = dict(os.environ if environ is None else environ)
        self.python = Path(python_executable or sys.executable).resolve()

    def targets(self) -> DesktopTargets:
        home = self.paths.app_dir.parent
        data_home = Path(self.environ.get("XDG_DATA_HOME") or home / ".local/share").expanduser()
        bin_home = Path(self.environ.get("XDG_BIN_HOME") or home / ".local/bin").expanduser()
        return DesktopTargets(
            launcher=bin_home / LAUNCHER_FILENAME,
            desktop_entry=data_home / "applications" / DESKTOP_FILENAME,
            icon=data_home / "icons" / "hicolor" / "scalable" / "apps" / ICON_FILENAME,
            receipt=self.paths.app_dir / RECEIPT_FILENAME,
        )

    @staticmethod
    def _icon_bytes() -> bytes:
        return files("bc250_llm_mode").joinpath("assets", ICON_FILENAME).read_bytes()

    def plan(self) -> dict:
        targets = self.targets()
        return {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "targets": {
                "launcher": str(targets.launcher),
                "desktop_entry": str(targets.desktop_entry),
                "icon": str(targets.icon),
                "receipt": str(targets.receipt),
            },
            "autostart": False,
            "system_writes": False,
        }

    def _payloads(self) -> dict[str, bytes]:
        targets = self.targets()
        return {
            "launcher": render_launcher(python=self.python).encode(),
            "desktop_entry": render_desktop_entry(
                launcher=targets.launcher, icon=targets.icon
            ).encode(),
            "icon": self._icon_bytes(),
        }

    def status(self) -> dict:
        targets = self.targets()
        receipt = self._read_receipt(targets.receipt)
        expected = self._payloads()
        records = {}
        for name, path in (("launcher", targets.launcher), ("desktop_entry", targets.desktop_entry), ("icon", targets.icon)):
            try:
                data = path.read_bytes()
            except OSError:
                data = None
            records[name] = {
                "path": str(path),
                "exists": data is not None,
                "matches_current": data is not None and _sha256(data) == _sha256(expected[name]),
                "owned": data is not None and receipt.get("digests", {}).get(name) == _sha256(data),
            }
        return {
            **self.plan(),
            "installed": all(record["exists"] and record["owned"] for record in records.values()),
            "files": records,
        }

    def install(self) -> dict:
        targets = self.targets()
        payloads = self._payloads()
        for parent in (targets.launcher.parent, targets.desktop_entry.parent, targets.icon.parent):
            parent.mkdir(parents=True, exist_ok=True)
        ensure_private_dir(self.paths.app_dir)
        atomic_write_bytes(targets.launcher, payloads["launcher"], mode=0o700)
        atomic_write_bytes(targets.desktop_entry, payloads["desktop_entry"], mode=0o644)
        atomic_write_bytes(targets.icon, payloads["icon"], mode=0o644)
        receipt = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "digests": {name: _sha256(data) for name, data in payloads.items()},
            "paths": {name: str(getattr(targets, name)) for name in payloads},
        }
        atomic_write_text(targets.receipt, json.dumps(receipt, sort_keys=True), mode=0o600)
        return self.status()

    def remove(self) -> dict:
        targets = self.targets()
        receipt = self._read_receipt(targets.receipt)
        if not receipt:
            return {**self.status(), "removed": False, "reason": "ownership receipt missing"}
        for name in ("launcher", "desktop_entry", "icon"):
            path = getattr(targets, name)
            if not path.exists():
                continue
            digest = _sha256(path.read_bytes())
            if digest != receipt.get("digests", {}).get(name):
                raise RuntimeError(f"refusing to remove modified desktop integration file: {path}")
        for name in ("desktop_entry", "launcher", "icon"):
            getattr(targets, name).unlink(missing_ok=True)
        targets.receipt.unlink(missing_ok=True)
        return {**self.status(), "removed": True}

    @staticmethod
    def _read_receipt(path: Path) -> dict:
        try:
            if stat.S_IMODE(path.stat().st_mode) & 0o077:
                return {}
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        if not isinstance(value, dict) or value.get("schema_version") != RECEIPT_SCHEMA_VERSION:
            return {}
        return value
