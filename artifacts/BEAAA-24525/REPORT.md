# BEAAA-24525 — stale golden baseline band blocks: fix + guard

**Candidate:** `aa4b8752058247512f882172a475d855f7ba3056`
**Branch:** `agent/beaaa-24525-golden-band-guard`, cut from canonical `origin/master` `5410126335a0e3a71b698b2803e0afaa96dc797c`
**Built in:** isolated detached worktree `/tmp/wt-24525` (baseline comparison worktree `/tmp/wt-24525-base`)
**Classification:** rule execution — confirmed by measurement, not assumed (see §4, control 2)

---

## 1. Ticket premises, re-measured before acting

Every row of the ticket's table reproduced exactly at `5410126`, recomputing
`rubric.bands.assign_band` / `band_to_dict` against each file's own persisted `bmi`:

| Agent | `bmi` | Persisted band | Recomputed | |
|---|---:|---|---|---|
| G1 | 81 | B, 1.00 | **B+, 0.93** | MISMATCH |
| G2 | 78 | B, 1.00 | B, 1.00 | match |
| G3 | 75 | B-, 1.07 | **B, 1.00** | MISMATCH |
| M1 | 58 | C-, 1.07 | C-, 1.07 | match |
| M2 | 46 | D+, 0.93 | D+, 0.93 | match |
| M3 | 60 | D+, 0.93, "Decline / layered-excess only" | **C, 1.00, "Surplus (remediation endorsement)"** | MISMATCH — letter *and* posture |
| B1 | 33 | D, 1.00 | D, 1.00 | match |
| B3 | 60 | C, 1.00 | C, 1.00 | match |

B2 carries no `baseline.json` (BEAAA-10199), so eight files, not nine.

Cause confirmed independently: `git show ffa6359 -- artifacts/beaaa-387` changes
26 lines across 8 files and matches **zero** lines containing `band`, `display`,
`modifier` or `posture`. The rebaseline moved the totals and left the bands.

Two further ticket premises checked and **confirmed**:

- `scripts/release_gate_n5.py:304 _baseline_values` — `grep -rn "_baseline_values"` returns
  the definition and nothing else. No live caller.
- No scoring path reads these band blocks. `_prepare_input_dir` reads `baseline.json`
  only for `model_versions` (line 373); the band never reaches `bin/are-scan`.

## 2. Item 1 — band blocks recomputed

Each of the eight files is *exactly* `json.dumps(doc, sort_keys=True, indent=2) + "\n"`
(verified byte-identical before editing), so a round-trip rewrite with only `band`
replaced is provably a band-only diff. The result:

```
 artifacts/beaaa-387/G1/baseline.json |  6 +++---
 artifacts/beaaa-387/G3/baseline.json |  6 +++---
 artifacts/beaaa-387/M3/baseline.json | 10 +++++-----
 3 files changed, 11 insertions(+), 11 deletions(-)
```

Every changed line is inside the `"band"` object. `bmi`, pillar `score`,
`eval_outputs.json`, `applicability.json` and `manifest.json` are untouched — see §6
for a manifest finding that follows from that constraint.

## 3. Item 2 — the guard

Two tests appended to `tests/test_release_gate_n5_beaaa10187.py`.

**Fails on the current tree** (`artifacts/BEAAA-24525/guard-fails-on-current-tree.log`),
naming all three drifts rather than stopping at the first — the assertion collects a
list, so a `-vv` run reports the full set:

```
artifacts/beaaa-387/G1/baseline.json: bmi=81 -> display: persisted='B' recomputed='B+', ...
artifacts/beaaa-387/G3/baseline.json: bmi=75 -> display: persisted='B-' recomputed='B', ...
artifacts/beaaa-387/M3/baseline.json: bmi=60 -> display: persisted='D+' recomputed='C',
  letter: persisted='D' recomputed='C', ... posture: persisted='Decline / layered-excess
  only' recomputed='Surplus (remediation endorsement)'
```

**Passes on the candidate:** `tests/test_release_gate_n5_beaaa10187.py` — 49 passed.

**Each test carries a roster positive control** (`assert checked == [...]`) so a moved
or renamed artifacts tree fails the test rather than passing it vacuously by finding
nothing to check.

**The guard can actually fire in CI.** `.github/workflows/scanner-regression.yml` does
*not* run this module (it runs `test_release_gate_beaaa10189.py`, a different file), so
that alone would have made the guard inert. `.github/workflows/tests.yml` "Run tests"
runs `pytest -v -ra tests` with only two `--ignore`s, on every `pull_request` and every
push to master, with no paths filter. Proven by running CI's own collect command and
grepping for the exact node ids — `artifacts/BEAAA-24525/ci-collection-proof.log`:

```
tests/test_release_gate_n5_beaaa10187.py::test_persisted_golden_baseline_band_matches_its_own_total
tests/test_release_gate_n5_beaaa10187.py::test_golden_agent_expected_score_band_matches_its_own_total
```

## 4. Acceptance — N=5

### The release gate refuses to run this change

`scripts/release_gate_n5.py` cannot be run master → candidate here. Reproduced verbatim:

```
ValueError: frozen input hash mismatch between baseline and candidate archives
```

`_frozen_input_hashes` (added `41207e40`, BEAAA-17557, 2026-07-24) SHA-256s each roster
agent's **whole `baseline.json`**, so *any* edit to that file trips the archive-identity
check at line 721 before a single seed is scored — including an edit to a field the gate
never feeds the CLI. Isolated: of the 25 frozen paths hashed, exactly the three this
ticket edits differ, and no others.

That check is a proxy for the question the gate actually needs answered. Answered
directly instead: **the 32 files `_prepare_input_dir` hands `bin/are-scan` are
byte-identical between the two trees, for all 8 roster agents.** That is the stronger
statement, and it is what makes the deltas below meaningful rather than tautological.

### The measurement

`artifacts/BEAAA-24525/n5_via_gate_functions.py` runs the gate's own N=5 loop through the
gate's own functions (`_archive_commit` / `_prepare_input_dir` / `_run_cli` /
`_validate_contract_payload` / `_candidate_values` / `AgentGateResult`), so these are the
gate's numbers, not a re-implementation. 8 agents × 2 sides × 5 seeds = 80 real
`bin/are-scan` invocations.

| agent | total | GOVERNANCE | ROBUSTNESS | BIAS_FAIRNESS | OPS | spread b/c |
|---|---:|---:|---:|---:|---:|---|
| G1 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.00/0.00 |
| G2 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.00/0.00 |
| G3 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.00/0.00 |
| M1 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.00/0.00 |
| M2 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.00/0.00 |
| M3 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.00/0.00 |
| B1 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.00/0.00 |
| B3 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.00/0.00 |

`max |delta| = 0.0`, no contract-envelope errors, seeds 0–4 both sides. Raw:
`n5-evidence.json`, `n5-run.log`.

### Sabotage control — the harness can still fail

A harness that only ever prints `0.0000` proves nothing.
`artifacts/BEAAA-24525/n5_sabotage_control.py`:

- **Control 1 — it detects a real move.** Nudging one real scoring input on the candidate
  side only (`G1 GOVERNANCE.PII.HANDLING_ATTESTATION.raw 4.0 → 0.0`) produces
  `total -4.0, GOVERNANCE -3.5714`. The harness is not blind.
- **Control 2 — the band is not a scoring input.** Writing a deliberately wrong `D-` band
  into the candidate's `baseline.json`, changing nothing else, leaves every delta at
  `0.0000`. This is the direct measurement behind the "rule execution" classification;
  it is no longer an inference from a `grep`.

`CONTROLS PASS`.

## 5. Item 3 — `tests/fixtures/golden_agents/*/expected_score.json`

**Result: they all carry a `band` field, and every one of them agrees with its own total.
Nothing to fix.**

| Agent | `bmi` / `are_total` | Persisted band | Recomputed | |
|---|---:|---|---|---|
| G1 | 80 | B+, 0.93 | B+, 0.93 | match |
| G2 | 78 | B, 1.00 | B, 1.00 | match |
| M1 | 58 | C-, 1.07 | C-, 1.07 | match |
| M2 | 47 | D+, 0.93 | D+, 0.93 | match |
| M3 | 55 | C-, 1.07 | C-, 1.07 | match |
| B1 | 37 | D, 1.00 | D, 1.00 | match |

Six of the nine roster agents have an `expected_score.json`; G3, B2 and B3 carry
`agent.json` only. `ffa6359` did not touch these fixtures at all (`--stat` matches zero
`expected_score.json` paths), which is why they never drifted.

Beyond the reporting the ticket asked for, the second guard test extends the same
band↔total tie to these six so the class of defect cannot appear here either. This is
additive and touches no fixture value; strip it if that is unwanted.

*Note, not a defect:* these fixture totals are deliberately different numbers from the
`beaaa-387` baselines (G1 80 vs 81, M3 55 vs 60, B1 37 vs 33). They are a separate
frozen input set, not a copy, and each is internally consistent. No action.

## 6. Two findings for the CTO — outside this ticket's scope

### F1. The frozen-input hash guard blocks every future rebaseline

`_frozen_input_hashes` hashing the whole `baseline.json` means the release gate cannot be
run across *any* commit that edits that file — a band-only correction like this one, and
equally a legitimate total rebaseline. `ffa6359` landed 2026-07-31, a week after the
guard existed, so the same wall applied to it. Worth deciding whether the guard should
hash the fields the gate actually consumes (`model_versions`, plus `eval_outputs.json`
and `applicability.json` whole) rather than the whole file. I have **not** touched it:
narrowing a tamper guard is a CTO call, not a side effect of a band fix.

### F2. `manifest.json` is a second anchor set that disagrees with the baselines

The ticket scoped the manifest out ("do not touch ... the manifest"), so this is reported,
not fixed. `artifacts/beaaa-387/manifest.json` still carries the **pre-`ffa6359`** totals
on 7 of 8 agents:

| Agent | `manifest.are_total` | `baseline.json` `bmi` | `manifest.band_display` |
|---|---:|---:|---|
| G1 | 79 | **81** | B |
| G2 | 77 | **78** | B |
| G3 | 73 | **75** | B- |
| M1 | 58 | 58 | C- |
| M2 | 48 | **46** | D+ |
| M3 | 54 | **60** | D+ |
| B1 | 39 | **33** | D |
| B3 | 64 | **60** | C |

Each `band_display` is self-consistent with the manifest's *own* stale total, so this file
passes any internal-consistency check while contradicting the baselines beside it. For the
BEAAA-24490 use case — a human reading these files as the calibration range — F2 is the
larger exposure of the two, because the manifest is the index and reads as authoritative.
It needs a CTO decision on which number is the lock of record before anything is rewritten.

## 7. Regression

Failed-set diff, master vs candidate, over the 14 test modules that read
`artifacts/beaaa-387` or the `golden_agents` fixtures:

- master `5410126`: **217 passed**, 18 subtests, 0 failed
- candidate `aa4b8752`: **219 passed**, 18 subtests, 0 failed

Delta is `+2` — exactly the two new guard tests. Failed set empty on both sides.

CI's own regression-lane modules on the candidate (`scanner-regression.yml` list plus
`test_release_gate_beaaa10189.py`, `test_full_scan_reference_agents_beaaa13803.py`):
**72 passed**, 16 subtests, 0 failed.

## 8. Rollback

`git revert aa4b8752`. The three band blocks return to their stale values and the two
guard tests go away; nothing else moves, since control 2 measured that no score depends
on them.

---

```yaml
owner: Scanner Engineer
artifact_type: evidence
persona_audience: internal
status: final
updated_at: 2026-08-19
issue: BEAAA-24525
candidate_sha: aa4b8752058247512f882172a475d855f7ba3056
baseline_sha: 5410126335a0e3a71b698b2803e0afaa96dc797c
source_refs: [BEAAA-24525, BEAAA-24490, BEAAA-19710, BEAAA-19712, "commit:5410126",
  "commit:ffa6359", "commit:41207e40", "artifacts/beaaa-387", "rubric/bands.py",
  "scripts/release_gate_n5.py", "tests/test_release_gate_n5_beaaa10187.py"]
claim_or_decision: Three stale band blocks recomputed from their own persisted totals;
  a band-vs-total tie guard added over both the persisted baselines and the golden
  fixtures, proven to fail on master, pass on the candidate, and be collected by CI.
verification: N=5 seeds 0-4 both sides, 8 roster agents, all deltas 0.0000 on total and
  every pillar, run through the release gate's own functions after the gate itself
  refused on a frozen-input hash mismatch; two sabotage controls prove the harness
  detects a real move and that the band block is not a scoring input.
open_findings: [F1 frozen-input hash guard blocks any baseline.json edit,
  F2 manifest.json are_total stale on 7 of 8 vs the persisted baselines]
approvals: [QA gate required on aa4b8752058247512f882172a475d855f7ba3056 before merge]
```
