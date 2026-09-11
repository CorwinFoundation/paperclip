# Calibration delta — BEAAA-31762 ProPILE evidence-only mapping

Date: 2026-09-11
Baseline: agent/beaaa-31700 (164b5314)
Candidate: agent/beaaa-31762-propile

## Δ=0 by construction

The ProPILE mapping carries `weight: 0.0`, `garak_live_dispatch: false`, and
`evidence_only: true`. No row contributes to the 100-point BackBond Score and
no row enters the live dispatch. The Garak live-dispatch count remains 27.

A reproducibility N=5 run on the golden set is not presented because no score
can move: the family is unscored and undispatched. Presenting Δ=0 as a
measurement would imply a measured delta where none is possible.

| Agent | Δ total | Δ GOV | Δ ROB | Δ BIAS | Δ OPS |
|-------|---------|-------|-------|--------|-------|
| (all) | 0 (by construction) | 0 | 0 | 0 | 0 |

## Dispatch count unchanged

`scheduled_scan.py` line 150: `expected 27 Garak dispatch rows` — unchanged.
ProPILE rows have `garak_live_dispatch: false` so the opt-in loader skips them.
