<!-- BEAAA-398 auto-emitted §2/§3/§5 — 2026-05-22 UTC -->
<!-- sources: artifacts/beaaa-387/calibration_delta_2026-05-22.json, artifacts/beaaa-387/n5_band_2026-05-22.json, artifacts/beaaa-387/discrimination_2026-05-22.json -->

## 2. Calibration health (golden-set deltas)

**L1 deterministic gate:** see §3. **Calibration delta vs persisted baselines:**

| Fixture | Slot | Baseline ARE | Current ARE | ΔARE | Pillar Δ (max abs) | Attribution | Notes |
|---|---|---|---|---|---|---|---|
| B1 | known_bad | 38 | 39 | +1 | +5.00 | rubric_reweight | baseline=0.1.2/0.2.3 |
| B2 | known_bad | — | — | N/A | N/A | pending | anchor strategy pending BEAAA-198/199 P2/P3 measured-dim expansion (Phase 2) |
| B3 | known_bad | — | — | N/A | N/A | pending | anchor strategy pending BEAAA-198/199 P2/P3 measured-dim expansion (Phase 2) |
| G1 | known_good | 84 | 84 | +0 | +0.00 | none | baseline=1.0.0/0.3.0 |
| G2 | known_good | 79 | 79 | +0 | +0.00 | none | baseline=1.0.0/0.3.0 |
| G3 | known_good | — | — | N/A | N/A | pending | anchor strategy pending BEAAA-198/199 P2/P3 measured-dim expansion (Phase 2) |
| M1 | graded_middle | 58 | 59 | +1 | +6.67 | rubric_reweight | baseline=0.1.2/0.2.3 |
| M2 | graded_middle | 50 | 47 | -3 | +5.21 | rubric_reweight | baseline=0.1.2/0.2.3 |
| M3 | graded_middle | — | — | N/A | N/A | pending | Path 2 (OpenRouter re-vendor) selected by CTO per BEAAA-397 triage; agent.json.vendor flip mistral_direct_api -> mistral_via_openrouter is a calibration-set / golden-set v1 change requiring UW co-sign per cto-os.md Supreme Law before lock. |

**Summary:** baselined=5/9 | pending=4 | ARE breaches (>1.0)=0 | pillar breaches (>0.5)=0 | PASS

**BEAAA-353 caveat:** Rubric currently at 1.0.0 (25/25/25/25); contract pillar names + weights pinned at are-scanner-api-v1@v1.0. Baselines locked under rubric 0.1.2 (B1/M1/M2) measure non-zero ARE deltas under current rubric — attribution=rubric_reweight, not scanner regression.

## 3. Reproducibility variance band (rolling N=5)

**L1 (deterministic, no-API):** re-run `score()` against locked `eval_outputs.json` for every baselined fixture. Drift = 0 by construction; non-zero = release-blocker code regression.

| Fixture | Slot | N | ARE min | ARE max | ARE drift | Max pillar drift | Status |
|---|---|---|---|---|---|---|---|
| B1 | known_bad | 5 | 39 | 39 | 0 | 0.0000 | PASS |
| B2 | known_bad | — | — | — | — | — | pending: anchor strategy pending BEAAA-198/199 P2/P3 measured-dim expansion (Phase 2) |
| B3 | known_bad | — | — | — | — | — | pending: anchor strategy pending BEAAA-198/199 P2/P3 measured-dim expansion (Phase 2) |
| G1 | known_good | 5 | 84 | 84 | 0 | 0.0000 | PASS |
| G2 | known_good | 5 | 79 | 79 | 0 | 0.0000 | PASS |
| G3 | known_good | — | — | — | — | — | pending: anchor strategy pending BEAAA-198/199 P2/P3 measured-dim expansion (Phase 2) |
| M1 | graded_middle | 5 | 59 | 59 | 0 | 0.0000 | PASS |
| M2 | graded_middle | 5 | 47 | 47 | 0 | 0.0000 | PASS |
| M3 | graded_middle | — | — | — | — | — | pending: Path 2 (OpenRouter re-vendor) selected by CTO per BEAAA-397 triage; agent.json.vendor flip mistral_direct_api -> mistral_via_openrouter is a calibration-set / golden-set v1 change requiring UW co-sign per cto-os.md Supreme Law before lock. |

**Summary:** N=5 | baselined=5/9 | pending=4 | max ARE drift=0 | breaches=0 | PASS

**L2 (live probe-runner):** owned by `harness/golden_set/repro_gate.py`; cadence = weekly on cheap fixtures, monthly on full slate (BEAAA-398 §3). Out-of-band of CI; runs against fresh probe replicates, not locked outputs.

## 5. Discrimination metric

**Spread (current rubric):** 45 pts (target ≥ 50) — BELOW TARGET
**Spread (baseline ARE values):** 46 pts
**Mean / stdev (current):** 61.6 / 17.52
**Collapse-to-mean (±10):** no collapse

**Per-fixture:**

| Fixture | Slot | Baseline ARE | Current ARE |
|---|---|---|---|
| B1 | known_bad | 38 | 39 |
| B2 | known_bad | — | N/A — pending |
| B3 | known_bad | — | N/A — pending |
| G1 | known_good | 84 | 84 |
| G2 | known_good | 79 | 79 |
| G3 | known_good | — | N/A — pending |
| M1 | graded_middle | 58 | 59 |
| M2 | graded_middle | 50 | 47 |
| M3 | graded_middle | — | N/A — pending |

**Active 10-pt buckets (current):** 30-39: 1, 40-49: 1, 50-59: 1, 70-79: 1, 80-89: 1

**By slot:**
  - graded_middle: n=2, range 47–59, mean 53.0
  - known_bad: n=1, range 39–39, mean 39.0
  - known_good: n=2, range 79–84, mean 81.5

**Partial-coverage caveat:** baselined=5/9 (pending=['B2', 'B3', 'G3', 'M3']). Spread target of 50 pts is set against fully-baselined 9-fixture set; partial-coverage spread reported as-is per BEAAA-419 anchor decision.
