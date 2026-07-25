---
artifact_id: beaaa-17900-adr-e2e-acceptance-fixes
title: "ADR — DP report E2E acceptance fixes (BEAAA-17900)"
owner_role: CTO
persona_audience: internal
issue: BEAAA-17900
parent_issue: BEAAA-17891
baseline_candidate_sha: 880edf19aaac3ca2220bbba33fea6d972dc88d08
classification: rule-execution + presentation-only output-shape change (CTO self-classified/co-signed)
scoring_impact: none (N=5 Δ total=0, Δ all pillars=0)
date: 2026-07-25
---

# ADR — DP report E2E acceptance fixes (BEAAA-17900)

## Context
BEAAA-17891 ran the evidence-backed E2E design-partner journey against the
QA-approved unified candidate `880edf19` (PR#147) and found three acceptance gaps
in the customer-facing Design-Partner (DP) report path:

1. **Output-language gate violation.** `dp.html` line 22 read
   *"Assessment result, not bound insurance coverage. No policy, binder, or quote is
   offered or implied. ARE Reports are produced under contract are-scanner-api-v1@v1.0."*
   — insurance / underwriting-decision language plus legacy "ARE" product wording,
   both prohibited on customer-facing output.
2. **No separate operational/legal readiness.** The DP report did not render
   operational readiness and legal readiness as distinct sections.
3. **Journey exercised fixture fallback**, not a real candidate-scanner execution,
   so it did not prove the authenticated real-scanner journey round-trips to HTML+PDF.

## Decision (CTO classification)
Presentation-only change to the DP HTML report and the assessment PDF. It touches
`render_report` (DP title), `_render_dp_report`, a new `_render_readiness_sections`,
and `render_assessment_pdf`. It changes **no** scoring, rubric, probe, judge, corpus,
scoring math, or API-output-schema field. The persisted BackBond Score is produced by
`produce_score`/`_run_scanner_adapter`, which are off the edited path.

Per contract `are-scanner-api-v1@v1.0` this is rule-execution for the language/legacy
fix and an output-shape (presentation) change for the readiness sections; the
output-shape change is CTO-classified and co-signed here. No API `output` field is
added or removed, so no Underwriter contract gate is triggered.

### Changes
- DP title: `ARE Assessment - Design Partner Report` → `BackBond Scanner Assessment - Design Partner Report`.
- Removed the insurance/underwriting/legacy disclaimer line.
- Added **Operational Readiness** (owner-attested control areas + any scan-unreached
  dimensions, reported separately from the technical scan) and **Legal Readiness**
  (technical-assessment posture: not a certification/warranty/guarantee/contractual
  commitment; no commercial or eligibility determination) sections to DP HTML and PDF.
- Explicit output-language gate enforced by regression: none of
  {insurance, underwriting, underwrite, policy, binder, premium, quote} nor legacy
  {"ARE Assessment", "ARE Reports"} may appear in the DP report.

### Regressions (failing-first verified against `880edf19`)
- `test_dp_report_output_language_gate_excludes_insurance_and_legacy_wording`
- `test_dp_report_separately_renders_operational_and_legal_readiness`
- `test_real_scanner_journey_persists_and_round_trips_reports` (full journey against a
  real scanner binary → `score_source == real_scanner_binary`, HTML+PDF round-trip,
  no insurance language).
All three fail on `880edf19` and pass on the candidate.

## Verification
- `tools/portal_v1/test_portal.py`: 66 passed (63 prior + 3 new).
- Issue repro suite (`tests/test_beaaa16995_framework_assessment.py`,
  `test_framework_envelope_round_trips_portal_html_json_and_pdf`,
  `tests/test_edge_gateway_beaaa11867.py`, `tests/test_scan_handlers_beaaa547.py`):
  95 passed (unchanged).
- N=5 development delta (baseline `880edf19` vs candidate): score_json byte-identical,
  Δ total=0, Δ GOV/ROB/BIAS/OPS=0. See
  `tools/calibration-deltas/2026-07-25-880edf19-vs-beaaa17900-e2e-fixes.md`.

## Consequences / rollback
Reversible: revert the single portal.py diff. No data migration, no score-history
impact. Promotion still requires QA `APPROVED` on the new exact candidate SHA and
Founder sign-off (BEAAA-15373); this ADR does not promote.
