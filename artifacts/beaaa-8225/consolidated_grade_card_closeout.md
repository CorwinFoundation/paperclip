---
artifact_type: report_card
owner: cto
persona_audience: internal
phase: 2
canonical_terms_only: true
focus_claim: "BEAAA-8225 produces the single consolidated Retell BackBond grade card needed for the outreach gate without changing Scanner rules."
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-8255", "BEAAA-8225", "BEAAA-8128", "BEAAA-8123"]
proof_points_cited: ["artifacts/beaaa-8225/consolidated_envelope_seed0.json", "artifacts/beaaa-8225/backbond_grade_card.html"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["Rule-execution; no rubric/scoring/admittance/pricing contract change."]
---

# BEAAA-8225 Consolidated Retell Grade Card

BLUF: one consolidated Retell scan envelope was run through admittance, pricing, and report-card render with BIAS live-fire and GOVERNANCE self-attestation included. N=5 spread is 0 on total and every pillar; measured dims are backed by non-placeholder `are-probe://.../sha256:` URIs.

- Contract: `are-scanner-api-v1@v1.3.0`
- scanner_version: `0.5.0`
- corpus_version: `retell-consolidated-full-coverage@2026-06-24`
- probe-set-version: `retell-consolidated-probe-set@2026-06-24`
- rubric_point_id: `P1.1, P1.2, P1.3, P1.4, P1.5, P1.6, P3.2, P3.3, ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL, ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER, ROBUSTNESS.PII.SCAN_TIME_DETECTION, P2.1, P2.3, P2.4, P2.5, P2.6, P3.1, P4.1, P4.2, P4.3, P4.4, P4.5, P4.6, GOVERNANCE.PII.HANDLING_ATTESTATION`
- are_total: `75`
- subscores: `{'OPS': 21.3542, 'BIAS_FAIRNESS': 18.75, 'ROBUSTNESS': 22.3958, 'GOVERNANCE': 12.5}`
- measured_dims_count: `24`
- na_dims_count: `1`
- pillars_with_all_na_count: `0`
- admittance: `REFER_TO_UW` via `ADM.R0`
- quote_status: `manual_referral`
- grade_card: `artifacts/beaaa-8225/backbond_grade_card.html`
- quote_reason: `admittance class REFER_TO_UW — rule ADM.R0 fired; route to manual UW queue (ADR-0007 §1.3).`
