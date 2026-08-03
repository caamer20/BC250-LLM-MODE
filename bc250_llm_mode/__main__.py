from __future__ import annotations

import argparse
import json
import sys

from .bootstrap import bootstrap_tkinter
from .catalog import calculate_fit, model_by_id
from .chat import run_chat
from .desktop import switch_to_desktop_mode
from .disclaimer import require_acknowledgment
from .download import download_model
from .hardware import detect_hardware
from .logging_utils import CommandRunner, configure_logging
from .optimize import kv_scale_for_settings
from .prepare import prepare_model
from .server import health_check, restart_service
from .state import StateStore
from .uninstall import uninstall


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bc250-llm-mode", description="BC250 local LLM setup and chat")
    parser.add_argument("--state", help="Override the state JSON path")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("setup", help="Open/resume the native setup wizard")
    sub.add_parser("repair", help="Open the native wizard at hardware validation")
    sub.add_parser("chat", help="Start terminal chat")
    sub.add_parser("status", help="Print hardware and server status")
    install = sub.add_parser("install-model", help="Download, verify, and install a catalog model")
    install.add_argument("model_id")
    install.add_argument("--quant")
    install.add_argument("--ctx", type=int, default=8192)
    switch = sub.add_parser("switch", help="Switch the single server service to an installed model")
    switch.add_argument("model_id")
    desktop = sub.add_parser(
        "desktop-mode",
        aliases=["desktop"],
        help="Restore normal Bazzite desktop mode without deleting models",
    )
    desktop.add_argument(
        "--now",
        action="store_true",
        help="Start graphical.target immediately as well as making it the boot default",
    )
    revert = sub.add_parser("uninstall", help="Revert LLM Mode and remove the service")
    revert.add_argument("--remove-container", action="store_true")
    revert.add_argument("--remove-models", action="store_true", help="Permanently delete downloaded model files")
    return parser


def _store(path: str | None) -> StateStore:
    return StateStore(path) if path else StateStore()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    store = _store(args.state)
    state = store.load()
    if args.command in (None, "setup"):
        if not bootstrap_tkinter(store):
            return 0
        from .gui import run_gui
        run_gui(store, management=bool(state.get("setup_complete")))
        return 0
    if args.command == "repair":
        if not bootstrap_tkinter(store):
            return 0
        from .gui import run_gui
        state["setup_phase"] = 0
        state["setup_complete"] = False
        store.save(state)
        run_gui(store)
        return 0
    if args.command == "chat":
        require_acknowledgment(state)
        run_chat(store)
        return 0
    if args.command == "status":
        report = detect_hardware(state["models_dir"])
        result: dict[str, object] = {"hardware": report.to_dict(), "state": state}
        if state.get("setup_complete"):
            try:
                result["server"] = health_check(state, timeout=3)
            except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as exc:
                result["server"] = {"healthy": False, "error": str(exc)}
        print(json.dumps(result, indent=2))
        return 0 if report.valid else 2
    logger = configure_logging(state["logs_dir"])
    runner = CommandRunner(logger, print)
    if args.command == "install-model":
        require_acknowledgment(state)
        if not state.get("env_ready"):
            raise RuntimeError("Inference environment is not ready; complete that wizard step first.")
        model = model_by_id(args.model_id)
        quant = args.quant or next(iter(model.allow_globs))
        fit = calculate_fit(
            model, quant, args.ctx, kv_scale=kv_scale_for_settings(state.get("optimizations"))
        )
        if fit.verdict == "NO-FIT":
            raise RuntimeError(fit.detail)
        downloaded = download_model(state, model, quant, runner)
        prepare_model(state, model, quant, downloaded, runner)
        state["current_ctx"] = args.ctx
        store.save(state)
        if state.get("setup_complete"):
            restart_service(state, runner)
            health_check(state, runner)
        return 0
    if args.command == "switch":
        require_acknowledgment(state)
        if args.model_id not in {item.get("id") for item in state.get("installed_models", [])}:
            raise RuntimeError(f"Model is not installed: {args.model_id}")
        state["current_model"] = args.model_id
        store.save(state)
        restart_service(state, runner)
        health_check(state, runner)
        return 0
    if args.command in {"desktop-mode", "desktop"}:
        state = switch_to_desktop_mode(state, runner, activate_now=args.now)
        store.save(state)
        return 0
    if args.command == "uninstall":
        state = uninstall(
            state, runner, remove_container=args.remove_container, remove_models=args.remove_models
        )
        store.save(state)
        return 0
    return 1


def cli(argv: list[str] | None = None) -> int:
    """Console-script boundary: never expose an implementation traceback to the user."""
    try:
        return main(argv)
    except (RuntimeError, PermissionError, KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
