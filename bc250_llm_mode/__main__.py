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
    advertised_model_by_id,
    best_quant,
    calculate_fit,
    catalog_rows,
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
    from .notifications import CATEGORIES, MASTER

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
    post_update = sub.add_parser("_post-update", help=argparse.SUPPRESS)
    post_update.add_argument("--operation-id", required=True)
    post_update.add_argument("--release-set-digest", required=True)
    post_update.add_argument("--nonce", required=True)
    post_update.add_argument("--target-schema", required=True, type=int)
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
    repair = sub.add_parser(
        "repair", help="Open Repair or use typed preview/run/verify commands"
    )
    repair_sub = repair.add_subparsers(dest="repair_action")
    repair_sub.add_parser("list", help="List typed repair actions")
    for repair_verb in ("preview", "run", "verify"):
        command = repair_sub.add_parser(
            repair_verb, help=f"{repair_verb.title()} one typed repair action"
        )
        command.add_argument("action_id")
        command.add_argument("target", nargs="?")
        if repair_verb == "run":
            command.add_argument("--preview", required=True)
            command.add_argument("--confirm", required=True)
    undo = sub.add_parser(
        "undo", help="List or execute evidence-backed exact inverses"
    )
    undo_sub = undo.add_subparsers(dest="undo_action", required=True)
    undo_sub.add_parser("list", help="List currently available exact inverses")
    undo_preview = undo_sub.add_parser("preview", help="Preview one exact inverse")
    undo_preview.add_argument("undo_id")
    undo_run = undo_sub.add_parser("run", help="Run one exact inverse")
    undo_run.add_argument("undo_id")
    undo_run.add_argument("--preview", required=True)
    undo_run.add_argument("--confirm", required=True)
    update = sub.add_parser(
        "update", help="Check, import, apply, roll back, or inspect application updates"
    )
    update_sub = update.add_subparsers(dest="update_action", required=True)
    update_sub.add_parser("status", help="Show installed slot and signed-channel status")
    update_sub.add_parser("check", help="Explicitly check the configured signed channel")
    update_preview = update_sub.add_parser(
        "preview", help="Preview one immutable verified release"
    )
    update_preview.add_argument("version")
    update_apply = update_sub.add_parser(
        "apply", help="Apply an exact, fresh update preview"
    )
    update_apply.add_argument("version")
    update_apply.add_argument("--preview", required=True)
    update_apply.add_argument("--confirm", required=True)
    update_import = update_sub.add_parser(
        "import-bundle", help="Import an explicitly selected signed offline bundle"
    )
    update_import.add_argument("path")
    update_rollback = update_sub.add_parser(
        "rollback", help="Restore the exact verified previous application slot"
    )
    update_rollback.add_argument("--preview")
    update_rollback.add_argument("--confirm")
    update_cleanup = update_sub.add_parser(
        "cleanup", help="List update artifacts eligible for typed cleanup"
    )
    update_cleanup.add_argument("--dry-run", action="store_true", required=True)
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
    webui.add_argument(
        "action", choices=("start", "stop", "restart", "status", "update")
    )
    tailscale = sub.add_parser("tailscale", help="Manage optional Tailscale")
    tailscale.add_argument(
        "action", choices=("start", "stop", "restart", "status", "connect", "disconnect")
    )
    sharing = sub.add_parser(
        "serve", aliases=["share"], help="Publish Open WebUI and the model API over tailnet HTTPS"
    )
    sharing.add_argument("action", choices=("start", "stop", "restart", "status"))
    gateway = sub.add_parser(
        "gateway", help="Manage gateway credentials and the current-boot runtime"
    )
    gateway.add_argument(
        "action", choices=("status", "provision", "rotate", "revoke", "verify", "service")
    )
    gateway.add_argument(
        "service_action", nargs="?",
        choices=("plan", "install", "status", "start", "stop", "restart", "remove"),
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
    connection_sub.add_parser(
        "doctor",
        help="Explain the first failed connection step and its safe next action",
    )
    connection_sub.add_parser(
        "capabilities", help="Show the versioned supported API/client matrix")
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
    connection_setup = connection_sub.add_parser(
        "setup", help="Run the complete guided private connection journey")
    connection_setup.add_argument(
        "--intent", required=True,
        choices=("openwebui", "phone", "desktop", "developer"))
    connection_setup.add_argument("--label", default="Connected app")
    connection_setup.add_argument("--client-id")
    connection_setup.add_argument(
        "--local-only", action="store_true",
        help="Verify local gateway access without publishing Tailscale Serve")
    connection_sub.add_parser(
        "legacy-status", help="Check whether the displayed legacy shared key can be retired")
    retire_legacy = connection_sub.add_parser(
        "retire-legacy", help="Explicitly revoke the replaced legacy shared key")
    retire_legacy.add_argument("--confirm", required=True)
    profiles = sub.add_parser(
        "profiles", help="Preview and apply outcome-oriented workload profiles"
    )
    profile_sub = profiles.add_subparsers(dest="profile_action", required=True)
    profile_list = profile_sub.add_parser("list", help="List workload profiles")
    profile_list.add_argument("--include-deleted", action="store_true")
    profile_show = profile_sub.add_parser("show", help="Show one workload profile")
    profile_show.add_argument("profile_id")
    profile_preview = profile_sub.add_parser(
        "preview", help="Resolve fit, thermal readiness, and rollback without writes"
    )
    profile_preview.add_argument("profile_id", nargs="+")
    profile_preview.add_argument("--model", dest="model_alias")

    def add_profile_values(target, *, editing: bool = False) -> None:
        target.add_argument("--name", required=True)
        target.add_argument("--ctx", dest="context_per_slot", type=int, required=True)
        target.add_argument("--slots", type=int, required=True)
        target.add_argument("--kv", dest="kv_cache_type", choices=("q8_0", "q4_0"), default="q8_0")
        target.add_argument("--batch", dest="batch_size", type=int, default=1024)
        target.add_argument("--ubatch", dest="ubatch_size", type=int, default=256)
        target.add_argument("--flash", dest="flash_attention", choices=("auto", "on", "off"), default="auto")
        target.add_argument("--preset", dest="optimization_preset_id", choices=("custom", "cool-quiet", "balanced", "maximum"), default="balanced")
        target.add_argument("--thermal", dest="thermal_policy", choices=("standard", "cool", "throughput-guarded"), default="standard")
        target.add_argument("--idle", dest="idle_policy", choices=("KEEP_LOADED", "STOP_AFTER", "STOP_ON_DESKTOP"), default="KEEP_LOADED")
        target.add_argument("--stop-after", dest="stop_after_minutes", type=int)
        if editing:
            target.add_argument("--revision", type=int, required=True)

    profile_create = profile_sub.add_parser("create", help="Create a bounded custom profile")
    add_profile_values(profile_create)
    profile_edit = profile_sub.add_parser("edit", help="Revision-fenced custom profile edit")
    profile_edit.add_argument("profile_id")
    add_profile_values(profile_edit, editing=True)
    profile_delete = profile_sub.add_parser("delete", help="Soft-delete a custom profile")
    profile_delete.add_argument("profile_id")
    profile_delete.add_argument("--revision", type=int, required=True)
    profile_apply = profile_sub.add_parser(
        "apply", help="Apply an exact preview through durable model activation"
    )
    profile_apply.add_argument("profile_id")
    profile_apply.add_argument("--model", dest="model_alias")
    profile_apply.add_argument("--revision", type=int, required=True)
    profile_apply.add_argument("--fingerprint", required=True)
    profile_apply.add_argument("--accept-tight", action="store_true")
    coach = sub.add_parser(
        "coach", help="Show up to three evidence-bound profile suggestions"
    )
    coach.add_argument("--profile", default="builtin-interactive")
    coach.add_argument("--model", dest="model_alias")
    coach.add_argument("--ctx", dest="requested_context", type=int)
    coach.add_argument("--users", dest="requested_users", type=int)
    calibrate = sub.add_parser(
        "calibrate", help="Run bounded fixed-prompt profile calibration"
    )
    calibrate.add_argument("--profile", required=True)
    calibrate.add_argument("--model", dest="model_alias")
    calibrate.add_argument(
        "--candidate-policy",
        choices=("balanced-v1", "conservative-v1"),
        default="balanced-v1",
    )
    calibrate.add_argument("--accept-tight", action="store_true")
    sub.add_parser(
        "home",
        help="Print the unified appliance home snapshot (query-only) as JSON",
    )
    sub.add_parser(
        "privacy",
        help="Print the local data, retention, and network privacy inventory",
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
    storage.add_argument(
        "--dry-run", action="store_true",
        help="Preview durable app-owned cleanup; never mutates",
    )
    storage.add_argument(
        "--apply", action="store_true",
        help="Apply an exact durable cleanup preview",
    )
    storage.add_argument(
        "--mode", choices=("QUARANTINE", "RESTORE", "PURGE"),
        default="QUARANTINE",
    )
    storage.add_argument("--target", action="append", default=[])
    storage.add_argument("--preview")
    storage.add_argument("--confirm")
    maintenance = sub.add_parser(
        "maintenance", help="Show or refresh the prioritized maintenance inbox"
    )
    maintenance.add_argument("action", choices=("status", "check", "cleanup"))
    maintenance.add_argument(
        "--dry-run", action="store_true",
        help="Required for cleanup; reports exact suggestions and deletes nothing",
    )
    notifications = sub.add_parser(
        "notifications", help="Manage optional privacy-safe desktop notices"
    )
    notification_sub = notifications.add_subparsers(
        dest="notification_action", required=True
    )
    notification_sub.add_parser("status", help="Show capability and redacted receipts")
    notification_sub.add_parser("test", help="Send fixed local test copy")
    notification_set = notification_sub.add_parser(
        "set", help="Enable or disable one category or all categories"
    )
    notification_set.add_argument(
        "category", type=str.upper, choices=("ALL", MASTER, *CATEGORIES),
    )
    notification_set.add_argument("value", choices=("on", "off"))
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


def _run_repair_cli(application, args, state: dict[str, Any]) -> int:
    """Shared typed Repair boundary; stdout is always secret-free JSON."""
    from .repair_commands import RepairCommandError

    action = args.repair_action
    try:
        if action == "list":
            payload: Any = application.repair_commands.list_actions()
            ok = True
            result = None
        elif action == "preview":
            result = application.repair_commands.preview(
                args.action_id, args.target
            )
            payload = result.to_dict()
            ok = result.ready
        elif action == "verify":
            result = application.repair_commands.verify(
                args.action_id, args.target
            )
            payload = result.to_dict()
            ok = result.ok
        else:
            # Never generate a one-time credential into a non-interactive
            # sink where it would be irretrievably lost or mixed with JSON.
            if (
                args.action_id == "rotate-gateway-credentials"
                and not sys.stderr.isatty()
            ):
                print(
                    "error: credential rotation requires an interactive terminal",
                    file=sys.stderr,
                )
                return 2
            require_acknowledgment(state)
            result = application.repair_commands.run(
                args.action_id,
                args.target,
                preview_digest=args.preview,
                confirmation_token=args.confirm,
            )
            payload = result.to_dict()
            ok = result.ok
            if result.one_time_secret is not None:
                print(
                    "API key (shown once): " + result.one_time_secret,
                    file=sys.stderr,
                )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if ok else 1
    except RepairCommandError as exc:
        print(json.dumps({
            "outcome": "REFUSED", "result_code": exc.code,
        }, sort_keys=True))
        return 1
    except KeyError:
        print(json.dumps({
            "outcome": "REFUSED", "result_code": "UNKNOWN_REPAIR_ACTION",
        }, sort_keys=True))
        return 1


def _run_undo_cli(application, args, state: dict[str, Any]) -> int:
    if args.undo_action == "list":
        print(json.dumps(application.undo.list(), indent=2, sort_keys=True))
        return 0
    if args.undo_action == "preview":
        preview = application.undo.preview(args.undo_id)
        print(json.dumps(preview.to_dict(), indent=2, sort_keys=True))
        return 0 if preview.ready else 1
    require_acknowledgment(state)
    result = application.undo.run(
        args.undo_id,
        preview_digest=args.preview,
        confirmation_token=args.confirm,
    )
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0 if result.ok else 1


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

    if args.command == "_post-update":
        from .application_post_update import run_post_update_mode

        return run_post_update_mode(
            paths=AppPaths.for_home(),
            operation_id=args.operation_id,
            release_set_digest=args.release_set_digest,
            process_nonce=args.nonce,
            target_schema=args.target_schema,
        )

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

    if (
        args.command == "connections"
        and args.connection_action == "capabilities"
    ):
        # Static/offline contract: usable from repair media and read-only
        # environments without creating or opening an installation profile.
        from .client_compatibility import capability_contract

        print(json.dumps(capability_contract(), indent=2))
        return 0

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
    if args.command == "repair" and args.repair_action is not None:
        return _run_repair_cli(application, args, state)
    if args.command == "undo":
        return _run_undo_cli(application, args, state)
    if args.command == "update":
        service = application.application_update_commands
        if args.update_action == "status":
            result = service.status()
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
            return 0
        if args.update_action == "check":
            result = service.check()
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
            return 0 if result.release is not None else 1
        if args.update_action == "preview":
            result = service.preview(args.version)
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
            return 0 if result.outcome.value == "READY" else 1
        if args.update_action == "import-bundle":
            result = service.import_bundle(args.path)
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
            return 0 if result.ok else 1
        if args.update_action == "apply":
            require_acknowledgment(state)
            result = service.apply(
                args.version,
                preview_digest=args.preview,
                confirmation_token=args.confirm,
                requested_by="cli",
            )
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
            return 0 if result.ok else 1
        if args.update_action == "rollback":
            if not args.preview and not args.confirm:
                result = service.rollback_preview()
                print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
                return 0 if result.outcome.value == "READY" else 1
            if not args.preview or not args.confirm:
                print("update rollback requires both --preview and --confirm", file=sys.stderr)
                return 2
            require_acknowledgment(state)
            result = service.rollback(
                preview_digest=args.preview,
                confirmation_token=args.confirm,
                requested_by="cli",
            )
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
            return 0 if result.ok else 1
        result = service.cleanup_preview()
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "repair":
        if not bootstrap_tkinter(application):
            return 0
        from .gui import run_gui
        management = bool(state.get("setup_complete"))
        run_gui(
            application,
            management=management,
            route="maintenance/repair" if management else None,
        )
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
        result["readiness"] = application.readiness.snapshot(
            target_journey="native_chat").to_dict()
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
            report["readiness"] = application.readiness.snapshot(
                target_journey="native_chat").to_dict()
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
            "update": lambda: svc.update(state, runner),
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
        if args.action == "service":
            if args.service_action is None:
                raise ValueError("gateway service requires an action")
            if args.service_action not in {"plan", "status"}:
                require_acknowledgment(state)
            service = application.gateway_service
            service_actions = {
                "plan": lambda: service.plan().to_dict(),
                "install": lambda: service.install(runner),
                "status": lambda: service.status(runner),
                "start": lambda: service.acquire("client", runner),
                "stop": lambda: service.release("client", runner),
                "restart": lambda: service.restart(runner),
                "remove": lambda: service.remove(runner),
            }
            result = service_actions[args.service_action]()
            print(json.dumps(result, indent=2))
            return 0
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
        if action == "doctor":
            from .ux_guidance import connection_doctor

            diagnosis = connection_doctor(
                application.connections.snapshot().to_dict()
            )
            print(json.dumps(diagnosis.to_dict(), indent=2))
            return 0 if diagnosis.ready else 1
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
        if action == "legacy-status":
            print(json.dumps(application.integration_setup.legacy_status(), indent=2))
            return 0

        require_acknowledgment(state)
        setup_creates_external = (
            action == "setup" and args.intent != "openwebui" and not args.client_id)
        if (
            action in {"add-client", "rotate-client"} or setup_creates_external
        ) and not sys.stderr.isatty():
            raise RuntimeError(
                "Creating a client whose key must be revealed requires an interactive terminal "
                "so the API key can be shown exactly once.")
        if action == "setup":
            intent = {
                "openwebui": "OPENWEBUI",
                "phone": "PHONE_TABLET",
                "desktop": "DESKTOP_APP",
                "developer": "DEVELOPER",
            }[args.intent]
            outcome = application.integration_setup.start(
                intent=intent,
                label=args.label,
                client_id=args.client_id,
                require_tailnet=not args.local_only,
                requested_by="cli",
            )
            print(json.dumps(outcome.to_dict(), indent=2))
            if outcome.secret is not None:
                print("API key (shown once):", outcome.secret, file=sys.stderr)
            return 0 if outcome.ok else 1
        if action == "retire-legacy":
            print(json.dumps(application.integration_setup.retire_legacy(
                confirmation=args.confirm), indent=2))
            return 0
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
    if args.command == "profiles":
        action = args.profile_action
        query = application.workload_profiles
        commands = application.workload_profile_commands
        if action == "list":
            result = query.list(include_deleted=args.include_deleted)
        elif action == "show":
            result = query.show(args.profile_id)
        elif action == "preview":
            profile_ids = tuple(args.profile_id)
            result = (
                query.preview(profile_ids[0], model_alias=args.model_alias)
                if len(profile_ids) == 1
                else query.compare(profile_ids, model_alias=args.model_alias)
            )
        else:
            require_acknowledgment(state)
            if action in {"create", "edit"}:
                values = {
                    key: getattr(args, key)
                    for key in (
                        "name", "context_per_slot", "slots", "kv_cache_type",
                        "batch_size", "ubatch_size", "flash_attention",
                        "optimization_preset_id", "thermal_policy", "idle_policy",
                        "stop_after_minutes",
                    )
                }
                result = (
                    commands.create(**values)
                    if action == "create"
                    else commands.edit(
                        args.profile_id,
                        expected_revision=args.revision,
                        **values,
                    )
                )
            elif action == "delete":
                result = commands.delete(
                    args.profile_id, expected_revision=args.revision
                )
            else:
                result = commands.apply(
                    args.profile_id,
                    model_alias=args.model_alias,
                    expected_profile_revision=args.revision,
                    preview_fingerprint=args.fingerprint,
                    accept_tight=args.accept_tight,
                    requested_by="cli",
                )
                result = result.to_dict() if hasattr(result, "to_dict") else result
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "coach":
        result = application.performance_coach.suggestions(
            profile_id=args.profile,
            model_alias=args.model_alias,
            requested_context=args.requested_context,
            requested_users=args.requested_users,
        )
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "calibrate":
        require_acknowledgment(state)
        preview = application.workload_profiles.preview(
            args.profile, model_alias=args.model_alias
        )
        outcome = application.calibration.calibrate(
            profile_id=args.profile,
            expected_profile_revision=int(preview["profile_revision"]),
            model_alias=str(preview["model_alias"]),
            candidate_policy=args.candidate_policy,
            accept_tight=args.accept_tight,
            requested_by="cli",
        )
        print(json.dumps(outcome.to_dict(), indent=2))
        return 0 if outcome.ok else 1
    if args.command == "home":
        # P5 §11.1: query-only snapshot; identical source for CLI/GUI/bundle.
        result = application.home.snapshot().to_dict()
        result["readiness"] = application.readiness.snapshot(
            target_journey="native_chat").to_dict()
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "privacy":
        print(json.dumps(application.privacy.snapshot().to_dict(), indent=2))
        return 0
    if args.command == "maintenance":
        if args.action == "status":
            result = application.maintenance_snapshot.snapshot().to_dict()
        elif args.action == "check":
            if args.dry_run:
                raise ValueError("--dry-run applies only to maintenance cleanup")
            result = application.maintenance_checks.run()
        else:
            if not args.dry_run:
                raise ValueError(
                    "maintenance cleanup is preview-only; pass --dry-run explicitly"
                )
            result = application.storage_capacity.dry_run_cleanup()
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "notifications":
        action = args.notification_action
        if action == "status":
            result = application.notifications.status()
        elif action == "test":
            result = application.notifications.test().to_dict()
        else:
            status = application.notification_preferences.status()
            if args.category == "ALL":
                changes = {"MASTER": args.value == "on"}
                revisions = {"MASTER": int(status["master_revision"])}
                for category, row in status["categories"].items():
                    changes[category] = args.value == "on"
                    revisions[category] = int(row["revision"])
            else:
                row = (
                    {"revision": status["master_revision"]}
                    if args.category == "MASTER"
                    else status["categories"][args.category]
                )
                changes = {args.category: args.value == "on"}
                revisions = {args.category: int(row["revision"])}
            result = application.notification_preferences.apply(
                changes, expected_revisions=revisions
            )
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "support-bundle":
        # P5 §11.3: redacted-by-construction export. Reuses the composed
        # home/doctor services so the bundle matches every other surface.
        from .support_bundle import SupportBundleService

        service = SupportBundleService(
            application.units, application.paths,
            home=application.home, doctor=application.doctor,
            platform=application.platform,
            readiness=application.readiness,
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
                        "recommendation_rank": index,
                        "recommendation_label": "Recommended to install",
                        "id": model.id,
                        "display_name": model.display_name,
                        "quant": quant,
                        "validation_tier": validation_tier(model),
                        "verdict": fit.verdict,
                        "required_gib": round(fit.required_gib, 2),
                        "detail": fit.detail,
                        "local_measurement": "Not measured on this machine",
                    }
                    for index, (model, quant, fit) in enumerate(ranked, 1)
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
            activate_console_now=True,
        )
        return 0
    if args.command == "install-model":
        require_acknowledgment(state)
        if not state.get("env_ready"):
            raise RuntimeError("Inference environment is not ready; complete that wizard step first.")
        model = advertised_model_by_id(args.model_id)
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
        # The legacy no-flag cleanup view remains a query-only compatibility
        # report. Explicit --dry-run/--apply use durable EXP-5 cleanup.
        if args.action == "report":
            if args.dry_run or args.apply:
                raise ValueError("storage report does not accept cleanup flags")
            print(json.dumps(application.storage_capacity.report(), indent=2))
            return 0
        if args.apply:
            if args.dry_run:
                raise ValueError("choose either --dry-run or --apply")
            if not args.preview or not args.confirm:
                raise ValueError(
                    "storage cleanup --apply requires --preview and --confirm"
                )
            require_acknowledgment(state)
            outcome = application.storage_cleanup.apply(
                mode=args.mode,
                target_ids=args.target or None,
                preview_digest=args.preview,
                confirmation_token=args.confirm,
                requested_by="cli",
            )
            print(json.dumps(outcome.to_dict(), indent=2, sort_keys=True))
            return 0 if outcome.ok else 1
        if args.dry_run:
            preview = application.storage_cleanup.preview(
                mode=args.mode, target_ids=args.target or None
            )
            print(json.dumps(preview.to_dict(), indent=2, sort_keys=True))
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
