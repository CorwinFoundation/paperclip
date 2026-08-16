# BEAAA-23752 — golden-set calibration_delta repair

**BLUF:** The three golden-set entrypoints (`calibration_delta.compute_delta`,
`n5_band.run_l1_n5`, `discrimination.compute_discrimination`) all raised
`ScoringError: corpus_version required (Supreme Law pin)` and scored nothing.
The cause is a stale `or None` coercion at each module's `score()` call site, not
a bad value in B1's baseline. Removing the coercion restores all three. The
repaired harness emits byte-identical output to the pre-fix reference on every
fixture — Δ = 0.0000 — so the ruler did not move and **B1 does not need a
re-pin or a re-baseline. The CTO pin decision the issue asked for is moot.**

Contract: `contracts/are-scanner-api-v1.md@v1.3.0`.
Classification: **rule-execution** — supervision contract v1.1 classification
table, row *"Refactor that doesn't touch behavior (move code, rename internal
variables) → Rule execution → NO (still requires N=5 on golden as regression
check)"*. No `cto_cosign_ref` required. Supreme Law Layer 2 (±0) applies because
this is a harness-itself change; the ±0 band is met and evidenced below.

## What the issue got right, and the one thing it got wrong

Right: `compute_delta()` cannot run in this tree, B1 sorts first so the run
aborts before any other fixture is scored, and `artifacts/beaaa-387/B1/baseline.json`
carries `corpus_version: ""`.

Wrong: the empty value did not enter on commit `40257512` (BEAAA-3009). B1 has
carried `corpus_version: ""` since its baseline was first persisted, at
`c6609045` (BEAAA-387, scanner 0.2.3 / rubric 0.1.2):

| commit | corpus_version | scanner | rubric |
| --- | --- | --- | --- |
| `c6609045` first persist | `""` | 0.2.3 | 0.1.2 |
| `d22bf848` P1..P4 rename | `""` | 0.2.3 | 0.1.2 |
| `40257512` BEAAA-3009 backfill | `""` | 0.4.0 | 1.1.0 |

`40257512` moved B1's scanner and rubric pins but left `corpus_version` as it
found it. That commit's own acceptance record shows `compute_delta` running
green on 2026-06-05 with all 40 deltas at 0.0, and the emission it produced
(`artifacts/beaaa-387/calibration_delta_2026-06-05.json`) records B1's
`baseline_corpus_version` as `null`. So the harness ran fine with the empty
value for as long as the boundary tolerated it.

## Actual cause

`abd9116e` (BEAAA-5276) made `corpus_version=None` a hard reject at the
`score()` boundary, and deliberately kept `""` valid: *"Empty string ("") passes
through unchanged (explicit preview/replay)."* B1 and M1 were locked under
`lock_source: deterministic_replay`, which is exactly the case `""` was
preserved for.

That commit also fixed this precise bug — in one of the four call sites. Its own
message: *"tests/test_golden_agents.py: fix `or None` coercion that converted
B1/M1 empty-string corpus_version to None; pass "" directly for byte-identical
replay."* The three `harness/golden_set/` call sites were missed, so they kept
converting B1's valid `""` into the one value the new boundary rejects.

This is why the failure is an entrypoint defect and not a data defect. B1's
persisted pin was never illegal; the harness was mistranslating it.

## Fix

Three lines, one per module — pass the persisted value through verbatim,
matching what `tests/test_golden_agents.py` already does:

- `harness/golden_set/calibration_delta.py:168`
- `harness/golden_set/n5_band.py:131`
- `harness/golden_set/discrimination.py:124`

`calibration_delta.py:225` keeps its `or None`. That one feeds the emitted
`baseline_corpus_version` report field, not `score()`; leaving it alone is what
keeps the emission byte-identical to the 2026-06-05 record, which also carried
`null` there.

## Scope note — this was wider than the issue stated

The issue scoped the defect to `compute_delta`. `n5_band.run_l1_n5` and
`discrimination.compute_discrimination` carry the same coercion and were failing
the same way. `run_l1_n5` **is** the N=5 merge gate, so fixing only
`compute_delta` would have left the gate itself down. All three are harness-itself
modules owned by this seat, so all three are repaired here.

## ±0 evidence (Supreme Law Layer 2)

The pre-fix reference cannot be taken by running the broken code, so it was taken
by restoring the pre-`abd9116e` boundary exactly: set `rubric.scoring._TEST_MODE
= True`, which suppresses only the `None` raise and changes no scoring math. That
recovers the emission the harness produced before the boundary tightened. The
post-fix runs use the production boundary (`_TEST_MODE = False`) with no
suppression. `generated_at` is wall-clock and is stripped before hashing; every
other byte is compared. Reproduce with `capture_emissions.py` (`PYTHONPATH=.`).

SHA-256 over each emission, `today="2026-08-16"`:

| entrypoint | pre-fix reference | post-fix r1–r5 |
| --- | --- | --- |
| `calibration_delta` | `7c645c74fdec5d3b21ae37c3ffcd4c7288f44293df501b21eddcfd15d20585cd` | identical, all 5 |
| `n5_band` | `10604cff5b9fced9aadb8a1afe927854cbaa6a106f0efedd417c3abe27c2d33f` | identical, all 5 |
| `discrimination` | `5e80d4d5df2ee80e9d6eeea9361ee93c43d4c14d6499e40bc7304d993e3f2473` | identical, all 5 |

15 of 15 post-fix emissions `cmp`-identical to the pre-fix reference. **Δ =
0.0000 on every pillar, every fixture, every run.** N=5 repeatability from
`run_l1_n5` itself: `N=5 | baselined=8/9 | pending=1 | max ARE drift=0 |
breaches=0 | PASS`.

Raw emissions are in `emissions/`.

## Tests

`PYTHONPATH=. python3 -m pytest` over the full modules (not `-k`):
`test_golden_agents.py`, `test_golden_set_l2_gate.py`,
`test_release_gate_n5_beaaa10187.py`, `test_beaaa14958_trusted_golden_replay.py`,
`test_beaaa14943_golden_applicability.py`, `test_scoring.py`
→ **52 passed, 18 subtests passed, 6 failed.**

The 6 failures are pre-existing and unrelated — 5 in
`test_beaaa14958_trusted_golden_replay.py`, 1 in
`test_beaaa14943_golden_applicability.py`, all in trusted-replay / applicability
derivation. Verified by stashing the three edited files and re-running: the same
6 fail, identically. They are not introduced or masked by this change and are not
in this issue's scope.

## Open finding for the CTO — not fixed here

With the harness running again, `calibration_delta` reports non-zero drift on 7
of 8 baselined fixtures, entirely in ROBUSTNESS:

```
B1 39->36 (-3)   B3 64->59 (-5)   G1 79->80 (+1)   G2 77->78 (+1)
G3 73->77 (+4)   M1 58->58 ( 0)   M2 48->47 (-1)   M3 54->57 (+3)
```

The module reports `PASS` because all 8 attribute to `rubric_reweight`:
baselines are locked at rubric 1.1.0 / 1.2.0 and the tree is at rubric 1.3.0, so
`version_aligned` is false and the breach test is suppressed by design. The
practical consequence is that the calibration gate is currently
attribution-masked on every fixture — it would not catch a real regression until
the baselines are re-locked at 1.3.0.

Re-baselining moves the ruler, which is a CTO call, not this seat's. Filed
separately rather than folded into this repair. `run_l1_n5` is unaffected: it
measures within-run repeatability across replicates, and that is a genuine 0.

## Required metadata

- `scanner_version`: 0.5.0
- `corpus_version`: per-fixture, unchanged — B1 `""` (deterministic_replay),
  others as persisted in `artifacts/beaaa-387/*/baseline.json`
- `probe-set-version`: unchanged, no probe touched
- `rubric_point_id`: none — no rubric point added, retired, reweighted or rescored
- `N=5 calibration delta`: Δ = 0.0000, byte-identical, 15/15 (above)
- `cto_cosign_ref`: n/a — rule-execution
- `contract`: `are-scanner-api-v1.md@v1.3.0`, no output-shape change
