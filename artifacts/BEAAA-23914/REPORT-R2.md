# BEAAA-23914 R2 — QA re-gate: registry-count refreeze `6366b77f` (supersedes `2e28a2cd`)

**Result: APPROVED** for exact SHA `6366b77f044296d3b393938851b7b03d7555b0f5`.

| | |
|---|---|
| Candidate | `6366b77f044296d3b393938851b7b03d7555b0f5` |
| Branch / tree | `agent/beaaa-23903-count-refreeze-r2` / `f6d275f44f883d9e70750f68b62c7a15029b83c7` |
| Merge commit | `00f542a3fbc66246c33b436c5bd70e945624c34a`, parents `7f8be7a1` + `2e28a2cd` |
| New base | `7f8be7a113d2418d4370117a430802303e43567b` (real canonical master) |
| R1 verdict | `2e28a2cd` APPROVED 2026-08-17, `artifacts/BEAAA-23914/REPORT.md` |
| Tested | 2026-08-17, worktrees `/tmp/qa23914r2/{cand,base}`, Python 3.12.3 |

Disclosure: `6366b77f` reads author `QA <qa@backbond.invalid>` — the shared VM git config, not authorship.
The producer wrote it; I tested it.

## The supersede rationale is true — verified at the remote, not from local refs

The producer's claim is that `fb2cf1ef` is no longer master head and the local `origin/master` was stale. I
did not take this on report, and local refs could not settle it: the loose ref
`.git/refs/remotes/origin/master` reads `fb2cf1ef` while `.git/packed-refs` reads `ee8af4cd` — two different
stale answers. `git fetch`/`gh` 401 at this seat, so I queried GitHub directly
(`CorwinFoundation/are-scanner`, MCP `list_commits`).

Real `master` head **is** `7f8be7a113d2418d4370117a430802303e43567b`, with `fb2cf1ef` seven commits behind it.
`2e28a2cd` genuinely could not fast-forward. The supersede was correct and necessary.

## Merge purity — re-derived, four checks

1. **Parents.** `00f542a3` has exactly two: `7f8be7a1` (real master) and `2e28a2cd` (the R1-approved commit).
2. **Approved blobs preserved.** All 14 R1-approved paths hash byte-identical between `2e28a2cd` and the
   merge — compared via `git rev-parse <sha>:<path>` per file, not by eyeballing a diff. Zero mismatches.
3. **No deletions, no smuggled files.** `git diff --name-status 7f8be7a1 00f542a3` returns exactly those 14
   paths, all `M`. `--diff-filter=D` is empty.
4. **Master's own work preserved.** `git diff --name-status 2e28a2cd 00f542a3` returns exactly the 3 Go files
   master added, all `A`, each blob byte-identical to master's.

Those two diffs together fully characterize the merge tree: it is master's tree with the 14 candidate blobs
substituted and nothing else. Master's 8 commits touch only `tools/are-collector/**` Go files — disjoint from
the candidate's Python/JSON paths, so no frozen count could have moved underneath the merge. I verified the Go
blobs by identity rather than re-running master's Go suite; the merge cannot have changed behavior it did not
touch.

## The residual I flagged in R1 is genuinely closed

`6366b77f` adds 3 files on top of the merge: `tests/test_beaaa18720_content_candidate_registration.py`,
`tests/test_full_scan_queue_worker_beaaa14843.py`, `tools/validate_content_candidate_registration.py`. Every
changed assertion is still exact equality — nothing loosened to a range or an inequality.

Values re-derived from the shipped loaders on the candidate tree, independent of the test suite:

| Surface | Frozen in candidate | My measurement |
|---|---:|---:|
| registry rows | 2252 | 2252 |
| `count_active_executable` | 2212 | 2212 |
| `count_target_bound_runtime` | 2223 | 2223 |
| distinct case total | 2211 | 2211 |
| published active count | 2212 | 2212 |
| core cohort active-executable | 350 | 350 |
| core cohort target-bound | 372 | 372 |

`validate_registry(strict=True)` returns no errors. `python3 -m tools.validate_content_candidate_registration`
exits 0 with `status: PASS` and an empty `mismatches` — it reported FAIL before this change.

Total candidate surface vs master: 17 files (14 approved + 3 residual), all under `tests/`, `scripts/`,
`tools/`. Nothing under `rubric/`, `api/`, `scanners/`, `corpus/`, `contracts/`, and no stray artifacts
committed. `tools/validate_content_candidate_registration.py` is a standalone validation tool whose only
importer is `tests/test_founder_dev_mode_emission_boundary.py` — no production, scoring, or API reach.

## Results reproduced, and the freeze still binds

- **13 affected modules:** `85 passed, 1 skipped` — matches the producer's claim exactly.
- **Base `7f8be7a1`:** `17 failed, 68 passed, 1 skipped`. All 17 fixed by the candidate, zero new failures.
- **Test-count conservation:** 86 tests collected on both base and candidate ⇒ nothing deleted or skipped.
- **Regression sweep** (`test_beaaa16967_wave1a_reconcile`, `test_probe_registry`,
  `test_full_suite_manifest_beaaa13645`, `test_demo_readiness_gate_beaaa15495`, `test_beaaa22121_probe_expansion`,
  `test_injecagent_pilot_beaaa19103`, plus `test_founder_dev_mode_emission_boundary` which I added as the
  validation tool's only importer): **47 passed on both base and candidate.** Flip-set identical.
- **`--check`** exits 0 and `git status --porcelain` on the emitted path is clean afterwards, so the committed
  registry is byte-identical to generator output.

**Mutant MR1 (add `P1.4-REF-027`)** — kills all four residual surfaces at once: rows 2253, active-executable
2213, target-bound 2224, distinct 2212; validation tool exits 1 with `status: FAIL`; `--check` exits 1; the 3
residual tests fail. The new constants bind. (R1 already killed add/delete/tamper mutants against the 14
approved blobs, which are byte-identical here, so that evidence carries.)

The producer's disclosure about test side-effects is accurate: after the run the only dirt is
`artifacts/beaaa-16992/instrumentation-store/result_record.jsonl`, and it is not in the candidate.

## Out of scope, disclosed, correctly not fixed here

`tools/probe-registry/active_registry.json` still reports `executable: 2202`. I agree with the producer's
reasoning for leaving it: it is read by `api/full_scan_worker.py`, `api/full_suite_manifest_registry.py`,
`tools/canonical_test_registry.py` and the post-bind-ingest demo, so regenerating it changes production
registry rows — outside this ticket's scope and a change I would want gated on its own. `tools/oss001_calibration.py`
keeps its hardcoded `registry_delta [2242, 2213, 2202, 2201]`; it is a historical fixture with `before == after`,
asserted nowhere, and rewriting it to current values would falsify a record of what was true then. Correct call.

So the two committed artifacts still disagree about how many probes exist, and per the producer this goes to a
follow-up. Consistent with my R1 position: **the BEAAA-22121 drift is not fully closed until that follow-up
lands.** This approval covers the candidate, not the wider drift.

## Merge-order hazard — real, acknowledged, not mine to resolve

BEAAA-23857 (`775c7ded`) is separately approved and moves these same registry counts. I checked for a textual
collision and found none — no file overlap between that tip's commits and this candidate's 17 paths. The
conflict is semantic, not textual: both change how many probes the loaders count, so whichever lands second
leaves the other's frozen constants stale and its modules red. The producer owns both and records sequencing on
BEAAA-23903. Flagging it so nobody promotes them independently and is surprised.

## Commands run

```
# real master head, via GitHub MCP list_commits (local origin/master is stale two different ways)
git diff --name-status 7f8be7a1 00f542a3          # exactly 14 M, no D
git diff --name-status 2e28a2cd 00f542a3          # exactly 3 A (master's Go)
git rev-parse <sha>:<path> per file               # blob identity, both directions
cd /tmp/qa23914r2/cand
python3 -m tools.probe_registry --repo . --emit tools/probe-registry/authoritative_registry.json --check  # 0
python3 -m tools.validate_content_candidate_registration                                                  # 0, PASS
python3 -m pytest -q <13 modules>                 # 85 passed, 1 skipped
cd /tmp/qa23914r2/base && python3 -m pytest -q <13 modules>   # 17 failed, 68 passed, 1 skipped
python3 -m pytest -q <7 sweep modules>            # 47 passed on both
```
