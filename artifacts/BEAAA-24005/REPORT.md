# BEAAA-24005 — QA gate report, BEAAA-23968 corpus_version pin

- Candidate: `325cc81f527f1589bb9fa69d8a2b92fa1fe90afd` (branch `beaaa-23968-corpus-pin`)
- Baseline / merge base: `dc113b6e537215ada56f5bf99a565bd01633b979`
- Repo: `CorwinFoundation/are-scanner`
- Verdict: **APPROVED** for merge after BEAAA-23966, with one required documentation correction (D1, non-blocking)
- Tested: 2026-08-17, clean clone at `/tmp/qa23968`, baseline in a sibling worktree at `/tmp/qa23968-base`
- Environment deviation from CI: local Python 3.12.3 in a venv (CI uses 3.11.15). Pinned
  `requirements-scanner-regression.txt` installed with `--require-hashes`. CI run 32035388918
  independently corroborates the same counts on 3.11.15.

## Acceptance criteria

| # | Criterion | Result |
|---|---|---|
| 1 | CI green on the exact SHA + log shows the enrolled test | **PASS** |
| 2 | All four job steps pass on a clean checkout | **PASS** |
| 3 | Defect is real and the fix fixes it | **PASS** |
| 4 | Field-set equivalence, 21/31 keys, exactly six differing lines | **PASS** |
| 5 | N=5 calibration gate, exit 0, file byte-identical | **PASS** (and proven non-vacuous) |
| 6 | `git diff --name-only` returns exactly nine paths | **PASS** |
| 7 | Fail-closed on missing and blank pin | **PASS** |
| 8 | Regression: BEAAA-22121 expansion + release-gate N=5 | **PASS** |

### Criterion 1 — CI

`GET /actions/runs?head_sha=` is 403 for this token, but `GET /actions/workflows/{id}/runs?head_sha=`
is 200. Run for the exact SHA:

- run `32035388918`, workflow `scanner-regression` (id 300034473), `path .github/workflows/scanner-regression.yml`
- `head_sha 325cc81f527f1589bb9fa69d8a2b92fa1fe90afd`, `head_branch beaaa-23968-corpus-pin`
- `event push`, `run_attempt 1`, `status completed`, `conclusion success`
- exactly one run on this SHA; the branch's other two runs are `06e34e2c` (cancelled) and `6cfc5d66` (success)

All eight job steps `success` (`ci-run-32035388918-steps.json`). The log
(`ci-run-32035388918.log`, from `/actions/runs/{id}/logs`) shows at line 186:

```
tests/test_beaaa23968_corpus_pin.py \
```

and at line 207: `83 passed, 24 subtests passed in 4.75s`.

The enrollment is load-bearing, not decorative: running the workflow's exact file list locally
collects and executes 8 node ids from that file, all PASSED.

### Criterion 2 — four job steps on a clean checkout

| Step | Result |
|---|---|
| Install pinned deps (`--require-hashes`) | OK |
| `pytest` amended 8-file list | `83 passed, 24 subtests passed` |
| `scripts/beaaa6190_v05_rc_n5.py` | real exit 0 |
| release gate + Phase-3 vanish gate | `17 passed`, `--check` exit 0 |

Exit codes were re-measured without a pipe. The first pass reported `$?` after `| tail`, which is
`tail`'s status, not the script's — `${PIPESTATUS[0]}` is empty under `/bin/sh`.

### Criterion 3 — the defect, four observed strings

Driven through `run()` with `harness.runner.MockRunner` on a two-probe slice, offline, calling
`run()` with only the arguments that exist on **both** trees (no `corpus_yaml`, no explicit pins),
so what is observed is the default path — the defect surface.

| Tree | Dim | YAML `reproducibility.probe_set_version` | emitted `corpus_version` |
|---|---|---|---|
| `dc113b6e` | P1.1 | `p1_1_factuality_golden@0.2.0` | `p1_1_factuality_golden@0.1.0` |
| `dc113b6e` | P1.4 | `p1_4_refusal_golden@0.2.0` | `p1_4_refusal_golden@0.1.0` |
| `325cc81f` | P1.1 | `p1_1_factuality_golden@0.2.0` | `p1_1_factuality_golden@0.2.0` |
| `325cc81f` | P1.4 | `p1_4_refusal_golden@0.2.0` | `p1_4_refusal_golden@0.2.0` |

Corroborated at source level: baseline hardcodes `@0.1.0` at `factuality_golden.py:161,162,196` and
`p1_4_refusal_golden.py:189,190,257`. The probe maps carry 34 and 26 probes on both trees.

### Criterion 4 — field-set equivalence

Full `run()` envelopes serialised with `sort_keys=True`, including the nested `trace`
(2 entries each, 9 keys per P1.1 entry, 9 per P1.4 entry).

| Dim | keys base | keys cand | added | removed | retyped | differing values |
|---|---|---|---|---|---|---|
| P1.1 | 21 | 21 | none | none | none | `benchmark`, `corpus_version`, `probe_set_version` |
| P1.4 | 31 | 31 | none | none | none | `benchmark`, `corpus_version`, `probe_set_version` |

Three fields per dim, two dims — the six differing lines the criterion predicts, and nothing else
moved. Raw diffs in `envelope-diff-P1.1.txt` / `envelope-diff-P1.4.txt`.

### Criterion 5 — N=5, and a check that it is not vacuous

`scripts/beaaa6190_v05_rc_n5.py` real exit 0; `git status --porcelain` on
`tools/calibration-deltas/2026-06-22-beaaa6190-v05-rc-n5.md` is empty.

A byte-identical result is also what you get from a script that never writes the file, so I
appended a marker, re-ran, and confirmed the script overwrites and restores it:

```
before      = 5d040476a4089d25281b1169be9456e4be447fbc1ae6c9a81c19deb7f7294a6d
mutated     = e1c0ff8053e5a1a33156f52416d136e3ceedd6a7f545b61101107d0de46079df
after_rerun = 5d040476a4089d25281b1169be9456e4be447fbc1ae6c9a81c19deb7f7294a6d
```

No total or pillar moved. All six live agents: total spread 0, max pillar spread 0.0000,
`Byte-identical to expected: True`; discrimination spread 43.

### Criterion 6 — nine paths

`git diff --name-only dc113b6e 325cc81f` returns exactly the nine named paths and no others
(`c6-changed-paths.txt`). No rubric file, probe map, judge prompt, fixture, or contract document is
touched. `dc113b6e` is an ancestor of `325cc81f`; the two intervening commits are `06e34e2c`
(merge of the base) and `325cc81f` (the one-line workflow enrollment).

### Criterion 7 — fail-closed

Ten malformed shapes through `load_probe_set_version`; every one raises, none returns a default:

| Case | Result |
|---|---|
| `probe_set_version` key missing | `ValueError` |
| blank whitespace `'   '` | `ValueError` |
| empty string | `ValueError` |
| tabs + newline | `ValueError` |
| `null` | `ValueError` |
| non-string (int `2`) | `ValueError` |
| `reproducibility` block absent | `ValueError` |
| `reproducibility` is null | `ValueError` |
| empty YAML file | `ValueError` |
| `reproducibility` is a list | `AttributeError` (still fails closed) |

Control: a valid padded pin returns stripped. The guard also fires through the real call path —
`run()` itself raises `ValueError` for both dims when pointed at a map whose pin was deleted, so
this is not a guard that can only fire in a unit test. Explicit pins still win: passing
`corpus_version="ARCHIVED@0.0.9"` yields that pin and `benchmark ARCHIVED-v0.0.9`.

### Criterion 8 — regression

`tests/test_beaaa22121_probe_expansion.py` + `tests/test_release_gate_n5_beaaa10187.py`:
`42 passed`, real exit 0.

## Independent checks beyond the criteria

**Mutation testing the new acceptance test — 9/9 killed.** A passing test proves nothing until it
can fail. Every mutant reintroducing the defect or weakening the guard is caught:

| Mutant | Result |
|---|---|
| P1.1 `corpus_version` back to hardcoded `@0.1.0` | KILLED |
| P1.1 `probe_set_version` back to hardcoded `@0.1.0` | KILLED |
| P1.4 `corpus_version` back to hardcoded `@0.1.0` | KILLED |
| P1.1 `benchmark` re-frozen to literal `-v0.1.0` | KILLED |
| P1.4 `benchmark` re-frozen to literal `-v0.1.0` | KILLED |
| loader returns silent `''` instead of raising | KILLED |
| loader drops the `.strip()` blankness check | KILLED |
| loader ignores the map, returns a frozen literal | KILLED |
| `benchmark_label` stops splitting the version | KILLED |

The test reads the expected pin out of the YAML rather than restating `0.2.0`, so it compares the
envelope against the corpus instead of against itself.

**Pre-existing tests were tightened, not weakened.** `tests/test_p1_1_factuality_golden.py` and
`tests/test_p1_4_refusal_golden.py` previously asserted `@0.1.0` under a comment stating the
mislabel was real and tracked in BEAAA-23968. Those comments are removed and the assertions moved
to `@0.2.0`. Independent corroboration that the defect was known and real.

**Fix completeness.** The only non-test callers of either `run()` are the two ops live-fire sites,
and both now pass `corpus_yaml=mapping_paths["P1.1"]` / `["P1.4"]` — the same paths used at
lines 413 and 416 to load the probes, so label and roster cannot diverge.
`scripts/beaaa8127_ops_live_fire.py` compiles.

**Secrets / PII.** No hardcoded credentials in the nine changed paths. The `api_key` hits in
`beaaa8127_ops_live_fire.py` are pre-existing env-var reads outside this diff.

**Clone hygiene.** `tools/partner-kit/` shows modified as the ticket predicted. Confirmed
line-endings only: content hashes match after `tr -d '\r'` for all three files. My test runs left
no tracked candidate file modified.

## Findings

### D1 — the calibration-delta doc reports a baseline test count that cannot be produced (minor, non-blocking, correction required)

`tools/calibration-deltas/2026-08-17-beaaa23966-vs-beaaa-23968.md`, "Tests" table:

| Check | Baseline `5a5cd0d8` | Candidate |
|---|---|---|
| Golden regression step, 7 files plus BEAAA-22121 expansion test | 86 passed | 86 passed |

Measured, both trees, same file list:

- 7 CI files + expansion test: **78 passed, 20 subtests** on `dc113b6e` **and** on `325cc81f`
- 8-file amended list + expansion test: **86 passed, 24 subtests** on `325cc81f`

So `86` is the candidate figure for the **8**-file list, mislabelled as 7 files, and the baseline
column is unachievable: the new file contributes those 8 tests and does not exist on the baseline
(`pytest` errors, `file or directory not found`). The row as written reads as "no test-count
change" when the candidate adds 8 tests. The doc's own adjacent row contradicts it, correctly
recording the new file as "collection error, module absent" on the baseline.

Correct values: baseline 78 passed / 20 subtests; candidate 86 passed / 24 subtests on the 8-file
list plus the expansion test, or 83 / 24 on the amended list alone as CI reports.

Not blocking: the file is non-executable, `persona_audience: internal`, no acceptance criterion
depends on it, and it misstates nothing about behaviour, scope, scores, or promotion risk. It does
falsify the doc's own `self_audit_passed: true`. CTO's call whether to fix forward — that changes
the SHA, and I would re-gate doc-only under `SUPERSEDED` on tree identity.

### Observations, not defects

- **O1 — the probe-map read is unconditional.** `load_probe_set_version(corpus_yaml)` runs before
  the `or` fallbacks, so explicit-pin replay still requires the map at `corpus_yaml` (default: the
  in-repo map, bound at def time) to be readable and validly pinned. `run(..., corpus_yaml=<missing
  path>, corpus_version=..., probe_set_version=...)` raises `FileNotFoundError`. Harmless in
  practice — the default map is version-controlled and an archived-corpus replay would pass
  `corpus_yaml` anyway. Flagging so it is a choice rather than a surprise.
- **O2 — the doc's stale-pin disclaimer is narrower than the facts.** It excuses "dated registry
  snapshots under `artifacts/`". `tools/probe-registry/active_registry.json` is not under
  `artifacts/` and carries 60 `p1_1_factuality_golden@0.1.0` and 40 `p1_4_refusal_golden@0.1.0`
  pins, with zero at `@0.2.0`. Registry and inventory drift is BEAAA-23969 and explicitly out of
  scope here; recorded so it is not lost. `demo-real-agent/real_scan.py:51,53` does still declare
  30 and 20, as the doc says.
- **O3 — the doc's baseline SHA is stale but its numbers still hold.** It names `5a5cd0d8` as the
  BEAAA-23966 candidate; that tip has since moved to `dc113b6e` (a merge of `5a5cd0d8` and master
  `b4344001`). `git diff 5a5cd0d8 dc113b6e` over the six files relevant here is empty, so the
  measurements transfer. The doc's claim that `git diff --stat 40abb5cb b4344001` over the five
  touched files is empty is true. Its delta table (medians 37/80/78/58/47/55, spread 0,
  discrimination 43) matches the regenerated calibration file exactly.
- **O4 — ticket wording.** The description calls `tests/test_release_gate_n5_beaaa10187.py` "new to
  this branch" via the BEAAA-23966 merge. It already existed on master `b4344001`, added by
  `cc0fdcfe` (BEAAA-23957). Immaterial — it passes either way.

## Out of scope, confirmed not gated

Fixture threshold sizing (BEAAA-24003; thresholds 28 and 18 confirmed as the doc states), P1.3/P1.5
pin derivation (BEAAA-24002), registry and inventory count drift (BEAAA-23969).

## Evidence in this directory

`qa_c34.py` (criteria 3+4 harness), `envelope-{P1.1,P1.4}-{base,cand}.json`,
`envelope-diff-{P1.1,P1.4}.txt`, `report-{base,cand}.json`, `ci-run-32035388918.log`,
`ci-run-32035388918-steps.json`, `c6-changed-paths.txt`.
