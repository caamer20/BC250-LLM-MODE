# Restore preservation contract (RV-3 amendment to ADR 006)

Restore retains the atomic whole-profile exchange. The candidate combines a
verified database backup with current local assets. Restoring a configuration
backup never deletes unarchived local files.

| Area | Source after restore |
| --- | --- |
| General settings, setup, historical benchmark data | Verified backup |
| Model service endpoint/container/runtime settings and desktop-next-boot policy | Current database, consistent with retained launcher/handoff |
| Application venv, installed slots and current/previous pointers | Current local profile |
| Model bytes, runtime trees, launcher and handoff | Current local profile |
| Conversations, drafts, exports, logs and backup archives | Current local profile |
| Current model/runtime identities and workload profiles | Current database; historical restore cannot select missing assets |
| Thermal latch and baseline | Current database; never cleared by restore |
| Client credentials, fingerprints, revocations and integration state | Current local profile and database; never exported as plaintext secrets |
| Operations, events, leases, storage reservations and workers | Current database; no old operation is revived |
| Update installation identities and receipts | Current database |

A shared/exclusive advisory lock is stored beside the profile, outside the
exchanged directory. Database access, durable operations and atomic file writers
respect it. Restore holds exclusive ownership through completion. A stable
receipt records exact directory identities before exchange; interrupted
publication blocks unrelated writes until recovery. The model service must be
inactive. No restore automatically starts inference or enables a boot service.

The candidate is checked before publication. Failed post-verification exchanges
the exact prior directory back and verifies its identity. Ambiguous identities
retain both copies for Repair. The GUI requires reopening after restore so
long-lived log descriptors and widgets bind to the restored profile.

Physical crash, reboot and multi-process qualification remains required.
