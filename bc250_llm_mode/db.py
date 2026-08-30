"""SQLite foundation: connections, PRAGMAs, migrations, integrity checks.

Contract (docs/adr/001-sqlite-cutover.md):
- state.db is the sole source of truth after cutover; JSON is a read-only
  import source.
- Every connection enables foreign keys, a bounded busy timeout, WAL where
  supported, and an explicit synchronous policy.
- Schema versions are tracked in ``schema_migrations``; a database newer than
  the supported version is refused, never reset.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 13
BUSY_TIMEOUT_MS = 5000

# (version, name, statements). Declared in ASCENDING version order; the
# registry is validated at initialization so ordering is a contract, not a
# convention. Each migration runs inside one explicit transaction: every
# statement is executed individually (never executescript(), which commits
# pending work first), and the schema_migrations row is written by the same
# transaction. A failure mid-migration rolls back the partial schema AND the
# version row.
MIGRATIONS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (
        1,
        "initial-schema",
        (
            """
            CREATE TABLE settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0
            )
            """,
            """
            CREATE TABLE runtime_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                model_alias TEXT,
                context INTEGER NOT NULL,
                slots INTEGER NOT NULL DEFAULT 1,
                profile_id TEXT,
                extra_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE model_installations (
                id INTEGER PRIMARY KEY,
                alias TEXT NOT NULL UNIQUE,
                path TEXT NOT NULL,
                quant TEXT,
                display_name TEXT,
                sampling_json TEXT NOT NULL DEFAULT '{}',
                provenance TEXT NOT NULL DEFAULT 'legacy-import',
                validation_status TEXT NOT NULL DEFAULT 'unverified',
                imported_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE bench_history (
                id INTEGER PRIMARY KEY,
                ts TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE autotune_history (
                id INTEGER PRIMARY KEY,
                payload_json TEXT NOT NULL
            )
            """,
            # The thermal latch is safety-authoritative: it survives migration
            # and reboot and is never marked stale.
            """
            CREATE TABLE thermal_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                latch_state TEXT NOT NULL,
                baseline_json TEXT,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE component_provenance (
                component TEXT PRIMARY KEY,
                describe TEXT,
                commit_sha TEXT,
                recorded_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE runtime_observations (
                key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                stale INTEGER NOT NULL DEFAULT 1
            )
            """,
            """
            CREATE TABLE legacy_import_extras (
                key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            )
            """,
        ),
    ),
    (
        2,
        "known-good-runtime",
        (
            """
            CREATE TABLE known_good_runtime (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                model_alias TEXT,
                context INTEGER NOT NULL,
                slots INTEGER NOT NULL DEFAULT 1,
                profile_id TEXT,
                runtime_json TEXT NOT NULL DEFAULT '{}',
                runtime_fingerprint TEXT,
                runtime_component_identity TEXT,
                verified_at TEXT NOT NULL
            )
            """,
        ),
    ),
    (
        3,
        "durable-operations",
        (
            # ADR 002: one row per durable operation. Request payloads are
            # sanitized/bounded by operations/validation before insert.
            """
            CREATE TABLE operations (
                id TEXT PRIMARY KEY,
                operation_type TEXT NOT NULL,
                request_version INTEGER NOT NULL,
                recovery_policy_version INTEGER NOT NULL DEFAULT 1,
                request_json TEXT NOT NULL,
                state TEXT NOT NULL,
                state_revision INTEGER NOT NULL DEFAULT 1,
                progress_phase TEXT,
                progress_current INTEGER NOT NULL DEFAULT 0,
                progress_total INTEGER,
                progress_unit TEXT,
                progress_summary TEXT,
                surface TEXT NOT NULL DEFAULT 'unknown',
                cancel_requested_at TEXT,
                result_code TEXT,
                result_detail TEXT,
                error_code TEXT,
                error_detail TEXT,
                parent_operation_id TEXT REFERENCES operations(id)
                    ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                updated_at TEXT NOT NULL,
                finished_at TEXT
            )
            """,
            """
            CREATE INDEX idx_operations_active ON operations(state)
                WHERE state NOT IN (
                    'SUCCEEDED',
                    'CANCELLED',
                    'FAILED_SAFE',
                    'FAILED_ROLLED_BACK',
                    'RECOVERY_REQUIRED'
                )
            """,
            """
            CREATE INDEX idx_operations_state_type_updated
                ON operations(state, operation_type, updated_at)
            """,
            """
            CREATE INDEX idx_operations_recent_terminal
                ON operations(finished_at DESC, id DESC)
                WHERE finished_at IS NOT NULL
            """,
            """
            CREATE INDEX idx_operations_parent
                ON operations(parent_operation_id)
            """,
            """
            CREATE TABLE operation_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL REFERENCES operations(id)
                    ON DELETE CASCADE,
                step_key TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                implementation_version INTEGER NOT NULL DEFAULT 1,
                state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                input_json TEXT,
                output_json TEXT,
                external_effect_id TEXT,
                failure_code TEXT,
                failure_detail TEXT,
                started_at TEXT,
                checkpointed_at TEXT,
                finished_at TEXT,
                UNIQUE (operation_id, step_key),
                UNIQUE (operation_id, sequence)
            )
            """,
            """
            CREATE INDEX idx_operation_steps_order
                ON operation_steps(operation_id, sequence)
            """,
            """
            CREATE TABLE operation_events (
                cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL REFERENCES operations(id)
                    ON DELETE CASCADE,
                ts TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'info',
                code TEXT,
                summary TEXT NOT NULL,
                detail_json TEXT,
                progress_json TEXT
            )
            """,
            """
            CREATE INDEX idx_operation_events_cursor
                ON operation_events(operation_id, cursor)
            """,
            """
            CREATE TABLE operation_leases (
                resource_key TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL REFERENCES operations(id)
                    ON DELETE CASCADE,
                owner TEXT NOT NULL,
                lease_revision INTEGER NOT NULL DEFAULT 1,
                acquired_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX idx_operation_leases_expiry
                ON operation_leases(expires_at)
            """,
        ),
    ),
    (
        4,
        "managed-model-artifacts",
        (
            # ADR 003 (U1.1): content-addressed managed artifacts. Digests
            # are normalized lower-case sha256:<64hex>; legacy backfill rows
            # use deterministic 'legacy:<installation id>' IDs and never
            # claim verified trust.
            """
            CREATE TABLE model_artifacts (
                id TEXT PRIMARY KEY,
                content_digest TEXT UNIQUE,
                byte_size INTEGER CHECK (byte_size >= 0),
                canonical_path TEXT NOT NULL UNIQUE,
                storage_state TEXT NOT NULL
                    CHECK (storage_state IN
                        ('MANAGED', 'QUARANTINED', 'LEGACY_EXTERNAL')),
                trust_state TEXT NOT NULL
                    CHECK (trust_state IN
                        ('VERIFIED', 'UNVERIFIED', 'QUARANTINED',
                         'LEGACY_UNVERIFIED')),
                format TEXT,
                architecture TEXT,
                quantization TEXT,
                tensor_count INTEGER,
                validator_version INTEGER NOT NULL DEFAULT 1,
                validation_detail_json TEXT NOT NULL DEFAULT '{}',
                source_kind TEXT NOT NULL DEFAULT 'legacy',
                source_repo TEXT,
                source_revision TEXT,
                source_filename TEXT,
                source_digest TEXT,
                catalog_id TEXT,
                license_id TEXT,
                provenance_json TEXT NOT NULL DEFAULT '{}',
                quarantine_reason_code TEXT,
                created_at TEXT NOT NULL,
                validated_at TEXT,
                CHECK (
                    (storage_state = 'MANAGED'
                     AND content_digest IS NOT NULL
                     AND validated_at IS NOT NULL)
                    OR (storage_state = 'QUARANTINED'
                        AND content_digest IS NOT NULL
                        AND quarantine_reason_code IS NOT NULL)
                    OR storage_state = 'LEGACY_EXTERNAL'
                )
            )
            """,
            """
            CREATE INDEX idx_model_artifacts_digest
                ON model_artifacts(content_digest)
            """,
            """
            CREATE INDEX idx_model_artifacts_state
                ON model_artifacts(storage_state, trust_state)
            """,
            """
            CREATE INDEX idx_model_artifacts_catalog
                ON model_artifacts(catalog_id)
            """,
            # Link installations to artifacts without rebuilding migration
            # 001's table. Legacy rows are backfilled deterministically in
            # SQL below; no user file is read or hashed.
            """
            ALTER TABLE model_installations
                ADD COLUMN artifact_id TEXT REFERENCES model_artifacts(id)
            """,
            """
            INSERT INTO model_artifacts (
                id, canonical_path, storage_state, trust_state,
                validator_version, provenance_json, created_at
            )
            SELECT
                'legacy:' || CAST(id AS TEXT),
                path,
                'LEGACY_EXTERNAL',
                'LEGACY_UNVERIFIED',
                1,
                '{}',
                imported_at
            FROM model_installations
            WHERE id IS NOT NULL
            """,
            """
            UPDATE model_installations
               SET artifact_id = 'legacy:' || CAST(id AS TEXT)
            WHERE artifact_id IS NULL
            """,
            """
            CREATE INDEX idx_model_installations_artifact
                ON model_installations(artifact_id)
            """,
            # U1.1: logical per-operation disk reservations. Release marks;
            # rows remain as Activity/support evidence.
            """
            CREATE TABLE operation_storage_reservations (
                operation_id TEXT PRIMARY KEY REFERENCES operations(id)
                    ON DELETE CASCADE,
                filesystem_identity TEXT NOT NULL,
                required_bytes INTEGER NOT NULL CHECK (required_bytes >= 0),
                available_bytes INTEGER NOT NULL CHECK (available_bytes >= 0),
                reserved_bytes INTEGER NOT NULL CHECK (reserved_bytes >= 0),
                reclaimable_owned_bytes INTEGER NOT NULL
                    CHECK (reclaimable_owned_bytes >= 0),
                credited_partial_bytes INTEGER NOT NULL
                    CHECK (credited_partial_bytes >= 0),
                state TEXT NOT NULL
                    CHECK (state IN ('RESERVED', 'RELEASED')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                released_at TEXT
            )
            """,
        ),
    ),
    (
        5,
        "immutable-runtime-lifecycle",
        (
            # ADR 004 (U1.2): immutable content-derived runtime builds,
            # append-only verification facts, operation-owned tree registry,
            # and one authoritative promoted/rollback component row.
            # Filesystem-free: no host, Git, container, or service access.
            """
            CREATE TABLE runtime_builds (
                build_id TEXT PRIMARY KEY
                    CHECK (
                        (length(build_id) = 80 AND build_id GLOB
                         'llamacpp:sha256:[0-9a-f]*')
                        OR build_id LIKE 'legacy:%'
                    ),
                component TEXT NOT NULL CHECK (component = 'llamacpp'),
                manifest_version INTEGER NOT NULL
                    CHECK (manifest_version >= 1),
                manifest_json TEXT NOT NULL CHECK (length(manifest_json) <= 65536),
                manifest_digest TEXT NOT NULL
                    CHECK (length(manifest_digest) = 64
                           AND manifest_digest GLOB '[0-9a-f]*'),
                source_commit TEXT
                    CHECK (source_commit IS NULL OR length(source_commit) = 40),
                requested_ref TEXT
                    CHECK (requested_ref IS NULL OR length(requested_ref) <= 128),
                recipe_version INTEGER NOT NULL CHECK (recipe_version >= 1),
                provenance_class TEXT NOT NULL
                    CHECK (provenance_class IN
                        ('LEGACY_UNVERIFIED', 'IMMUTABLE_SOURCE')),
                created_by_operation_id TEXT REFERENCES operations(id)
                    ON DELETE SET NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX idx_runtime_builds_component
                ON runtime_builds(component, created_at)
            """,
            """
            CREATE TABLE runtime_build_verifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                build_id TEXT NOT NULL REFERENCES runtime_builds(build_id)
                    ON DELETE RESTRICT,
                operation_id TEXT REFERENCES operations(id)
                    ON DELETE SET NULL,
                kind TEXT NOT NULL
                    CHECK (kind IN
                        ('SMOKE', 'ACTIVE_HEALTH', 'ACTIVE_INFERENCE',
                         'RESTORED_HEALTH', 'RESTORED_INFERENCE')),
                evidence_json TEXT NOT NULL
                    CHECK (length(evidence_json) <= 4096),
                observed_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX idx_runtime_verifications_build
                ON runtime_build_verifications(build_id, observed_at)
            """,
            """
            CREATE TABLE runtime_trees (
                tree_id TEXT PRIMARY KEY,
                build_id TEXT NOT NULL REFERENCES runtime_builds(build_id)
                    ON DELETE RESTRICT,
                container_profile TEXT NOT NULL
                    CHECK (length(container_profile) <= 128),
                locator TEXT NOT NULL
                    CHECK (
                        length(locator) BETWEEN 1 AND 256
                        AND locator NOT LIKE '/%'
                        AND locator NOT LIKE '%..%'
                    ),
                role TEXT NOT NULL
                    CHECK (role IN
                        ('ACTIVE_OBSERVED', 'CANDIDATE', 'ROLLBACK',
                         'RETAINED', 'QUARANTINED')),
                manifest_digest TEXT NOT NULL
                    CHECK (length(manifest_digest) = 64
                           AND manifest_digest GLOB '[0-9a-f]*'),
                server_binary_digest TEXT NOT NULL
                    CHECK (length(server_binary_digest) = 64
                           AND server_binary_digest GLOB '[0-9a-f]*'),
                ownership_class TEXT NOT NULL
                    CHECK (ownership_class IN
                        ('OPERATION_OWNED', 'LEGACY_ADOPTED')),
                created_by_operation_id TEXT REFERENCES operations(id)
                    ON DELETE SET NULL,
                last_observed_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX idx_runtime_trees_role
                ON runtime_trees(role, build_id)
            """,
            """
            CREATE INDEX idx_runtime_trees_operation
                ON runtime_trees(created_by_operation_id)
            """,
            """
            CREATE TABLE runtime_component_state (
                component TEXT PRIMARY KEY CHECK (component = 'llamacpp'),
                promoted_build_id TEXT REFERENCES runtime_builds(build_id)
                    ON DELETE RESTRICT,
                rollback_build_id TEXT REFERENCES runtime_builds(build_id)
                    ON DELETE RESTRICT,
                generation INTEGER NOT NULL CHECK (generation >= 1),
                promoted_tree_id TEXT REFERENCES runtime_trees(tree_id)
                    ON DELETE SET NULL,
                rollback_tree_id TEXT REFERENCES runtime_trees(tree_id)
                    ON DELETE SET NULL,
                last_operation_id TEXT REFERENCES operations(id)
                    ON DELETE SET NULL,
                updated_at TEXT NOT NULL
            )
            """,
            # Deterministic legacy backfill: when pre-migration provenance
            # exists for llama.cpp, record ONE synthetic LEGACY_UNVERIFIED
            # build. It never claims an active or rollback tree; the first
            # managed preflight may adopt the legacy active tree only after
            # exact host observation. A missing/malformed value inserts no
            # row and never blocks migration. The digest is a deterministic
            # truncation of the bounded metadata hex — clearly synthetic,
            # never claimed cryptographic for a legacy row. Old settings
            # bytes stay untouched for downgrade/forensic compatibility.
            """
            INSERT INTO runtime_builds (
                build_id, component, manifest_version, manifest_json,
                manifest_digest, source_commit, requested_ref,
                recipe_version, provenance_class, created_by_operation_id,
                created_at
            )
            SELECT 'legacy:llamacpp', 'llamacpp', 1,
                   json_object('describe', p.describe, 'commit_sha',
                               p.commit_sha, 'recorded_at', p.recorded_at),
                   substr(lower(hex(cast(json_object(
                       'describe', p.describe, 'commit_sha', p.commit_sha
                   ) AS BLOB)) || '0000000000000000000000000000000000000000000000000000000000000000'), 1, 64),
                   CASE WHEN length(p.commit_sha) = 40 THEN p.commit_sha END,
                   substr(COALESCE(p.describe, ''), 1, 128),
                   1,
                   'LEGACY_UNVERIFIED',
                   NULL,
                   COALESCE(p.recorded_at, datetime('now'))
            FROM component_provenance p
            WHERE p.component = 'llamacpp'
              AND p.recorded_at IS NOT NULL
            """,
        ),
    ),
    (
        6,
        "explicit-worker-lifecycle",
        (
            # U1.3: one bounded worker host per application profile. The
            # lock lives OUTSIDE operation_leases (whose FK requires a
            # real operation row) so a sentinel can never pollute
            # operation history or boot behavior.
            """
            CREATE TABLE worker_locks (
                token TEXT PRIMARY KEY CHECK (token = 'worker-host'),
                owner TEXT NOT NULL,
                lease_revision INTEGER NOT NULL DEFAULT 1
                    CHECK (lease_revision >= 1),
                acquired_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """,
        ),
    ),
    (
        7,
        "operation-dismissal",
        (
            # U1.4 §7.3 `dismiss`: hide a terminal operation from DEFAULT
            # views without deleting any audit history. The timestamp is
            # the durable flag; events and receipts are never touched.
            """
            ALTER TABLE operations ADD COLUMN dismissed_at TEXT
            """,
            """
            CREATE INDEX idx_operations_default_view
                ON operations(updated_at DESC, id DESC)
                WHERE dismissed_at IS NULL
            """,
        ),
    ),
    (
        8,
        "gateway-credential",
        (
            # ADR 005 D3: the authenticated gateway credential is the ONLY
            # bridge to the loopback backend for remote/container traffic.
            # ONLY the NON-SECRET fingerprint is durable here (revocation +
            # rotation are auditable and durable); the secret itself lives
            # in a 0600 profile file and never touches SQLite/logs/argv.
            # Singleton row (id=1) mirrors runtime_component_state.
            """
            CREATE TABLE gateway_credentials (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                fingerprint TEXT NOT NULL
                    CHECK (length(fingerprint) = 64
                           AND fingerprint GLOB '[0-9a-f]*'),
                scopes TEXT NOT NULL DEFAULT 'inference:read,inference:stream,models:list'
                    CHECK (length(scopes) <= 256),
                created_at TEXT NOT NULL,
                rotated_at TEXT,
                revoked_at TEXT,
                revision INTEGER NOT NULL DEFAULT 1
                    CHECK (revision >= 1)
            )
            """,
        ),
    ),
    (
        9,
        "model-library-meta",
        (
            # P6 §12.1: per-alias library metadata that is not part of the
            # immutable artifact identity — pinned/favorite state, usage
            # timestamps (never prompt content), and a bounded benchmark /
            # last-verified-inference summary. Keyed by installation alias
            # and cascades on removal.
            """
            CREATE TABLE model_library_meta (
                alias TEXT PRIMARY KEY
                    REFERENCES model_installations(alias)
                    ON DELETE CASCADE,
                pinned INTEGER NOT NULL DEFAULT 0
                    CHECK (pinned IN (0, 1)),
                last_used_at TEXT,
                last_verified_inference_at TEXT,
                benchmark_summary_json TEXT NOT NULL DEFAULT '{}'
                    CHECK (length(benchmark_summary_json) <= 65536),
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX idx_model_library_meta_pinned
                ON model_library_meta(pinned)
            """,
        ),
    ),
    (
        10,
        "backup-restore-lifecycle",
        (
            # ADR 006 / C2.2: durable backup + restore publication state.
            # Labels + digests + states ONLY — never passphrases, raw keys,
            # private paths, prompts, or full archive listings. Both tables are
            # revision-fenced for CAS updates by the repositories.
            """
            CREATE TABLE backup_sets (
                backup_id TEXT PRIMARY KEY,
                manifest_digest TEXT NOT NULL
                    CHECK (length(manifest_digest) = 64
                           AND manifest_digest GLOB '[0-9a-f]*'),
                format_version INTEGER NOT NULL DEFAULT 1
                    CHECK (format_version >= 1),
                created_by_operation_id TEXT
                    REFERENCES operations(id) ON DELETE SET NULL,
                storage_path_label TEXT NOT NULL
                    CHECK (length(storage_path_label) <= 512),
                bytes_total INTEGER NOT NULL DEFAULT 0
                    CHECK (bytes_total >= 0),
                encryption_mode TEXT NOT NULL DEFAULT 'none'
                    CHECK (encryption_mode IN ('none', 'aes-256-gcm')),
                verification_state TEXT NOT NULL DEFAULT 'pending'
                    CHECK (verification_state IN
                           ('pending', 'verified', 'failed')),
                created_at TEXT NOT NULL,
                verified_at TEXT,
                revision INTEGER NOT NULL DEFAULT 1
                    CHECK (revision >= 1)
            )
            """,
            """
            CREATE TABLE restore_attempts (
                restore_id TEXT PRIMARY KEY,
                source_manifest_digest TEXT NOT NULL
                    CHECK (length(source_manifest_digest) = 64
                           AND source_manifest_digest GLOB '[0-9a-f]*'),
                created_by_operation_id TEXT
                    REFERENCES operations(id) ON DELETE SET NULL,
                staging_identity TEXT,
                prior_profile_identity TEXT,
                candidate_profile_identity TEXT,
                publish_state TEXT NOT NULL DEFAULT 'pending'
                    CHECK (publish_state IN
                           ('pending', 'staged', 'published',
                            'rolled_back', 'recovery_required')),
                post_verify_state TEXT
                    CHECK (post_verify_state IN
                           ('pending', 'passed', 'failed') OR
                           post_verify_state IS NULL),
                rollback_state TEXT
                    CHECK (rollback_state IN
                           ('not_needed', 'succeeded', 'failed') OR
                           rollback_state IS NULL),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1
                    CHECK (revision >= 1)
            )
            """,
        ),
    ),
    (
        11,
        "multi-client-credentials",
        (
            # ADR 009 / EXP-2: independently revocable client metadata.
            # Secret bytes remain in separate mode-0600 files and never enter
            # these tables. Labels are display-only and never form paths.
            """
            CREATE TABLE connection_clients (
                client_id TEXT PRIMARY KEY
                    CHECK (length(client_id) BETWEEN 1 AND 64),
                label TEXT NOT NULL
                    CHECK (length(trim(label)) BETWEEN 1 AND 80),
                client_kind TEXT NOT NULL
                    CHECK (client_kind IN
                           ('openwebui', 'pocketpal', 'openai', 'curl', 'sse')),
                scopes TEXT NOT NULL
                    CHECK (length(scopes) BETWEEN 1 AND 128),
                active_fingerprint TEXT NOT NULL
                    CHECK (length(active_fingerprint) = 64
                           AND active_fingerprint GLOB '[0-9a-f]*'),
                active_generation INTEGER NOT NULL DEFAULT 1
                    CHECK (active_generation >= 1),
                secret_storage TEXT NOT NULL DEFAULT 'managed'
                    CHECK (secret_storage IN ('managed', 'legacy-singleton')),
                created_at TEXT NOT NULL,
                rotated_at TEXT,
                revoked_at TEXT,
                last_used_at TEXT,
                last_endpoint_class TEXT
                    CHECK (last_endpoint_class IN
                           ('models', 'chat', 'stream')
                           OR last_endpoint_class IS NULL),
                revision INTEGER NOT NULL DEFAULT 1
                    CHECK (revision >= 1)
            )
            """,
            """
            CREATE UNIQUE INDEX idx_connection_clients_active_label
                ON connection_clients(lower(label))
                WHERE revoked_at IS NULL
            """,
            """
            CREATE INDEX idx_connection_clients_state
                ON connection_clients(revoked_at, created_at, client_id)
            """,
            """
            CREATE TABLE connection_client_secrets (
                client_id TEXT NOT NULL
                    REFERENCES connection_clients(client_id) ON DELETE CASCADE,
                generation INTEGER NOT NULL CHECK (generation >= 1),
                fingerprint TEXT NOT NULL UNIQUE
                    CHECK (length(fingerprint) = 64
                           AND fingerprint GLOB '[0-9a-f]*'),
                state TEXT NOT NULL
                    CHECK (state IN ('ACTIVE', 'OVERLAP', 'RETIRED')),
                created_at TEXT NOT NULL,
                overlap_expires_at TEXT,
                retired_at TEXT,
                PRIMARY KEY (client_id, generation)
            )
            """,
            """
            CREATE INDEX idx_connection_client_secrets_auth
                ON connection_client_secrets(state, overlap_expires_at)
            """,
            """
            CREATE TABLE connection_access_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                disabled_at TEXT,
                revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1)
            )
            """,
            """
            INSERT INTO connection_access_state(id, enabled, revision)
                VALUES (1, 1, 1)
            """,
            # Preserve the migration-008 singleton as metadata only. Invalid
            # handcrafted legacy fingerprints remain untouched in their old
            # table but are not promoted into an authenticatable client.
            """
            INSERT INTO connection_clients (
                client_id, label, client_kind, scopes, active_fingerprint,
                active_generation, secret_storage, created_at, rotated_at,
                revoked_at, revision
            )
            SELECT 'legacy-install', 'Legacy installation', 'openwebui',
                   scopes, fingerprint, 1, 'legacy-singleton', created_at,
                   rotated_at, revoked_at, revision
              FROM gateway_credentials
             WHERE id = 1
               AND length(fingerprint) = 64
               AND fingerprint NOT GLOB '*[^0-9a-f]*'
            """,
            """
            INSERT INTO connection_client_secrets (
                client_id, generation, fingerprint, state, created_at,
                retired_at
            )
            SELECT 'legacy-install', 1, fingerprint,
                   CASE WHEN revoked_at IS NULL THEN 'ACTIVE' ELSE 'RETIRED' END,
                   created_at, revoked_at
              FROM gateway_credentials
             WHERE id = 1
               AND length(fingerprint) = 64
               AND fingerprint NOT GLOB '*[^0-9a-f]*'
            """,
        ),
    ),
    (
        12,
        "workload-profiles",
        (
            # ADR 010 / EXP-3: named goal profiles. All runtime-affecting
            # fields are explicit columns; arbitrary settings JSON is not a
            # profile persistence surface.
            """
            CREATE TABLE workload_profiles (
                profile_id TEXT PRIMARY KEY
                    CHECK (length(profile_id) BETWEEN 1 AND 64),
                owner TEXT NOT NULL
                    CHECK (owner IN ('builtin', 'user')),
                name TEXT NOT NULL
                    CHECK (length(trim(name)) BETWEEN 1 AND 80),
                purpose TEXT NOT NULL
                    CHECK (purpose IN
                           ('interactive', 'long_context', 'shared', 'cool',
                            'throughput', 'custom')),
                resolution_policy TEXT NOT NULL
                    CHECK (resolution_policy IN
                           ('interactive-v1', 'long-context-v1', 'shared-v1',
                            'cool-v1', 'throughput-v1', 'custom-v1')),
                schema_version INTEGER NOT NULL DEFAULT 1
                    CHECK (schema_version = 1),
                revision INTEGER NOT NULL DEFAULT 1
                    CHECK (revision >= 1),
                context_per_slot INTEGER
                    CHECK (context_per_slot BETWEEN 512 AND 262144
                           OR context_per_slot IS NULL),
                slots INTEGER
                    CHECK (slots BETWEEN 1 AND 8 OR slots IS NULL),
                kv_cache_type TEXT NOT NULL DEFAULT 'q8_0'
                    CHECK (kv_cache_type IN ('q8_0', 'q4_0')),
                batch_size INTEGER NOT NULL DEFAULT 1024
                    CHECK (batch_size BETWEEN 128 AND 2048
                           AND batch_size % 64 = 0),
                ubatch_size INTEGER NOT NULL DEFAULT 256
                    CHECK (ubatch_size BETWEEN 64 AND 512
                           AND ubatch_size % 64 = 0
                           AND ubatch_size <= batch_size),
                flash_attention TEXT NOT NULL DEFAULT 'auto'
                    CHECK (flash_attention IN ('auto', 'on', 'off')),
                optimization_preset_id TEXT NOT NULL DEFAULT 'balanced'
                    CHECK (optimization_preset_id IN
                           ('custom', 'cool-quiet', 'balanced', 'maximum')),
                thermal_policy TEXT NOT NULL DEFAULT 'standard'
                    CHECK (thermal_policy IN
                           ('standard', 'cool', 'throughput-guarded')),
                idle_policy TEXT NOT NULL DEFAULT 'KEEP_LOADED'
                    CHECK (idle_policy IN
                           ('KEEP_LOADED', 'STOP_AFTER', 'STOP_ON_DESKTOP')),
                stop_after_minutes INTEGER
                    CHECK (stop_after_minutes BETWEEN 5 AND 240
                           OR stop_after_minutes IS NULL),
                evidence_class TEXT NOT NULL DEFAULT 'ESTIMATED'
                    CHECK (evidence_class IN
                           ('MEASURED_LOCAL', 'HARDWARE_VALIDATED', 'ESTIMATED')),
                evidence_fingerprint TEXT
                    CHECK ((length(evidence_fingerprint) = 64
                            AND evidence_fingerprint NOT GLOB '*[^0-9a-f]*')
                           OR evidence_fingerprint IS NULL),
                evidence_recorded_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                deleted_at TEXT,
                CHECK ((idle_policy = 'STOP_AFTER' AND stop_after_minutes IS NOT NULL)
                       OR (idle_policy != 'STOP_AFTER'
                           AND stop_after_minutes IS NULL)),
                CHECK ((owner = 'builtin' AND purpose != 'custom')
                       OR (owner = 'user' AND purpose = 'custom'))
            )
            """,
            """
            CREATE UNIQUE INDEX idx_workload_profiles_active_name
                ON workload_profiles(lower(name))
                WHERE deleted_at IS NULL
            """,
            """
            CREATE INDEX idx_workload_profiles_owner_state
                ON workload_profiles(owner, deleted_at, name, profile_id)
            """,
            """
            ALTER TABLE runtime_config ADD COLUMN profile_revision INTEGER
                CHECK (profile_revision >= 1 OR profile_revision IS NULL)
            """,
            """
            ALTER TABLE runtime_config ADD COLUMN profile_fingerprint TEXT
                CHECK ((length(profile_fingerprint) = 64
                        AND profile_fingerprint NOT GLOB '*[^0-9a-f]*')
                       OR profile_fingerprint IS NULL)
            """,
            """
            ALTER TABLE known_good_runtime ADD COLUMN profile_revision INTEGER
                CHECK (profile_revision >= 1 OR profile_revision IS NULL)
            """,
            """
            ALTER TABLE known_good_runtime ADD COLUMN profile_fingerprint TEXT
                CHECK ((length(profile_fingerprint) = 64
                        AND profile_fingerprint NOT GLOB '*[^0-9a-f]*')
                       OR profile_fingerprint IS NULL)
            """,
            """
            INSERT INTO workload_profiles (
                profile_id, owner, name, purpose, resolution_policy,
                context_per_slot, slots, kv_cache_type, batch_size,
                ubatch_size, flash_attention, optimization_preset_id,
                thermal_policy, idle_policy, stop_after_minutes, evidence_class,
                created_at, updated_at
            ) VALUES
                ('builtin-interactive', 'builtin', 'Interactive',
                 'interactive', 'interactive-v1', NULL, 1, 'q8_0', 1024,
                 256, 'auto', 'balanced', 'standard', 'KEEP_LOADED', NULL,
                 'ESTIMATED', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                 strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                ('builtin-long-context', 'builtin', 'Long context',
                 'long_context', 'long-context-v1', NULL, 1, 'q8_0', 512,
                 128, 'auto', 'balanced', 'standard', 'KEEP_LOADED', NULL,
                 'ESTIMATED', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                 strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                ('builtin-shared', 'builtin', 'Shared',
                 'shared', 'shared-v1', NULL, 2, 'q4_0', 512,
                 128, 'auto', 'balanced', 'standard', 'KEEP_LOADED', NULL,
                 'ESTIMATED', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                 strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                ('builtin-cool', 'builtin', 'Cool',
                 'cool', 'cool-v1', NULL, 1, 'q8_0', 512,
                 128, 'auto', 'cool-quiet', 'cool', 'STOP_AFTER', 30,
                 'ESTIMATED', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                 strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                ('builtin-throughput', 'builtin', 'Throughput',
                 'throughput', 'throughput-v1', NULL, 1, 'q8_0', 1024,
                 256, 'auto', 'balanced', 'throughput-guarded', 'KEEP_LOADED', NULL,
                 'ESTIMATED', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                 strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
        ),
    ),
    (
        13,
        "notification-preferences-receipts",
        (
            # ADR 011 / EXP-4: preferences and redacted receipt identities.
            # Rendered title/body and raw source identities are never durable.
            """
            CREATE TABLE notification_preferences (
                category TEXT PRIMARY KEY
                    CHECK (category IN
                           ('MASTER', 'OPERATION_SUCCESS', 'OPERATION_FAILURE',
                            'THERMAL_WARNING', 'THERMAL_STOP',
                            'STORAGE_CRITICAL', 'BACKUP_FAILURE',
                            'BACKUP_STALE', 'REMOTE_SAFETY_DISABLE',
                            'APPLICATION_UPDATE')),
                enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1)
            )
            """,
            """
            INSERT INTO notification_preferences (
                category, enabled, created_at, updated_at, revision)
            WITH categories(category) AS (VALUES
                ('MASTER'), ('OPERATION_SUCCESS'), ('OPERATION_FAILURE'),
                ('THERMAL_WARNING'), ('THERMAL_STOP'), ('STORAGE_CRITICAL'),
                ('BACKUP_FAILURE'), ('BACKUP_STALE'),
                ('REMOTE_SAFETY_DISABLE'), ('APPLICATION_UPDATE'))
            SELECT category, 0,
                   strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                   strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), 1
              FROM categories
            """,
            """
            CREATE TABLE notification_receipts (
                receipt_key TEXT PRIMARY KEY
                    CHECK (length(receipt_key) = 64
                           AND receipt_key NOT GLOB '*[^0-9a-f]*'),
                category TEXT NOT NULL
                    CHECK (category IN
                           ('OPERATION_SUCCESS', 'OPERATION_FAILURE',
                            'THERMAL_WARNING', 'THERMAL_STOP',
                            'STORAGE_CRITICAL', 'BACKUP_FAILURE',
                            'BACKUP_STALE', 'REMOTE_SAFETY_DISABLE',
                            'APPLICATION_UPDATE')),
                source_class TEXT NOT NULL
                    CHECK (source_class IN
                           ('OPERATION', 'THERMAL', 'STORAGE', 'BACKUP',
                            'REMOTE_ACCESS', 'APPLICATION_UPDATE')),
                delivery_state TEXT NOT NULL
                    CHECK (delivery_state IN
                           ('DELIVERED', 'SUPPRESSED', 'FAILED')),
                reason_code TEXT NOT NULL
                    CHECK (reason_code IN
                           ('DELIVERED', 'PREFERENCE_DISABLED',
                            'CAPABILITY_UNAVAILABLE', 'DUPLICATE',
                            'RATE_GLOBAL', 'RATE_CATEGORY',
                            'DELIVERY_RESERVED', 'ADAPTER_FAILED',
                            'ADAPTER_TIMEOUT')),
                adapter_class TEXT NOT NULL
                    CHECK (adapter_class IN ('notify-send', 'unavailable')),
                first_attempt_at TEXT NOT NULL,
                last_attempt_at TEXT NOT NULL,
                delivered_at TEXT,
                occurrence_count INTEGER NOT NULL DEFAULT 1
                    CHECK (occurrence_count BETWEEN 1 AND 1000000),
                attempt_count INTEGER NOT NULL DEFAULT 0
                    CHECK (attempt_count BETWEEN 0 AND 2),
                revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1)
            )
            """,
            """
            CREATE INDEX idx_notification_receipts_rate
                ON notification_receipts(delivery_state, last_attempt_at,
                                         category)
            """,
            """
            CREATE INDEX idx_notification_receipts_retention
                ON notification_receipts(last_attempt_at, receipt_key)
            """,
        ),
    ),
)


class DatabaseTooNew(RuntimeError):
    """The on-disk schema is newer than this application supports."""


def open_database(
    database_path: str | Path,
    *,
    mode: str = "write",
    journal: str | None = None,
) -> sqlite3.Connection:
    """Open a connection under the single production PRAGMA contract.

    This is the one connection factory for initialization, units of work,
    import staging, queries, and tests. SQLite PRAGMAs are connection-local:
    any connection that bypasses this factory silently loses foreign-key
    enforcement and the busy-timeout discipline.

    Modes:
    - ``read``: the file must already exist; ``query_only=ON`` after setup so
      read units can never write. No journal/synchronous changes.
    - ``write``: published runtime policy — WAL journal + FULL synchronous.
    - ``migration``: same as ``write``; staging may pass ``journal="delete"``
      to build a self-contained rollback-journal file for atomic publication.

    Every PRAGMA result is fully consumed: an unconsumed statement keeps a
    read transaction open and would block WAL checkpoints later.
    """
    if mode not in ("read", "write", "migration"):
        raise ValueError(f"unknown database mode: {mode!r}")
    path = Path(database_path)
    if mode == "read" and not path.exists():
        raise FileNotFoundError(f"database does not exist: {path}")
    existed_before = path.exists()
    conn = sqlite3.connect(
        str(path),
        timeout=BUSY_TIMEOUT_MS / 1000.0,
        check_same_thread=False,
    )
    if not existed_before:
        # A freshly created database holds the entire durable state; keep
        # it private from the first moment (WAL sidecars inherit umask).
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    conn.row_factory = sqlite3.Row
    pragmas = [
        "PRAGMA foreign_keys=ON",
        f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}",
    ]
    if mode in ("write", "migration"):
        pragmas.append(f"PRAGMA journal_mode={journal or 'wal'}")
        pragmas.append("PRAGMA synchronous=FULL")
    for pragma in pragmas:
        conn.execute(pragma).fetchall()
    if mode == "read":
        conn.execute("PRAGMA query_only=ON").fetchall()
    return conn


# Backwards-compatible alias for the historical call sites; new code must
# name the mode explicitly.
connect = open_database


def initialize_and_close(database_path: str | Path) -> None:
    """Initialize a database file and deterministically close the connection.

    Composition owns no long-lived connection: services use short-lived
    per-command units of work, so the initialization connection must never
    depend on garbage collection for closure.
    """
    conn = initialize_file(database_path)
    conn.close()


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchall()
    if not rows:
        return set()
    return {int(r["version"]) for r in conn.execute("SELECT version FROM schema_migrations")}


class MigrationRegistryError(RuntimeError):
    """The declared migration registry violates the ordering contract."""


def _apply_versioned_data_migration(
    conn: sqlite3.Connection, version: int
) -> None:
    """Run guarded data-only work inside the migration transaction.

    Old qualification fixtures may truthfully record an early migration as
    applied while omitting an optional historical table.  Migration 013 must
    therefore inspect sqlite_master before honoring an explicit legacy
    notification opt-in; absence or malformed/non-true content remains off.
    """
    if version != 13:
        return
    has_settings = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'settings'"
    ).fetchone()
    if has_settings is None:
        return
    legacy = conn.execute(
        "SELECT value_json FROM settings WHERE key = 'notifications_enabled'"
    ).fetchone()
    if legacy is not None and legacy["value_json"] == "true":
        conn.execute("UPDATE notification_preferences SET enabled = 1")


def validate_registry(
    migrations: tuple[tuple[int, str, tuple[str, ...]], ...],
    schema_version: int,
) -> None:
    """Reject duplicate versions, gaps, non-positive versions, and a
    ``SCHEMA_VERSION`` that is not the highest declared version."""
    versions = [version for version, _name, _statements in migrations]
    if not versions:
        raise MigrationRegistryError("migration registry is empty")
    if any(not isinstance(v, int) or v <= 0 for v in versions):
        raise MigrationRegistryError("migration versions must be positive integers")
    if len(set(versions)) != len(versions):
        raise MigrationRegistryError(f"duplicate migration versions: {versions}")
    expected = set(range(1, max(versions) + 1))
    if set(versions) != expected:
        raise MigrationRegistryError(
            f"migration versions must be contiguous from 1; got {sorted(versions)}"
        )
    if schema_version != max(versions):
        raise MigrationRegistryError(
            f"SCHEMA_VERSION {schema_version} does not equal the highest "
            f"declared migration version {max(versions)}"
        )


def initialize(conn: sqlite3.Connection) -> int:
    """Apply pending migrations; returns the resulting schema version."""
    validate_registry(MIGRATIONS, SCHEMA_VERSION)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    applied = _applied_versions(conn)
    if applied:
        newest = max(applied)
        if newest > SCHEMA_VERSION:
            raise DatabaseTooNew(
                f"Database schema v{newest} is newer than supported v{SCHEMA_VERSION}; "
                "refusing to open (repair mode required)."
            )
    # Execute unapplied migrations in NUMERIC order even if a future edit
    # accidentally reorders declarations.
    for version, name, statements in sorted(MIGRATIONS, key=lambda m: m[0]):
        if version in applied:
            continue
        # Explicit transaction: sqlite supports transactional DDL, so a
        # failure rolls back every earlier statement AND keeps the
        # schema_migrations row from ever being written. (executescript()
        # cannot provide this: it commits pending work before running.)
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in statements:
                conn.execute(statement)
            _apply_versioned_data_migration(conn, version)
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) "
                "VALUES (?, ?, datetime('now'))",
                (version, name),
            )
        except BaseException:
            conn.rollback()
            raise
        conn.commit()
    applied_now = _applied_versions(conn)
    return max(applied_now) if applied_now else 0


def initialize_file(database_path: str | Path) -> sqlite3.Connection:
    """Create/initialize a database file with production permissions.

    The caller owns the returned connection and MUST close it; prefer
    :func:`initialize_and_close` at composition boundaries.
    """
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = open_database(path, mode="write")
    initialize(conn)
    Path(path).chmod(0o600)
    return conn


def integrity_ok(conn: sqlite3.Connection) -> bool:
    # fetchall() is deliberate: an unconsumed cursor holds a read
    # transaction open, which would block WAL checkpoints.
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    if [str(row[0]).lower() for row in rows] != ["ok"]:
        return False
    return not conn.execute("PRAGMA foreign_key_check").fetchall()
