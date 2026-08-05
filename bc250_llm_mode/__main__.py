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
from .llmmode import apply_llm_mode, stage_desktop_boot
from .local_models import discover_local_models
from .logging_utils import CommandRunner, configure_logging
from .model_manager import change_context, register_and_switch_local, switch_model
from .openwebui import (
    open_webui_status,
    restart_open_webui,
    start_open_webui,
    stop_open_webui,
)
from .optimize import kv_scale_for_settings
from .prepare import prepare_model
from .server import (
    health_check,
    install_service,
    restart_and_wait,
    restart_service,
    service_status,
    start_service,
    stop_service,
)
from .state import StateStore
from .tailscale import (
    connect_tailscale,
    disconnect_tailscale,
    restart_tailscale,
    start_tailscale,
    stop_tailscale,
    tailscale_status,
)
from .uninstall import uninstall


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bc250-llm-mode", description="BC250 local LLM setup and chat")
    parser.add_argument("--state", help="Override the state JSON path")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("setup", help="Open/resume the native setup wizard")
    sub.add_parser("repair", help="Open the native wizard at hardware validation")
    sub.add_parser("chat", help="Start terminal chat")
    sub.add_parser("status", help="Print hardware and server status")
    llm = sub.add_parser("llm", help="Manage the single systemd-owned model server")
    llm.add_argument("action", choices=("start", "stop", "restart", "status"))
    webui = sub.add_parser("webui", aliases=["openwebui"], help="Manage optional Open WebUI")
    webui.add_argument("action", choices=("start", "stop", "restart", "status"))
    tailscale = sub.add_parser("tailscale", help="Manage optional Tailscale")
    tailscale.add_argument(
        "action", choices=("start", "stop", "restart", "status", "connect", "disconnect")
    )
    models = sub.add_parser("models", help="List, scan, or select models")
    models.add_argument("action", choices=("list", "scan", "use"))
    models.add_argument("model_id", nargs="?")
    context = sub.add_parser("ctx", aliases=["context"], help="Change context size and restart safely")
    context.add_argument("tokens", type=int)
    boot = sub.add_parser("boot-policy", help="Show or enforce safe desktop behavior for next boot")
    boot.add_argument("action", choices=("status", "desktop"), nargs="?", default="status")
    logs = sub.add_parser("logs", help="Tail setup or model-server logs")
    logs.add_argument("kind", choices=("server", "setup"), nargs="?", default="server")
    logs.add_argument("--lines", type=int, default=120)
    sub.add_parser("llm-mode", help="Return the configured machine to dedicated LLM Mode")
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
        logger = configure_logging(state["logs_dir"])
        quiet_runner = CommandRunner(logger)
        result["llm_service"] = service_status(state, quiet_runner)
        result["openwebui"] = open_webui_status(state, quiet_runner)
        result["tailscale"] = tailscale_status(quiet_runner)
        if result["llm_service"].get("active"):
            try:
                result["server"] = health_check(state, timeout=3)
            except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as exc:
                result["server"] = {"healthy": False, "error": str(exc)}
        print(json.dumps(result, indent=2))
        return 0 if report.valid else 2
    logger = configure_logging(state["logs_dir"])
    runner = CommandRunner(logger, print)
    if args.command == "llm":
        require_acknowledgment(state)
        actions = {
            "start": lambda: start_service(state, runner),
            "stop": lambda: stop_service(state, runner),
            "restart": lambda: restart_and_wait(state, runner),
            "status": lambda: service_status(state, runner),
        }
        print(json.dumps(actions[args.action](), indent=2))
        return 0
    if args.command in {"webui", "openwebui"}:
        require_acknowledgment(state)
        actions = {
            "start": lambda: start_open_webui(state, runner),
            "stop": lambda: stop_open_webui(state, runner),
            "restart": lambda: restart_open_webui(state, runner),
            "status": lambda: open_webui_status(state, runner),
        }
        result = actions[args.action]()
        store.save(state)
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "tailscale":
        actions = {
            "start": lambda: start_tailscale(runner),
            "stop": lambda: stop_tailscale(runner),
            "restart": lambda: restart_tailscale(runner),
            "status": lambda: tailscale_status(runner),
            "connect": lambda: connect_tailscale(runner),
            "disconnect": lambda: disconnect_tailscale(runner),
        }
        print(json.dumps(actions[args.action](), indent=2))
        return 0
    if args.command == "models":
        if args.action == "list":
            print(json.dumps(state.get("installed_models", []), indent=2))
            return 0
        discovery = discover_local_models(state)
        if args.action == "scan":
            print(json.dumps({
                "models": [item.to_dict() for item in discovery.models],
                "rejected": discovery.rejected,
                "roots": discovery.roots,
            }, indent=2))
            return 0
        if not args.model_id:
            raise ValueError("models use requires an installed or discovered model id")
        require_acknowledgment(state)
        if args.model_id in {item.get("id") for item in state.get("installed_models", [])}:
            switch_model(store, state, args.model_id, runner)
        else:
            register_and_switch_local(store, state, args.model_id, runner)
        return 0
    if args.command in {"ctx", "context"}:
        require_acknowledgment(state)
        print(change_context(store, state, args.tokens, runner))
        return 0
    if args.command == "boot-policy":
        if args.action == "desktop":
            require_acknowledgment(state)
            stage_desktop_boot(state, runner)
            store.save(state)
        default_target = runner.run(["systemctl", "get-default"], check=False).stdout.strip()
        print(json.dumps({
            "policy": state.get("boot_policy", "desktop"),
            "next_boot_target": default_target or "unknown",
            "llm_autostart": False,
            "current_llm_service": service_status(state, runner),
        }, indent=2))
        return 0
    if args.command == "logs":
        if not 1 <= args.lines <= 1000:
            raise ValueError("--lines must be from 1 to 1000")
        path = (
            str(state.get("server_log", "/var/log/bc250-llm-server.log"))
            if args.kind == "server"
            else str(state["logs_dir"] + "/setup.log")
        )
        result = runner.run(["tail", "-n", str(args.lines), path], check=False)
        if result.returncode:
            raise RuntimeError(f"Could not read {path}")
        return 0
    if args.command == "llm-mode":
        require_acknowledgment(state)
        apply_llm_mode(state, runner)
        if state.get("setup_complete") and state.get("current_model"):
            install_service(state, runner)
        store.save(state)
        runner.emit("LLM Mode is active for this boot; reboot returns to desktop with no LLM auto-start.")
        return 0
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
        switch_model(store, state, args.model_id, runner)
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
