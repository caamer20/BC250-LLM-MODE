# State schema reference (JSON, schemas v1–v5)

> **Status: historical.** Durable state lives in SQLite (`state.db`) since the
> R2 cutover. This document freezes the JSON contract that
> `legacy_schema.canonicalize_legacy_state` consumes as an immutable import
> source; it no longer describes a runtime store.

v5 was the final JSON schema. Fixtures: `tests/fixtures/`.

## Field ownership

| Field | Type | Default | Class | Notes |
| --- | --- | --- | --- | --- |
| `schema_version` | int | 5 | derived | Migration target version |
| `revision` | int | 0 | derived | Incremented by `StateStore.transaction()` |
| `disclaimer_ack` | bool | False | configuration | Safety gate; never reset silently |
| `ack_timestamp` | str/null | null | configuration | ISO date of acknowledgment |
| `setup_phase` | int | 0 | derived | Wizard resume position |
| `setup_complete` | bool | False | configuration | |
| `llm_mode_done` / `system_mode` / `boot_policy` / `desktop_on_reboot` / `llm_autostart` | mixed | see state.py | configuration | Reboot-safety contract |
| `llm_session_boot_id` | str/null | null | observation | Invalidated when boot id changes |
| `desktop_reboot_pending` / `reboot_required` / `pending_karg_mode` | mixed | — | observation | Transient; must not block recovery |
| `https_sharing_enabled` / `https_webui_port` / `https_api_port` / `https_webui_url` / `https_api_base_url` | mixed | — | configuration | Tailnet publishing |
| `env_ready` | bool | False | observation | Container/build smoke result |
| `installed_models` | list[dict] | [] | configuration | id/path/quant/sampling profile |
| `current_model` / `selected_model` / `selected_quant` / `selected_source` / `selected_local_model` | mixed | null | configuration | Fit-gated activation inputs |
| `current_ctx` | int | 8192 | configuration | Per-slot context |
| `server_port` | int | 8080 | configuration | |
| `container_name` / `service_name` | str | llm / bc250-llm.service | configuration | |
| `app_dir` / `logs_dir` / `state_path` | str | profile | derived | Always rewritten from composed `AppPaths` |
| `models_dir` | str | profile | configuration | Custom locations preserved |
| `model_search_paths` | list[str] | [] | configuration | External read-only roots |
| `llama_cpp_path` / `venv_path` | str | /root/... | configuration | Container-internal paths |
| `optimizations` | dict | DEFAULT_OPTIMIZATIONS | configuration | Validated by `optimize.validate_settings` |
| `openwebui_container` / `openwebui_installed` | mixed | — | observation | Written by status probes |
| `download_dir` | str/null | null | observation | Last download staging dir |
| `bench_history` | list[dict] | [] | history | Capped at 20 by `record_benchmark` |
| `autotune_history` | list[dict] | [] | history | Capped at 40 by autotune |
| `thermal_watchdog_state` | str | nominal | observation | nominal/throttled/stopped(latched)/degraded |
| `thermal_watchdog_baseline` | dict/null | null | configuration | Saved GPU profile during throttle; restored/cleared |
| `llamacpp_build` | dict/null | null | observation | commit/describe/recorded |
| `llamacpp_history` | list[dict] | [] | history | Capped at 1 (matches single physical backup) |
| `optimization_*` keys | mixed | — | configuration | Rollback records for host mutations |
| `server_log` | str | platform default | derived | Resolved at service install |

## Rules

- Unknown keys are preserved on load (forward compatibility) and persisted.
- No secret values are stored here: HF tokens live in ephemeral 0600 env-files;
  future API credentials go to protected secret storage, never this file.
- `app_dir` / `logs_dir` / `state_path` always follow the composed `AppPaths`
  profile; `models_dir` follows it only when never customized.
