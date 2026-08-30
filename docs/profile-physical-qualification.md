# Workload profile physical qualification

This checklist collects the hardware evidence required by EXP-3. Developer
tests and simulated measurements do not satisfy it. Run it on the exact package
candidate on a physical AMD BC-250 under both advertised native host profiles:
Bazzite and CachyOS.

Record no prompt or completion content. Record only the candidate commit,
package/artifact digest, host profile, GPU/driver/runtime identity, model content
digest and quant, exact profile revision/fingerprint, bounded performance
metrics, thermal observations, and terminal operation evidence.

For each host, qualify both a small standard-layout model and a validated 9B
standard-layout model:

- [ ] Interactive: preview, apply, inference, calibrate, separately apply the
  accepted proposal, then restore the known-good runtime.
- [ ] Long context: verify resolved context and multiplied KV fit; confirm any
  `TIGHT` run explicitly; verify cancellation between candidates restores the
  exact prior runtime.
- [ ] Shared: exercise the resolved concurrent slot count with independent
  clients and verify total-context/VRAM and response behavior.
- [ ] Cool: verify the conservative runtime shape, `STOP_AFTER` threshold,
  request/operation suppression, and thermal margin behavior.
- [ ] Throughput: keep it `ESTIMATED` unless the exact local evidence
  fingerprint matches; verify no legacy benchmark row upgrades its label.

For every run capture:

- [ ] preflight fit verdict, required/headroom GiB, thermal latch/readiness, and
  whether known-good restoration is available;
- [ ] each candidate fingerprint, TTFT, prompt rate, generation rate, peak
  temperature, throttling class, and complete/partial status;
- [ ] cancellation and process-death/takeover evidence showing no duplicate
  trial and an exact restored baseline;
- [ ] final model/context/slots/profile identity, systemd owner, health, and one
  bounded inference probe;
- [ ] reboot to the normal desktop with no LLM auto-start.

Do not turn observations from one host, model, quant, runtime build, or profile
fingerprint into a general BC-250 claim. Attach the completed record through the
candidate-bound release evidence process. Until both host profiles pass, EXP-3
physical qualification remains pending and release eligibility remains
unchanged.
