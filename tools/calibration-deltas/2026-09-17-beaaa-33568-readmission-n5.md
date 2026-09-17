# BEAAA-33568 — N=5 seeds 0-4 golden calibration delta (stability-floor re-admission)

Acceptance item: *N=5 seeds 0..4 zero total and pillar delta measured at the head
that merges, not at its base.*

Baseline `origin/master` @ `f4abb933a` (BEAAA-33596, #578). Candidate: PR #577
head `2889d93d5`, the tree that actually lands. An earlier revision of this note
measured the `e8457b07` -> `209a1390` pair; that base is no longer this PR's base,
so the pair below is a fresh measurement rather than a carry-forward.
Generator: `scripts/beaaa33175_n5_calibration.py`, run once per tree, from a
detached worktree at each SHA. 6 live golden agents x 5 seeds = 30 scorings per
tree. Both payloads are checked in beside this note as
`2026-09-17-beaaa-33568-readmission-n5.json`.

The head that merges is the artifact-only commit carrying this note, one
commit above the measured `2889d93d5`. That commit's diff is confined to
`tools/calibration-deltas/` — no code, no ledger, no registry input — so the
numbers below are unchanged at it by construction. Stating the SHA the
measurement was actually taken at is more honest than re-labelling it with a
SHA that did not exist when the processes ran.

## Result: PASS — worst absolute movement 0.0

| agent | baseline median | candidate median | Δ total | Δ pillar (max abs) | seed spread (base / cand) |
|---|---|---|---|---|---|
| B1 | 37.0 | 37.0 | 0.0 | 0.0 | 0.0 / 0.0 |
| G1 | 80.0 | 80.0 | 0.0 | 0.0 | 0.0 / 0.0 |
| G2 | 78.0 | 78.0 | 0.0 | 0.0 | 0.0 / 0.0 |
| M1 | 58.0 | 58.0 | 0.0 | 0.0 | 0.0 / 0.0 |
| M2 | 47.0 | 47.0 | 0.0 | 0.0 | 0.0 / 0.0 |
| M3 | 55.0 | 55.0 | 0.0 | 0.0 | 0.0 / 0.0 |

The serialized `rows` payloads from the two trees are byte-identical under a
sorted-key dump, and seed spread is 0.0 on both sides — so the zero is a real
invariance and not a wide distribution whose medians happen to coincide.

## Why the zero is structural

Same argument as `2026-09-16-beaaa-33175-d2-repair-n5.md` and
`2026-09-17-beaaa-33538-dev-carve-out-deletion-n5.md`: the golden scoring path
(`rubric/scoring.score`) has no import of `tools.active_test_registry`, and this
card changes only rank-2 ledger state plus the slice tool that derives it. No
rubric YAML, no scoring function, no probe specification, no golden fixture is
touched. There is no mechanism by which a re-admission could move a golden score.

## Registry census at the candidate head `2889d93d5`

One `build_registry(include_content_candidates=True)` process per tree per flag
state, four in total, compared positionally against the base `f4abb933a` (row
identity matched on all 2,490 positions, 0 mismatches).

| metric | baseline `f4abb933a` | head `2889d93d5` | required |
|---|---|---|---|
| flag OFF `reporting_status=scored` | 444 | **444** | unmoved |
| flag OFF `scoring_active` | 479 | **479** | unmoved |
| flag ON `scoring_active` | 704 | **726** | moves only by rows re-admitted |

The flag-off positional diff is 0 rows across both `scoring_active` and
`reporting_status`. The flag-on delta is **+22**: exactly 22 rows change
`scoring_active`, every one `False -> True`, and every one of the 22 ids is
`bb18716.approval_freshness_replay.*` — checked on all 22, not sampled
(suffixes 01-23 with 09 absent, which is the family's own roster). No row of any
other family moves, in either direction. That is the whole of
`approval_freshness_replay` and nothing else — the no-cross-family-inference
rule holds as measured, not as asserted.
