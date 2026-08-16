# BEAAA-23752 — golden-set entrypoint repair (master-base candidate)

**Base SHA: `ae14923083d3b002de3926b8eff6fabb26038c89`** (`origin/master`,
"Preserve deploy grant when judge socket is unavailable (BEAAA-23772)"). Built in a
clean worktree, not the shared working directory.

Supersedes candidate `516078e2`, which QA correctly rejected on BEAAA-23754. Every
number in this report was re-derived on the master base; none is carried over.

Master moved twice while this candidate was being built — `d9d680f5` (the base QA
tested `516078e2` against) is now two commits behind `ae149230`. Both new commits
(`c06e7ef2`, `ae149230`) touch only `demo-real-agent/` and `artifacts/`; neither
touches `harness/`, `rubric/`, or any of the six test modules. The candidate is
rebased onto `ae149230` and every number below was re-derived there. The emissions
came out byte-identical to the `d9d680f5` capture, which is the expected result
given the diff, and is stated here as a measurement rather than an assumption.

**BLUF:** Two independent breaks put the three golden-set entrypoints down on
master. Both are fixed. All three return, Δ = 0.0000 byte-identical across N=5,
62 tests pass with 0 failures, and the applicability path fails closed on every
forgery route tested.

## What QA found, and what was wrong with the first candidate

All three findings were correct.

**F1 — the fix did not work on the promotion target.** `516078e2` sat 557 commits
behind master. In that gap `rubric/scoring.py` gained the BEAAA-18082
applicability-authentication boundary, which rejects a raw `applicability_decisions`
dict carrying any `fires: false`. That is precisely what the three entrypoints
read off disk. So on master the corpus fix alone just moves the failure one step:
`corpus_version required` becomes `raw applicability_decisions cannot authorize
N/A waivers`.

**F2 — two of the six named test modules were not in the candidate.**
`test_beaaa14958_trusted_golden_replay.py` and `test_beaaa14943_golden_applicability.py`
were untracked master-era files sitting in the shared working directory. The "6
pre-existing failures" I reported were master-era tests run against a two-week-old
tree. They do not exist on master, and the stash check I proposed could not have
detected that. Confirmed: all six modules are tracked at `ae149230`, and all six
pass here.

**F3 — the SHA carried five unrelated commits.** This candidate is built directly
on `ae149230` and contains only the harness change plus its evidence.

## The two breaks

**Break 1 — corpus pin.** `baseline.get("corpus_version") or None` converted B1's
`""` into `None`. B1 is locked `lock_source: deterministic_replay` and has carried
`""` since its first persist at `c6609045`; BEAAA-5276 kept `""` valid precisely
for that case and removed this same coercion from `tests/test_golden_agents.py`,
missing the harness call sites. Fixed by passing the persisted value verbatim.

Note for re-verification: the `_TEST_MODE` technique from the previous report is
**obsolete on master**. `_TEST_MODE` is still declared at `rubric/scoring.py:99`
but is never read — the corpus pin at line 275 is unconditional now.

**Break 2 — applicability authentication.** The entrypoints passed a raw dict.
Since BEAAA-18082 that cannot authorize N/A waivers. Fixed by routing all three
through `harness.trusted_replay.validate_trusted_replay`, which is the sanctioned
path for frozen golden calibration replay: it verifies the manifest against
`PINNED_MANIFEST_SHA256`, checks fixture identity, and verifies each sidecar's
digest against the pinned per-fixture value before signing.

The alternative — `ValidatedApplicability._signed_from_policy_validation` on
whatever is on disk, as `scripts/beaaa22774_n5_calibration.py` does — would have
preserved the old policy-version stamp exactly, but it self-signs unauthenticated
input and would reopen the hole BEAAA-18082 closed. Not taken.

## Disclosed output change

Routing through the trust boundary restamps one field:
`applicability_policy_version` goes from `0.1.2` (the sidecar's self-declared
value) to `trusted-replay-manifest@1.0.0` (the authenticated manifest version).
It also changes the `applicability_policy_version` echoed inside the
`coverage_caveat` diagnostic detail.

`artifacts/BEAAA-23752/applicability_equivalence.py` proves this is the *only*
difference: `bmi` and `pillars` are identical on all eight fixtures under both
paths. No ARE or pillar value moves. The field does not appear in any of the three
harness emissions, which is why the ±0 comparison below is byte-clean.

## New operational requirement

The entrypoints now need a bundle signing key, because authenticating evidence
requires one. `ARE_BUNDLE_SIGNING_KEY` in production, or
`ARE_ALLOW_DEV_SIGNING_KEY=true` for dev and CI. Without either,
`api/config.py:143` raises `ARE_BUNDLE_SIGNING_KEY must be set in production`.
Verified working under both. This is inherent to the fix, not incidental — a
harness that could authenticate without a key would not be authenticating.

## Files

Four files. `harness/golden_set/_trusted_applicability.py` is new: the three
modules previously triplicated the sidecar-loading block, and it is now a
security-relevant call, so it lives in one place rather than three copies.

- `harness/golden_set/_trusted_applicability.py` (new)
- `harness/golden_set/calibration_delta.py`
- `harness/golden_set/n5_band.py`
- `harness/golden_set/discrimination.py`

Nothing under `artifacts/beaaa-387/` is touched.

## Evidence

All commands from the repo root with `ARE_ALLOW_DEV_SIGNING_KEY=true PYTHONPATH=.`.

**Entrypoints return on master:**
```
N5  : N=5 | baselined=8/9 | pending=1 | max ARE drift=0 | breaches=0 | PASS
CD  : baselined=8/9 | pending=1 | ARE breaches (>1.0)=0 | pillar breaches (>0.5)=0 | PASS
DISC: baselined=8/9 | current spread=48 (target 50) | collapse=False
```

**±0, N=5** — `capture_emissions.py <outdir> reference|candidate`. The reference
runs master's pristine modules with only the two rejections shimmed (corpus
`None`→`""`; raw dict signed with the *baseline's* policy version, preserving old
stamps). The candidate runs with no suppression at all.

| entrypoint | SHA-256 (reference and all 5 candidate replicates) |
| --- | --- |
| `calibration_delta` | `3d1a18dd8a3c14dd1d88c65dcbcfe3ea15c6b8ad98f5537675a9f7128277d603` |
| `n5_band` | `cdcf5b2e4603c04e055da687b0583210692164a7ef1e4adb802f0bfad50de242` |
| `discrimination` | `1b7ad850cf8c7da2ec83db16be72b2b34acb7c1896aed854fcf0a52c9cf4b289` |

15/15 `cmp`-identical to the reference. Δ = 0.0000. Raw emissions in
`emissions/reference/` and `emissions/candidate-r1..r5/`.

**Fails closed** — `forgery_repro.py`, exit 0:
```
PASS  untampered sidecar accepted: ValidatedApplicability
PASS  forged N/A waiver rejected: trusted replay applicability digest mismatch for fixture 'B1'
PASS  cross-fixture sidecar rejected: trusted replay applicability digest mismatch for fixture 'B1'
PASS  tampered manifest rejected: trusted replay manifest digest is not the code-pinned digest
```

**Tests**, clean worktree, full modules:
`test_golden_agents`, `test_golden_set_l2_gate`, `test_release_gate_n5_beaaa10187`,
`test_beaaa14958_trusted_golden_replay`, `test_beaaa14943_golden_applicability`,
`test_scoring` → **62 passed, 18 subtests passed, 0 failed.** No waiver needed.

The two other modules that import these entrypoints — `test_beaaa398_wiring`,
`test_bias_pack_ci_gate_beaaa749` — also pass, 12 passed.

## Correction that matters beyond this gate

**ADR-0067's evidence table is built on my stale-base numbers and does not hold on
master.** I reported the old-engine drift to the CTO, ADR-0067 was written around
it, and BEAAA-23757 now asks the Underwriter to dispose of four movements beyond
±1. On `ae149230` those four movements do not exist.

Master already rebaselined the golden locks at `ffa63592` (BEAAA-19710,
"rebaseline current-master golden locks"). Actual drift on the promotion target:

| Fixture | ADR-0067 (stale base) | Master `ae149230` |
|---|---:|---:|
| B1 | −3 | **0** |
| B3 | −5 | **0** |
| G1 | +1 | **0** |
| G2 | +1 | **0** |
| G3 | +4 | **0** |
| M1 | 0 | −1 |
| M2 | −1 | −1 |
| M3 | +3 | **−3** |

Five fixtures reproduce their baselines exactly. M1 and M2 are within ±1. **Only
M3 exceeds the threshold, at −3** — and it moves in the opposite direction to the
+3 the ADR records. So the Underwriter review scope on BEAAA-23757 is one fixture,
not four, and the disposition it currently asks for is against numbers that no
longer describe the target.

Re-derived on the master base:

- `n5-seeds-0-4-table.json` — ADR-0067 §4. Max total spread 0, max pillar spread
  0.0. **The only field that differs across seeds 0–4, on any fixture, is the
  `seed` stamp itself.** On the locked-replay path `seed` is a provenance stamp,
  not a sampling input: `actual_seed` is read only by the `seed_overridden`
  diagnostic and the output stamp, and there is no RNG in `rubric/` or
  `harness/golden_set/`. This gate cannot fail, so it should carry no weight in
  the disposition.
- `robustness-movement-decomposition.json` — no pre-existing dimension's raw moved
  on any fixture; every pillar score reconstructs exactly from Option-C
  mean-of-active-dims; **no fixture changes bind eligibility**. The movement that
  remains is attributable to newly-active dims (P3.5, P3.6,
  `ROBUSTNESS.PII.SCAN_TIME_DETECTION`).

The attribution-masking finding still stands, and is sharper here: all eight
fixtures still attribute to `rubric_reweight` because their `rubric_version` fields
still read 1.1.0/1.2.0 even though `ffa63592` rebaselined the values against
current master. So M3's −3 is suppressed by stale version *metadata* rather than a
genuine version gap. That is CTO/Underwriter territory, not this seat's, and it is
unchanged by this candidate.

## Metadata

- `scanner_version`: 0.5.0 · `rubric_version`: 1.3.0 · contract
  `are-scanner-api-v1@v1.3.0`, no output-shape change
- `corpus_version`: per-fixture, unchanged — B1 `""`, others as persisted
- `probe-set-version`: unchanged · `rubric_point_id`: none
- `N=5 calibration delta`: Δ = 0.0000, 15/15 byte-identical
- `cto_cosign_ref`: n/a — rule-execution (supervision contract v1.1, "refactor
  that doesn't touch behavior")
- `merged_sha_on_canon_master_at_close`: pending QA approval and merge
