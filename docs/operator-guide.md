# BC250 LLM MODE operator guide

This runbook covers an installed development candidate. Release evidence must
use `release/RUNBOOK.md` and `release/EVIDENCE_HANDOFF.md`; commands here do not
create or imply release, hardware, security, or human-acceptance evidence.

## Establish the baseline

Run read-only observations first:

```bash
bc250-llm-mode platform status
bc250-llm-mode status
bc250-llm-mode memory-profile
bc250-llm-mode boot-policy status
bc250-llm-mode desktop-integration status
bc250-llm-mode update status
bc250-llm-mode privacy
```

Expected hardware is AMD BC-250/GFX1013 with about 12 GiB GPU UMA, at least
about 3 GiB host RAM, all available compute units, adequate cooling, and a
standard-layout GGUF. The host must be systemd-based Bazzite or CachyOS for the
advertised integration. The next boot must remain graphical and model
auto-start must remain off.

## Observe and control the runtime

```bash
bc250-llm-mode llm status
bc250-llm-mode llm start
bc250-llm-mode llm stop
bc250-llm-mode llm restart
bc250-llm-mode thermals status
bc250-llm-mode models library
```

The systemd service is the sole model-server owner. Do not launch a competing
`llama-server`. Service activity alone is not inference health; use the app's
health/model observation or the authenticated connection probe before calling
a model active. A thermal stop is latched until cooling is inspected and an
explicit safe reset is permitted.

## Diagnose durable work

Use the GUI Activity page or:

```bash
bc250-llm-mode operations list
bc250-llm-mode operations list --active
bc250-llm-mode operations show OPERATION-ID
```

Operation leases and revisions fence stale owners. A process death after an
external effect may leave intent RUNNING until lease expiry; the next worker
probes before repeating the effect. `RECOVERY_REQUIRED` is a barrier, not a
retry suggestion. Preserve operation staging and follow the recorded recovery
recommendation or a typed Repair action.

## Maintenance, Repair, cleanup, and Undo

```bash
bc250-llm-mode maintenance status
bc250-llm-mode maintenance check
bc250-llm-mode repair list
bc250-llm-mode repair preview ACTION [TARGET]
bc250-llm-mode repair run ACTION [TARGET] --preview SHA256 --confirm TOKEN
bc250-llm-mode storage cleanup --dry-run --mode QUARANTINE
bc250-llm-mode undo list
```

Maintenance status does not run Doctor, hash trees, host commands, or update
network checks. Repair previews bind current evidence/revisions and expire.
Cleanup excludes external models, active/known-good/runtime/application/profile/
backup/credential/conversation/log data, symlinks, devices, sockets, and mount
crossings. PURGE is allowed only for expired quarantines and can stop at
`RECOVERY_REQUIRED` if a partial permanent effect cannot be rolled back.

## Backup and restore

```bash
bc250-llm-mode backup create before-maintenance
bc250-llm-mode backup list
bc250-llm-mode backup verify BACKUP-ID
bc250-llm-mode restore inspect BACKUP-ID
bc250-llm-mode restore start BACKUP-ID --confirmation-digest SHA256
bc250-llm-mode restore status OPERATION-ID
```

Models and runtime bytes are excluded by default. Encryption is unavailable
in this build and an encryption request is refused. Restore uses an exact
digest-bound preview, profile-exclusive barrier, atomic exchange where
supported, post-restore integrity checks, and retained prior profile. Physical
Linux exchange/inference evidence is still pending for the final candidate.

## Client access and emergency revocation

```bash
bc250-llm-mode connections status
bc250-llm-mode connections clients
bc250-llm-mode connections instructions pocketpal
bc250-llm-mode connections test CLIENT-ID
bc250-llm-mode connections revoke-client CLIENT-ID
bc250-llm-mode connections disable-all
```

Remote API traffic must use `https://<node>.<tailnet>.ts.net:10000/v1` through
the authenticated gateway and Tailscale Serve. Port 8080 remains loopback-only;
Funnel remains off. New or rotated keys are shown only in an interactive TTY or
the time-bounded GUI view. `disable-all` remains available when the model is
unhealthy.

The gateway runtime is installed disabled and is owned only for the current
boot by explicit client, Open WebUI, or sharing actions. Inspect it with:

```bash
bc250-llm-mode gateway service plan
bc250-llm-mode gateway service status
```

The expected listeners are exactly loopback `127.0.0.1:9071` and the observed
RFC1918 gateway of the dedicated `bc250-openwebui` bridge. `plan` refuses an
absent, host, or ambiguous bridge, an unfamiliar unit, or an existing unowned
listener. Do not replace that refusal with a wildcard bind or a hard-coded
bridge address. Starting the service never enables it for the next boot and
never starts the model through a systemd dependency.

## Runtime and application updates

`llamacpp update` changes the inference runtime through its durable build,
atomic publication, restart, and live-verification workflow. `llamacpp
rollback` restores the retained known-good runtime.

Application self-update is separate:

```bash
bc250-llm-mode update status
bc250-llm-mode update check
bc250-llm-mode update import-bundle /media/path/release.tar
bc250-llm-mode update preview VERSION
bc250-llm-mode update apply VERSION --preview SHA256 --confirm TOKEN
bc250-llm-mode update rollback
bc250-llm-mode update cleanup --dry-run
```

The current production channel is unavailable. Do not weaken that refusal.
Offline archives are streamed into private staging, reject unsafe tar members,
and must pass the same release verifier. Publication changes only the
digest-pinned `current`/`previous` application slots; post-update smoke starts
no model, gateway, Open WebUI, or Tailscale component.

## Logs and support handoff

```bash
bc250-llm-mode logs setup --lines 200
bc250-llm-mode logs server --lines 200
bc250-llm-mode doctor
bc250-llm-mode support-bundle --output /chosen/local/directory
```

The support bundle is bounded, redacted by construction, self-verifies its
manifest, and is never uploaded. Before sharing it, follow the evidence/privacy
review for the destination. Never paste credential values, authorization
headers, prompts, completions, raw remote bodies, host addresses, or private
paths into issue reports.

## Desktop return, uninstall, and reinstall

```bash
~/.bc250-llm-mode/app-venv/bin/bc250-llm-mode desktop-mode --now
bc250-llm-mode uninstall
bc250-llm-mode desktop-integration remove
```

Default uninstall preserves models, profile/database content, backups,
conversations, and the Open WebUI volume. `--remove-container` removes the
containers. `--remove-models` is a separate permanent deletion of app-managed
models only. Reinstall into the same user profile and rerun setup to rediscover
preserved managed and configured external GGUFs.

## Candidate qualification

After any package-code change, rebuild the exact candidate and recollect all
candidate-bound evidence. Run the separate Bazzite and CachyOS procedures in:

- `docs/gui-physical-qualification.md`
- `docs/connection-physical-qualification.md`
- `docs/profile-physical-qualification.md`
- `docs/notification-physical-qualification.md`
- `docs/repair-physical-qualification.md`
- `docs/application-update-physical-qualification.md`
- `docs/appliance-experience-physical-qualification.md`
- `release/EVIDENCE_HANDOFF.md`

Developer tests, old screenshots, plans, and pending worksheets never count as
physical or human PASS evidence.
