---
artifact_type: report
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-12312 preserves BackBond Scanner pricing discipline by ensuring Probe SDK placeholders are surfaced as uncovered dimensions, not scored evidence."
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-12312"]
proof_points_cited: []
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["CTO co-sign required: placeholder removal moves the Probe SDK fixture score by more than ±1 versus the prior placeholder-bearing posture."]
---

# BEAAA-12312 Probe SDK Placeholder Scoring Delta

BLUF: Probe SDK placeholder dimensions no longer produce score-bearing BackBond Scanner output. A `pre_bind` SDK run with any remaining `r1_placeholder` dimension exits non-zero before invoking the Scanner; score-bearing output remains available only when every active dimension is measured or DP-attested.

## Classification

- Deliverable: bug fix for CTO / Underwriter evidence discipline that prevents Probe SDK placeholder dimensions from carrying score.
- Scope: Eval & Calibration seat owns Probe SDK wrapper and N=5 harness-adjacent evidence checks.
- Contract anchor: `contracts/are-scanner-api-v1.md@v1.0`; `contracts/scanner-implementation-supervision-v1.md@v1.1` rule-execution row for wrapper plumbing. The ticket's explicit approval gate requires Underwriter co-sign because the patch changes admissible score-bearing evidence behavior.
- Harness layer: not a harness-itself change; N=5 SDK output still must be byte-identical across runs.

## Patch

- `engineering/probe-sdk/src/are_probe/eval_results_builder.py`: omits `r1_placeholder` dimensions from `eval_results.json`; records them in `applicability.json` only as non-scoring metadata with `na_justification_key=probe_sdk.r1_placeholder`.
- `engineering/probe-sdk/src/are_probe/cli.py`: refuses score-bearing output when any `r1_placeholder` remains, before calling `run_master_scan`.
- `engineering/probe-sdk/tests/test_placeholder_gate.py`: proves placeholder-bearing default pre-bind output exits non-zero and writes no envelope even when `scanner_bin_default=/bin/false`, demonstrating the Scanner subprocess was not accepted.
- `engineering/probe-sdk/tests/test_n5_determinism.py`: score-bearing N=5 path now passes explicit DP attestations for unmeasured dimensions.
- `engineering/probe-sdk/lockfile.toml`: build hash updated to `b9ee4927aed6440cc49e31bf2a5a5f0644dfd1c5115fcce187521b1a4a5fd541`.

## N=5 Score-Bearing DP-Attested Candidate

| Run | Total | GOV | ROB | BIAS | OPS | Uncovered dims |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 64 | 15.6250 | 17.1875 | 15.6250 | 15.6250 | 19 |
| 1 | 64 | 15.6250 | 17.1875 | 15.6250 | 15.6250 | 19 |
| 2 | 64 | 15.6250 | 17.1875 | 15.6250 | 15.6250 | 19 |
| 3 | 64 | 15.6250 | 17.1875 | 15.6250 | 15.6250 | 19 |
| 4 | 64 | 15.6250 | 17.1875 | 15.6250 | 15.6250 | 19 |

Spread: total 0; GOVERNANCE 0; ROBUSTNESS 0; BIAS_FAIRNESS 0; OPS 0. Harness-adjacent determinism holds.

Default placeholder-bearing pre-bind posture now has no BackBond Score output. The SDK returns non-zero before Scanner invocation while any active dimension remains `r1_placeholder`.

## Verification

- `pytest -q engineering/probe-sdk/tests/test_eval_results_builder.py engineering/probe-sdk/tests/test_placeholder_gate.py` → 2 passed.
- `ARE_SCAN_BIN=/mnt/paperclipdata/dot-paperclip/instances/default/companies/59f8876e-e729-4dda-98f9-1317c2b50492/projects/are-scanner/bin/are-scan pytest -q engineering/probe-sdk/tests/test_n5_determinism.py` → 5 passed.
- `ARE_SCAN_BIN=/mnt/paperclipdata/dot-paperclip/instances/default/companies/59f8876e-e729-4dda-98f9-1317c2b50492/projects/are-scanner/bin/are-scan pytest -q engineering/probe-sdk/tests` → 45 passed.

## Required Metadata

- scanner_version: `0.5.0`
- corpus_version: `0.1.0`
- probe-set-version: `0.1.0`
- rubric_point_id: `P1.1-P1.6`, `P2.1-P2.6`, `P3.1-P3.3`, `ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL`, `ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER`, `ROBUSTNESS.PII.SCAN_TIME_DETECTION`, `P4.1-P4.6`, `GOVERNANCE.PII.HANDLING_ATTESTATION`
- N=5 calibration delta: score-bearing DP-attested candidate spread 0 on total and every pillar; placeholder-bearing pre-bind path exits non-zero and emits no score.
- upstream_eval_version: N/A, no upstream eval version bump.
- underwriter_cosign_ref: pending per BEAAA-12312 approval gate.
- cto_cosign_ref: not required unless CTO classifies this as a rule-change during review.
- merged_sha_on_canon_master_at_close: pending; do not close `done` until a reachable merge SHA exists.

## Rollback

Revert the SDK code/test changes and restore lockfile build hash `48090588e213c4e80194dcf419e3b81dad74a4cc1d17618217386459d7fa9433`. This returns the SDK to the prior placeholder-bearing behavior.
