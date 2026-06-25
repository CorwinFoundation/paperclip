---
artifact_type: scanner_closeout
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "IFA rates and insures AI agents; this BEAAA-6187 Scanner closeout records customer-VPC artifact URI evidence custody for ARE Scanner v1.0 M3."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-6187", "BEAAA-6194", "BEAAA-6202", "BEAAA-6180", "BEAAA-6149"]
scanner_version: "0.4.2"
corpus_version: "0.1.1"
probe-set-version: "1.2.0"
rubric_point_id: "G1 golden-agent contract-envelope fixture; no rubric scoring point changed"
n5_calibration_delta: "spread=0; five runs score 79/B; 19/19 active evidence_refs customer-VPC artifact_uri"
cto_cosign_ref: "BEAAA-6202"
uw_cosign_ref: "BEAAA-6194"
contract_version: "are-scanner-api-v1@v1.3.0"
fal_audit: "PASS: focus recoverable as rate-and-insure evidence infrastructure; no external customer or carrier named; no guarantee language."
---

BLUF: BEAAA-6187 is merged into canonical master with a rule-change co-sign, while preserving the `are-scanner-api-v1@v1.3.0` score surface.

## Deliverable

This is a customer-VPC artifact URI implementation for the CTO and Underwriter that causes ARE Scanner evidence references to resolve to tenant-isolated object-store URLs within the M3 sprint budget.

## Classification

Rule-change per `contracts/scanner-implementation-supervision-v1.md` because the work adds customer-VPC evidence custody semantics to Scanner output handling. CTO co-sign is `BEAAA-6202`; UW co-sign is `BEAAA-6194`.

## Implementation

- `harness/contract_envelope.py` adds feature-flagged `BACKBOND_EMIT_MODE=customer_vpc`.
- Default `repo_local` behavior remains unchanged.
- Customer-VPC mode validates tenant and run identifiers, then rewrites active `evidence_refs[].artifact_uri` values to `s3://customer-{tenant_id}/are-scans/{run_id}/evidence/...`.
- `tests/test_cli_runner_beaaa464.py` covers customer-VPC URI projection and invalid tenant rejection.

No rubric weights, scoring math, judge model choices, band thresholds, or output score fields changed. No contract bump is required.

## Verification

- `python3 -m pytest tests/test_cli_runner_beaaa464.py -k 'run_scan_matches_g1_expected or customer_vpc_emit_mode'`
  - Result: 3 passed, 18 deselected.
- N=5 golden run using `BACKBOND_EMIT_MODE=customer_vpc`, agent `G1`, seed `42`, corpus `0.1.1`, rubric `rubric/rubric.yaml`.
  - Result: all five runs score `79/B`.
  - Subscores: `GOVERNANCE=21.875`, `ROBUSTNESS=23.4375`, `BIAS_FAIRNESS=13.125`, `OPS=20.3125`.
  - Calibration delta: `spread=0`.
  - Evidence URI coverage: `19/19` active refs use `s3://customer-dp-acme/are-scans/scan-run-6187/evidence/`.

## Merge Evidence

- Merged SHA on canonical master at close: populated in the issue closeout comment.
- Reachability command: `git -C projects/are-scanner merge-base --is-ancestor <merged_sha> master && echo REACHABLE`.
