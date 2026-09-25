# BEAAA-35843 N=5 golden zero-delta

Candidate: `7d8658fd96f6b71010c4a8a4b22a0573c556a22a` plus the evidence-only capture harness.
Baseline: `origin/master` at `15bd39532ccb2367462798b9a240ee493c2d312e`.
Harness: `scripts/beaaa33175_n5_calibration.py`, seeds 0–4.
Contract: `contracts/are-scanner-api-v1.md@v1.3.0`.

| Agent | Total delta | BIAS_FAIRNESS | GOVERNANCE | OPS | ROBUSTNESS | Max N=5 spread |
|---|---:|---:|---:|---:|---:|---:|
| B1 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| G1 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| G2 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| M1 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| M2 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| M3 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

Result: PASS. All 13 rows remain `reported_not_scored`; the live capture contains 12 report-only verdicts and one explicit N/A (case 008), with `scored: 0`.
