---
artifact_type: other
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This calibration record preserves the BackBond Scanner scoring boundary while post-bind telemetry feeds underwriting and claims review after bind."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1", "telemetry-api-v0"]
ticket_refs: ["BEAAA-5944", "BEAAA-5898", "BEAAA-5920", "BEAAA-5867", "BEAAA-5812"]
proof_points_cited: ["tools/post-bind-ingest/tests/test_sinks.py", "tools/post-bind-ingest/tests/test_no_score_imports.py", "tools/portal_v1/test_post_bind_sinks.py", "tools/post-bind-ingest/tests"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["BEAAA-5920 is reassigned to CTO for re-review after canon-reachable SHA verification."]
---

# BEAAA-5944 N=5 calibration delta

**BLUF:** PASS. Reworking the BEAAA-5898 / BEAAA-5867 post-bind sinks into the canonical `tools/post-bind-ingest/src/post_bind_ingest/sinks/` package, with a minimal `tools/portal_v1/portal.py::_render_internal_report` OVP appendix anchor, produced `Delta_total=0` and `Delta_subscore=0` across N=5 baseline-vs-candidate runs. The candidate run exercised all three export-only sinks: claims queue, per-customer rescore feed, and portfolio calibration feed.

## Classification

- Deliverable: bug fix / refactor for CTO re-review that moves post-bind sink calculations under canonical placement and closes the BEAAA-5920 blocker loop within one heartbeat.
- Classification: rule-execution under `scanner-implementation-supervision-v1@v1.1`, table rows “refactor that doesn't touch behavior,” “smoke test for an existing operation,” and ADR-0034 §7(f,g,h,i) export-only sink / appendix rows.
- Contract anchor: `are-scanner-api-v1@v1.0`; output shape unchanged.
- CTO co-sign: not required before SE close because this is rule-execution; BEAAA-5920 is reassigned to CTO for canonical SHA re-review.

## Required operational metadata

| Field | Value |
| --- | --- |
| scanner_version | `0.4.1` (not changed) |
| corpus_version | `0.2.0` |
| probe-set-version | `post-bind-sinks-v0.1` |
| rubric_point_id | `n/a` — post-bind export-only sinks; no BackBond Scanner rubric point changed |
| N=5 calibration delta | `are_total=0`; `GOVERNANCE=0`; `ROBUSTNESS=0`; `BIAS_FAIRNESS=0`; `OPS=0` |
| upstream_eval_version | `n/a` — no upstream eval wrapper |
| cto_cosign_ref | `n/a` for this rule-execution child |
| merged_sha_on_canon_master_at_close | populated in BEAAA-5944 closeout comment after merge-base verification |

## N=5 run design

Baseline cohort: five fresh minimal Portal v1 services produced the locked fixture BackBond Score with no post-bind telemetry records.

Candidate cohort: five fresh minimal Portal v1 services produced the same BackBond Score, then submitted locked telemetry fixture `hallucination` through the post-bind path. Each candidate run emitted:

| Sink | Count per run |
| --- | ---: |
| claims queue | 1 |
| per-customer rescore feed perils | 2 |
| portfolio calibration feed perils | 1 |

The candidate score object was serialized before and after sink + OVP appendix emission. All five candidate runs returned `score_unchanged_after_sink=true`.

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
PYTHONPATH=tools/post-bind-ingest/src:tools/portal_v1 pytest \
  tools/post-bind-ingest/tests/test_sinks.py \
  tools/post-bind-ingest/tests/test_no_score_imports.py \
  tools/portal_v1/test_post_bind_sinks.py -q
11 passed

PYTHONPATH=tools/post-bind-ingest/src:tools/portal_v1 python3 <N=5 calibration script>
median_delta: are_total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0
spreads: baseline_are_total=0, candidate_are_total=0, all candidate subscores=0

PYTHONPATH=tools/post-bind-ingest/src:tools/portal_v1 pytest tools/post-bind-ingest/tests -q
63 passed
```

Additional guard: `tools/post-bind-ingest/tests/test_no_score_imports.py` parses every sink module and rejects imports from `portal`, `rubric`, `scoring`, `scanner`, or `scanners`.

## FAL audit

- Focus: pass. The artifact reinforces BackBond’s rate-and-insure model by keeping post-bind telemetry downstream of the BackBond Scanner score.
- Alignment: pass. No evaluation-as-product, governance-as-product, guarantee, carrier, or customer claim.
- Linkage: pass. Uses the common proof point: BackBond rates and insures AI agents with version-pinned evidence.
