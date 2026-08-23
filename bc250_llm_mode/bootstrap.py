"""Native dependency bootstrap for immutable Bazzite images lacking tkinter."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from typing import Any

from .disclaimer import DISCLAIMER_TEXT, acknowledge
from .hardware import detect_hardware
from .logging_utils import CommandRunner, configure_logging
from .privilege import elevated

ACKS = (
    "I understand the BC-250 may run very hot and I will monitor it.",
    "I understand I should unlock all 40 CUs for best compute.",
    "I have set the BIOS VRAM allocation appropriately (~12 GiB GPU / ~4 GiB system).",
)


def tkinter_available() -> bool:
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return False
    return True


def _zenity(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["zenity", *args], capture_output=True, text=True, check=False)


def terminal_acknowledgment(
    state: dict[str, Any],
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> bool:
    """Enforce the same mandatory gate when bootstrapping from an SSH terminal."""
    output_fn(DISCLAIMER_TEXT)
    output_fn("")
    output_fn("All acknowledgments below are mandatory.")
    for index, label in enumerate(ACKS, start=1):
        response = input_fn(f"[{index}/3] {label}\nType YES: ").strip()
        if response != "YES":
            output_fn("Acknowledgment cancelled; no system changes were made.")
            return False
    if input_fn('Type "I ACCEPT" to continue: ').strip() != "I ACCEPT":
        output_fn("Acknowledgment cancelled; exact text was not entered. No system changes were made.")
        return False
    acknowledge(state)
    return True


def _setup_service(store: Any):
    """SetupService for SQLite-backed stores; None on legacy JSON doubles."""
    database_path = getattr(getattr(store, "paths", None), "database_path", None)
    if database_path is None:
        return None
    from .services import SetupService
    from .unit_of_work import UnitOfWorkFactory

    return SetupService(UnitOfWorkFactory(database_path))


def _persist_without_save(store: Any, state: dict[str, Any], changes: dict[str, Any]) -> None:
    """Persist setup-scoped settings without a whole-state save.

    SQLite stores go through SetupService's unit of work; legacy JSON
    stores use a settings-only transaction; in-memory doubles keep the
    dict mutation only.
    """
    state.update(changes)
    service = _setup_service(store)
    if service is not None:
        if changes.get("disclaimer_ack"):
            service.acknowledge_safety()
        if changes.get("bootstrap_tkinter_staged"):
            service.mark_tkinter_staged(evidence={"staged_by": "rpm-ostree"})
        return
    if hasattr(store, "transaction"):
        def mutate(current: dict[str, Any]) -> dict[str, Any]:
            current.update(changes)
            return current

        store.transaction(mutate)


def _stage_tkinter(state: dict[str, Any], store: StateStore, runner: CommandRunner) -> None:
    runner.run(elevated(["rpm-ostree", "install", "python3-tkinter"]))
    _persist_without_save(
        store,
        state,
        {"reboot_required": True, "bootstrap_tkinter_staged": True},
    )


def _terminal_bootstrap(store: StateStore, state: dict[str, Any]) -> bool:
    if not sys.stdin.isatty():
        raise RuntimeError(
            "python3-tkinter is absent and no graphical display is available. Re-run from an interactive "
            "terminal to complete the safety-gated bootstrap, or connect with X11 forwarding."
        )
    if not state.get("disclaimer_ack"):
        if not terminal_acknowledgment(state):
            return False
        _persist_without_save(
            store, state, {"disclaimer_ack": True, "ack_timestamp": state.get("ack_timestamp")}
        )
    answer = input(
        "Stage the native python3-tkinter package with rpm-ostree now? A reboot is required. [y/N]: "
    ).strip().lower()
    if answer not in {"y", "yes"}:
        print("Not staged; no additional system changes were made.")
        return False
    runner = CommandRunner(configure_logging(state["logs_dir"]), print)
    _stage_tkinter(state, store, runner)
    print("python3-tkinter is staged. Reboot, then launch BC250 LLM MODE again.")
    return False


def bootstrap_tkinter(application: Any) -> bool:
    """Return True only when tkinter is already importable; otherwise stage it safely.

    Accepts the composed ``Application``; helpers below duck-type on
    ``paths``/``transaction`` so in-memory test doubles keep working.
    """
    if tkinter_available():
        return True
    read = getattr(application, "read_model", None)
    state = read() if read is not None else application.load()
    report = detect_hardware(state["models_dir"])
    if report.errors:
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            raise RuntimeError("Hardware validation failed:\n" + "\n".join(report.errors))
        _zenity([
            "--error", "--title=BC250 LLM MODE — Hardware validation failed",
            "--text=" + "\n".join(report.errors), "--width=620",
        ])
        return False
    if state.get("bootstrap_tkinter_staged"):
        message = "python3-tkinter is staged but not active. Reboot, then launch BC250 LLM MODE again."
        if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            _zenity(["--info", "--title=BC250 LLM MODE — Reboot required", f"--text={message}"])
        else:
            print(message)
        return False
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return _terminal_bootstrap(store, state)
    if not shutil.which("zenity"):
        raise RuntimeError("python3-tkinter is absent and the native Zenity bootstrap is unavailable.")
    if not state.get("disclaimer_ack"):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt") as warning:
            warning.write(DISCLAIMER_TEXT)
            warning.flush()
            shown = _zenity([
                "--text-info", "--title=BC250 LLM MODE — Safety Warning",
                f"--filename={warning.name}", "--width=760", "--height=680",
            ])
        if shown.returncode:
            return False
        checklist: list[str] = [
            "--list", "--checklist", "--title=BC250 LLM MODE — Required acknowledgments",
            "--text=Select all three items to continue.", "--column=Confirmed", "--column=Acknowledgment",
            "--separator=|", "--width=820", "--height=360",
        ]
        for label in ACKS:
            checklist.extend(["FALSE", label])
        checked = _zenity(checklist)
        if checked.returncode or set(filter(None, checked.stdout.strip().split("|"))) != set(ACKS):
            _zenity(["--warning", "--text=All three acknowledgments are required."])
            return False
        typed = _zenity([
            "--entry", "--title=BC250 LLM MODE — Typed confirmation", "--text=Type I ACCEPT to continue:",
        ])
        if typed.returncode or typed.stdout.strip() != "I ACCEPT":
            _zenity(["--warning", "--text=The exact text I ACCEPT is required."])
            return False
        acknowledge(state)
        _persist_without_save(
            store, state, {"disclaimer_ack": True, "ack_timestamp": state.get("ack_timestamp")}
        )
    proceed = _zenity([
        "--question", "--title=BC250 LLM MODE — Native GUI dependency",
        "--text=The Bazzite host needs the python3-tkinter package for the local wizard. Stage it now with rpm-ostree? A reboot will be required.",
        "--ok-label=Stage package", "--cancel-label=Not now", "--width=620",
    ])
    if proceed.returncode:
        return False
    runner = CommandRunner(configure_logging(state["logs_dir"]), print)
    _stage_tkinter(state, store, runner)
    _zenity([
        "--info", "--title=BC250 LLM MODE — Reboot required",
        "--text=python3-tkinter has been staged. Reboot, then launch BC250 LLM MODE again; setup will resume.",
        "--width=620",
    ])
    return False
