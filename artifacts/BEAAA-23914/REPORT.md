# BEAAA-23914 — QA gate: registry-count refreeze candidate `2e28a2cd`

**Result: APPROVED** for the stated acceptance scope.

| | |
|---|---|
| Candidate | `2e28a2cd0f8cef4969c44035afe0c85065bf956b` |
| Branch | `agent/beaaa-23903-count-refreeze` |
| Parent commit | `fb2cf1effef84da7b549897f4b9794fce9481e7b` (canonical master) — verified sole parent |
| Candidate tree | `bda2fc8004c5d533010bcf53fca0d5dcb3a4d5dc` |
| Implementation parent | BEAAA-23903 |
| Tested | 2026-08-17, detached worktrees `/tmp/qa23914/{cand,base}`, Python 3.12.3 |

Disclosure: the candidate commit carries author `QA <qa@backbond.invalid>`. That is this VM's shared git
config, not authorship. I did not write `2e28a2cd`; I tested it.

## Acceptance criteria

**(1) Authoritative registry check passes.** `python3 -m tools.probe_registry --repo . --emit
tools/probe-registry/authoritative_registry.json --check` exits 0. `git status --porcelain` afterwards shows
the emitted file unmodified, so the committed artifact is byte-identical to what the generator produces from
the candidate tree. PASS.

**(2) The 11 assigned modules pass with exact assertions preserved.** `68 passed, 1 skipped in 163.74s`,
matching the stated implementation evidence exactly. Every changed assertion is still exact equality
(`assertEqual` / `==`); the strict inequalities in `test_authoritative_probe_registry_beaaa20357.py` are still
`<`. Nothing was loosened to a range, and no test was deleted or skipped — 69 tests collected on both base and
candidate. PASS.

**(3) No scoring, rubric, or API behavior changed.** The diff touches zero files under `rubric/`, `api/`,
`scanners/`, `corpus/`, `contracts/`, `underwriting/`, or `harness/`. The two changed files under `scripts/`
are evidence generators reachable only from each other and from
`tests/test_beaaa16992_production_controls.py` — no production, scoring, or API importer. PASS.

**(4) Candidate contains only the freeze, coupled evidence-helper counts, and the regenerated registry.**
14 files: 11 test modules, 2 evidence scripts, 1 regenerated registry. Row-keyed diff of
`authoritative_registry.json` against the base blob: **10 rows added, 0 removed**, sourced 4 from
`p1_1_factuality_golden.yaml` and 6 from `p1_4_refusal_golden.yaml`. 50 shared rows changed in exactly three
fields — `framework_version`, `version_pin`, `source_provenance` — all `0.1.0` → `0.2.0`, the mechanical
consequence of the mapping version bumps already on master. No unexplained row or field edit. PASS.

## The drift is real, and the numbers are true

I did not take `+10` on the producer's word. `1a1ce3c0` ("BEAAA-22121 expand scoreable single-turn probes",
already merged to master) adds four probes to P1.1 (`P1.1-FAC-031`…`034`) and six to P1.4
(`P1.4-REF-021`…`026`) — ten real probes with prompts, ground truth, and rubric notes. That is the entire
source of the drift.

Every refrozen constant reproduces from the shipped loaders on the candidate tree, computed independently of
the test suite:

| Quantity | Frozen in candidate | My independent measurement |
|---|---|---|
| `count_active_executable` (core cohort) | 350 | 350 |
| `count_target_bound_runtime` (core cohort) | 372 | 372 |
| `count_active_executable` (expanded) | 2212 | 2212 |
| `count_target_bound_runtime` (expanded) | 2223 | 2223 |
| `canonical_runtime_case` rows | 1964 | 1964 |
| alias rows | 12 | 12 |
| `category_counts.executable` | 2212 | 2212 |

The published customer-visible count follows automatically: `active_test_registry_claims.json` is a retired
tombstone that redirects `load_published_count()` to the authoritative registry's `category_counts.executable`,
so there is no second number to keep in sync.

## The refreeze is load-bearing, not a rubber stamp

Baseline on `fb2cf1ef` with the same 11 modules: **14 failed, 54 passed, 1 skipped** — 11 count-assertion
tests plus the 3 production-control tests the parent set out to restore. Candidate: 0 failed. The change
fixes exactly what it claims.

Three mutants, all killed:

- **M1 — addition.** Added an 11th probe to `p1_1_factuality_golden.yaml`. Counts moved to 351 / 373;
  `--check` exit 1; 8 tests failed across 7 of the 11 modules.
- **M2 — artifact tamper.** Edited `"executable": 2212` → `2400` in the committed registry with the mappings
  untouched. `--check` exit 1, and it did **not** silently rewrite the tampered file — fail-closed, not
  fail-fixed. (First attempt at this mutant emitted to a scratch path and so proved nothing; redone against
  the real path.)
- **M3 — deletion.** Removed `P1.4-REF-026`. Count fell to 349; `--check` exit 1.

Note the division of labor: artifact tampering is caught by `--check`, not by the pytest modules, which
rebuild from source. Both halves of the acceptance command are load-bearing; neither substitutes for the other.

## Regression

Flip-set comparison across the count-dependent modules outside the assigned 11
(`test_beaaa16967_wave1a_reconcile`, `test_beaaa18720_content_candidate_registration`,
`test_full_scan_queue_worker_beaaa14843`, `test_probe_registry`, `test_full_suite_manifest_beaaa13645`,
`test_demo_readiness_gate_beaaa15495`, `test_beaaa22121_probe_expansion`, `test_injecagent_pilot_beaaa19103`):

- base `fb2cf1ef`: 3 failed, 56 passed
- candidate `2e28a2cd`: 3 failed, 56 passed — **the same three**

Zero regressions. Nothing that passed on master fails on the candidate.

## Residual, out of scope, disclosed — do not read this gate as "the drift is closed"

The same BEAAA-22121 drift leaves three tests red in two modules that were **not** in the assigned 11, red
before and after this candidate:

- `tests/test_beaaa18720_content_candidate_registration.py::test_all_candidates_are_registered_without_duplicate_family_case_ids` — `assert 2252 == 2242`
- `tests/test_beaaa18720_content_candidate_registration.py::test_registered_count_and_distinct_runtime_count_reconcile` — `assert 2212 == 2202`
- `tests/test_full_scan_queue_worker_beaaa14843.py::test_default_registry_loader_matches_standard_production_count` — `assert 2212 == 2202`

Separately, `tools/probe-registry/active_registry.json` — a different committed artifact, read by
`api/full_scan_worker.py` and `api/full_suite_manifest_registry.py` — still reports `executable: 2202` and
`unique_executable_count: 2202`, ten short of the authoritative registry this candidate refreezes at 2212.
`tools/oss001_calibration.py` still emits hardcoded `registry_delta [2242, 2213, 2202, 2201]` into its
summary.

None of this is caused by the candidate and none of it is a regression, so it does not block this gate. But
master remains red on those three tests after this lands, and two committed artifacts disagree with each other
about how many probes exist. Recommendation for the CTO: extend BEAAA-23903 or open a follow-up covering the
two remaining modules and `active_registry.json` before anyone describes the BEAAA-22121 count drift as
resolved.

Also noted, no action: `tools/partner-kit/*` shows as modified in every fresh worktree from a CRLF/LF
normalization setting. It is present in an untouched base worktree where no command was run, so it is not a
side effect of the registry generator.

## Commands run

```
git worktree add --detach /tmp/qa23914/cand 2e28a2cd
git worktree add --detach /tmp/qa23914/base fb2cf1ef
cd /tmp/qa23914/cand && python3 -m tools.probe_registry --repo . \
  --emit tools/probe-registry/authoritative_registry.json --check     # exit 0
cd /tmp/qa23914/cand && python3 -m pytest -q <the 11 modules>          # 68 passed, 1 skipped
cd /tmp/qa23914/base && python3 -m pytest -q <the 11 modules>          # 14 failed, 54 passed, 1 skipped
cd /tmp/qa23914/{cand,base} && python3 -m pytest -q <8 regression modules>   # 3 failed, 56 passed on both
```
