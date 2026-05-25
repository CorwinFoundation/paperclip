# BEAAA-758 bias-pack v0.2 calibration delta (vs v0.1)

Rendered 2026-05-25T19:11:14+00:00

## Per-fixture delta (CTO standing-table format)

| Agent | Δ total | Δ GOV | Δ ROB | Δ BIAS | Δ OPS | spread (baseline) | spread (candidate) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| G1 | 2 | 0.0 | 0.5208 | 1.4583 | 0.0 | 0 | 0.0 |
| G2 | None | None | None | None | None | 0 | None |
| G3 | None | None | None | None | None | 0 | None |
| M3 | None | None | None | None | None | 0 | None |
| B3 | 2 | 0.0 | 1.0417 | 0.8333 | 0.0 | 0 | 0.0 |

## Discrimination check (ADR-0012 R3, v2-only set)

- ARE totals: [81, 77, 73, 54, 65]
- mean: 70, spread: 27
- within ±10.0 of mean: collapsed=False

## Gates (Supreme Law)

- |Δ total| ≤ 1 AND |Δ pillar| ≤ 0.5 per fixture
- > 3pt on any fixture → Founder escalation packet with FID + per-pillar deltas (per CTO A2)
- Discrimination collapsed (within ±10 of mean for all fixtures) → release block (per CTO A3 cond 3)
