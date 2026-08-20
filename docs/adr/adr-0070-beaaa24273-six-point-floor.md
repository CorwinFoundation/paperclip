# ADR-0070: Additive six-point 30-case floor

- Ticket: BEAAA-24273
- Status: accepted
- Classification: rule-execution, existing deterministic probe implementation
- Contract: `contracts/are-scanner-api-v1.md@v1.3.0`
- Supervision: `scanner-implementation-supervision-v1@v1.1`

## Decision

Add six new, versioned, deterministic single-turn families with ten concrete behavior scenarios each. Each scenario names a rubric-specific situation and pairs a safe expected response with a materially unsafe negative fixture. Keep the BEAAA-24210 family corpora immutable. The new families reuse the active registry and `scored-lane-substring-oracle@1.0.0`; they do not change rubric weights, scoring math, judge models, API shape, contract output, tamper resistance, or published counts.

Each case has a distinct prompt hash and oracle hash, an explicit binary PASS/FAIL disposition, a negative fixture, pinned authored provenance, and a reachable `yaml://` evidence reference. Strict registry validation must identify every row as canonical, scored, execution-evidenced, target-bound, and free of runtime aliases.

## Consequences

The six target points move from 20 to 30 canonical scored cases, adding 60 cases in total. Rollback is `git revert <BEAAA-24273-candidate-sha>`.

The original candidate `9736dbf70f0cb3b7504d8f8b9268e1c0b031f768` is superseded because its numbered placeholder prompts and tautological markers did not demonstrate rubric-specific behavior.

The integration candidate is based on exact canonical upstream `71c5124d994ac007837712f51c4b21dbb17d9aa8`, preserving the overlapping BEAAA-24310 registry and canary reconciliation. The functionally approved pre-integration candidate `b848816f1adaa8447974816a7e489b531ce2b6d7` is superseded and must not be promoted. N=5 evidence compares seeds 0 through 4 between `71c5124d...` and the integration tree; rollback remains a revert of the final immutable integration candidate.

## Ruling: the published-count freeze holds (CTO, 2026-08-19)

Adding 60 evidence-gated executable rows reddens seven guards that pin live registry
counts, and one runtime check that enforces the same equality outside the test suite.
The CTO ruled on comment `23540c9e-1758-46cb-8789-dec46cd4d9cd` of BEAAA-24273:
the published executed count stays frozen at 2,279, `tools/probe-registry/authoritative_registry.json`
is not regenerated in this ticket, the equality invariant is relaxed to the one the
tooling actually enforces, and the live-count pins move to the measured candidate values.
That ruling is the CTO co-sign for the guard edits in this candidate.

Regenerating the authoritative registry was refused on scope grounds. On a clean
`71c5124d...` worktree the committed file already differs from a fresh generation by
+45 `registered` rows and +10 `fixture_test_only` rows, and `metadata.generated_from_content_sha`
differs (`4e32652f3266` committed against `8536491ede11` generated). Regenerating inside a
rule-execution ticket would publish 45 rows nobody has reviewed and re-bind the
content-provenance hash. BEAAA-24454 owns that drift and the eventual deliberate
re-publish, which needs a CEO and Underwriter gate.

### Why under-claiming is the safe direction

`validate_registry` errors only when the published count exceeds the active executable
count. Publishing 2,279 while 2,339 are executable therefore keeps strict validation
clean, and it is the conservative direction for underwriting: we never claim coverage we
do not run. The relaxed guard is not a bare `>=`. It pairs `assertGreaterEqual(active, published)`
with an explicit `assertEqual(load_published_count(), 2279)`, so a silent drop in the
published count is still caught.

### Counts, measured directly on both trees

| metric | 71c5124d | candidate | delta |
| --- | --- | --- | --- |
| registry rows | 2319 | 2379 | +60 |
| active executable | 2279 | 2339 | +60 |
| target-bound runtime | 2290 | 2350 | +60 |
| `canonical_runtime_case` | 2031 | 2091 | +60 |
| distinct (prompts, expected) pairs | 2278 | 2338 | +60 |
| runtime aliases | 12 | 12 | 0 |
| active executable, `include_content_candidates=False` | 417 | 477 | +60 |
| target-bound runtime, `include_content_candidates=False` | 439 | 499 | +60 |
| published (committed `category_counts.executable`) | 2279 | 2279 | 0 |

### Tests newly red on the candidate, and what they pinned

| test | pinned | candidate |
| --- | --- | --- |
| `test_active_test_registry_beaaa13644.py::test_production_registry_counts_canonical_runtime_identities_once` | published equality, 2279, 2290, 2031 | 2339, 2350, 2091 |
| `test_authoritative_probe_registry_beaaa20357.py::test_executable_claim_is_not_default_executed_reported_or_scored` | 2279 in five places | 2339 |
| `test_canonical_test_registry_beaaa13762.py::test_beaaa13992_active_rows_count_only_target_bound_execution_evidence` | 2279 twice | 2339 |
| `test_beaaa18720_content_candidate_registration.py::test_all_candidates_are_registered_without_duplicate_family_case_ids` | rows 2319 | 2379 |
| `test_beaaa18720_content_candidate_registration.py::test_registered_count_and_distinct_runtime_count_reconcile` | 2279, 2290, distinct 2278 | 2339, 2350, 2338 |
| `test_lane_c_registration_beaaa16272.py::test_frozen_production_counts_unchanged` | 417, 439 on the no-content-candidate view | 477, 499 |
| `test_beaaa24210_scored_floor.py::test_strict_registry_and_floor_counts` | `counts[point] - before == EXPECTED_ADDITIONS[point]` | `assert (30 - 16) == 4` |
| `demo-real-agent/tests/test_real_agent_demo.py::test_registry_inventory_is_exactly_the_real_203_case_plan` | already red on master at the 203-case plan pin; the candidate failed earlier, at the runtime equality check | restored to the baseline failure |

### Why the authoritative registry was not regenerated

Committed `tools/probe-registry/authoritative_registry.json` against a fresh generation on a clean `71c5124d` worktree:

| category_counts | committed | live | delta |
| --- | --- | --- | --- |
| registered | 2608 | 2653 | +45 |
| fixture_test_only | 89 | 99 | +10 |
| executable | 2279 | 2279 | 0 |
| all others | equal | equal | 0 |

`metadata.generated_from_content_sha` is `4e32652f3266` committed against `8536491ede11` live. `load_published_count` resolves through
`tools/active_test_registry_claims.json`, which is marked retired and therefore delegates to `category_counts.executable` in that generated
file, so a regenerate moves the customer-visible published count as a side effect of picking up 45 unreviewed rows.

### Guards moved in this candidate

| file | change |
| --- | --- |
| `tests/test_active_test_registry_beaaa13644.py` | equality replaced by `>=` plus an explicit 2,279 published pin; 2279 to 2339, 2290 to 2350, 2031 to 2091 |
| `tests/test_authoritative_probe_registry_beaaa20357.py` | five `2279` pins to `2339` |
| `tests/test_canonical_test_registry_beaaa13762.py` | metadata and row-count pins 2279 to 2339; the stale "2,244" comment corrected |
| `tests/test_beaaa18720_content_candidate_registration.py` | rows 2_319 to 2_379, active 2_279 to 2_339, target-bound 2_290 to 2_350, distinct total 2_278 to 2_338; the `published_count=2_279` under-claim proof left as it was |
| `tests/test_lane_c_registration_beaaa16272.py` | frozen no-content-candidate view 417 to 477, 439 to 499 |
| `tests/test_beaaa24210_scored_floor.py` | exact-delta assertion scoped to that ticket's own rows instead of the global scored counter |
| `demo-real-agent/real_scan.py` | the runtime check raises on over-claim only |
| `demo-real-agent/tests/test_real_agent_demo.py` | `evidenced_executable_case_count` asserted against `count_active_executable`, not against the published count |

The distinct-pair total was not in the ruling's edit list. It is asserted on the line
below the two counts the ruling did name, inside the same test, and it moves with the
new rows, so `test_registered_count_and_distinct_runtime_count_reconcile` would have
stayed red without it. Measured, not assumed: 2,278 on the baseline and 2,338 on the
candidate.

`tests/test_beaaa24210_scored_floor.py` is the substantive one. BEAAA-24210 installed
`counts[point] - before == EXPECTED_ADDITIONS[point]` where `counts` is a Counter over
every scored row in the registry, so any later additive ticket breaks it. The assertion
now reads a Counter over that ticket's own 35 selected rows and stays exact, while the
global assertion keeps its role as the 20-case floor.

### The equality was enforced in runtime code as well

`demo-real-agent/real_scan.py` raised `RuntimeError("published executed count does not
equal evidence-gated registry count")` whenever the two counts differed, so relaxing the
test alone would have left the demo entrypoint crashing on the candidate. It now raises
only when `published > evidenced_executable`. `demo-real-agent/tests/test_real_agent_demo.py`
is already red on canonical master for an unrelated reason, the 203-case plan pin against
213 observed cases, and this candidate restores that same baseline failure instead of the
earlier equality failure.

### Left untouched, tracked on BEAAA-24454

- `python3 -m tools.probe_registry --repo . --check` fails on canonical master itself.
- GitHub Actions workflow `authoritative-probe-registry` (id 328422667) is failing on master, run 32173299637.
- `scripts/authoritative_probe_registry_integration_gate.sh` still names the pytest node id
  `test_legacy_2244_is_not_default_executed_reported_or_scored`, which BEAAA-24310 renamed.
- Eight failures already red on `71c5124d` and unchanged by this candidate.

### Acceptance evidence

The registry-importing test set is enumerated by transitive static import closure, not by
hand: every file under `tests/` and `demo-real-agent/tests/` that reaches
`tools.active_test_registry`, `tools.canonical_test_registry`, or `tools.probe_registry`
through any chain of first-party imports. That yields 34 files on the baseline and 35 on
the candidate, the difference being this ticket's own new test file. Acceptance is that the
candidate's failed set equals the baseline's failed set, printed as a diff of the two sets,
plus the N=5 golden delta re-run on the final SHA.

### Candidate structure, and why it is two commits

`scripts/beaaa24273_n5.py` stamps `candidate_tree` from `git rev-parse HEAD^{tree}` in the candidate
root. A single commit would therefore record the tree of the previous commit rather than the tree the
run actually measured, which is a wrong datum in a calibration receipt. The follow-up is split so the
recorded value is exactly true:

- The first commit carries all eight guard and runtime edits plus this ADR.
- The N=5 run is then executed in that tree, so `candidate_tree` names it.
- The second commit adds only the regenerated report, and its SHA is the candidate handed to QA.

Verifiable as `git rev-parse <candidate>^^{tree}` equal to `candidate_tree` in the report, with
`git diff --name-only <candidate>^ <candidate>` naming only the report.

## Second drift: the BEAAA-24445 count refreeze (2026-08-19)

`e2411fe5` was green on its own base and on the merge with master as it stood at handoff
(`8b0b340b`, merge tree `3170bda1`, empty failed-set diff at 14 = 14). About thirty minutes
later master merged BEAAA-24445's registry count refreeze (`1fb6bf24`, merge `76da84ea`),
which re-pinned the same live constants this ticket moves. QA recorded CHANGES REQUESTED on
that basis. The candidate was not defective; the constants it moves were refrozen underneath it.

Those pins do not exist on this branch's base `71c5124d`, which still carried the
pre-refreeze values (350/372, 315, 2_244/2_243). The +60 adjustment therefore cannot be
expressed on the candidate side alone: any edit to those lines would collide with the
refreeze at the same lines. The branch absorbs master by **merge**, not rebase, so
`e2411fe5` stays immutable and reachable, and the follow-up commit expresses the nine
constants as a diff against the refreeze that owns them. The merge carries no adjustment of
its own: its tree is exactly `b789e8275c5535e73ba434b901825ec39d7d4c55`, the same tree
`git merge-tree --write-tree 54101263 e2411fe5` writes.

Nine constants in four files, every one measured on the merge tree rather than inferred:

| file | line | from | to |
| --- | --- | --- | --- |
| `tests/test_beaaa16647_aiuc_count_freeze.py` | 26, 28, 29, 32 | 417 | 477 |
| `tests/test_beaaa16647_aiuc_count_freeze.py` | 30, 31 | 439 | 499 |
| `tests/test_beaaa16972_wave1a_scored_activation.py` | 68 | 417 | 477 |
| `tests/test_beaaa16972_wave1a_scored_activation.py` | 90 | 382 | 442 |
| `tests/test_full_scan_queue_worker_beaaa14843.py` | 98 | 2_279 | 2_339 |
| `tests/test_full_scan_queue_worker_beaaa14843.py` | 99 | 2_278 | 2_338 |
| `tests/test_text_case_activation.py` | 38 | 417 | 477 |

Line 90 is a second-order red: pytest stops at line 68, so it is invisible from the failure
output. It was measured by replicating that test's own rollback procedure on the merge tree,
copying `rubric/probe_mappings` and flipping every `AIUC_PATH_B_SCORING_ACTIVE_FAMILIES`
mapping to `reported_not_scored` with `scoring_active` and `underwriting_active` false, then
rebuilding. Before 477, after 442. The 60 new rows sit outside those families, so they
survive the rollback and the count moves by the same +60.

Measured and deliberately left alone: the content-candidate inventory at `2_000`, the text-row
count at `113` with both Counters at `{True: 90, False: 23}`, and the frozen published pins,
`load_published_count() == 2279` and `validate_registry(rows, published_count=2_279, strict=True)`.
The published count is still BEAAA-24454's to move. `test_beaaa16972_executable_rollback_restores_305_...`
keeps its name even though the `305` in it is already stale on master, because renaming a test
silently breaks any CI gate pinning the pytest node id.

### The acceptance set is now one command

Three different numbers were quoted for "the tests that touch the registry" during this
ticket: 30/31 from a flat grep, 34/35 from a partial import walk, and 60/61 from a full one.
`scripts/beaaa24273_acceptance_closure.py` is the single definition, and it is what the
acceptance runs. It walks first-party imports from every source root, not just the ones the
registry happens to live under, so a test that reaches the registry only through
`api.full_scan_worker` is in the set exactly as much as one that imports it directly. It
yields 68 files on master and 69 on the candidate, the difference being this ticket's own
test file.

