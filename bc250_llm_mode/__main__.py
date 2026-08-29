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
from .disclaimer import require_acknowledgment
from .hardware import detect_hardware
from .local_models import discover_local_models
from .logging_utils import CommandRunner, configure_logging
from .memory_profile import analyze_memory_profile
from .model_manager import (
    change_context,
    change_parallel_slots,
    register_and_switch_local,
    switch_model,
)
from .openwebui import (
    open_webui_status,
    restart_open_webui,
    start_open_webui,
    stop_open_webui,
)
from .optimize import kv_scale_for_settings, parallel_slots_for_settings
from .paths import AppPaths
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
    parser.add_argument(
        "--state",
        help="Deprecated: a legacy state.json source, accepted only with 'repair-retry'",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("setup", help="Open/resume the native setup wizard")
    gui = sub.add_parser("gui", help="Open or activate the native management window")
    gui.add_argument(
        "--route",
        choices=("home", "models", "chat", "activity", "system", "settings", "help", "connections", "profiles", "maintenance", "maintenance/repair", "maintenance/updates"),
        default=None,
    )
    desktop_integration = sub.add_parser(
        "desktop-integration", help="Manage the user-local application-menu launcher"
    )
    desktop_integration.add_argument("action", choices=("status", "plan", "install", "remove"))
    sub.add_parser("repair", help="Open the native wizard at hardware validation")
    sub.add_parser(
        "repair-status",
        help="Report why the state migration requires repair (repair mode)",
    )
    sub.add_parser(
        "repair-retry",
        help="Retry legacy-state migration after fixing the JSON source",
    )
    import_state = sub.add_parser(
        "import-state",
        help=(
            "Publish a legacy state.json file as the SQLite runtime state "
            "(one-time; import-only — legacy JSON is never a live store)"
        ),
    )
    import_state.add_argument(
        "state_path", help="Path to the legacy state.json import source"
    )
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
    gateway = sub.add_parser(
        "gateway", help="Manage the authenticated integration gateway credential (ADR 005 D3)"
    )
    gateway.add_argument(
        "action", choices=("status", "provision", "rotate", "revoke", "verify")
    )
    gateway.add_argument(
        "--secret", help="Use an explicit secret (test/tooling only; prefer generated)"
    )
    connections = sub.add_parser(
        "connections",
        help="Configure independently revocable OpenAI-compatible clients",
    )
    connection_sub = connections.add_subparsers(
        dest="connection_action", required=True)
    connection_sub.add_parser("status", help="Show the guided connection snapshot")
    connection_sub.add_parser("clients", help="List redacted client metadata")
    add_client = connection_sub.add_parser(
        "add-client", help="Create a client and reveal its API key once")
    add_client.add_argument("--label", required=True)
    add_client.add_argument(
        "--type", dest="client_type", default="openai",
        choices=("openwebui", "pocketpal", "openai", "curl", "sse"))
    add_client.add_argument(
        "--scope", dest="scopes", action="append",
        choices=("models:list", "inference:read", "inference:stream"))
    rotate_client = connection_sub.add_parser(
        "rotate-client", help="Rotate one client and reveal the new API key once")
    rotate_client.add_argument("client_id")
    rotate_client.add_argument("--overlap-seconds", type=int, default=0)
    revoke_client = connection_sub.add_parser(
        "revoke-client", help="Revoke exactly one client")
    revoke_client.add_argument("client_id")
    connection_sub.add_parser(
        "disable-all", help="Emergency-disable and revoke every remote API client")
    instructions = connection_sub.add_parser(
        "instructions", help="Print exact redacted settings for a client type")
    instructions.add_argument(
        "client_type",
        choices=("openwebui", "pocketpal", "openai", "curl", "python", "sse"))
    connection_test = connection_sub.add_parser(
        "test", help="Run bounded positive and required negative endpoint probes")
    connection_test.add_argument("client_id")
    sub.add_parser(
        "home",
        help="Print the unified appliance home snapshot (query-only) as JSON",
    )
    platform = sub.add_parser(
        "platform",
        help="Detect host compatibility and show reviewed package/boot plans",
    )
    platform.add_argument(
        "action", choices=("status", "plan"), nargs="?", default="status"
    )
    support_bundle = sub.add_parser(
        "support-bundle",
        help="Write a redacted-by-construction support bundle to a directory",
    )
    support_bundle.add_argument(
        "--output", required=True, help="Directory to write the bundle into"
    )
    support_bundle.add_argument(
        "--keep-model-filenames",
        action="store_true",
        help="Do not replace model filenames with <model-N> labels",
    )
    models = sub.add_parser("models", help="List, scan, search, recommend, or select models")
    models.add_argument("action", choices=("list", "scan", "search", "recommend", "use", "library"))
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
    llamacpp = sub.add_parser("llamacpp", help="Manage the pinned llama.cpp Vulkan build")
    llamacpp.add_argument("action", choices=("status", "update", "rollback",
                                             "resume"))
    llamacpp.add_argument("--tag", help="Update to a specific upstream tag instead of the shipped pin")
    llamacpp.add_argument("--operation-id", help="Operation id for explicit resume", default=None)
    llamacpp.add_argument("--detach", action="store_true",
                          help="Hand the operation to one detached worker "
                               "host instead of running in the foreground")
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
    remove_model = sub.add_parser(
        "remove-model",
        help="Remove an installed model alias (dry-run by default; quarantine, never delete)",
    )
    remove_model.add_argument("alias")
    remove_model.add_argument(
        "--yes",
        action="store_true",
        help="Actually perform the removal (default is a dry-run impact report)",
    )
    storage = sub.add_parser(
        "storage",
        help="Capacity/dedup report and ranked cleanup suggestions (never deletes)",
    )
    storage.add_argument("action", choices=("report", "cleanup"))
    convert = sub.add_parser(
        "convert-model",
        help="Convert a model to another quantization (unavailable until a verified converter is provisioned)",
    )
    convert.add_argument("source_alias")
    convert.add_argument("target_quantization")
    # C2 (ADR 006): durable backup create/list/verify + restore inspect/start.
    backup = sub.add_parser(
        "backup",
        help="Create, list, or verify a durable profile backup",
    )
    backup.add_argument("action", choices=("create", "list", "verify"))
    backup.add_argument(
        "destination", nargs="?", default=None,
        help="create: relative backup label; verify: backup id")
    backup.add_argument(
        "--include-models", action="store_true",
        help="include model bytes (excluded by default)")
    backup.add_argument(
        "--include-runtime", action="store_true",
        help="include runtime bytes (excluded by default)")
    backup.add_argument(
        "--encrypt", action="store_true",
        help="request encryption (refused fail-closed until reviewed crypto)")
    restore = sub.add_parser(
        "restore",
        help="Inspect or start a durable profile restore",
    )
    restore.add_argument("action", choices=("inspect", "start", "status"))
    restore.add_argument(
        "backup", nargs="?", default=None,
        help="inspect/start: backup id; status: operation id")
    restore.add_argument(
        "--confirmation-digest", default=None,
        help="start: the digest from `restore inspect` (binds the restore)")
    switch = sub.add_parser("switch", help="Switch the single server service to an installed model")
    switch.add_argument("model_id")
    desktop = sub.add_parser(
        "desktop-mode",
        aliases=["desktop"],
        help="Restore the normal Linux desktop mode without deleting models",
    )
    desktop.add_argument(
        "--now",
        action="store_true",
        help="Start graphical.target immediately as well as making it the boot default",
    )
    revert = sub.add_parser("uninstall", help="Revert LLM Mode and remove the service")
    revert.add_argument("--remove-container", action="store_true")
    revert.add_argument("--remove-models", action="store_true", help="Permanently delete downloaded model files")
    from .operations_cli import register_operations_parser

    register_operations_parser(sub)
    return parser


def _repair_entry(args, application) -> int:
    """Repair-mode entry: only migration diagnosis is permitted here.

    ``repair-retry`` is handled by the caller (it must run before the gate
    because a successful retry composes an operational application).
    """
    import sys as _sys

    if args.command == "repair-status":
        print(json.dumps({
            "repair_required": True,
            "reason": application.repair_reason,
            "database": str(application.paths.database_path),
            "legacy_state": str(application.paths.legacy_state_path),
        }, indent=2))
        return 0
    print(
        json.dumps({
            "error": "repair required",
            "reason": application.repair_reason,
            "allowed_commands": ["repair-status", "repair-retry"],
        }, indent=2),
        file=_sys.stderr,
    )
    return 78  # EX_CONFIG: the installation cannot operate until repaired


def _import_state_entry(args) -> int:
    """One-time publication of an explicit legacy JSON file as SQLite state.

    The database is the sole runtime store afterwards; the JSON source is
    left byte-identical on disk. An existing database is never overwritten.
    """
    from .app import Application
    from .legacy_import import LegacyImportError, LegacyImporter

    paths = AppPaths.for_home()
    if paths.database_path.exists():
        print(json.dumps({
            "imported": False,
            "reason": "a database is already published; it will not be overwritten",
            "database": str(paths.database_path),
        }, indent=2))
        return 0
    logger = configure_logging(paths.logs_dir)
    runner = CommandRunner(logger)
    try:
        LegacyImporter(paths, runner).import_legacy(source=Path(args.state_path).expanduser())
    except LegacyImportError as exc:
        logger.error("Legacy state import failed: %s", exc)
        print(json.dumps({"imported": False, "reason": str(exc)}, indent=2))
        return 78
    published = Application.compose(paths)
    if not published.operational:
        print(json.dumps({"imported": False, "reason": published.repair_reason}, indent=2))
        return 78
    print("Legacy state migration succeeded; database published.")
    print("Run 'bc250-llm-mode setup' to continue.")
    return 0


def _retry_import_from(application, source: Path) -> int:
    """repair-retry against an explicit --state source (deprecated alias)."""
    from .app import Application
    from .legacy_import import LegacyImportError, LegacyImporter

    runner = CommandRunner(configure_logging(application.paths.logs_dir))
    try:
        LegacyImporter(application.paths, runner).import_legacy(source=source.expanduser())
    except LegacyImportError as exc:
        print(json.dumps({"repair_required": True, "reason": str(exc)}, indent=2))
        return 78
    retried = Application.compose(application.paths)
    if not retried.operational:
        print(json.dumps({"repair_required": True, "reason": retried.repair_reason}, indent=2))
        return 78
    print("Legacy state migration succeeded; database published.")
    print("Run 'bc250-llm-mode setup' to continue.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # Composition root: one validated path profile drives every surface.
    # SQLite is the sole runtime state; legacy JSON is import-only.
    from .app import Application

    # --state is a deprecated alias accepted only for repair-retry
    # (R1/R2 exit plan 6.2); every other use is rejected before any file
    # is touched so no parallel JSON-backed runtime can be created.
    if args.state and args.command != "repair-retry":
        print(
            "--state is no longer a runtime mode; legacy JSON is import-only.\n"
            "Use 'import-state PATH' to publish a legacy state.json instead.",
            file=sys.stderr,
        )
        return 2

    if args.command == "import-state":
        return _import_state_entry(args)

    if args.command == "platform":
        # This diagnostic is intentionally pre-composition: it creates no
        # profile directories or database and remains usable during repair.
        from .host_platform import HostPlatformService

        payload = HostPlatformService.detect().status()
        if args.action == "plan":
            payload = {
                "schema_version": payload["schema_version"],
                "distribution": payload["distribution"],
                "integration_tier": payload["integration_tier"],
                "boot_manager": payload["boot_manager"],
                "persistent_kernel_policy": payload["persistent_kernel_policy"],
                "plans": payload["plans"],
                "blockers": payload["blockers"],
            }
        print(json.dumps(payload, indent=2))
        return 2 if payload["integration_tier"] == "unsupported" else 0

    application = Application.compose()
    if args.command == "repair-retry":
        if not application.operational and args.state:
            return _retry_import_from(application, Path(args.state))
        if not application.operational:
            print(json.dumps({
                "repair_required": True,
                "reason": application.repair_reason,
            }, indent=2))
            return 78
        print("Legacy state migration succeeded; database published.")
        print("Run 'bc250-llm-mode setup' to continue.")
        return 0
    if not application.operational:
        return _repair_entry(args, application)
    if args.command == "operations":
        from .operations_cli import run_operations_command

        return run_operations_command(application, args)
    state = application.read_model()
    # ADR 005 D3: refresh durable gateway view fields into the working snapshot
    # for anything that shares/Open WebUI depends on (credential file, verified
    # state, last_verified_at) without persisting them here.
    if args.command in {"gateway", "serve", "share", "webui", "openwebui"}:
        application.gateway.write_state_fields(state)
        application.connection_credentials.write_state_fields(state)
    if args.command in (None, "setup", "gui"):
        if not bootstrap_tkinter(application):
            return 0
        from .gui import run_gui
        run_gui(
            application,
            management=bool(state.get("setup_complete")),
            route=getattr(args, "route", None),
        )
        return 0
    if args.command == "repair":
        if not bootstrap_tkinter(application):
            return 0
        from .gui import run_gui
        if application.setup is not None:
            reset = application.setup.reset_for_repair("repair command")
            state.update(setup_complete=False, setup_phase=reset["phase"])
        else:
            state.update(setup_complete=False, setup_phase=0)
        run_gui(application, management=bool(state.get("setup_complete")))
        return 0
    if args.command == "chat":
        require_acknowledgment(state)
        run_chat(application)
        return 0
    if args.command == "desktop-integration":
        service = application.desktop_integration
        if args.action == "status":
            payload = service.status()
        elif args.action == "plan":
            payload = service.plan()
        elif args.action == "install":
            payload = service.install()
        else:
            payload = service.remove()
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "status":
        report = detect_hardware(state["models_dir"])
        result: dict[str, object] = {
            "hardware": report.to_dict(),
            "platform": application.platform.status(),
            "state": state,
        }
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
        report: dict[str, Any] = {
            "state_path": str(application.paths.database_path),
            "state_readable": True,
            "platform": application.platform.status(),
        }
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
            "on_pin": bool(describe),
            "build_id": build.get("build_id") if isinstance(build, dict) else None,
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
        # P5 §11.3: structured read-only findings with stable IDs, severity,
        # evidence, and a recommended command. Merged alongside the legacy
        # probe report so existing consumers keep working.
        try:
            doctor_report = application.doctor.run()
            report["doctor"] = doctor_report.to_dict()
            report["findings"] = [f.to_dict() for f in doctor_report.findings]
            report["overall"] = doctor_report.overall
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            report["doctor_error"] = str(exc)
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

        record_benchmark(application, state, result)
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
        before = dict(state)
        try:
            result = actions[args.action]()
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if args.action != "status":
            application.commit_settings_changes(before, state)
        print(json.dumps(result, indent=2, default=str))
        return 0
    if args.command == "autotune":
        require_acknowledgment(state)
        report = autotune(
            application, state, runner,
            runtime_service=application.runtime_config,
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
            print(json.dumps(run_watchdog_once(application, state, runner), indent=2))
        elif args.action == "reset":
            from .thermals import reset_latch

            print(json.dumps(
                reset_latch(application, state, runner),
                indent=2,
            ))
        else:
            watch_loop(application, state, runner, interval_sec=max(1.0, args.interval))
        return 0
    if args.command == "llamacpp":
        # U1.2: ONE durable runtime lifecycle route (ADR 004 D11).
        lifecycle = application.runtime_lifecycle
        if args.action == "status":
            print(json.dumps(lifecycle.status(), indent=2, default=str))
            return 0
        require_acknowledgment(state)
        try:
            if args.action == "update":
                outcome = lifecycle.update(
                    requested_ref=getattr(args, "tag", None),
                    requested_by="cli",
                    detach=bool(getattr(args, "detach", False)),
                )
            elif args.action == "rollback":
                outcome = lifecycle.rollback(requested_by="cli")
            else:
                outcome = lifecycle.resume(args.operation_id)
        except KeyboardInterrupt:
            # §15.3: second Ctrl-C — the operation is durably PAUSED and
            # resumable; nothing was killed mid-effect.
            print(json.dumps({
                "status": "PAUSED",
                "operation_id": lifecycle.status().get("active_operation", {})
                .get("operation_id"),
                "foreground_only": True,
                "reason": "Interrupted by Ctrl-C; the durable operation is "
                          "paused safely. Resume with: bc250-llm-mode "
                          "llamacpp resume --operation-id <id>",
            }, indent=2))
            return 130
        exit_code = _lifecycle_exit_code(outcome)
        print(json.dumps(outcome.to_dict(), indent=2, default=str))
        return exit_code
    if args.command in {"webui", "openwebui"}:
        require_acknowledgment(state)
        svc = application.openwebui
        actions = {
            "start": lambda: svc.start(state, runner),
            "stop": lambda: svc.stop(state, runner),
            "restart": lambda: svc.restart(state, runner),
            "status": lambda: svc.status(state, runner),
        }
        result = actions[args.action]()
        # Persistence owner: OpenWebUIService committed internally; the CLI
        # only refreshes nothing here — state is a disposable snapshot.
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
        svc = application.sharing
        actions = {
            "start": lambda: svc.start(state, runner),
            "stop": lambda: svc.stop(state, runner),
            "restart": lambda: svc.start(state, runner),
            "status": lambda: https_sharing_status(state, runner),
        }
        result = actions[args.action]()
        # Persistence owner: SharingService committed internally.
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "gateway":
        if args.action in {"provision", "rotate", "revoke"}:
            require_acknowledgment(state)
        svc = application.gateway
        if args.action == "status":
            result = svc.write_state_fields(state)
        elif args.action == "provision":
            r = svc.provision(secret=args.secret)
            result = {
                "provisioned": r.provisioned,
                "fingerprint_prefix": r.fingerprint[:8],
                "credential_file": r.credential_file,
                "scopes": r.scopes,
            }
        elif args.action == "rotate":
            r = svc.rotate(secret=args.secret)
            result = {
                "rotated": True,
                "fingerprint_prefix": r.fingerprint[:8],
                "credential_file": r.credential_file,
                "scopes": r.scopes,
            }
        elif args.action == "revoke":
            result = svc.revoke()
        else:  # verify
            result = svc.verify()
        # Mutations persisted internally by the durable service.
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "connections":
        from .connection_setup import CLIENT_SCOPES, instructions_for

        action = args.connection_action
        credentials = application.connection_credentials
        if action == "status":
            print(json.dumps(application.connections.snapshot().to_dict(), indent=2))
            return 0
        if action == "clients":
            print(json.dumps({
                "access": credentials.access_state(),
                "clients": credentials.list_clients(),
            }, indent=2))
            return 0
        if action == "instructions":
            snapshot = application.connections.snapshot().to_dict()
            print(json.dumps(instructions_for(
                args.client_type, urls=snapshot["urls"],
                public_alias=snapshot["model"].get("public_alias")), indent=2))
            return 0
        if action == "test":
            snapshot = application.connections.snapshot(client_id=args.client_id).to_dict()
            alias = snapshot["model"].get("public_alias")
            if not alias:
                raise RuntimeError(
                    "The running model has no safe observed public alias; start and verify it first.")
            result = application.connection_probes.run(
                client_id=args.client_id, public_alias=alias,
                tailnet_base_url=snapshot["urls"].get("base_url"),
            )
            print(json.dumps(result.to_dict(), indent=2))
            return 0 if result.passed else 1

        require_acknowledgment(state)
        if action in {"add-client", "rotate-client"} and not sys.stderr.isatty():
            raise RuntimeError(
                "Creating or rotating a client requires an interactive terminal "
                "so the API key can be shown exactly once.")
        if action == "add-client":
            result = credentials.add_client(
                label=args.label, client_kind=args.client_type,
                scopes=args.scopes or CLIENT_SCOPES)
        elif action in {"rotate-client", "revoke-client"}:
            records = {
                item["client_id"]: item for item in credentials.list_clients()
            }
            current = records.get(args.client_id)
            if current is None:
                raise ValueError("connection client does not exist")
            if action == "rotate-client":
                result = credentials.rotate_client(
                    args.client_id, expected_revision=int(current["revision"]),
                    overlap_seconds=args.overlap_seconds)
            else:
                result = credentials.revoke_client(
                    args.client_id, expected_revision=int(current["revision"]))
        else:  # disable-all
            access = credentials.access_state()
            print(json.dumps(credentials.disable_all(
                expected_revision=int(access["revision"])), indent=2))
            return 0

        # stdout remains machine-readable and redacted.  The one-time secret
        # goes only to the controlling terminal stream, never JSON/log/state.
        print(json.dumps(result.to_dict(), indent=2))
        if result.secret is not None:
            print("API key (shown once):", result.secret, file=sys.stderr)
        return 0
    if args.command == "home":
        # P5 §11.1: query-only snapshot; identical source for CLI/GUI/bundle.
        print(json.dumps(application.home.snapshot().to_dict(), indent=2))
        return 0
    if args.command == "support-bundle":
        # P5 §11.3: redacted-by-construction export. Reuses the composed
        # home/doctor services so the bundle matches every other surface.
        from .support_bundle import SupportBundleService

        service = SupportBundleService(
            application.units, application.paths,
            home=application.home, doctor=application.doctor,
            platform=application.platform,
            redact_model_filenames=not args.keep_model_filenames,
        )
        manifest = service.build(args.output)
        print(json.dumps(manifest.to_dict(), indent=2))
        return 0
    if args.command == "models":
        if args.action == "list":
            print(json.dumps(state.get("installed_models", []), indent=2))
            return 0
        if args.action == "library":
            # P6 §12.1: the Model Library read model (query-only).
            ctx = args.ctx or int(state.get("current_ctx", 8192))
            slots = args.slots or parallel_slots_for_settings(
                state.get("optimizations"))
            print(json.dumps(
                application.model_library.to_dict(context=ctx, slots=slots),
                indent=2))
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
            switch_model(application, state, args.model_id, runner)
        else:
            register_and_switch_local(application, state, args.model_id, runner)
        return 0
    if args.command in {"ctx", "context"}:
        require_acknowledgment(state)
        print(change_context(application, state, args.tokens, runner))
        return 0
    if args.command in {"slots", "users"}:
        require_acknowledgment(state)
        print(change_parallel_slots(application, state, args.count, runner))
        return 0
    if args.command == "boot-policy":
        if args.action == "desktop":
            require_acknowledgment(state)
            application.host_mode.enforce_desktop_next_boot(state, runner)
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
        application.host_mode.enter_llm_mode(
            state, runner,
            install_service_fn=install_service,
            install=bool(state.get("setup_complete") and state.get("current_model")),
        )
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
        # U1.1: durable acquisition through the ONE composed command; the
        # old synchronous download/prepare route is deleted.
        outcome = application.model_acquisition.acquire_catalog(
            model.id, quant, requested_by="cli"
        )
        if not outcome.ok:
            print(
                f"Acquisition did not complete: {outcome.status} "
                f"(operation {outcome.operation_id})"
            )
            return 1
        alias = outcome.detail.get("alias") or model.id
        if state.get("setup_complete"):
            # Durable activation through the ONE composed command.
            switch_model(application, state, alias, runner)
        return 0
    if args.command == "remove-model":
        # P6 §12.2: dry-run by default; --yes performs the durable removal.
        plan = application.model_remove.dry_run(args.alias)
        if not args.yes:
            print(json.dumps(plan, indent=2))
            return 0
        require_acknowledgment(state)
        outcome = application.model_remove.remove(args.alias, requested_by="cli")
        print(json.dumps(outcome.to_dict(), indent=2))
        return 0 if outcome.ok else 1
    if args.command == "storage":
        # P6 §12.3: capacity/dedup report + ranked cleanup (never deletes).
        if args.action == "report":
            print(json.dumps(application.storage_capacity.report(), indent=2))
            return 0
        print(json.dumps(application.storage_capacity.dry_run_cleanup(), indent=2))
        return 0
    if args.command == "convert-model":
        # P6 §12.4: honestly unavailable until a verified converter exists.
        outcome = application.model_convert.convert(
            args.source_alias, args.target_quantization, requested_by="cli")
        print(json.dumps(outcome.to_dict(), indent=2))
        return 0 if outcome.ok else 1
    if args.command == "backup":
        # C2 (ADR 006): create/list/verify a durable profile backup.
        if args.action == "list":
            print(json.dumps(application.backup.list_backups(), indent=2))
            return 0
        if args.action == "verify":
            if not args.destination:
                print("backup verify requires a backup id", file=sys.stderr)
                return 2
            print(json.dumps(
                application.backup.verify_backup(args.destination), indent=2))
            return 0
        # create
        if not args.destination:
            print("backup create requires a destination label", file=sys.stderr)
            return 2
        outcome = application.backup.create_backup(
            args.destination,
            include_models=args.include_models,
            include_runtime=args.include_runtime,
            encrypt=args.encrypt,
            requested_by="cli")
        print(json.dumps(outcome.to_dict(), indent=2))
        return 0 if outcome.ok else 1
    if args.command == "restore":
        # C2 (ADR 006): inspect (dry-run) or start a durable profile restore.
        if args.action == "inspect":
            if not args.backup:
                print("restore inspect requires a backup id", file=sys.stderr)
                return 2
            print(json.dumps(
                application.backup.restore_inspect(args.backup), indent=2))
            return 0
        if args.action == "status":
            if not args.backup:
                print("restore status requires an operation id", file=sys.stderr)
                return 2
            detail = application.operation_query.show(args.backup)
            if detail is None:
                print(json.dumps({"operation_id": args.backup,
                                  "found": False}, indent=2))
                return 1
            print(json.dumps(detail.to_dict(), indent=2))
            return 0
        # start
        if not args.backup or not args.confirmation_digest:
            print("restore start requires a backup id and "
                  "--confirmation-digest", file=sys.stderr)
            return 2
        require_acknowledgment(state)
        outcome = application.backup.restore_start(
            args.backup, args.confirmation_digest, requested_by="cli")
        print(json.dumps(outcome.to_dict(), indent=2))
        return 0 if outcome.ok else 1
    if args.command == "switch":
        require_acknowledgment(state)
        switch_model(application, state, args.model_id, runner)
        return 0
    if args.command in {"desktop-mode", "desktop"}:
        application.host_mode.return_to_desktop(
            state, runner, activate_now=args.now
        )
        return 0
    if args.command == "uninstall":
        application.maintenance.uninstall(
            state, runner,
            remove_container=args.remove_container,
            remove_models=args.remove_models,
        )
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
