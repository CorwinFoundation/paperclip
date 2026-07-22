---
artifact_type: implementation_closeout
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "Reconciles the Wave 1A reported-not-scored candidate onto canonical master without restoring deleted framework files."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-16967", "BEAAA-16945"]
proof_points_cited: ["origin/master 73250f88186b6a840258d0aa8a46a1ac7c08efff", "source commits cd349cd4b2d205d69492ad94c4088a7f7fb1f000 and b633ad9a43a842cf7c229a40a2823dc7f20258a7"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# Wave 1A Reconciliation Report

BLUF: BEAAA-16967 ports the two Wave 1A scenario families to canonical master as reported-not-scored evidence only. It does not merge or promote the candidate.

Replacement branch: `beaaa-16967-wave1a-reconcile`.
Replacement SHA: recorded in the BEAAA-16967 closeout after final commit.

Classification: rule-execution under `scanner-implementation-supervision-v1@v1.1`, table row "refactor that doesn't touch behavior" plus evidence-only probe packaging. No score math, judge model, band, pillar weight, tamper-resistance posture, or API output shape changed.

Modify/delete resolution:
- Deleted Path B framework files stayed deleted: `evals/aiuc_path_b/*` and `rubric/aiuc_path_b/family_registry.yaml` were not restored.
- The two family definitions moved into `evals/perils/wave1a/candidate.py`, matching current master peril-package architecture.
- New evidence-only mapping files were added for the two Wave 1A families with `scoring: reported_not_scored` and `scoring_active: false`.

Operational metadata:
- scanner_version: `0.5.0`
- corpus_version: `cross_session_data_leak@0.1.0`, `consequential_action_confirmation@0.1.0`
- probe-set-version: `cross_session_data_leak@0.1.0`, `consequential_action_confirmation@0.1.0`
- rubric_point_id: `P3.9`, `ROBUSTNESS.DATA_ISOLATION.CROSS_SESSION_DATA_LEAK`, `P3.7`, `ROBUSTNESS.TOOL_MISUSE.CONSEQUENTIAL_ACTION_CONFIRMATION`
- N=5 calibration delta: `0 total / 0 pillars` by reported-not-scored construction; direct test asserts the invariant.
- cto_cosign_ref: not required for this rule-execution reconciliation.

Promotion state remains `reported_not_scored`; underwriting-active count remains `0`.

Verification:
- `python3 -m pytest tests/test_beaaa16967_wave1a_reconcile.py tests/test_beaaa15162_irreversible_action_authority.py -q` -> `23 passed in 0.27s`.
- `python3 -m pytest tests/test_golden_agents.py tests/test_beaaa16967_wave1a_reconcile.py -q` -> `13 passed, 18 subtests passed in 0.34s`.

Changed files:
- `evals/perils/wave1a/__init__.py`
- `evals/perils/wave1a/candidate.py`
- `evals/perils/wave1a/registry.yaml`
- `rubric/probe_mappings/cross_session_data_leak.yaml`
- `rubric/probe_mappings/consequential_action_confirmation.yaml`
- `tests/test_beaaa16967_wave1a_reconcile.py`
- `artifacts/beaaa-16967/wave1a-reconciliation-report.md`
