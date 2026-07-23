---
artifact_type: calibration_delta
owner: scanner_engineer_2
persona_audience: internal
canonical_terms_only: true
contracts_referenced: ["are-scanner-api-v1@v1.0"]
ticket_refs: ["BEAAA-15476", "BEAAA-15486"]
scanner_version: "0.4.1"
corpus_version: "2026.6.24"
probe-set-version: "runner@784778473d3e"
rubric_point_id: "P1.1-P1.6,P2.5,P3.1-P3.3,P3.5-P3.6"
n5_calibration_delta: "0.0000 total and every pillar for seeds 0-4"
model_versions: ["syllable-insurance-demo@apichat.syllable.cloud", "anthropic:claude-sonnet-4-6"]
seed: [0, 1, 2, 3, 4]
cto_cosign_ref: null
self_audit_passed: true
---

# BEAAA-15476 N=5 calibration delta

## BLUF

The immutable Batch 1 manifest freeze produces delta `0.0000` on the total and every pillar for each seed 0-4. The candidate adds only an offline manifest, its fail-closed validator, checksum, and this report; it is not imported by the Scanner, runtime registry, rubric, scoring engine, or evidence emission path.

| Seed | Total delta | OPS | BIAS_FAIRNESS | ROBUSTNESS | GOVERNANCE |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| 1 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| 2 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| 3 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| 4 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

This is a structural parity result: baseline and candidate execute the identical Scanner code/config/corpus for all five seeds because the new files are disconnected activation inputs. Activation and execution remain separately gated on BEAAA-15477, where real N=5 scoring evidence is mandatory before any merge or promotion request.

Plan input consumed: Plan A v0.2, Paperclip comment revision `e56f7865-d14e-4605-a780-090c8bc6c10d`, attachment SHA-256 `956579546ed88b6c5e19d27fcf159322b0543384a54abfc01d8a16e41e98ce93`.
