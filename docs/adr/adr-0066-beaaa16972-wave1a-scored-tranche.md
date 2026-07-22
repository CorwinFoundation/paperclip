---
artifact_type: adr
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "The first Wave 1A activation turns two QA-approved BackBond Scanner families into underwriting eligibility gates without changing score math, bands, pricing, or API shape."
contracts_referenced:
  - are-scanner-api-v1@v1.0
  - scanner-implementation-supervision-v1@v1.1
ticket_refs:
  - BEAAA-16972
  - BEAAA-16971
  - BEAAA-16973
  - BEAAA-16945
proof_points_cited:
  - BEAAA-16971 Underwriting co-sign
  - tools/calibration-deltas/2026-07-22-beaaa16972-wave1a-scored-tranche-n5.md
  - tests/test_beaaa16972_wave1a_scored_activation.py
version: 0.1
prior_version: docs/adr/adr-0063-beaaa16943-wave-1a-slice.md
self_audit_passed: true
escalations_noted:
  - "Underwriting co-sign is recorded on BEAAA-16971."
  - "QA exact-SHA approval is still required on BEAAA-16973 before shared or production promotion."
---

# ADR-0066: BEAAA-16972 Wave 1A Scored Tranche Activation

## Decision

Activate exactly two Wave 1A families as scoring-active and underwriting-active eligibility gates: `cross_session_data_leak` and `consequential_action_confirmation`.

The activation is zero-weight for BackBond Score math. It does not change `are_total`, pillar weights, band cuts, premium mapping, warranty language, coverage, exclusions, judge model, or the `are-scanner-api-v1@v1.0` output shape.

## Scope

- `cross_session_data_leak`: 20 unique scenarios, 100 executions at K=5.
- `consequential_action_confirmation`: 15 unique scenarios, 75 executions at K=5.
- Total: 35 unique scenarios, 175 executions.
- Active claim count moves from 305 to 340 because these scenarios are now active eligibility gates.

## Evidence Boundary

The activation binds to the BEAAA-16945 candidate lineage and the BEAAA-16971 Underwriting co-sign. The scored-tranche packet records:

- scanner version `0.5.0`;
- corpus/probe-set versions `cross_session_data_leak@0.1.0` and `consequential_action_confirmation@0.1.0`;
- seeds `0, 1, 2, 3, 4`;
- local deterministic evaluators;
- canary rows `csdl-20-canary` and `cac-15-canary`;
- fail-closed behavior for missing authorization, hash mismatch, mutable evidence, scope conflict, incomplete runs, missing decision fields, and non-boolean decision fields.

## Underwriting Effect

The families are P0 eligibility gates:

- Cross-session or cross-tenant disclosure blocks auto-bind for customer-data-bearing agents and routes to remediation or decline until a clean rescan.
- Consequential action without explicit confirmation blocks auto-bind for agents with irreversible, financial, external-commitment, account-modification, or production-mutation authority.

No AIUC certification, equivalence, warranty, coverage, exclusion, rate, or pricing approval is authorized by this ADR.

## Calibration

Golden N=5 replay passes with total and every pillar delta 0, deterministic spread 0, and score/band/pricing movement 0. Calibration table: `tools/calibration-deltas/2026-07-22-beaaa16972-wave1a-scored-tranche-n5.md`.

## Rollback

Rollback is a config restoration for the two activated mappings only:

1. Restore `reporting_status: reported_not_scored`.
2. Restore `scoring: reported_not_scored`.
3. Restore `binding.scoring_active: false`.
4. Restore `binding.underwriting_active: false`.

This returns both families to the prior reported-not-scored and not-underwriting-active state without modifying other active registry rows. Historical evidence remains append-only.

## QA Gate

Do not merge, deploy, or promote this candidate to a shared or production environment until the QA Engineer records evidence-backed `APPROVED` on BEAAA-16973 for the exact candidate SHA.
