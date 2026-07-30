---
artifact_type: report
owner: scanner_engineer_2
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "All 502 structurally constrained Vapi rows now have an exact per-row capability decision without changing the frozen inventory or sending Vapi requests."
contracts_referenced: ["are-scanner-api-v1.md@v1.3.0", "scanner-implementation-supervision-v1.md@v1.0"]
ticket_refs: ["BEAAA-19284", "BEAAA-19220", "BEAAA-19240"]
proof_points_cited:
  - "artifacts/beaaa-19284/vapi-exact-transport-ledger-v1.0.json"
  - "artifacts/beaaa-19284/n5-calibration-delta.json"
version: 1.0
self_audit_passed: true
escalations_noted: []
---

# Exact Vapi transport closeout

## BLUF

BEAAA-19284 produced a non-sending, immutable capability ledger for all 502 v1.4 `structurally_non_runnable` rows. Of these, 290 rows have byte-exact Vapi chat transport manifests and no-duplicate resume tokens; 212 rows fail closed with the missing primitive and remediation owner named.

## Decision and scope

This is an adapter-materialization artifact for the CTO that identifies which frozen rows can be transported without flattening or rewriting and makes every remaining technical constraint actionable. It is rule-execution under `scanner-implementation-supervision-v1.md@v1.0`: no scoring rule, rubric weight, API response shape, judge, corpus, or harness behavior changes.

The 25 `privacy_minimization_reidentification` role-bearing cases are recorded separately from the 477 original structural cases. All 25 remain genuine impossibilities because Vapi's authorized text-input path has no input role primitive for `system_event` or `assistant_observation`; the remediation owner is the Vapi adapter owner.

## Reconciliation

| Measure | Count |
|---|---:|
| Structurally constrained rows | 502 |
| Original structural cases | 477 |
| Role-bearing privacy cases | 25 |
| Exact executable manifests | 290 |
| Genuine impossibilities | 212 |
| Vapi requests | 0 |

Executable manifests cover only source shapes with explicit textual conversation semantics: single `prompt_text` or `query`; ordered `prior_turn` plus `query`; isolated A/B `prompt_a` and `prompt_b`; isolated counterfactual `pair_a` and `pair_b`; and isolated `user_turns_a` / `user_turns_b` branches followed by their exact `final_query`.

Structured scenario, fixture, payload, and action-like objects are not guessed into user prompts. They name the missing `authored_role_and_action_mapping_for_structured_probe_fields` primitive and route remediation to the probe-mapping owner.

## Exactness and resume boundary

Every transported content scalar is copied directly from the frozen row and carries its UTF-8 SHA-256. Each independent branch uses a new Vapi chat. Every step has a stable resume token composed from branch, step index, and content hash; uniqueness is verified across the row ledger so a resume can skip an already-recorded boundary instead of duplicating it.

The ledger itself is immutable by manifest declaration and carries a canonical manifest SHA-256 per executable row.

## Verification

- `pytest -q tests/test_beaaa19284_vapi_transport.py` → 3 passed.
- N=5 golden verification: five runs of `tests/test_golden_agents.py` plus `tests/test_golden_set_l2_gate.py`; every run reported 19 passed and 18 subtests passed.
- Golden calibration delta: 0.0000 on every pillar and total.
- Frozen v1.4 inventory SHA-256 remained `b32a1b7ec582ee55fd80e05332448c584a4237acbe792c914e5a8cf0739fc008`.
- Vapi requests: 0.

## Operational metadata

- `scanner_version`: `0.5.0`
- `corpus_version`: `fa32e0e`
- `probe-set-version`: `Vapi authorization freeze v1.4`
- `rubric_point_id`: `none - derived transport only`
- `N=5 calibration delta`: `0.0000`
- `cto_cosign_ref`: `null` (rule-execution)
- `seed`: `42`
- Contract: `contracts/are-scanner-api-v1.md@v1.3.0`

## FAL audit and rollback

The report leads with the decision, separates measured counts from interpretation, and makes no customer, coverage, score, or band claim. Canonical BackBond vocabulary is used.

Rollback is deletion of `artifacts/beaaa-19284/`, the builder, and its focused test. The frozen inventory was not edited.
