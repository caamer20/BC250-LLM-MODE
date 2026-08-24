# BC250 LLM MODE — ADR 003: Managed Model Artifacts

**Status:** Accepted (Session 6A / U1.1)

## Context

Before Session 6A, models were downloaded into `models_dir/<id>/source` by an
unbounded shell-out (`hf download` inside a container) and "prepared" in
place. There was no content identity, no quarantine, no deduplication, and a
frontend could activate a model directly from an arbitrary external path or
from unvalidated download staging.

Session 6A introduces durable `MODEL_ACQUIRE v1` / `MODEL_IMPORT v1`
operations backed by content-addressed managed storage.

## Decision

1. **Content-addressed managed namespace.** Every new validated artifact is
   published atomically (no-replace) to
   `<models_dir>/.bc250-artifacts/sha256/<digest[:2]>/<digest>.gguf`. The
   full SHA-256 digest is the artifact identity; filenames, aliases, catalog
   IDs, and HTTP success are never proof of content.
2. **No final path before validation.** Staging lives under
   `<models_dir>/.bc250-staging/<operation-id>/` and quarantine under
   `<models_dir>/.bc250-quarantine/<operation-id>/`; neither is visible to
   the query layer as installed.
3. **Trust states are closed:** `VERIFIED`, `UNVERIFIED`, `QUARANTINED`,
   with storage states `MANAGED`, `QUARANTINED`, `LEGACY_EXTERNAL`.
   Only `MANAGED`+`VERIFIED` artifacts receive installation aliases; legacy
   installations keep explicit `LEGACY_EXTERNAL/LEGACY_UNVERIFIED` status.
4. **One storage mutation owner.** Acquisition/import operations hold the
   durable `model-storage` lease plus one persisted logical reservation row
   per operation. Reservations are logical, not physical disk guarantees;
   free space is rechecked at every growth boundary.
5. **Deduplication by digest.** Publishing an exact existing digest reuses
   the managed artifact; a path collision with different bytes is
   `RECOVERY_REQUIRED`.
6. **Quarantine is a safe terminal.** Invalid complete candidates terminate
   `FAILED_SAFE / ARTIFACT_QUARANTINED` with an inspectable record and no
   installation alias. Removal is deferred to the later storage slice.
7. **Acquisition never activates.** Install-and-use surfaces run two
   separate durable operations in sequence.

## Consequences

- Migration 004 adds `model_artifacts`, links `model_installations.artifact_id`,
  and backfills deterministic `legacy:<installation id>` rows without
  reading user files.
- The interim backend route for Open WebUI and any future consumers uses the
  same managed namespace; nothing outside `artifact_storage.py` may write to
  it.
- Quarantine removal, garbage collection, and storage migration remain
  future work and are deliberately not decided here.
