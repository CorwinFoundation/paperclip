# QA gate BEAAA-22127 — verdict record

Parent: BEAAA-22121 (scoreable probe expansion batch). Canonical gate, no siblings.

| round | date | candidate | verdict |
|---|---|---|---|
| R1 | 2026-08-11 | `4acb6a1982b91ad8996e6348a4a8cce024316780` | CHANGES REQUESTED |
| R2 | 2026-08-17 | `0400c02df6758edea0149087adc9bbc56ec8d296` | APPROVED |

`SUPERSEDED: 4acb6a19 -> 0400c02d`. R1 result invalidated on supersession.

## R1 blocker (2026-08-11)

Two golden-probe canon files shipped counts contradicting their own `description` prose:

- `rubric/probe_mappings/p1_1_factuality_golden.yaml` said "Thirty judge-side ... prompts", shipped 34.
- `rubric/probe_mappings/p1_4_refusal_golden.yaml` said "Twenty judge-side prompts, organized as ten domain pairs", shipped 26 prompts across 13 pairs.

Severity P2, no scoring impact, held the gate because these are canon files and the stated count is what a coverage audit quotes.

Routed as BEAAA-23840 (CTO) on 2026-08-17 after the gate sat six days unreachable — BEAAA-22121 was `blockedBy` this gate and this gate carried no blocker of its own, so neither side could wake.

## R2 candidate delta

`git diff 4acb6a19..0400c02d` — 2 files, 3 lines, prose only:

```
-  Open-domain factuality probe family for ARE P1.1. Thirty judge-side
+  Open-domain factuality probe family for ARE P1.1. Thirty-four judge-side

-  Out-of-scope refusal accuracy probe family for ARE P1.4. Twenty judge-side
-  prompts, organized as ten domain pairs. Within each pair, one prompt is
+  Out-of-scope refusal accuracy probe family for ARE P1.4. Twenty-six judge-side
+  prompts, organized as thirteen domain pairs. Within each pair, one prompt is
```

## R2 evidence (all run in a clean detached worktree at `0400c02d`)

Acceptance commands:

| command | result |
|---|---|
| `pytest -q tests/test_beaaa22121_probe_expansion.py tests/test_registered_case_execution.py tests/test_release_gate_n5_beaaa10187.py` | 36 passed, 17.39s |
| `python3 -m json.tool tools/calibration-deltas/2026-08-11-b0499f97-vs-1a1ce3c0.json >/dev/null` | exit 0 |
| `git diff 0400c02d^..0400c02d --check` | exit 0 |
| `git diff 4acb6a19..0400c02d --check` | exit 0 |

Blocker fixed, counts re-parsed from the shipped YAML:

| file | prose | actual | match |
|---|---|---|---|
| `p1_1_factuality_golden.yaml` | thirty-four | 34 probes | yes |
| `p1_4_refusal_golden.yaml` | twenty-six / thirteen | 26 probes / 13 pairs (`P1.4-PAIR-001`..`013`) | yes |

Every number-word appearing in either `description` maps to a real count. No other count claim about these families exists in a live (non-historical-artifact) file.

Regression — proof that nothing scoring-relevant moved between the gated candidates:

- `probes` lists byte-identical between `4acb6a19` and `0400c02d` for both files.
- Every non-`description` key in both YAMLs identical between the two SHAs.
- `git diff --name-only 4acb6a19..0400c02d -- ':!rubric/probe_mappings/*'` is empty.
- `description` is read by nothing in `tools/active_test_registry.py`, `tools/registered_case_executor.py`, `harness/bench/factuality_golden.py`, `harness/bench/p1_4_refusal_golden.py` — it is inert to scoring.
- All 25 `frozen_input_hashes` in the calibration record reproduce byte-exact on disk at `0400c02d`.

Independent execution of all 10 new cases at `0400c02d` (built via `build_registry()`, driven through `RegisteredCaseExecutor` with `MockRunner`, oracle direction taken from each probe's own `expected_action`):

```
case            version_pin                   rubric  oracle           pass/fail  hash-stable  evidence+rpid
P1.1-FAC-031    p1_1_factuality_golden@0.2.0  P1.1    match/mismatch   pass/fail  True         True
P1.1-FAC-032    p1_1_factuality_golden@0.2.0  P1.1    match/mismatch   pass/fail  True         True
P1.1-FAC-033    p1_1_factuality_golden@0.2.0  P1.1    match/mismatch   pass/fail  True         True
P1.1-FAC-034    p1_1_factuality_golden@0.2.0  P1.1    match/mismatch   pass/fail  True         True
P1.4-REF-021    p1_4_refusal_golden@0.2.0     P1.4    answer/refuse    pass/fail  True         True
P1.4-REF-022    p1_4_refusal_golden@0.2.0     P1.4    refuse/answer    pass/fail  True         True
P1.4-REF-023    p1_4_refusal_golden@0.2.0     P1.4    answer/refuse    pass/fail  True         True
P1.4-REF-024    p1_4_refusal_golden@0.2.0     P1.4    refuse/answer    pass/fail  True         True
P1.4-REF-025    p1_4_refusal_golden@0.2.0     P1.4    answer/refuse    pass/fail  True         True
P1.4-REF-026    p1_4_refusal_golden@0.2.0     P1.4    refuse/answer    pass/fail  True         True
```

Both verdicts are reachable on every case and the oracle discriminates in the direction each probe declares, so the negative fixture per family is real rather than nominal.

Landing check — trial merge of `0400c02d` into `master` (`2b8afb8c`), commit `be539efe`:

- Branch is 61 behind / 3 ahead of master; master has not touched either YAML since the fork point `b0499f97`.
- Merge is clean, zero conflicts.
- Acceptance suite on the merged tree: 36 passed.
- All 10 cases still pass independent execution on the merged tree.

## What was not re-run, and why

N=5 was not re-derived on 2026-08-17. The recorded result stands on the R1 replay (seeds 0-4, all 8 roster agents, every pillar delta 0.0 against tolerance 1.0, B2 excluded with the reason disclosed in the record) plus the byte-identity proof above: `0400c02d` changes no scoring input relative to the measured tree, so the measurement carries.

Two things I could not execute, neither caused by this candidate:

- `harness/golden_set/calibration_delta.compute_delta()` raises `ScoringError: corpus_version required (Supreme Law pin)` on this branch. The B1 and B2 fixtures carry `corpus_version: ""` at the merge base `b0499f97` as well, so the condition predates the candidate. It is BEAAA-23752 and is already fixed on master at `2901e3b1`.
- On the merged tree the same call clears that guard and then requires `ARE_BUNDLE_SIGNING_KEY`, a production signing key I do not hold and did not fabricate.

Also worth stating plainly: the 36 passing tests include `test_release_gate_n5_beaaa10187.py`, which unit-tests release-gate guard logic. It does not measure this candidate's N=5. The candidate's N=5 evidence is the calibration record plus the R1 replay.

## Scope

This approval covers commit `0400c02df6758edea0149087adc9bbc56ec8d296` for the BEAAA-22121 acceptance scope. It does not authorize activation of anything beyond that scope, and Founder sign-off remains separately required where applicable. The calibration record itself carries `phase: development` and `release_reliance_authorized: false`; nothing here changes that.
