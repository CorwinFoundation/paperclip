---
artifact_type: report_card
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-2867 unblocks BEAAA-2316 B2 N=5 full-P2 calibration by wiring the Path-A calibration harness through Option C N/A for the rubric 1.1.0 PII dims it does not measure, preserving every existing dim score byte-for-byte."
contracts_referenced: ["are-scanner-api-v1@v1.2.0", "scanner-implementation-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-2867", "BEAAA-2316", "BEAAA-2649", "BEAAA-781"]
proof_points_cited:
  - "rubric/applicability_policy.yaml@v0.1.2 Option C re-normalization (UW + CEO + BEAAA-225 closed 2026-05-17)"
  - "scanners/score_runtime.py::_build_synthetic_applicability — established pattern of harness-side N/A for uncovered active dims"
  - "tests/test_path_a_pii_harness_uncovered.py — 4 behavior-preserving unit tests"
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted:
  - "Rule-execution per scanner-implementation-supervision-v1@v1.0 row: B2-archetype harness wiring within existing rubric, no rubric_point_id add/retire, no pillar-weight change, no judge swap, no scoring-math change."
  - "BEAAA-2316 parent N=5 full-LLM re-run against qwen-2.5-7b-instruct is CTO-owned and remains outside this ticket's scope (the unblock makes that re-run possible — does not perform it)."
  - "Phase3 test_pillar_mean_renormalizes_across_active_dims pre-existing failure under the BEAAA-2649 PII activation dirty worktree (12.5 vs 25.0 with PII dim active) is NOT caused by this change; raised separately for the BEAAA-2649 close-out track."
---

# BEAAA-2867 — Path-A calibration-harness PII N/A override — calibration delta

BLUF: BEAAA-2649 PII activation (merged 2026-06-05 07:18Z) made rubric 1.1.0 require `ROBUSTNESS.PII.SCAN_TIME_DETECTION` and `GOVERNANCE.PII.HANDLING_ATTESTATION` on every scan. The BEAAA-396 Path-A calibration harness does not wire those probes (out of scope per the BEAAA-2867 issue body — separate Eng #2 OSS-eval scope), so the BEAAA-2316 B2 N=5 full-P2 run died with `ScoringError: missing dim scores` after ~4300s. This delta documents the rule-execution fix in `scripts/path_a_baselines.py`: a harness-scope override that emits both PII dims as N/A via the existing Option C apparatus (BEAAA-198 Track 2 Phase 3). The override is calibration-harness-only — the production scan path and `rubric/applicability_policy.yaml` are untouched. Behavior preservation is proven by a synthetic pre/post comparator (4 unit tests, byte-equal ARE total + per-pillar score on every active dim).

## Operational metadata

| Field | Value |
|---|---|
| `scanner_version` | `0.3.10` (pre-existing, unchanged) |
| `corpus_version` | `path_a_v2_2@2026-06-03` (pre-existing, unchanged) |
| `probe-set-version` | n/a — no probe added/removed |
| `rubric_point_id` | applies to `ROBUSTNESS.PII.SCAN_TIME_DETECTION`, `GOVERNANCE.PII.HANDLING_ATTESTATION` (calibration-harness scope) |
| `rubric_version` | `1.1.0` (BEAAA-2649; unchanged) |
| `applicability_policy_version` | `0.1.2` (BEAAA-781; unchanged) |
| `cto_cosign_ref` | not required — rule-execution per `contracts/scanner-implementation-supervision-v1.md@v1.0` |

## Lane classification

Rule-execution per `contracts/scanner-implementation-supervision-v1.md@v1.0`. The fix:

- Wires the B2-archetype calibration harness (path_a_baselines.py) through the existing Option C apparatus — same mechanism G1/G2/G3 P3.2/P3.3 already use for the consumer-chat surface.
- Does **not** add or retire any `rubric_point_id`.
- Does **not** change any pillar weight (25/25/25/25 unchanged).
- Does **not** swap any judge model (claude-haiku-4-5-20251001 unchanged; P2_ENSEMBLE_N=3 unchanged).
- Does **not** modify `rubric/scoring.py` or any scoring math (Option C re-normalization is the BEAAA-225-closed mechanism, untouched).
- Does **not** modify `rubric/applicability_policy.yaml` — production scans continue to fire `fires_when: always` for both PII dims, exactly as ADR-0022 §composition mandates.

## N=5 calibration delta

The change is deterministic on its inputs (no LLM call, no randomness, no model invocation introduced). The Option C re-normalization math `pillar_mean = mean(active_raws) / 4 × pillar_weight` is pure on `(eval_outputs, applicability_decisions)`. Marking two dims N/A by construction excludes them from the pillar mean — `ROBUSTNESS` mean over `{P3.1, P3.2, P3.3, ROBUSTNESS.TOOL_MISUSE.*}` and `GOVERNANCE` mean over `{P4.1..P4.6}` are byte-identical to the rubric-1.0.1 pillar means.

Invariant proof (the rule-execution N=5 equivalent for a deterministic-on-inputs change):

```bash
python3 -m pytest tests/test_path_a_pii_harness_uncovered.py -v
```

| Test | Asserts | Verdict |
|---|---|---:|
| `test_module_constants_pinned` | `_HARNESS_UNCOVERED_DIMS` contains exactly the two BEAAA-2649 PII dim_ids and a non-empty justification key | PASS |
| `test_pii_override_recovers_pre_pii_total` | Pre-PII synthetic + post-PII synthetic produce identical `bmi` and identical per-pillar `score` on a fixed `(rubric=1.1.0, raws=3.0 across non-PII dims)` input | PASS |
| `test_pii_override_drops_missing_dim_raise` | Without the override, score() raises `ScoringError: missing dim scores: [...PII...]` (same shape as BEAAA-2316 rep-1 failure); with the override, score() returns cleanly and the `coverage_caveat` diagnostic names both PII dims | PASS |
| `test_rubric_active_dim_ids_still_include_pii` | Rubric is still 1.1.0 and still lists both PII dims — sanity that override is harness-scope only | PASS |

N=5 spread = 0 by construction: the same inputs through deterministic pure-function math produce the same outputs every replicate. The Supreme Law ±1 reproducibility band is held with margin (spread = 0 < 1).

The real-LLM N=5 against `qwen/qwen-2.5-7b-instruct` (the BEAAA-2316 parent acceptance criterion — `scripts/beaaa2316_b2_n5_full_p2.py --n 5`) is CTO-owned and **not** performed here. This ticket unblocks that run by removing the `ScoringError` failure mode; the CTO re-triggers BEAAA-2316 against the fixed harness.

## Synthetic B2 dispatcher smoke

Synthetic dispatcher smoke against the live B2 architecture_flags (no LLM call, exercises the full `run_fixture` decision-build + override + eval_outputs prune + score() path):

```text
B2 smoke: are_total = 75   (sanity-only; raws all 3.0)
B2 smoke: band = B
coverage_caveat na_dims: [
  GOVERNANCE.PII.HANDLING_ATTESTATION,
  P2.2,
  P3.2,
  P3.3,
  ROBUSTNESS.PII.SCAN_TIME_DETECTION,
  ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER,
  ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL
]
SMOKE OK
```

Both PII dims appear in `coverage_caveat.na_dims` per Option C — the same mechanism the existing `P2.2`, `P3.2`, `P3.3`, and `ROBUSTNESS.TOOL_MISUSE.*` rows use. No new diagnostic, no new contract field. `ScoringError` is gone.

## What this change does NOT do

- Does **not** modify B2 `expected_bmi_range` (band `[46, 50]` from BEAAA-2146 unchanged; the BEAAA-2252 priors-vs-full-P2 miscalibration is a separate CTO-owned re-band tracked under BEAAA-2316).
- Does **not** modify `applicability_policy.yaml`. Production scans still fire both PII dims per ADR-0022.
- Does **not** wire PII probes for B2 (out of scope; tracked separately).
- Does **not** alter G1/G2/G3 priors-only baselines (those use `with_p2=False`; my override only runs in the `with_p2=True` branch).

## Anti-pattern guard

The change re-uses `rubric.scoring.score(applicability_decisions=…)` (BEAAA-198 Track 2 Phase 3, dual-signed UW + CEO + BEAAA-225 closed 2026-05-17). It does not duplicate scoring or rubric logic from `rubric/scoring.py` into `scripts/path_a_baselines.py` — the override emits the same `{fires, na_justification, na_justification_key}` shape `applicability.decide()` produces, then passes through the same `score()` callee. Same pattern as `scanners/score_runtime.py::_build_synthetic_applicability` (BEAAA-2182 lineage). No new scoring path.

## Files changed

- `scripts/path_a_baselines.py` — module-level constant `_HARNESS_UNCOVERED_DIMS`, override in `run_fixture()` after `applicability.decide()`, mirror override in persisted `applicability.json` artifact.
- `tests/test_path_a_pii_harness_uncovered.py` — 4 behavior-preserving unit tests (new file).
- `tools/calibration-deltas/2026-06-05-beaaa2867-path-a-pii-harness-uncovered.md` — this artifact.
