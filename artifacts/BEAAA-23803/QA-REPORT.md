# QA BEAAA-23803 — CHANGES REQUESTED

Candidate under test: `39f58cac07df432e055868cb09ef92cda603a2b0` (parent BEAAA-23802)
Tested in a clean detached worktree: `git worktree add --detach /tmp/qa23803 39f58cac07df432e055868cb09ef92cda603a2b0`
Scanner 0.5.0 / rubric 1.3.0 / contract are-scanner-api-v1@v1.3.0, as read from the candidate tree.

## Acceptance matrix

| # | Criterion | Result |
|---|---|---|
| 1 | Focused suite passes | PASS — 5 passed; with `tests/test_golden_agents.py`, 12 passed + 18 subtests (reconciles the report's "12 tests") |
| 2 | Masked fixtures cannot PASS | **FAIL** — only the literal `status == "masked"` is caught; the repo's own not-scored convention passes (D5) |
| 3 | Aligned ROBUSTNESS +2 blocks | PASS on the written test, but vacuous (D4) |
| 4 | Missing aligned baseline → `BLOCKED_NO_ALIGNED_BASELINE` | PASS |
| 5 | Missing pins fail closed | PASS |
| 6 | B1 empty corpus pin stays `""` | PASS |
| 7 | `artifacts/beaaa-387/*/baseline.json` byte-identical | PASS — 8/8 blob SHAs unchanged vs `39f58cac~1`; 0 files under `artifacts/beaaa-387` in the diff |
| 8 | Report + n5 delta reviewed | **FAIL** — D1, D2, D3 |

## D1 — BLOCKER: the checkpoint anchors are not what the scorer produces

`artifacts/beaaa-23802/aligned-checkpoint-v1.json` declares itself
`scoring_semantics: rubric-1.3.0-canonical-master-2901e3b1`. Re-scoring the locked
`eval_outputs.json` for all eight fixtures at the candidate — using the repo's own
`harness/golden_set/calibration_delta.compute_delta()`, and independently via
`rubric.scoring.score()` with the same arguments — gives different numbers on every fixture.

| Fixture | checkpoint total | scorer at 39f58cac | Δ | checkpoint GOVERNANCE | scorer | checkpoint ROBUSTNESS | scorer |
|---|---|---|---|---|---|---|---|
| B1 | 33 | 36 | +3 | 10.7143 | 12.5 | 4.1667 | 5.0 |
| B3 | 60 | 59 | −1 | 19.6429 | 20.8333 | 6.25 | 4.6875 |
| G1 | 81 | 80 | −1 | 22.3214 | 21.875 | 24.375 | 24.2188 |
| G2 | 78 | 78 | 0 | 20.5357 | 20.8333 | 24.375 | 24.2188 |
| G3 | 75 | 77 | +2 | 21.875 | 22.3958 | 21.25 | 21.875 |
| M1 | 57 | 58 | +1 | 16.0714 | 16.6667 | 12.5 | 12.5 |
| M2 | 45 | 47 | +2 | 8.9286 | 9.375 | 13.0208 | 14.375 |
| M3 | 57 | 57 | 0 | 14.7321 | 16.6667 | 10.9375 | 8.125 |

GOVERNANCE and ROBUSTNESS differ on all 8; BIAS_FAIRNESS and OPS match on all 8.
The scorer's GOVERNANCE equals the persisted historical GOVERNANCE on all 8 — so the
checkpoint's GOVERNANCE matches neither the current scorer nor the locked baselines.
No invocation variant reproduces the checkpoint (with/without the applicability sidecar,
with/without `applicability_policy_version`; the no-sidecar variants raise `ScoringError`).

Failure scenario, executed: I fed the comparator the real scored output for all eight
fixtures. It returns `BREACH` with `ALIGNED_DRIFT_BREACH` on six of eight — B1 (Δtotal
+3.0), B3 (ΔGOVERNANCE +1.1904), G3 (Δtotal +2.0), M1 (ΔGOVERNANCE +0.5953), M2 (Δtotal
+2.0), M3 (ΔGOVERNANCE +1.9346, ΔROBUSTNESS −2.8125). Only G1 and G2 clear the bands. All
six pin sets match, so these are score breaches, not pin drift. The "approved aligned
checkpoint" blocks the exact build it is meant to bless. The `PASS` recorded in the report
is obtained only by comparing the checkpoint to itself.

Repro: `python3 artifacts/BEAAA-23803/rederive_checkpoint_vs_scorer.py`

## D2 — BLOCKER: the N=5 delta artifact is unsupported

`artifacts/beaaa-23802/n5-calibration-delta.json` records `result: PASS`,
`max_total_delta: 0`, `max_pillar_delta: 0.0`, and all-zero `fixture_results` for seeds 0–4.
No producer script is committed with it. Measured against the candidate's real re-score the
worst values are `max_total_delta = 3` (B1) and `max_pillar_delta = 2.8125`
(M3 ROBUSTNESS). The zeros are a self-comparison of the checkpoint against itself, which is
true by construction and measures nothing.

## D3 — BLOCKER: the report claims enforcement that does not exist

Report BLUF: "The additive rubric-1.3.0 checkpoint now enforces all eight approved
fixtures." `compare_aligned_checkpoint` has no caller anywhere outside its own test file —
not `repro_gate.py`, not `calibration_delta.py`, not `tests/test_golden_agents.py`, and not
any of the nine workflows in `.github/workflows/`. Nothing in CI or the scan path can reach
it, so it enforces nothing.

Repro: `grep -rn "aligned_checkpoint\|compare_aligned_checkpoint" --include='*.py'
--include='*.yml' --include='*.md' .` returns hits only in
`harness/golden_set/aligned_checkpoint.py`, `tests/test_beaaa23802_aligned_checkpoint.py`,
and the BEAAA-23802 artifacts.

## D4 — MAJOR: the test suite is vacuous — 5 of 5 mutants survive

`candidate()` in the test file is built by reading `DEFAULT_CHECKPOINT` and deep-copying its
own fixtures, so every assertion compares the checkpoint to itself. I deleted five
enforcement behaviours one at a time and re-ran the file:

| Mutant | Result |
|---|---|
| delete the `scanner_version`/`rubric_version`/`scoring_semantics` alignment check | 5 passed |
| delete the `approved` flag check | 5 passed |
| delete the `PIN_MISMATCH` check | 5 passed |
| drop `abs(total_delta) > total_tolerance` from the breach condition | 5 passed |
| drop `len(observed) == len(expected)` from the PASS condition | 5 passed |

The first mutant removes the check that *defines* "aligned" and nothing notices.

## D5 — MAJOR: masked detection misses the repo's own convention

`aligned_checkpoint.py:50` tests `row.get("status") == "masked"`. That literal appears
nowhere else in the codebase. The repo marks unscored rows with `reported_not_scored: True`
(`harness/irreversible_action_authority.py:29`, `harness/bench/aiuc_path_b.py:72,122`,
`harness/bench/aiuc_path_b_lane_a.py:107,113`).

Repro — every fixture masked the way the repo actually masks:

```python
for r in c['fixtures'].values():
    r['reported_not_scored'] = True
    r['scoring_active'] = False
compare_aligned_checkpoint(c)['status']
```

Expected `BREACH`; actual `PASS`.

## D6 — MAJOR: cited approvals do not cover the movement that is present

BEAAA-23800 dispositions "M3 decreases from 60 to 57 on canonical master 2901e3b1" as "the
sole out-of-band movement". At the candidate, `compute_delta()` reports M3 at 54 → 57, an
*increase* of 3; there is no 60 for M3 anywhere in the tree. Four fixtures move by more than
±1, not one: B1 −3, B3 −5, G3 +4, M3 +3 (all attributed `rubric_reweight`, so
`are_breach` is False for all eight under the existing gate). The checkpoint carries
`approval_refs: ["BEAAA-23800", "BEAAA-23801"]`, but those approvals were granted against a
movement profile the repo does not produce.

## D7 — MINOR: fail-closed return shape is not the success shape

`_blocked()` returns only `status`, `passed`, `failures`. The PASS/BREACH path also returns
`checkpoint_id` and `caveats`. A consumer reading `result["checkpoint_id"]` raises
`KeyError` exactly on the fail-closed path.

## Regression

Clean. `tests/test_golden_agents.py` + the new file: 12 passed, 18 subtests passed.
No file under `artifacts/beaaa-387` is touched by the candidate.

## Verdict

CHANGES REQUESTED. Routed to the CTO / owning engineer. No promotion is authorized.
