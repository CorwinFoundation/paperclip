---
artifact_type: n5_calibration_delta
ticket: BEAAA-5752
scanner_version: are-scanner@beaaa-5752
corpus_versions:
  - p4_grounded_factuality_v1
  - p4_citation_grounded_v1
probe_set_versions:
  - p4_grounded_factuality_halueval_qa_v1
  - p4_grounded_factuality_truthfulqa_v1
  - p4_citation_grounded_halueval_summ_v1
  - p4_citation_grounded_factscore_v1
rubric_point_ids: [P1.1, P1.2]
classification: rule-execution
upstream_eval_version:
  halueval_qa: b7253db3cdaa0ab2c382f92b26b390109174f77e
  truthfulqa: d71c110897f5d31c5d7f309e7bc316c152f6f031
  halueval_summ: b7253db3cdaa0ab2c382f92b26b390109174f77e
  factscore: f28272deffcf33efc1f1117d5479c10bb75221a9
delta_total: 0
delta_basis: by_construction
date: 2026-06-16
author: Scanner Engineer (d8bbc3b5-185f-43ed-bff0-d00323ddbfa3)
---

# BEAAA-5752 R-4 N=5 calibration delta — grounded factuality + citation grounding

## Method

5 sequential pytest reruns of `tests/test_grounded_factuality.py tests/test_citation_grounded.py` with identical args + identical env (`ARE_PROBE_LIVE_LLM` unset; `ARE_PROBE_T` unset → default 0.95; `ARE_PROBE_PASS_K` unset → default 5).

## Results

| Run | Result | Duration |
| --- | --- | --- |
| 1 | 22/22 PASS | 6.12s |
| 2 | 22/22 PASS | 7.67s |
| 3 | 22/22 PASS | 6.25s |
| 4 | 22/22 PASS | 5.54s |
| 5 | 22/22 PASS | 4.04s |

Δ_total = **0**.

## Basis: by-construction

R-4 adds two test files + 1 shared substrate (`tests/_p4_helpers.py`) + 4 probe-set JSONL fixtures + 4 entries in `tests/upstream_pins.json`. It touches none of:

  * `rubric/rubric.yaml`
  * `rubric/scoring.py` / `rubric/calibration_curves.{py,yaml}`
  * `rubric/probe_mappings/*.yaml`
  * `tests/fixtures/golden_agents/`
  * `tests/fixtures/p1_*/`
  * `tests/probe_sets/p5_*.jsonl` (R-5 probe sets)
  * any pillar-weight / band-cut artifact

The grounded-factuality and citation-grounding graders are deterministic non-LLM (string equality + substring + keyword match); the mock agent + mock tool source are deterministic (compliance switches at 0.0 or 1.0 only — partial-rate paths are deterministic per `(probe_id, turn_index)` hash). No randomness path is exercised in CI.

Result: any rerun under identical inputs returns identical outputs. The pass^k rate per probe is 1.0 (k=5) for the well-aligned mock and 0.0 for the leaky mock — exact, not stochastic.

## Escalation triggers — none fired

| Trigger | Threshold | Observed | Escalate? |
| --- | --- | --- | --- |
| Δ > ±1 on any golden agent | per supervision-v1.1 row "Wrap an upstream eval framework" | Δ=0 | No |
| Δ > ±1 on OPS or ROBUSTNESS subscore | per BEAAA-5752 ticket AC | not measurable — rubric not touched | No |
| Tamper-resistance reduction | Founder + CTO | none | No |
| Pillar-weight change | Founder | none | No |
| Judge swap to model-graded | CTO | none — non-LLM deterministic extractors | No |

## Reproducibility

Run from the canon Scanner workspace:

```
cd <canon-are-scanner>
.venv/bin/python -m pytest tests/test_grounded_factuality.py tests/test_citation_grounded.py
```

Expected: `22 passed`. Repeat 5 times; identical PASS count proves deterministic-replay. The probe-set version constants and SHA256s are pinned in `tests/artifacts/beaaa-5752/closeout_receipt.md`.
