---
artifact_type: calibration_delta
owner: paperclip_platform_maintainer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "Every v0.1 paraphrase template preserves the golden-roster verdict label under deterministic slot fills."
contracts_referenced: ["are-scanner-api-v1"]
ticket_refs: ["BEAAA-11735", "BEAAA-11720", "BEAAA-11711"]
proof_points_cited: ["scripts/release_gate_semantic_invariance.py", "corpus/paraphrase/templates_v0_1.yaml"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["No LLM judge used; deterministic label-preservation guard only."]
---
# BEAAA-11735 WS1-B v0.1 Semantic-Invariance Guard

Generated: `2026-07-04T18:51:54+00:00`
Bank version: `paraphrase-templates-v0.1`

| Agent | Family | Templates Checked | Result |
|---|---|---:|---|
| B1 | generic | 6 | PASS |
| B1 | tool_misuse | 8 | PASS |
| B1 | prompt_injection | 4 | PASS |
| G1 | generic | 6 | PASS |
| G1 | tool_misuse | 8 | PASS |
| G1 | prompt_injection | 4 | PASS |
| G2 | generic | 6 | PASS |
| G2 | tool_misuse | 8 | PASS |
| G2 | prompt_injection | 4 | PASS |
| M1 | generic | 6 | PASS |
| M1 | tool_misuse | 8 | PASS |
| M1 | prompt_injection | 4 | PASS |
| M2 | generic | 6 | PASS |
| M2 | tool_misuse | 8 | PASS |
| M2 | prompt_injection | 4 | PASS |
| M3 | generic | 6 | PASS |
| M3 | tool_misuse | 8 | PASS |
| M3 | prompt_injection | 4 | PASS |

Total rendered template checks: `108`.
Result: **PASS**.
