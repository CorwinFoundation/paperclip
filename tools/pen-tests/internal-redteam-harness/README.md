---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-7321 pre-stages HG#5 internal tamper regressions so vendor pen-test findings become single-case additions instead of new harness design."
contracts_referenced: ["are-scanner-api-v1.md@v1.0", "scanner-implementation-supervision-v1.md@v1.1"]
ticket_refs: ["BEAAA-7321", "BEAAA-6185", "BEAAA-6186", "BEAAA-6199"]
proof_points_cited: []
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["No escalation: rule-execution row 'Smoke test for an existing operation'; no rubric, scoring math, judge-model, contract-shape, or tamper-posture change."]
scanner_version: "0.4.1"
corpus_version: "corpus-v0.1.0"
probe_set_version: "internal-redteam-harness-v0.1"
rubric_point_id: "HG5.TAMPER_RESISTANCE"
n5_calibration_delta: "tools/calibration-deltas/2026-06-22-beaaa7321-tamper-redteam-harness-n5.md; max numeric spread 0; unique canonical envelopes 1"
cto_cosign_ref: null
brand_backbone_version: null
brand_backbone_sha256: null
brand_backbone_verified: null
---

# BEAAA-7321 Internal Tamper Red-Team Harness

## BLUF

`tests/test_scanner_tamper_red_team.py` is the pre-staged internal harness for HG#5 surfaces. It is confirmation and regression coverage only; it does not replace the independent signed pen-test report tracked through BEAAA-6185 / BEAAA-6186 / BEAAA-6199.

## Classification

Rule-execution under `scanner-implementation-supervision-v1.md@v1.1`, table row: "Smoke test for an existing operation." The harness adds tests and handoff structure only. It does not change BackBond Scanner scoring math, rubric points, pillar weights, judge models, contract output shape, evidence requirements, or tamper-resistance posture.

## Surface Map

| Surface | Harness cases | What the case proves |
| --- | --- | --- |
| Intake guard | `test_rejects_non_numeric_raw_smuggling`; `test_rejects_evidence_uri_and_control_character_injection` | Adversarial `eval_outputs` are rejected before scoring when raw values are non-numeric, evidence URIs are unsafe, or string fields contain control characters. |
| Judge invocation path | `test_parity_judge_treats_agent_json_injection_as_response_text`; `test_counterfactual_judge_majority_ignores_stable_label_in_responses` | Crafted agent outputs remain data inside judge prompts; the scanner parses pinned judge output only, then applies deterministic ensemble aggregation. |
| Report-card renderer | `test_probe_artifact_html_is_escaped_not_executed`; `test_probe_artifact_cannot_tamper_displayed_score` | Probe artifacts are HTML-escaped and cannot inject script tags or overwrite the rendered score band. |
| Forensic bundle signer | `test_signing_key_is_required_unless_dev_key_explicitly_enabled`; `test_verify_on_read_rejects_evidence_tamper_and_signature_forgery` | Signing keys are not silently defaulted in production, and verify-on-read rejects evidence replay or forged signatures. |

## Vendor-Finding Slot Protocol

When the external report lands, add one regression case per vendor finding:

1. Add the case to `tests/test_scanner_tamper_red_team.py` under the matching surface class.
2. Name it `test_vendor_finding_<report_id>_<short_surface>`.
3. Keep it offline and deterministic. If a finding needs live infrastructure, add a small fixture that models the boundary and open a separate implementation issue for the live path.
4. If the failing case exposes a bug under the existing posture, fix it in the same PR after committing the failing test.
5. If the fix would amend tamper-resistance posture, stop and route to CTO for Founder-fenced handling.

Empty slot directory: `tools/pen-tests/internal-redteam-harness/vendor-findings/`.

## Repro Notes

Targeted run:

```bash
pytest tests/test_scanner_tamper_red_team.py
```

N=5 delta: `tools/calibration-deltas/2026-06-22-beaaa7321-tamper-redteam-harness-n5.md`. Max numeric spread 0; unique canonical envelopes 1. The harness does not emit or mutate BackBond Scanner scores; it exercises input, rendering, and signing boundaries around existing code paths.
