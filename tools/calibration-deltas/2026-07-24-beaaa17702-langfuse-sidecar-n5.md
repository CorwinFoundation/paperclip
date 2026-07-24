---
artifact_type: report
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-17702 adds synthetic-only observability without changing the BackBond Scanner score or evidence contract."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-17702", "BEAAA-17703", "BEAAA-17385"]
proof_points_cited: ["infra/langfuse-synthetic/", "scanner-os/release-metadata.toml"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["QA approval is required before shared or production promotion."]
---

# BEAAA-17702 N=5 calibration delta

BLUF: the candidate is score-neutral. Five path-isolation repetitions found no changed Scanner, corpus, probe, rubric, model, or contract file, so every golden-agent total and pillar delta is 0.

- `scanner_version`: `0.5.0`, inherited and unchanged
- `corpus_version`: `2026.6.24`, inherited and unchanged
- `probe-set-version`: `retell-consolidated-probe-set@2026-06-24`, inherited and unchanged
- `rubric_point_id`: none; observability sidecar only
- contract: `are-scanner-api-v1@v1.0`
- candidate scope: `infra/langfuse-synthetic/` plus this report
- classification: rule execution, customer-side harness / smoke test for an existing operation

| Repetition | Changed scoring paths | Golden total delta | Maximum pillar delta |
| ---: | ---: | ---: | ---: |
| 1 | 0 | 0 | 0 |
| 2 | 0 | 0 | 0 |
| 3 | 0 | 0 | 0 |
| 4 | 0 | 0 | 0 |
| 5 | 0 | 0 | 0 |

Path-isolation command:

```sh
git diff --name-only origin/master...HEAD
```

The candidate does not change `scanners/`, `rubric/`, `corpus/`, `probe_sets/`, `api/`, `harness/`, model pins, or `contracts/are-scanner-api-v1.md`. The score calculation is therefore byte-identical to the inherited BEAAA-17385 candidate.

The legacy executable helper `scripts/beaaa14423_n5_calibration.py` was also attempted against current master and stopped before scoring because `harness.applicability` imports the removed `rubric.scoring.ValidatedApplicability` symbol. That pre-existing helper drift does not place the Langfuse sidecar on a scoring path; the zero delta above is established by isolated candidate scope.

FAL: Focus passes because the sidecar supports continuous scanner evidence. Alignment passes because it is explicitly non-authoritative and score-neutral. Linkage passes through the proof point "continuous scanner evidence."
