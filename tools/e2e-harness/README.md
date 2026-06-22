# Retell E2E production-like test harness + adversarial scenario kit (BEAAA-7446)

Retell AI is the locked vendor path for the production-like shadow test. The
harness drives the full chain a real scan exercises: `install -> load context ->
run adversarial traffic -> capture -> BackBond Scanner probe detection ->
Monitor post-bind log->claim`, in **SHADOW / no-pay** mode.

Parent: [BEAAA-7427](/BEAAA/issues/BEAAA-7427). Credentials for the live Retell
run remain tracked on [BEAAA-7424](/BEAAA/issues/BEAAA-7424) and execution
against real Retell traffic remains [BEAAA-7445](/BEAAA/issues/BEAAA-7445).

## What's in the box

| Piece | Path | Done item |
|---|---|---|
| Adversarial scenario kit (P1–P6) | `src/e2e_harness/scenario_kit.py` | #1 |
| Capture adapters (Retell live path + fixture; Voiceflow/Vapi remain generic placeholders) | `src/e2e_harness/adapters/` | #2 |
| Probe detection → Monitor claim pipeline | `src/e2e_harness/pipeline.py` | #3 |
| Run-book entrypoint | `src/e2e_harness/runbook.py` + this doc | #4 |
| Unit-exercise against fixtures | `tests/test_e2e_harness.py` | Done |

## Peril coverage (canonical paid-peril taxonomy)

The kit probes every underwritten peril band, with explicit coverage of the two
highest-severity perils — **unauthorized money movement** and **tool misuse**:

| Band | Peril | Scenario family | Measured on |
|---|---|---|---|
| P1 | Wrong-recipient / misdirected action | `unauthorized_money_movement` | BackBond Scanner (money-movement verifier) |
| P2 | Unauthorized commitment / runaway | `tool_misuse_unauthorized_call`, `tool_misuse_param_tamper`, `stop_instruction_refusal` | BackBond Scanner (R2.ROB benches, stop-instruction probe) |
| P3 | Data leak (PII / secret exfil) | `pii_exfiltration` | Monitor-primary (BackBond Scanner PII bench wired at run) |
| P4 | Hallucination | `grounded_hallucination` | Monitor (signal-envelope seed) |
| P5 | Prompt injection | `indirect_prompt_injection` | Monitor (`detect_p5_events`, live today) |
| P6 | Regulatory response | `unauthorized_regulatory_commitment` | Monitor-primary (future detector) |

## Run it

### Shadow self-test (no vendor, no credentials — this is what CI runs)

```bash
cd tools/e2e-harness
python -m pytest tests/ -q                       # unit-exercise the whole chain
PYTHONPATH=src python -m e2e_harness.runbook --fixture --out report.json
```

Emits a measured report and a human summary, e.g.:

```
E2E harness report — vendor=fixture mode=shadow
  perils covered:            P1, P2, P3, P4, P5, P6
  ARE detections fired:      4   (money-movement 1, tool-misuse 2, stop-instruction 1)
  Monitor claim candidates:  1   (P5.prompt_injection.indirect_injection_marker)
```

### Live Retell run (parameterized on credentials)

```bash
PYTHONPATH=src python -m e2e_harness.runbook \
    --vendor retell --creds-file creds.json --retell-agent-id "$RETELL_AGENT_ID" --out report.json
```

Retell accepts either `--retell-agent-id` for bounded `list-calls` backfill or
`--retell-call-id` for one known call. `RETELL_API_KEY` can live in the
credentials JSON or the process environment. The live path fails before network
use when the requested run exceeds 20 calls or 100 QA minutes.

## Firewall discipline (Δ=0)

Same posture as the internal dogfood (BEAAA-7320 / 7346):

- The scanned target is an **explicit test target, not customer traffic** —
  shadow / no-pay, no carrier / reinsurance / counsel / live-bind gate is tripped.
- The package imports the **shipped** probe families and the **shipped** Monitor
  normalizer, but imports **no** pricing / portal / band / rubric-weight /
  scoring-engine module (enforced by `test_harness_does_not_couple_to_pricing_or_portal`).
- It is **purely additive** — a new `tools/e2e-harness/` tree, touching zero
  existing files — so it moves **no golden score**. No Underwriter co-sign
  required (offline, additive; mirrors BEAAA-7255 / 7235 / 7346).
- Capture **retains no raw transcripts**; redaction is applied on receipt and
  prompt-injection markers are preserved as structured `detector_features`
  (so P5 fires on the redacted event — the dogfood invariant).

## What is parameterized on Retell credentials

1. **Scoped credentials**: `RETELL_API_KEY`.
2. **Capture selector**: `--retell-agent-id` or `--retell-call-id`.
3. **Budget caps**: `--max-calls` and `--max-qa-minutes`, both bounded inside
   the free envelope.
4. **Export-shape drift**: if Retell changes a payload field, the fix is
   localized to `RetellAdapter.to_session`; the manifest, probe, and Monitor
   wiring downstream are untouched.

Build time remaining at run: none expected; credentials and a Retell agent/call
identifier are the live inputs.
