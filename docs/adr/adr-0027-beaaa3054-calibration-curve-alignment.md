# ADR-0027 — Named-Curve Registry and dim_id Shape Gate

- **Ticket**: BEAAA-3054 (calibration curve alignment) / BEAAA-2198 (dim_id expansion)
- **Status**: Accepted
- **Supersedes**: ad-hoc inline curve constants in individual bench modules
- **Date**: 2026-06-06

## Context

Phase-1 scoring relied on inline magic constants for calibration curves in each bench module.
BEAAA-3054 / BEAAA-3161 centralized these as a named-curve registry so that a single curve change
updates all callers deterministically.

Separately, BEAAA-2198 expanded the canonical dim_id shape from the legacy `P<n>.<m>` shorthand
to also accept a fully-qualified 3-segment `PILLAR.FAMILY.SUB` shape (all-caps, ADR rubric v1.0.1),
matching the same expansion already shipped in `harness/cli_runner.py`. This was applied to
`api/intake_guard._DIM_ID_RE` in BEAAA-3337 (commit 340bcd9).

## Decision

1. **Named-curve registry**: calibration curves are registered in `scanners/curve_registry.py`
   under stable string keys (e.g. `CURVE_RUNNER_V1`, `CURVE_LEGACY_TQA_FRONTIER`). All scorers
   import curves by key; no inline constants allowed.

2. **Dual dim_id shape**: `_DIM_ID_RE` accepts both `P<n>.<m>` (legacy) and
   `PILLAR.FAMILY.SUB` (fully-qualified). The two patterns are non-overlapping; the FQ shape
   requires exactly 3 ALL_CAPS segments separated by `.`.

## Addendum: Pillar-5+ Guard (BEAAA-5270)

**Date**: 2026-06-14

When BEAAA-3337 widened the legacy `P<n>.<m>` branch from `P[1-4]` to `P\d+`, it broke the
invariant that the rubric has exactly 4 pillars (P1–P4). Any `P5.x`, `P6.x`, etc. must be
rejected at the intake edge because:

- Pillar-5+ is explicitly out of scope for the current rubric version.
- Accepting unknown pillars allows unverified scores to flow into the scoring engine.

**Fix (commit on branch `beaaa-5270-intake-guard-fix`)**:

```python
# Correct — pillar index locked to 1-4; sub-dim starts at 1
_DIM_ID_RE = re.compile(
    r"^(?:P[1-4]\.[1-9][0-9]?|[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*)$"
)
```

The FQ `PILLAR.FAMILY.SUB` branch is unaffected by this guard; it does not use a numeric pillar
index and is unrestricted in that dimension.

**Rule**: any future rubric expansion to a fifth pillar MUST update `_DIM_ID_RE` and the
corresponding allowlist gate before wiring in new dim_ids. Do not widen `P\d+` — change the
character class explicitly (e.g. `P[1-5]`).
