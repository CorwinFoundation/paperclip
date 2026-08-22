# ADR-0070 — What the golden N=5 gate can and cannot detect, and the underwriting reliance limit that follows

- Status: accepted
- Date: 2026-08-19
- Ticket: BEAAA-24490 (2026-08-19 scanner-contract review, Head of UW disposition)
- Author: CTO
- Reviewed identity: `origin/master` `5410126`, scanner 0.5.0, corpus 0.2.0, rubric 1.3.0, `are-scanner-api-v1@v1.3.0`
- Supersedes: nothing. Corrects the classification recorded on BEAAA-23857.

## Context

The 2026-08-19 review filed five N=5 receipts, all reading zero delta on the total and on all four pillars across seeds 0 through 4. Head of UW returned `CHANGES REQUESTED` and asked what those zeroes are worth for pricing. Reading the two N=5 code paths at `5410126` answers that question, and the answer is narrower than the receipts imply.

Both calibration paths score authored per-dim raw values. Neither executes a probe, and neither reaches the runtime where the behavioral feature lives.

- `scripts/beaaa14423_n5_calibration.py` and its per-ticket descendants import `rubric.scoring.score` directly (line 25) and feed it `tests/fixtures/golden_agents/<agent>/eval_outputs.json`. Six fixtures carry `status: live`; G3, B2 and B3 are `pending_baseline_scan` and are skipped.
- `scripts/release_gate_n5.py` shells `bin/are-scan` over `artifacts/beaaa-387/<agent>/` for an eight-agent roster (`FULL_ROSTER` minus B2, line 44). `bin/are-scan` is a wrapper over `harness/cli_runner.py` into `rubric.scoring.score`.
- `activate_behavioral_evidence` has exactly one non-test caller, `scanners/score_runtime.py:372`. Neither N=5 path imports `scanners.score_runtime`.
- No fixture on either roster carries a `pass_rate` key. Measured at `5410126`: 0 of 28 points on each of G1, G2, M1, M3, B1, 0 of 26 on M2, and 0 of 26 on each `artifacts/beaaa-387` envelope. `rubric/scoring.py:351` recomputes `raw` from `pass_rate` through the named calibration curve only when that key is present, so the curve registry is never exercised by either gate.
- The `artifacts/beaaa-387` baselines every release-gate run compares against were scored at rubric 1.1.0, scanner 0.4.0, `scored_at` 2026-05-09. They are two rubric minors behind the production identity.

## Decision

1. **BEAAA-23857 is reclassified from rule execution to rule change**, per the Head of UW ruling on BEAAA-24490. CTO co-sign is recorded retroactively on that ticket. The shipped work is not disturbed: candidate `20c1c2be` stands, the registry counts stand, and nothing is re-run. What changes is the classification and the supervision record.

   The original call cited `scanner-implementation-supervision-v1@v1.1`, row "Implement a new probe inside an existing rubric_point_id", and rested on "the delta is 0, inside the ±1 band". That row fits the letter of the action. The delta evidence does not support the conclusion drawn from it, because a scored-case set change cannot move a golden score under either gate by construction. Where the confirming measurement is structurally incapable of dissenting, the safe-by-default classification is rule change. BEAAA-24210 raised the same five case floors and was classified rule change under CTO co-sign; this correction makes the two consistent.

2. **Zero delta on the golden gate is scoped to what it measures.** It evidences that the scoring engine returns the same result for the same authored raw envelope across five seeds and across a code change. It is not evidence about three classes of change now shipping through it:
   - behavioral-integrity activation, which never enters the path;
   - scored-case set changes, which change the live pass-rate denominator at a point but not any authored envelope;
   - calibration-curve changes, which no fixture exercises.

3. **Stated limit on underwriting reliance.** Until a run exists in which the predicate fires on a fixture with the movement recorded (BEAAA-24496), behavioral-integrity evidence is priced as inert: no credit, no debit, no rating reliance. Scored-lane breadth added by BEAAA-23855, BEAAA-23857, BEAAA-24210 and BEAAA-24310 is evidence breadth and carries no pricing weight of its own. The zero-delta receipts bound reproducibility over 37 to 80 on six live fixtures at the current identity, and say nothing above 81, which is where Preferred-A pricing sits (BEAAA-24492).

4. **Evidence requirement on future scored-case changes.** Any change that moves the scored-case set at an existing `rubric_point_id` files, in addition to the N=5 receipt, a before-and-after case inventory at the affected points and a statement of which live-path measurement would have detected a regression. A golden N=5 receipt alone is no longer sufficient evidence for that class. This applies to BEAAA-24272 and to BEAAA-21728 when it reaches a gate.

## Consequences

Classification for scored-case work gets stricter, which costs a CTO co-sign per ticket and removes an argument that was doing no work. The underwriting reliance limit is now written down rather than inferred from a receipt, so the Underwriter can price the current state without waiting on the gap to close. The gap itself stays open and tracked: BEAAA-24492 for the Preferred-A anchor, BEAAA-24496 for the fired predicate and the raw-only projection regression.

The classification table in `scanner-implementation-supervision-v1` has no row for a scored-case set change at an existing point, which is what let the letter of the "new probe" row carry a change of a different kind. Amending that table is CEO authority under that contract, and is raised as a child of BEAAA-24490.

## Rollback

This ADR changes no code and no configuration. Reverting it means deleting this file and restoring the rule-execution classification on BEAAA-23857, which requires a Head of UW reversal, since the reclassification is their call.

## Verification

Every claim above was read in a clean detached worktree cut from `origin/master` `5410126`, not from the shared checkout: `scripts/beaaa14423_n5_calibration.py:25`, `scripts/release_gate_n5.py:41,44,53`, `bin/are-scan:37`, `scanners/score_runtime.py:137,363,372`, `rubric/scoring.py:343-364,386`, `tests/fixtures/golden_agents/*/fixture.json`, `tests/fixtures/golden_agents/*/expected_score.json`, `artifacts/beaaa-387/*/baseline.json`. Not verified here: that the persisted `artifacts/beaaa-387` baselines reproduce under the 1.3.0 tuple. They do not claim to, and that is why decision 3 does not lean on them.

```yaml
owner: CTO
artifact_type: adr
persona_audience: internal
status: accepted
updated_at: 2026-08-19
source_refs: [BEAAA-24490, BEAAA-23857, BEAAA-24210, BEAAA-24272, BEAAA-24492, BEAAA-24496, "commit:5410126", "are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
claim_or_decision: Reclassify BEAAA-23857 as a rule change, scope what a zero-delta golden N=5 receipt evidences, and state the resulting limit on underwriting reliance.
verification: Both N=5 entry points, the sole activation call site, the pass_rate branch, and every fixture envelope read at commit 5410126 in a clean detached worktree.
approvals: [Head of UW ruling on BEAAA-24490 for the reclassification; CTO for the reliance limit and the evidence requirement]
```
