---
artifact_type: adr
owner: scanner_engineer
persona_audience: internal
canonical_terms_only: true
ticket_refs: ["BEAAA-21395"]
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
scanner_version: "0.5.0"
corpus_version: "consequential_action_confirmation@0.1.0"
probe-set-version: "consequential_action_confirmation@0.1.0"
rubric_point_id: "ROBUSTNESS.TOOL_MISUSE.CONSEQUENTIAL_ACTION_CONFIRMATION"
N=5 calibration delta: "not_applicable_report_only_zero_weight"
cto_cosign_ref: null
self_audit_passed: true
---

# ADR-0064: report-only consequential-action tranche

## BLUF

Five authoritative registry cases gain a BackBond-owned deterministic mock-tool runner and report evidence without entering scoring or the default runtime.

## Decision

Authorize only `cac-02`, `cac-03`, `cac-08`, `cac-11`, and `cac-12`. Each emits an attempted call, confirmation decision, final state, verdict, and evidence digest. Consequential calls mutate only after confirmation. The two negative controls execute directly when confirmation is absent and fail when over-confirmed.

Registry rows retain their stable identifiers and source provenance. Their classifications change from `reported=false` to `reported=true`; `scoring_active=false`, default-runtime execution, weights, bands, thresholds, public counts, and the Scanner API remain unchanged.

## Classification and reproducibility

Rule execution under the supervision-contract row for implementing a probe inside an existing rubric point. Seeds 0-4 produce identical verdicts and evidence digests. N=5 is `not_applicable_report_only_zero_weight` because the runner imports no scoring module and every tranche row has zero weight.

## Rollback

Revert this ADR, the runner, tests, registry-builder annotation, and the five mapping annotations; regenerate both registries. Score behavior remains byte-identical.
