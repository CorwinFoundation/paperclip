---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-7843 preserves BackBond Scanner subprocess reproducibility while fixing the parent/scorer timeout ordering that masked the scorer row-timeout error."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-7843", "BEAAA-7842", "BEAAA-7840", "BEAAA-2182"]
proof_points_cited: ["pytest tests/test_beaaa7840_scorer_recv_bound.py tests/test_live_probe_workers_beaaa542.py::test_scorer_recv_row_timeout_surfaces_on_return_channel tests/test_live_probe_workers_beaaa542.py::test_scorer_default_timeout_exceeds_customer_request_timeout -q", "python3 scripts/beaaa2182_a_subprocess_end_to_end.py --n 5 --output-dir tools/calibration-deltas"]
version: "0.1"
prior_version: null
self_audit_passed: true
escalations_noted: ["No CTO co-sign required: rule-execution bug fix to production subprocess plumbing; no rubric_point_id, scoring math, judge model, pillar weight, or customer-visible contract output shape changed.", "FAL audit source unavailable: agents/cmo-os.md not present in the loaded instruction tree; house-style and glossary audits completed."]
---

# BEAAA-7843 — Scorer Timeout N=5 Calibration Receipt

BLUF: PASS. The BEAAA-7843 patch changes parent-side wait ordering for the scorer return channel only. It does not change BackBond Scanner scoring rules or the `are-scanner-api-v1@v1.3.0` response shape.

## Classification

Rule-execution under `scanner-implementation-supervision-v1@v1.1` classification rows:

* "Smoke test for an existing operation" for the regression.
* "Refactor that doesn't touch behavior" / bug fix for subprocess plumbing.

No CTO co-sign is required because the patch changes no rubric point, scoring math, judge model, pillar weight, tamper-resistance posture, or customer-visible output field.

## Required Scanner Metadata

| Field | Value |
| --- | --- |
| `scanner_version` | `0.3.11` |
| `corpus_version` | `0.1.0` |
| `probe-set-version` | golden subprocess fixture set: `synthetic_v0`, `B1`, `G1`, `G2`, `M1`, `M2`, `M3`; `N=5` |
| `rubric_point_id` | no rubric point changed; all active subprocess envelope subscores checked |
| `N=5 calibration delta` | PASS: spread `0` on `are_total` and every subscore across all 7 fixtures x 5 replicates; byte-equal vs in-process baseline |
| `cto_cosign_ref` | null: rule-execution |

## Verification

Focused regression:

```text
pytest tests/test_beaaa7840_scorer_recv_bound.py tests/test_live_probe_workers_beaaa542.py::test_scorer_recv_row_timeout_surfaces_on_return_channel tests/test_live_probe_workers_beaaa542.py::test_scorer_default_timeout_exceeds_customer_request_timeout -q
.... [100%]
4 passed in 0.59s
```

N=5 subprocess calibration:

```text
python3 scripts/beaaa2182_a_subprocess_end_to_end.py --n 5 --output-dir tools/calibration-deltas
[subprocess-e2e] PASS (7 fixtures x N=5 replicates)
```

Per-fixture result:

| Fixture | in-process BackBond Score | subprocess N=5 spread | byte-equal vs in-process |
| --- | ---: | ---: | :---: |
| `synthetic_v0` | 69 | 0 | yes |
| `B1` | 39 | 0 | yes |
| `G1` | 79 | 0 | yes |
| `G2` | 77 | 0 | yes |
| `M1` | 58 | 0 | yes |
| `M2` | 48 | 0 | yes |
| `M3` | 54 | 0 | yes |

## Implementation Insight

BEAAA-7840 bounded the parent-side scorer envelope drain, but that made the parent give up before a live scorer could finish its own bounded `recv_row` wait and emit the typed `("err", "TimeoutError", ...)` tuple. BEAAA-7843 keeps the short dead-scorer path while extending the parent drain only when the scorer process is still alive, so operational errors surface with the scorer's precise timeout instead of a generic parent queue-drain failure.

The patch preserves reproducibility and evidence completeness because it does not touch scoring inputs, score projection, rubric data, judge behavior, or the §2.2 envelope allow-list.
