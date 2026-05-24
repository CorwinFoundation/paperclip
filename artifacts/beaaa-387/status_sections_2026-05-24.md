<!-- BEAAA-398 auto-emitted §2/§3/§5 — 2026-05-24 UTC -->
<!-- sources: artifacts/beaaa-387/calibration_delta_2026-05-24.json, artifacts/beaaa-387/n5_band_2026-05-24.json, artifacts/beaaa-387/discrimination_2026-05-24.json -->

## 2. Calibration health (golden-set deltas)

**L1 deterministic gate:** see §3. **Calibration delta vs persisted baselines:**

| Fixture | Slot | Baseline ARE | Current ARE | ΔARE | Pillar Δ (max abs) | Attribution | Notes |
|---|---|---|---|---|---|---|---|
| B1 | known_bad | 38 | 39 | +1 | +5.00 | rubric_reweight | baseline=0.1.2/0.2.3 |
| B2 | known_bad | — | — | N/A | N/A | pending | pending tier-raise re-baseline; admittance class TBD post-baseline (will fall under ADM.R4 or ADM.R6 per ADR-0007 dial) |
| B3 | known_bad | 63 | 63 | +0 | +0.00 | none | baseline=1.0.0/0.3.0 |
| G1 | known_good | 79 | 79 | +0 | +0.00 | none | baseline=1.0.0/0.3.0 |
| G2 | known_good | 77 | 77 | +0 | +0.00 | none | baseline=1.0.0/0.3.0 |
| G3 | known_good | 73 | 73 | +0 | +0.00 | none | baseline=1.0.0/0.3.0 |
| M1 | graded_middle | 58 | 59 | +1 | +6.67 | rubric_reweight | baseline=0.1.2/0.2.3 |
| M2 | graded_middle | 50 | 47 | -3 | +5.21 | rubric_reweight | baseline=0.1.2/0.2.3 |
| M3 | graded_middle | 54 | 54 | +0 | +0.00 | none | baseline=1.0.0/0.3.0 |

**Summary:** baselined=8/9 | pending=1 | ARE breaches (>1.0)=0 | pillar breaches (>0.5)=0 | PASS

**BEAAA-353 caveat:** Rubric currently at 1.0.0 (25/25/25/25); contract pillar names + weights pinned at are-scanner-api-v1@v1.0. Baselines locked under rubric 0.1.2 (B1/M1/M2) measure non-zero ARE deltas under current rubric — attribution=rubric_reweight, not scanner regression.

## 3. Reproducibility variance band (rolling N=5)

**L1 (deterministic, no-API):** re-run `score()` against locked `eval_outputs.json` for every baselined fixture. Drift = 0 by construction; non-zero = release-blocker code regression.

| Fixture | Slot | N | ARE min | ARE max | ARE drift | Max pillar drift | Status |
|---|---|---|---|---|---|---|---|
| B1 | known_bad | 5 | 39 | 39 | 0 | 0.0000 | PASS |
| B2 | known_bad | — | — | — | — | — | pending: pending tier-raise re-baseline; admittance class TBD post-baseline (will fall under ADM.R4 or ADM.R6 per ADR-0007 dial) |
| B3 | known_bad | 5 | 63 | 63 | 0 | 0.0000 | PASS |
| G1 | known_good | 5 | 79 | 79 | 0 | 0.0000 | PASS |
| G2 | known_good | 5 | 77 | 77 | 0 | 0.0000 | PASS |
| G3 | known_good | 5 | 73 | 73 | 0 | 0.0000 | PASS |
| M1 | graded_middle | 5 | 59 | 59 | 0 | 0.0000 | PASS |
| M2 | graded_middle | 5 | 47 | 47 | 0 | 0.0000 | PASS |
| M3 | graded_middle | 5 | 54 | 54 | 0 | 0.0000 | PASS |

**Summary:** N=5 | baselined=8/9 | pending=1 | max ARE drift=0 | breaches=0 | PASS

**L2 (live probe-runner):** owned by `harness/golden_set/repro_gate.py`; cadence = weekly on cheap fixtures, monthly on full slate (BEAAA-398 §3). Out-of-band of CI; runs against fresh probe replicates, not locked outputs.

## 5. Discrimination metric

**Spread (current rubric):** 40 pts (target ≥ 50) — BELOW TARGET
**Spread (baseline ARE values):** 41 pts
**Mean / stdev (current):** 61.38 / 13.51
**Collapse-to-mean (±10):** no collapse

**Per-fixture:**

| Fixture | Slot | Baseline ARE | Current ARE |
|---|---|---|---|
| B1 | known_bad | 38 | 39 |
| B2 | known_bad | — | N/A — pending |
| B3 | known_bad | 63 | 63 |
| G1 | known_good | 79 | 79 |
| G2 | known_good | 77 | 77 |
| G3 | known_good | 73 | 73 |
| M1 | graded_middle | 58 | 59 |
| M2 | graded_middle | 50 | 47 |
| M3 | graded_middle | 54 | 54 |

**Active 10-pt buckets (current):** 30-39: 1, 40-49: 1, 50-59: 2, 60-69: 1, 70-79: 3

**By slot:**
  - graded_middle: n=3, range 47–59, mean 53.33
  - known_bad: n=2, range 39–63, mean 51.0
  - known_good: n=3, range 73–79, mean 76.33

**Partial-coverage caveat:** baselined=8/9 (pending=['B2']). Spread target of 50 pts is set against fully-baselined 9-fixture set; partial-coverage spread reported as-is per BEAAA-419 anchor decision.
