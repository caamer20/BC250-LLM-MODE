from __future__ import annotations

import argparse
import httpx
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from .bootstrap import bootstrap_tkinter
from .catalog import (
    best_quant,
    calculate_fit,
    catalog_rows,
    model_by_id,
    recommend_models,
    validation_tier,
)
from .chat import benchmark, benchmark_repeat, run_chat
from .constants import KNOWN_GOOD_LLAMACPP
from .desktop import switch_to_desktop_mode
from .disclaimer import require_acknowledgment
from .download import download_model
from .env import (
    evaluate_pin,
    llamacpp_status,
    rollback_llamacpp,
    setup_environment,
    update_llamacpp,
)
from .paths import AppPaths
from .hardware import detect_hardware
from .llmmode import apply_llm_mode, stage_desktop_boot
from .local_models import discover_local_models
from .logging_utils import CommandRunner, configure_logging
from .memory_profile import analyze_memory_profile
from .model_manager import (
    change_context,
    change_parallel_slots,
    register_and_switch_local,
    restart_with_rollback,
    switch_model,
)
from .openwebui import (
    open_webui_status,
    restart_open_webui,
    start_open_webui,
    stop_open_webui,
)
from .optimize import kv_scale_for_settings, parallel_slots_for_settings
from .prepare import prepare_model
from .server import (
    ensure_server,
    health_check,
    install_service,
    restart_and_wait,
    restart_service,
    service_status,
    start_service,
    stop_service,
)
from .sharing import https_sharing_status, start_https_sharing, stop_https_sharing
from .state import StateStore
from .thermals import read_gpu_temperature, run_watchdog_once, watch_loop
from .tune import autotune
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
    from . import __version__

    parser = argparse.ArgumentParser(
        prog="bc250-llm-mode", description="BC250 local LLM setup and chat"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--state", help="Override the state JSON path")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("setup", help="Open/resume the native setup wizard")
    sub.add_parser("repair", help="Open the native wizard at hardware validation")
    sub.add_parser("chat", help="Start terminal chat")
    sub.add_parser("status", help="Print hardware and server status")
    sub.add_parser("memory-profile", help="Analyze the boot-time BC-250 UMA split")
    llm = sub.add_parser("llm", help="Manage the single systemd-owned model server")
    llm.add_argument("action", choices=("start", "stop", "restart", "status", "ensure"))
    webui = sub.add_parser("webui", aliases=["openwebui"], help="Manage optional Open WebUI")
    webui.add_argument("action", choices=("start", "stop", "restart", "status"))
    tailscale = sub.add_parser("tailscale", help="Manage optional Tailscale")
    tailscale.add_argument(
        "action", choices=("start", "stop", "restart", "status", "connect", "disconnect")
    )
    sharing = sub.add_parser(
        "serve", aliases=["share"], help="Publish Open WebUI and the model API over tailnet HTTPS"
    )
    sharing.add_argument("action", choices=("start", "stop", "restart", "status"))
    models = sub.add_parser("models", help="List, scan, search, recommend, or select models")
    models.add_argument("action", choices=("list", "scan", "search", "recommend", "use"))
    models.add_argument("model_id", nargs="?")
    models.add_argument("--ctx", type=int, help="Context used for search/recommend fit projections")
    models.add_argument("--slots", type=int, help="Concurrent slots used for fit projections")
    models.add_argument("--tag", help="Filter recommendations by task tag (e.g. code, reasoning)")
    models.add_argument("--limit", type=int, help="Maximum number of recommendations to print")
    bench = sub.add_parser("bench", help="Measure generation speed on the running model server")
    bench.add_argument("--prompt", default="Summarize the advantages of local LLM inference in two sentences.")
    bench.add_argument("--max-tokens", type=int, default=128)
    bench.add_argument("--repeat", type=int, default=3, help="Number of runs (1-10) for min/median/max")
    sub.add_parser("doctor", help="Run local diagnostics and print a JSON report")
    autotune_p = sub.add_parser("autotune", help="Benchmark runtime combos and apply the fastest safe one")
    autotune_p.add_argument("--repeat", type=int, default=2, help="Benchmark repeats per combo (1-3)")
    autotune_p.add_argument("--max-tokens", type=int, default=96)
    thermals_p = sub.add_parser("thermals", help="GPU thermal watchdog controls")
    thermals_p.add_argument("action", choices=("status", "once", "watch", "reset"))
    thermals_p.add_argument("--interval", type=float, default=5.0)
    thermals_p.add_argument(
        "--force-reset", action="store_true",
        help="Clear the latch without requiring a safe temperature reading",
    )
    llamacpp = sub.add_parser("llamacpp", help="Manage the pinned llama.cpp Vulkan build")
    llamacpp.add_argument("action", choices=("status", "update", "rollback"))
    llamacpp.add_argument("--tag", help="Update to a specific upstream tag instead of the shipped pin")
    context = sub.add_parser("ctx", aliases=["context"], help="Change context size and restart safely")
    context.add_argument("tokens", type=int)
    slots = sub.add_parser("slots", aliases=["users"], help="Set concurrent request slots and restart safely")
    slots.add_argument("count", type=int)
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # Composition root: one validated path profile drives every surface.
    from .app import Application, load_state_with_paths

    explicit_state = bool(args.state)
    application = Application.compose(
        AppPaths.from_app_dir(Path(args.state).parent) if explicit_state else None
    )
    store = StateStore(args.state) if explicit_state else application.store
    state = load_state_with_paths(store, application.paths)
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
        result["memory_profile"] = analyze_memory_profile(report).to_dict()
        logger = configure_logging(state["logs_dir"])
        quiet_runner = CommandRunner(logger)
        result["llm_service"] = service_status(state, quiet_runner)
        result["openwebui"] = open_webui_status(state, quiet_runner)
        result["tailscale"] = tailscale_status(quiet_runner)
        result["https_sharing"] = https_sharing_status(state, quiet_runner)
        if result["llm_service"].get("active"):
            try:
                result["server"] = health_check(state, timeout=3)
            except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as exc:
                result["server"] = {"healthy": False, "error": str(exc)}
        print(json.dumps(result, indent=2))
        return 0 if report.valid else 2
    if args.command == "memory-profile":
        report = detect_hardware(state["models_dir"])
        print(json.dumps(analyze_memory_profile(report).to_dict(), indent=2))
        return 0 if report.valid else 2
    if args.command == "doctor":
        logger = configure_logging(state["logs_dir"])
        quiet_runner = CommandRunner(logger)
        report: dict[str, Any] = {"state_path": str(store.path), "state_readable": True}
        try:
            hardware = detect_hardware(state["models_dir"])
            report["hardware"] = hardware.to_dict()
            report["hardware_valid"] = bool(hardware.valid)
            report["memory_profile"] = analyze_memory_profile(hardware).to_dict()
        except (OSError, RuntimeError, ValueError) as exc:
            report["hardware_error"] = str(exc)
        try:
            llm_service = service_status(state, quiet_runner)
            report["llm_service"] = llm_service
            if llm_service.get("active"):
                try:
                    report["server"] = health_check(state, timeout=3)
                except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as exc:
                    report["server"] = {"healthy": False, "error": str(exc)}
            report["openwebui"] = open_webui_status(state, quiet_runner)
            report["tailscale"] = tailscale_status(quiet_runner)
            report["https_sharing"] = https_sharing_status(state, quiet_runner)
        except (OSError, RuntimeError, ValueError) as exc:
            report["services_error"] = str(exc)
        try:
            probe = Path(str(state["models_dir"])).expanduser()
            while not probe.exists() and probe != probe.parent:
                probe = probe.parent
            report["models_dir_free_gib"] = round(shutil.disk_usage(probe).free / (1024**3), 2)
        except OSError as exc:
            report["disk_error"] = str(exc)
        try:
            temp = read_gpu_temperature()
            report["gpu_temperature_c"] = round(temp, 1) if temp is not None else None
            report["thermal_watchdog"] = {
                "enabled": bool(state.get("optimizations", {}).get("thermal_watchdog_enabled")),
                "state": state.get("thermal_watchdog_state", "nominal"),
            }
        except (OSError, RuntimeError, ValueError) as exc:
            report["thermal_error"] = str(exc)
        build = state.get("llamacpp_build")
        describe = build.get("describe") if isinstance(build, dict) else None
        report["llamacpp"] = {
            "installed": describe,
            "pin": KNOWN_GOOD_LLAMACPP,
            "on_pin": evaluate_pin(describe),
        }
        try:
            zram_size = Path("/sys/block/zram0/disksize")
            if zram_size.exists():
                gib = int(zram_size.read_text(encoding="utf-8").strip()) / (1024**3)
                report["zram_gib"] = round(gib, 2)
                host_mib = report.get("hardware", {}).get("host_ram_mib") or 0
                if host_mib and gib > 8:
                    report.setdefault("warnings", []).append(
                        "zram is larger than 8 GiB on a ~4 GiB host; shrinking it frees CPU and RAM"
                    )
        except (OSError, ValueError) as exc:
            report["zram_error"] = str(exc)
        try:
            unit_path = f"/etc/systemd/system/{state.get('service_name', 'bc250-llm.service')}"
            unit_text = quiet_runner.run(["cat", unit_path], check=False).stdout
            report["service_memory_guard"] = "MemoryMax" in unit_text
        except (OSError, RuntimeError, ValueError) as exc:
            report["service_guard_error"] = str(exc)
        print(json.dumps(report, indent=2))
        return 0
    if args.command == "bench":
        try:
            if args.repeat > 1:
                result = benchmark_repeat(
                    state, prompt=args.prompt, max_tokens=args.max_tokens, repeat=args.repeat
                )
            else:
                result = benchmark(state, prompt=args.prompt, max_tokens=args.max_tokens)
        except (httpx.HTTPError, OSError) as exc:
            raise RuntimeError(f"Benchmark failed: {exc}. Is the model server running?") from exc
        print(json.dumps(result, indent=2))
        from .chat import record_benchmark

        record_benchmark(store, state, result)
        return 0
    logger = configure_logging(state["logs_dir"])
    runner = CommandRunner(logger, print)
    if args.command == "llm":
        if args.action != "status":
            require_acknowledgment(state)
        actions = {
            "start": lambda: start_service(state, runner),
            "stop": lambda: stop_service(state, runner),
            "restart": lambda: restart_and_wait(state, runner),
            "status": lambda: service_status(state, runner),
            "ensure": lambda: ensure_server(state, runner),
        }
        try:
            result = actions[args.action]()
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if args.action != "status":
            store.save(state)
        print(json.dumps(result, indent=2, default=str))
        return 0
    if args.command == "autotune":
        require_acknowledgment(state)
        report = autotune(
            store, state, runner,
            repeat=max(1, min(3, args.repeat)), max_tokens=max(16, min(512, args.max_tokens)),
        )
        print(json.dumps(report, indent=2))
        return 0
    if args.command == "thermals":
        temp = read_gpu_temperature()
        if args.action == "status":
            print(json.dumps({
                "temperature_c": round(temp, 1) if temp is not None else None,
                "watchdog_enabled": bool(state.get("optimizations", {}).get("thermal_watchdog_enabled")),
                "watchdog_state": state.get("thermal_watchdog_state", "nominal"),
                "throttle_c": state.get("optimizations", {}).get("thermal_throttle_c"),
                "recovery_c": state.get("optimizations", {}).get("thermal_recovery_c"),
                "stop_c": state.get("optimizations", {}).get("thermal_stop_c"),
            }, indent=2))
            return 0
        require_acknowledgment(state)
        if args.action == "once":
            print(json.dumps(run_watchdog_once(store, state, runner), indent=2))
        elif args.action == "reset":
            from .thermals import reset_latch

            print(json.dumps(
                reset_latch(store, state, runner, require_safe_temperature=not args.force_reset),
                indent=2,
            ))
        else:
            watch_loop(store, state, runner, interval_sec=max(1.0, args.interval))
        return 0
    if args.command == "llamacpp":
        if args.action == "status":
            status_runner = CommandRunner(configure_logging(state["logs_dir"]))
            print(json.dumps(llamacpp_status(state, status_runner), indent=2))
            return 0
        require_acknowledgment(state)
        if args.action == "update":
            result = update_llamacpp(state, runner, tag=args.tag)
        else:
            result = rollback_llamacpp(state, runner)
        store.save(state)
        print(json.dumps(result, indent=2, default=str))
        return 0
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
    if args.command in {"serve", "share"}:
        if args.action != "status":
            require_acknowledgment(state)
        actions = {
            "start": lambda: start_https_sharing(state, runner),
            "stop": lambda: stop_https_sharing(state, runner),
            "restart": lambda: start_https_sharing(state, runner),
            "status": lambda: https_sharing_status(state, runner),
        }
        result = actions[args.action]()
        store.save(state)
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "models":
        if args.action == "list":
            print(json.dumps(state.get("installed_models", []), indent=2))
            return 0
        if args.action == "search":
            ctx = args.ctx or int(state.get("current_ctx", 8192))
            slots = args.slots or parallel_slots_for_settings(state.get("optimizations"))
            results = catalog_rows(
                args.model_id or "",
                ctx_tokens=ctx,
                kv_scale=kv_scale_for_settings(state.get("optimizations")),
                parallel_slots=slots,
            )
            print(json.dumps({
                "query": args.model_id or "",
                "context_tokens": ctx,
                "parallel_slots": slots,
                "count": len(results),
                "results": results,
            }, indent=2))
            return 0
        if args.action == "recommend":
            ctx = args.ctx or int(state.get("current_ctx", 8192))
            slots = args.slots or parallel_slots_for_settings(state.get("optimizations"))
            ranked = recommend_models(
                ctx,
                parallel_slots=slots,
                kv_scale=kv_scale_for_settings(state.get("optimizations")),
                tag=args.tag,
                limit=args.limit or 8,
            )
            print(json.dumps({
                "context_tokens": ctx,
                "parallel_slots": slots,
                "tag": args.tag,
                "count": len(ranked),
                "results": [
                    {
                        "id": model.id,
                        "display_name": model.display_name,
                        "quant": quant,
                        "validation_tier": validation_tier(model),
                        "verdict": fit.verdict,
                        "required_gib": round(fit.required_gib, 2),
                        "detail": fit.detail,
                    }
                    for model, quant, fit in ranked
                ],
            }, indent=2))
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
    if args.command in {"slots", "users"}:
        require_acknowledgment(state)
        print(change_parallel_slots(store, state, args.count, runner))
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
        kv_scale = kv_scale_for_settings(state.get("optimizations"))
        slots = parallel_slots_for_settings(state.get("optimizations"))
        if args.quant:
            quant = args.quant
            fit = calculate_fit(model, quant, args.ctx, kv_scale=kv_scale, parallel_slots=slots)
            if fit.verdict == "NO-FIT":
                raise RuntimeError(fit.detail)
        else:
            pick = best_quant(model, args.ctx, kv_scale=kv_scale, parallel_slots=slots)
            if pick is None:
                raise RuntimeError(
                    f"No quantization of {model.display_name} fits {args.ctx} tokens; "
                    "lower --ctx or choose a smaller model"
                )
            quant, fit = pick
        previous = {
            "current_model": state.get("current_model"),
            "current_ctx": state.get("current_ctx", 8192),
        }
        downloaded = download_model(state, model, quant, runner)
        prepare_model(state, model, quant, downloaded, runner)
        state["current_ctx"] = args.ctx
        store.save(state)
        if state.get("setup_complete"):
            restart_with_rollback(
                store, state, runner, previous, f"Activating {model.display_name}"
            )
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
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except (RuntimeError, PermissionError, KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
