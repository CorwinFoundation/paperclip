---
artifact_type: calibration_delta
owner: paperclip_platform_maintainer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "WS1-B paraphrase-off default does not move the locked golden roster outside the ±1 release band."
contracts_referenced: ["are-scanner-api-v1"]
ticket_refs: ["BEAAA-11735", "BEAAA-11720", "BEAAA-11711"]
proof_points_cited: ["scripts/release_gate_ws1b_n5.py", "tools/calibration-deltas/2026-07-04-beaaa11735-ws1b-paraphrase-off-n5.json"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["No scoring logic changed; paraphrase is default-off in this gate."]
---
# BEAAA-11735 WS1-B Paraphrase-Off N=5

Generated: `2026-07-04T18:51:54+00:00`

| Agent | Δ total | Δ GOVERNANCE | Δ ROBUSTNESS | Δ BIAS_FAIRNESS | Δ OPS | spread (total) | max pillar spread |
|---|---:|---:|---:|---:|---:|---:|---:|
| B1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| G1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| G2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| M1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| M2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| M3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

Result: **PASS**.
