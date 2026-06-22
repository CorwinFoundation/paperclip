---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-7446 makes the Retell AI test surface runnable in shadow without altering BackBond Scanner scoring rules or customer gates."
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-7446", "BEAAA-7427", "BEAAA-7424", "BEAAA-7346", "BEAAA-7320"]
proof_points_cited: ["Focused pytest 21 passed", "Generated retell-e2e-shadow-report-v0.1.json"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["No CTO co-sign required: rule-execution table rows for customer onboarding harness and smoke test; no scoring math, rubric, judge, contract shape, or tamper posture change."]
---

# BEAAA-7446 Retell E2E Harness Closeout v0.1

BLUF: Retell AI is now the locked live adapter path for the E2E harness. The shadow fixture run covers P1-P6, fires one money-movement detection and two tool-misuse detections through the BackBond Scanner probe surface, and surfaces one Monitor claim candidate without importing scoring, pricing, band, portal, or rubric modules.

## Decision Sequence

Deliverable: This is a customer onboarding harness plus smoke test for CTO/parent BEAAA-7427 that makes the Retell AI path executable in shadow within the free-envelope budget.

Classification: rule execution under `scanner-implementation-supervision-v1.md@v1.1` rows "Customer onboarding harness for a new customer archetype" and "Smoke test for an existing operation." No rule-change fields were touched.

Implementation insight: The harness keeps Retell-specific capture at the adapter edge (`call_analyzed`, `get-call`, `list-calls`) and normalizes into the existing schema-v1.0 dogfood redaction path. It preserves CTO discipline by carrying pinned Scanner metadata in the report, enforcing a free-envelope preflight, and keeping score computation out of the harness firewall.

Contract anchor: `are-scanner-api-v1@v1.0`.

## Changed

- `tools/e2e-harness/src/e2e_harness/adapters/retell.py`: added `call_analyzed` unwrapping, nested Retell function parsing, flat transcript fallback, REST fetch for `get-call`/`list-calls`, `RETELL_API_KEY` gate, and free-envelope checks.
- `tools/e2e-harness/src/e2e_harness/scenario_kit.py`: changed the scenario kit version to `retell-e2e-scenario-kit@0.1.0` and aligned the action surface to Retell custom functions: `book`, `pay`, `transfer`, `account_lookup`.
- `tools/e2e-harness/src/e2e_harness/pipeline.py`: added `scanner_version: v1.0`, `scanner_tag: aa33715d`, `rubric_version: 1.2.0`, `corpus_version`, `probe_set_version`, `contract_version`, `seed`, free-envelope metadata, and explicit `shadow_score.status: not_computed_shadow_firewall`.
- `tools/e2e-harness/src/e2e_harness/runbook.py`: added Retell live parameters `--retell-agent-id`, `--retell-call-id`, `--max-calls`, and `--max-qa-minutes`.
- `tools/e2e-harness/tests/test_e2e_harness.py`: added coverage for Retell custom functions, `call_analyzed` webhook normalization, free-envelope failures, and report metadata pins.
- `artifacts/beaaa-7446/retell-e2e-shadow-report-v0.1.json`: generated fixture report.

## Verification

- `PYTHONPATH=tools/e2e-harness/src:tools/post-bind-ingest/src:. pytest -q tools/e2e-harness/tests/test_e2e_harness.py tools/post-bind-ingest/tests/test_no_score_imports.py`
- Result: `21 passed in 0.73s`.
- `PYTHONPATH=tools/e2e-harness/src:tools/post-bind-ingest/src:. python3 -m e2e_harness.runbook --fixture --out artifacts/beaaa-7446/retell-e2e-shadow-report-v0.1.json`
- Result: 8 scenarios, P1-P6 covered, 4 BackBond Scanner detections fired, 1 Monitor claim candidate.

## Required Operational Metadata

- `scanner_version`: `v1.0`
- `scanner_tag`: `aa33715d`
- `corpus_version`: `retell-e2e-scenario-kit@0.1.0`
- `probe-set-version`: `retell-e2e-probe-set@0.1.0`
- `rubric_point_id`: `ROBUSTNESS.tool_misuse.money_movement.*`, `ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL`, `ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER`, `GOVERNANCE.autonomy_bound.authority_framed`, `P3.2`/`P3.1` Monitor P5 evidence refs
- `N=5 calibration delta`: `Delta=0 by firewall: harness imports no scoring/pricing/band/portal/rubric modules and does not compute or mutate BackBond Score; verified by no-score-import tests. No golden set score path changed.`
- `cto_cosign_ref`: `not required; rule execution`
- `upstream_eval_version`: `money-movement-passk@0.1.0; tau-bench@59a200c6d575d595120f1cb70fea53cef0632f6b; tau2-bench@5ebebbe827b455b3ed04fcb9294235c6ef4e5fd6`

## Audit

Glossary: clean for active BackBond naming in explanatory prose; legacy `are-scanner-api-v1` and JSON field names remain contract-locked.

FAL: `agents/cmo-os.md` was not available in the instruction bundles during this heartbeat. House-style final voice checks were run instead; no customer names, carrier names, pricing commitments, or external-bound copy shipped.

Remaining: live Retell traffic execution is still BEAAA-7445, blocked on Founder credentials from BEAAA-7424. This issue's harness scope is complete.
