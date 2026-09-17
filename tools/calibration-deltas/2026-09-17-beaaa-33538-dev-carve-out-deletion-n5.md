# BEAAA-33538 — N=5 seeds 0-4 golden calibration delta (dev_scoring_active carve-out deletion)

Candidate branch `beaaa-33538-drop-dev-carveout`, merged with master at `00825874`
(BEAAA-33537 TASK 3a, #575). Adapted from the analysis first written on PR #576,
which implemented the same deletion in parallel; the numbers below are re-taken at
this head.

## Result: PASS — delta is structurally zero; no calibration run required

The golden scoring path (`rubric/scoring.score`, exercised by the N=5 calibration
script) does not import `tools.active_test_registry`. The change in this card is
confined to `tools/active_test_registry.py::row_blocks_scoring_activation()`: the
`dev_scoring_active` early-return is deleted. No rubric YAML, no scoring function,
no probe specification, and no golden fixture changes.

This is structurally identical to the D2 landing documented in
`2026-09-16-beaaa-33175-d2-repair-n5.md`, which proved byte-identical output across
six live golden agents x 5 seeds. That proof covers this card by the same argument:
the golden path has no read path into the registry.

**Stronger than the structural argument, measured here:** with
`tools/active_test_registry.py` restored to `origin/master` at this same merged
head, the full `build_registry()` output serialises identically to the candidate in
both flag states — ON `4b018cf1`, OFF `6e09bde4`. Nothing the scorer could read
changes, so the seed sweep is zero by construction rather than by sampling.

## Registry measurement at the merged head, carve-out deleted

One `build_registry()` process per flag state.

Flag OFF (production-equivalent):

| metric | value |
|---|---|
| scoring_active=True | 479 |
| reporting_status=scored | 444 |
| D2 violations (scoring_active + unmapped or unevidenced) | **0** |
| rows carrying dev_scoring_active | 143 |
| of those, scoring_active | 122 |
| dev rows that demote due to D2 with carve-out gone | **0** |

Flag ON (`BACKBOND_FOUNDER_DEV_MODE=on`, dev-tier rows scoring):

| metric | value |
|---|---|
| scoring_active=True | 704 |
| D2 violations | **0** |
| rows carrying dev_scoring_active, scoring_active | 143 |
| dev rows that demote due to D2 | **0** |

The flag-swung dev set is 143 - 122 = **21**, the size BEAAA-33537's spread gate
was expected to leave (down from 67). Every one of those rows carries the P3.4
mapping and target-measured evidence written by PR #569 (BEAAA-33507 TASK 2) and
clears D2 on its own fields. Zero rows needed the carve-out to stay scoring-active.

## Behaviour guard

`tests/test_beaaa33538_no_tier_exemption.py` asserts that a row carrying
`dev_scoring_active=True` still blocks when unmapped and again when unevidenced,
and additionally replays every non-D2 field/value pair the real registry carries
onto a D2-blocked row, asserting it stays blocked. A future tier-keyed early-return
on any field breaks it, not just one on `dev_scoring_active`.

## Derived pin

`tools/active_test_registry.py` is inside the probe registry's authoritative source
closure, so the deletion moved the embedded closure digest `0e8800b` -> `705a18d`
and both artifacts under `tools/probe-registry/` were regenerated. The entire delta
is that digest string; no row, count, or category moved.
