---
artifact_type: other
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This calibration record preserves the BackBond Scanner scoring boundary while post-bind telemetry feeds underwriting and claims review after bind."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1", "telemetry-api-v0"]
ticket_refs: ["BEAAA-5919", "BEAAA-5867", "BEAAA-5812"]
proof_points_cited: ["tools/post-bind-ingest/tests/test_sinks.py", "tools/post-bind-ingest/tests/test_no_score_imports.py", "tools/portal_v1/test_post_bind_sinks.py"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["CTO co-sign remains required before BEAAA-5867 parent close because BEAAA-5894 withheld canonical placement approval."]
---

# BEAAA-5919 N=5 calibration delta

**BLUF:** PASS. Moving BEAAA-5867 sinks to the canonical post-bind-ingest package produced `Delta_total=0` and `Delta_subscore=0` across N=5 baseline-vs-candidate portal runs. The candidate run exercised all three export-only sinks: claims queue, per-customer rescore feed, and portfolio calibration feed.

## Classification

- Deliverable: bug fix / refactor for CTO review that moves post-bind sink calculations under `tools/post-bind-ingest` within one heartbeat.
- Classification: rule-execution under `scanner-implementation-supervision-v1@v1.1`, table rows “refactor that doesn't touch behavior” and “smoke test for an existing operation.”
- Contract anchor: `are-scanner-api-v1@v1.0`; output shape unchanged.
- CTO co-sign: not required for this rule-execution child; parent BEAAA-5867 still requires CTO review before close because BEAAA-5894 withheld canonical placement approval.

## Required operational metadata

| Field | Value |
| --- | --- |
| scanner_version | `0.4.1` |
| corpus_version | `0.2.0` |
| probe-set-version | `post-bind-sinks-v0.1` |
| rubric_point_id | `n/a` — post-bind export-only sinks; no BackBond Scanner rubric point changed |
| N=5 calibration delta | `are_total=0`; `GOVERNANCE=0`; `ROBUSTNESS=0`; `BIAS_FAIRNESS=0`; `OPS=0` |
| upstream_eval_version | `n/a` — no upstream eval wrapper |
| cto_cosign_ref | `n/a` for this rule-execution child |

## N=5 run design

Baseline cohort: five fresh Portal v1 services produced a BackBond Score from the locked portal task flow with no post-bind telemetry records.

Candidate cohort: five fresh Portal v1 services produced the same BackBond Score, then submitted locked telemetry fixture `hl-01` through the post-bind path. Each candidate run emitted:

| Sink | Count per run |
| --- | ---: |
| claims queue | 1 |
| per-customer rescore feed perils | 2 |
| portfolio calibration feed perils | 1 |

The candidate score row was read back from `scans.score_json` after sink emission and compared to the pre-sink score object. All five candidate runs returned `score_unchanged_after_sink=true`.

## Score deltas

| Dimension | Baseline spread | Candidate spread | Median delta |
| --- | ---: | ---: | ---: |
| are_total | 0 | 0 | 0 |
| GOVERNANCE | 0 | 0 | 0 |
| ROBUSTNESS | 0 | 0 | 0 |
| BIAS_FAIRNESS | 0 | 0 | 0 |
| OPS | 0 | 0 | 0 |

Per-run scores were identical in both cohorts:

| Cohort | Runs | are_total | GOVERNANCE | ROBUSTNESS | BIAS_FAIRNESS | OPS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 5 | 64 | 15 | 16 | 15 | 18 |
| candidate | 5 | 64 | 15 | 16 | 15 | 18 |

## Verification

```
python3 -m pytest tools/post-bind-ingest/tests -q
16 passed in 0.30s

python3 -m pytest tools/portal_v1/test_post_bind_sinks.py -q
11 passed in 29.98s
```

Additional guard: `tools/post-bind-ingest/tests/test_no_score_imports.py` parses every sink module and rejects imports from `portal`, `rubric`, `scoring`, `scanner`, or `scanners`.

## FAL audit

- Focus: pass. The artifact reinforces BackBond’s rate-and-insure model by keeping post-bind telemetry downstream of the BackBond Scanner score.
- Alignment: pass. No evaluation-as-product, governance-as-product, guarantee, carrier, or customer claim.
- Linkage: pass. Uses the common proof point: BackBond rates and insures AI agents with version-pinned evidence.
