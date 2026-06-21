# Calibration delta — BEAAA-7235 golden fixture repair (missing active dims)

**Ticket:** BEAAA-7235 (blocker for BEAAA-7234)
**Contract anchor:** `are-scanner-api-v1@v1.3.0`
**Supervision anchor:** `scanner-implementation-supervision-v1@v1.1`
**Scanner version:** 0.4.1 · **Rubric:** 1.2.0 (25 active dims) · **Applicability policy:** 0.1.2
**Date:** 2026-06-21
**Author:** CTO (88e68019)

## What changed and why

The canonical golden fixtures under `tests/fixtures/golden_agents/` were last
re-baselined before the rubric-1.1.0 activation of four dims:

- `ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL` (BEAAA-1682 / BEAAA-794 — gates on `has_tool_calling`)
- `ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER` (same gate)
- `ROBUSTNESS.PII.SCAN_TIME_DETECTION` (BEAAA-781 — `fires_when: always`)
- `GOVERNANCE.PII.HANDLING_ATTESTATION` (BEAAA-781 — `fires_when: always`)

`rubric.scoring.score()` raised `ScoringError: missing dim scores` because these
active dims were absent from both `eval_outputs.json` and the N/A set —
`tests/test_golden_agents.py` was RED at clean HEAD, blocking BEAAA-7234's N=5
calibration gate before its code was even reached.

**Repair = sync the test fixtures to the already-committed canonical baseline
artifacts** (`artifacts/beaaa-387/{fid}/`, commit `4025751` "BEAAA-3009 SE1 PII
activation post-merge backfill (rule-execution)", `applies_to_rubric=1.1.0`,
`policy_version=0.1.2`). For every live golden agent all four new dims resolve
to **N/A (fires=False)**:

- TOOL_MISUSE → `ROBUSTNESS.TOOL_MISUSE.no_tool_surface`: the entire golden
  roster is non-tool-calling (`has_tool_calling=false`).
- PII → `PII.calibration_harness_uncovered`: the LLM Guard wrap is not invoked
  against locked `eval_outputs.json` in the deterministic L1 replay; substrate +
  attestation raws are preserved in `eval_outputs.json` as the evidence trail.
  Production scan path is unaffected — `applicability_policy.yaml` retains
  `fires_when: always` for both PII dims.

Because all four dims are N/A, Option C re-normalization computes pillar means
over the **same original 21 firing dims** → **no score moves**. Original-dim
`eval_outputs` were preserved byte-for-byte (G1's artifact P2.1/P2.4 values
differ from the locked fixture and were deliberately NOT imported; only the two
PII raws were merged additively).

## Per-agent delta (baseline = pre-repair locked expected_score; candidate = post-repair)

| Agent | Δ total | Δ GOV | Δ ROB | Δ BIAS | Δ OPS | spread (baseline) | spread (candidate) |
|-------|---------|-------|-------|--------|-------|-------------------|--------------------|
| B1 | +0 | +0.0 | +0.0 | +0.0 | +0.0 | 0 (N=5 byte-identical) | 0 (N=5 byte-identical) |
| G1 | +0 | +0.0 | +0.0 | +0.0 | +0.0 | 0 | 0 |
| G2 | +0 | +0.0 | +0.0 | +0.0 | +0.0 | 0 | 0 |
| M1 | +0 | +0.0 | +0.0 | +0.0 | +0.0 | 0 | 0 |
| M2 | +0 | +0.0 | +0.0 | +0.0 | +0.0 | 0 | 0 |
| M3 | +0 | +0.0 | +0.0 | +0.0 | +0.0 | 0 | 0 |

ARE totals unchanged: B1=39, G1=79, G2=77, M1=59, M2=47, M3=54.

**Verdict: CLEAN.** Every delta is exactly 0 — well within the ±1 band of
`are-scanner-api-v1@v1.0`/`@v1.3.0`. No Underwriter co-sign required (Supreme Law
threshold is >±1). This is a rule-execution fixture sync to already-co-signed
rubric/policy/artifacts, not a scoring-logic change.

## Files touched

- `tests/fixtures/golden_agents/{B1,M1,M2,M3}/applicability.json` — **new** (4 N/A new-dim decisions, mirrored from `artifacts/beaaa-387`).
- `tests/fixtures/golden_agents/{G1,G2}/applicability.json` — upgraded: existing original-dim N/A decisions preserved; 4 new-dim N/A decisions added; `applies_to_rubric→1.1.0`, `policy_version→0.1.2`.
- `tests/fixtures/golden_agents/*/eval_outputs.json` — 2 PII raws merged additively (evidence trail); original 21 dims untouched.
- `tests/fixtures/golden_agents/*/expected_score.json` — regenerated via `score()`; ARE total + all four pillar scores byte-identical to prior lock.
- `tests/test_golden_set_l2_gate.py` — `_baseline_replicate()` now threads M1's applicability sidecar (same missing-dim root cause).
- `harness/golden_set/repro_gate.py` — N=5 gate engine now skips N/A dims (raw=null) from the per-dim variance band; `float(None)` would otherwise raise once N/A dims enter golden baselines.

## Verification

- `tests/test_golden_agents.py` — 7 passed, 18 subtests passed; **N=5 byte-identical** (5/5 runs PASS).
- `tests/test_golden_set_l2_gate.py` — 19 passed (was 12 failed).
- `tests/test_pii_activation_version_pins.py` — green.

## Non-gating follow-ups (out of BEAAA-7235 scope — for BEAAA-7234 owner / separate cleanup)

1. Broad stale-version test cohort fails at clean HEAD on the same root cause
   (`test_scoring::test_active_dim_count_is_21`, `test_v0_1_1_additive::test_rubric_version_is_v1_0`,
   `test_repro_diff_remediation`, etc.) — these assert pre-activation rubric state
   (21 dims / v1.0) and were never updated after the 1.1.0/1.2.0 activations. Not
   caused by this repair; needs a dedicated version-assertion refresh ticket.
2. The canonical artifact's TOOL_MISUSE `na_justification` string differs from the
   byte-stable policy string `ROBUSTNESS.TOOL_MISUSE.no_tool_surface` in
   `applicability_policy.yaml`. Mirrored verbatim here to stay faithful to the
   committed source; flag for a byte-stability reconciliation if the Underwriter
   consumes the justification text downstream.
