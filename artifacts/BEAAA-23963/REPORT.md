# BEAAA-23963 QA report — APPROVED

Candidate `cc0fdcfe273cca7be868272aa2e0173ac2fc0546` (branch `agent/beaaa-23957-receipt-rubric-points`),
the BEAAA-23957 fix for the N=5 receipt generator.

Verdict: **APPROVED**. All seven acceptance criteria pass. Nine targeted mutants killed,
zero regressions, and the fix is demonstrated on real emitted receipts rather than on
the producer's description of it.

## Candidate identity

Fetched into an independent clone and pinned before testing.

| | |
|---|---|
| SHA | `cc0fdcfe273cca7be868272aa2e0173ac2fc0546` |
| Author | `Scanner Engineer 2 <scanner-eng-2@backbond.local>`, 2026-08-17 09:43:52 +0000 |
| Parent | `4acd94ef4870a904aefa3baa81691687df9d0741` — matches the stated base |
| Diff | 2 files, +157/−9: `scripts/release_gate_n5.py`, `tests/test_release_gate_n5_beaaa10187.py` |
| Master at test time | `40abb5cb821718641f3322b0eb656c50f462e46b`, settled from GitHub, not the local ref |

`058c868` was not reviewed. It is not reachable from the branch, as stated.

## Acceptance criteria

| # | Criterion | Result |
|---|---|---|
| 1 | Test module passes, 39 tests | **PASS** — 39 passed. See correction C1 on the "up from 33" figure |
| 2 | CLI fails closed on the new argument, writes no receipt | **PASS** — exit 2, stderr names `--rubric-point-id`, zero files added or modified in `tools/calibration-deltas` (251 files, md5 set identical before and after), worktree clean |
| 3 | Malformed rubric points rejected | **PASS** — all four stated cases reject, plus 11 further cases I added |
| 4 | No PII literal survives in the generator | **PASS** — zero occurrences of either literal in any form, not merely outside emitted values |
| 5 | Emitted receipt carries the points passed in | **PASS** — verified on a real generated receipt, not a monkeypatched one |
| 6 | Supreme Law Layer 2, N=5 unaffected | **PASS** — confirmed from the diff, see below |
| 7 | Contract `are-scanner-api-v1@v1.3.0` unchanged | **PASS** — 2 passed |

### Criterion 5, verified end to end

I ran the real CLI against real commits and inspected the artifact it wrote, then ran the
same command against the base generator. The A/B is the direct proof of the defect and the fix:

```
BASE  4acd94ef : - Rubric points: `GOVERNANCE.PII.HANDLING_ATTESTATION, ROBUSTNESS.PII.SCAN_TIME_DETECTION`
CAND  cc0fdcfe : - Rubric points: `P1.1, ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL`
```

The base run exercised neither PII point and asserted both anyway. The candidate states exactly
what was passed in. The string `PII` occurs zero times in the candidate's emitted markdown.

Both receipts are committed alongside this report.

The emitted JSON does contain `PII` in eight places, all of them the pre-existing
`agents[].approved_movements[].description` string "authenticated applicability for
always-applicable PII dimensions". That is movement-accounting prose about dimensions,
not a `rubric_point_id` provenance claim, it is identical on the base, and the diff does
not touch it. Not a finding.

### Criterion 6, checked rather than taken on trust

The CTO accepted the zero-delta reading without a delta table. The diff supports it. The
only two files touched are the receipt generator and its test. Filtering every added and
removed line for `are_total`, `subscore`, `band`, `threshold`, `weight`, `SEEDS`, `score`,
`golden`, `corpus_version`, `dimension` and `rubric/rubric.yaml` returns two comment lines
and nothing else. No scoring path, gate logic, golden fixture, rubric weight, dimension,
threshold or band is modified. The delta is 0 by construction. I did not run N=5.

## Mutation testing

Substituting the `4acd94ef` generator under the candidate tests fails 21 tests, but most die
on `TypeError` from the signature change, so that kill is confounded and proves little on its
own. I wrote nine targeted mutants that each keep the new API and break exactly one behaviour.

| Mutant | Result | Killed by |
|---|---|---|
| M1 re-hardcode both PII literals as the emitted value | KILLED | source guard + end-to-end |
| M2 same, single-quoted | KILLED | end-to-end only, see note N2 |
| M3 regex accepts lowercase | KILLED | `kwargs10` |
| M4 regex no longer requires a dot | KILLED | `kwargs11` |
| M5 drop sentinel-plus-named-point check | KILLED | `kwargs12` |
| M6 drop empty/absent check | KILLED | `kwargs8`, `kwargs9` |
| M7 CLI arg no longer required, defaults to a PII point | KILLED | source guard + CLI test |
| M8 `_render_markdown` stops requiring rubric points | KILLED | render test |
| M9 format validation skipped | KILLED | `kwargs10`, `kwargs11` |

9 of 9 killed, each by the specific test that should catch it. `test_no_pii_rubric_point_literal_survives_in_the_generator`
is a live guard, not a tautology: M1 and M7 both trip it.

## Edge cases beyond the stated scope

Twenty cases against `_validate_receipt_provenance`, all twenty behaved correctly.
Rejected: empty list, whitespace-only, `None`, lowercase, bare pillar, mixed case,
leading digit, double dot, injection characters, a newline crafted to forge a second
`Rubric points:` line in the markdown, a trailing backtick crafted to escape the code
span, a duplicated sentinel, an uppercase sentinel, and a good-plus-bad pair.
Accepted: `P1.1`, the sentinel alone, multiple named points, whitespace-padded input,
and `GOVERNANCE.PII.HANDLING_ATTESTATION` when the run actually states it — the fix
removes the hardcode, not the id.

I also checked the format claim against canon rather than against the producer's word.
All 28 distinct rubric ids in `rubric/rubric.yaml` are accepted by `RUBRIC_POINT_RE`,
with zero false rejects. A legitimate run cannot be blocked by the new validation.

## Regression

The new required argument on `run_gate` and on the CLI is a breaking signature change,
so I swept for everything that could call it.

- Python importers of `release_gate_n5`: two. The test module, and
  `tests/test_warranty_recipient_integrity_beaaa12031.py`, which only asserts on source
  text and has no signature coupling. 16 passed.
- CI: no workflow in `.github/` invokes the script. The `rubric_point_id` mention in
  `.githooks/post-commit` is a metadata comment, not a caller.
- Remaining `release_gate_n5` references are ADR and audit prose.

Regression run over the 30 receipt, provenance and calibration adjacent test modules:

```
master 40abb5cb   396 passed, 1 skipped
merge result      406 passed, 1 skipped
```

Delta +10, exactly the ten new tests. Same single skip. Nothing that passed before fails now.

## Merge check, re-derived

Not taken from the ticket.

- merge-base of candidate and master is `4acd94ef`, the candidate's own parent
- master has not touched either file since that base, zero path overlap
- `git merge-tree` reports zero conflict markers
- the merge result is `581f9a1c`; both files are blob-identical to the candidate
  (`4653c834`, `54964d42`)
- zero PII literals in the merged generator
- 41 passed across the two named modules on the merge result

## Corrections and notes

**C1. The test-count figure in the ticket is wrong, the code is not.** The description says
"39 tests, up from 33". The candidate is 39, which is correct. The baseline is **29**, not 33,
so the delta is **+10**, not +6. Confirmed two ways: running the module at `4acd94ef` gives
29 passed, and the diff adds exactly 5 parametrize cases plus 5 new test functions. The
independent regression sweep agrees, 396 to 406. Nothing ships with "33" in it and no shipped
behaviour is affected, so this does not block. The promotion note should not repeat the figure.

**N2. The source guard is quote-shape specific.** `test_no_pii_rubric_point_literal_survives_in_the_generator`
asserts `f'"{point}"' not in body`, so a single-quoted re-hardcode passes that test. Mutant M2
confirmed this. It was still killed by the end-to-end assertion on the emitted report, so the
guard set as a whole holds and no change is required here. Worth knowing before anyone relies
on that one test in isolation.

**N3. Pre-existing, out of scope.** `main()` raises `ValueError` on the closing
`print(md_path.relative_to(REPO))` when `--out-dir` points outside the repo, after the receipt
has already been written, so the process exits 1 on a successful run. Identical on `4acd94ef`,
untouched by this diff, and it only triggers on an out-of-repo out-dir. Recorded, not charged
against this candidate.

The out-of-scope items in the ticket, historical receipt backfill and the unpinned rubric
version, are correctly deferred to BEAAA-23947. I did not widen the candidate for either.

## How to reproduce

```
git worktree add --detach /tmp/qa/cand cc0fdcfe
cd /tmp/qa/cand
python3 -m pytest -q tests/test_release_gate_n5_beaaa10187.py            # 39 passed
python3 -m pytest -q tests/test_contract_pin_reconciliation_beaaa18467.py # 2 passed
python3 artifacts/BEAAA-23963/qa_mutants.py                               # 9/9 killed
python3 artifacts/BEAAA-23963/qa_edge_cases.py                            # 20/20 correct
python3 artifacts/BEAAA-23963/qa_rubric_id_coverage.py                    # 28/28 accepted
```

Evidence in this directory: `receipt-BASE-4acd94ef.md`, `receipt-CANDIDATE-cc0fdcfe.md`,
`qa_mutants.py`, `qa_edge_cases.py`, `qa_rubric_id_coverage.py`.
