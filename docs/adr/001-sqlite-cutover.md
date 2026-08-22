# ADR 001 — SQLite cutover and legacy-state field mapping

Status: accepted for the 0.9 milestone. Supersedes any "copy every JSON key
into SQLite" interpretation of earlier plans.

## Decision

1. `state.db` (SQLite) becomes the **sole source of truth** after cutover.
2. JSON schema versions and SQLite schema versions are **separate version
   sequences**. The JSON importer canonicalizes anything ≤ v5 through the
   existing in-process migration before mapping fields.
3. **No dual writes.** After cutover, `state.json` is a read-only migration
   backup; the application never writes it again.
4. The original JSON file is **never modified** during import.
5. An import failure at any phase leaves **no published database**: staging is
   deleted and the JSON remains authoritative.
6. A database whose `schema_migrations` version is **newer than supported is
   refused** (repair mode), never reset or downgraded.
7. Derived paths (`app_dir`, `logs_dir`, `models_dir` when default,
   `state_path`, `download_dir`) are **not persisted as active settings**;
   they are recomputed from the injected `AppPaths` profile at composition.
8. A customized `models_dir` is preserved as an explicit setting.
9. Legacy installed models import as `provenance='legacy-import'`,
   `validation_status='unverified'` — they require re-verification before
   they may be treated as hardware-validated.
10. `--state <legacy-json>` remains supported during the 0.9 window as the
    explicit import source; `--app-dir` becomes the future-facing option.

## Field-mapping contract

Canonicalization first: raw JSON → existing in-process v5 migration → mapped
per class below.

| JSON field class | Fields | SQLite treatment |
| --- | --- | --- |
| Configuration | disclaimer_ack, ack_timestamp, setup_complete, boot_policy, desktop_on_reboot, llm_autostart, llm_mode_done, system_mode, https_* , server_port, container_name, service_name, llama_cpp_path, venv_path, model_search_paths, optimizations, optimization_* receipts, selected_local_model | `settings` (typed key/value_json) |
| Setup progress | setup_phase | `settings` (resumable workflow state) |
| Model records | installed_models[] | `model_installations` — provenance `legacy-import`, status `unverified` |
| History | bench_history, autotune_history | normalized history tables |
| Derived paths | app_dir, logs_dir, models_dir (default), state_path, download_dir, custom_paths | **not stored** — recomputed from injected `AppPaths` (`download_dir` → stale observation) |
| Ordinary observations | env_ready, openwebui_container/installed, server_log, llm_session_boot_id, desktop_reboot_pending, reboot_required, pending_karg_mode | `runtime_observations` marked **stale**; reconciled against the host on next probe |
| Safety latch | thermal_watchdog_state (+ baseline), thermal_stop latches | **preserved authoritatively** in `thermal_state` across migration and reboot |
| Provenance | llamacpp_build | `component_provenance('llamacpp')` |
| Unknown keys | anything unmapped | `legacy_import_extras` — preserved outside active configuration |
| Secrets / secret-like keys | names containing token/secret/password/key (except known placeholder) | **rejected from import** into general tables; recorded as redacted events |

## Import semantics

- Exclusive `migration_lock_path` guards the whole flow.
- Staging database lives under `staging/`; migrations run there; integrity +
  foreign-key checks must pass; then an atomic `os.replace` publishes to
  `database_path` with file+parent fsync and mode `0600`.
- A receipt (counts, warnings, source digest) is written to
  `migration_receipts_dir/`.
- Re-running after successful publication is idempotent: the published
  database is detected and the JSON is not re-imported.
- Failure before publication removes staging and publishes nothing.

## Compatibility facade removal criteria

The temporary whole-state compatibility view is removed when no production
module outside repositories writes durable state, verified by a guard test
driving compatibility-save call sites to zero.
