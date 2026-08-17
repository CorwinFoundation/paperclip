---
owner: Scanner Engineer #2
artifact_type: release_evidence
status: review
updated_at: 2026-08-17
version: 0.1
source_refs:
  - "BEAAA-23857 (this issue); parent BEAAA-23855; ancestor BEAAA-22115"
  - "candidate commit 20c1c2bec11814f7adc7dd0e37736de76d970aa2"
  - "pre-change baseline commit fb2cf1effef84da7b549897f4b9794fce9481e7b (canonical master)"
  - "contracts/are-scanner-api-v1.md@v1.3.0"
  - "contracts/scanner-implementation-supervision-v1.md@v1.1"
  - "tools/calibration-deltas/2026-08-17-fb2cf1ef-vs-20c1c2be.md"
  - "rubric/probe_mappings/p1_2_citation_integrity_golden.yaml"
  - "rubric/probe_mappings/p1_6_eval_coverage_breadth_golden.yaml"
  - "rubric/probe_mappings/p2_5_sentiment_skew_golden.yaml"
  - "tests/test_beaaa23857_scored_lane_expansion.py"
claim_or_decision: >-
  32 net-new deterministic single-turn cases take the three thinnest scored
  rubric points from 6/1/3 to 16/13/13 canonical scored runtime cases with a
  0-point N=5 golden delta and no change to scoring math, weights, judge
  models, API shape, published probe count or tamper-resistance posture.
verification: >-
  Registry rebuilt and validated strict (zero errors, zero runtime aliases);
  full focused test modules run with no -k selection; N=5 golden gate over
  seeds 0-4 returned PASS with maximum total delta 0 and maximum pillar delta
  0 on all eight in-roster golden agents. Remaining uncertainty is stated in
  section 7: thirteen pre-existing frozen-registry-count assertions were
  already red on canonical master before this change and are left red.
approvals:
  - "CTO co-sign: not required — rule execution per scanner-implementation-supervision-v1@v1.1, table row 'Implement a new probe inside an existing rubric_point_id'."
  - "QA Engineer (cd1e82a0-d1cf-4b44-a8ea-a8b312120baa): REQUIRED before any promotion to canonical master. Not yet obtained."
---

# BEAAA-23857 — scored-lane expansion at P1.2, P1.6 and P2.5

## BLUF

The three thinnest scored rubric points now carry at least twelve canonical
scored runtime cases each. P1.2 goes from 6 to 16, P1.6 from 1 to 13, P2.5
from 3 to 13 — 32 net-new cases against a required floor of 30. Every case is
single-turn, deterministic and judge-model-free. The N=5 golden delta is 0 on
every agent and every pillar. Nothing is promoted to canonical master until
the QA gate signs the candidate.

## 1. Versions and classification

| Field | Value |
|---|---|
| scanner_version | `0.5.0` |
| contract | `are-scanner-api-v1@v1.3.0` |
| rubric_point_id range affected | `P1.2`, `P1.6`, `P2.5` |
| probe-set-version | `p1_2_citation_integrity_golden@0.1.0`, `p1_6_eval_coverage_breadth_golden@0.1.0`, `p2_5_sentiment_skew_golden@0.1.0` |
| corpus_version (P1.2) | `p1-2-citation-integrity-2026.08.17` |
| corpus_version (P1.6) | `p1-6-eval-coverage-breadth-2026.08.17` |
| corpus_version (P2.5) | `p2-5-sentiment-skew-2026.08.17` |
| classification | **Rule execution** — `scanner-implementation-supervision-v1@v1.1`, row "Implement a new probe inside an existing rubric_point_id" |
| cto_cosign_ref | not required (rule execution); N=5 delta recorded below |
| upstream_eval_version | not applicable — no upstream eval framework is wrapped by these three families |
| merged_sha_on_canon_master_at_close | not yet — candidate is unmerged pending QA |

The classification held after measurement: the N=5 delta is 0, well inside the
±1 rule-execution band, so no re-classification to rule change was triggered.

## 2. Candidate

- Candidate commit: `20c1c2bec11814f7adc7dd0e37736de76d970aa2`
- Branch: `agent/beaaa-23857-scored-lane-expansion`
- Parent (pre-change baseline): `fb2cf1effef84da7b549897f4b9794fce9481e7b`
- Built in an isolated clean worktree at `/tmp/se23857`, cut from canonical
  master. The shared dirty checkout was neither read from nor written to.

Two documentation-only commits sit on top of the candidate: this report and
the generated N=5 delta record. They contain no code, no mapping and no
registry change; `git diff --stat 20c1c2be <tip>` shows only files under
`artifacts/BEAAA-23857/` and `tools/calibration-deltas/`. The candidate for
QA is `20c1c2be`.

## 3. Registry counts, before and after

Measured with the same `tools/active_test_registry.build_registry()` call on
both trees. A case counts when it is canonical, active and scored.

| Rubric point | Before (`fb2cf1ef`) | After (`20c1c2be`) | Net new |
|---|---:|---:|---:|
| P1.2 Citation integrity | 6 | 16 | +10 |
| P1.6 Eval coverage breadth | 1 | 13 | +12 |
| P2.5 Output sentiment skew by attr | 3 | 13 | +10 |
| **Total across the three points** | **10** | **42** | **+32** |
| All scored canonical rows | 182 | 214 | +32 |

Every one of the 32 new rows carries `scoring_active=true`,
`reporting_status=scored`, `execution_evidenced=true`, `target_bound=true`,
`canonical_runtime_case=true` and `alias_of_runtime_case_identity=null`.
`validate_registry(rows, 0, strict=True)` returns an empty error list: zero
missing-contract rows and zero runtime aliases. The 32 runtime case
identities are distinct from each other, and the 32 payload hashes are
distinct.

No existing `reported_not_scored` row was promoted. The test module asserts
this directly for `p2_5_sentiment_skew_by_attribute` and
`p3_5_multiturn_jailbreak`.

## 4. What each family measures

| Family | Cases | Design |
|---|---:|---|
| `p1_2_citation_integrity_golden` | 10 | Five fabrication traps (non-existent paper, invented quote pinpoint, invented CVE, invented government URL, retracted source) and five positive controls with a pinned public identifier (DOI, ISO/IEC number, RFC number, dataset citation, USC section). Covers the non-legal citation surfaces `citation_integrity_legal` does not reach. |
| `p1_6_eval_coverage_breadth_golden` | 12 | One case per in-scope risk surface drawn from the BEAAA-209 in-scope dim set. Before this change P1.6 rested on a single runtime case, so the breadth claim had one probe behind it. |
| `p2_5_sentiment_skew_golden` | 10 | One case per protected-attribute axis, no axis repeated. Each fails on an adverse rationale grounded in the attribute and passes only on a criteria-grounded rationale. Axis-level reporting only, per the Carrier Rule. |

Deterministic behaviour, per acceptance item 2:

- **Explicit PASS/FAIL.** `harness/bench/scored_lane_common.evaluate` returns
  score 1.0 or 0.0 and never "partial" or "unresolved". A forbidden marker
  fails the case even when a required marker is also present; absence of every
  required marker is a FAIL, not silence. Both branches are asserted.
- **Interpretable evidence.** Each case row carries the matched markers, the
  reason code, a trimmed evidence span, and the oracle hash.
- **Negative fixture per family.** `known_bad_v0.json` in each family's
  fixture directory fails at least four cases, and the test asserts the
  observed failure count meets the count the fixture itself declares.
- **Pinned provenance.** Every case names a primary source and a pin date; the
  test asserts both are present on all 32.
- **Stable hashes.** Payload and response evidence hashes are byte-stable
  across identical replays, and a changed response moves the response hash
  while leaving the payload hash fixed. Asserted through the real
  `RegisteredCaseExecutor`, not a stub.
- **Reachable evidence refs.** Every case emits
  `yaml://rubric/probe_mappings/<family>.yaml#<case_id>`, and the executor
  writes a resolvable evidence file per case.

No judge model is introduced. The executor spends zero judge calls and zero
judge cost on these families; that is asserted.

## 5. Tests

Full focused modules, no `-k` selection, run from the candidate worktree.

| Command | Result |
|---|---|
| `python3 -m pytest tests/test_beaaa23857_scored_lane_expansion.py -q` | 26 passed |
| `python3 -m pytest tests/test_beaaa19021_tranche1_canary.py tests/test_beaaa18953_tranche1_observational.py tests/test_registered_case_execution.py tests/test_beaaa22121_probe_expansion.py -q` | 74 passed |
| registry-adjacent batch 1 (14 modules, see section 7) | 146 passed, 8 failed, 1 skipped |
| registry-adjacent batch 2 (11 modules, see section 7) | 71 passed, 6 failed, 4 skipped, 36 subtests passed |

Batch 1 modules: `test_active_test_registry_beaaa13644`,
`test_agent_egress_control_beaaa19098`, `test_beaaa16270_aiuc_path_b_lane_a`,
`test_beaaa16271_aiuc_path_b_lane_b`, `test_beaaa16647_aiuc_count_freeze`,
`test_beaaa16967_wave1a_reconcile`, `test_beaaa16972_wave1a_scored_activation`,
`test_beaaa18004_wave3_candidate`, `test_beaaa18720_content_candidate_registration`,
`test_beaaa18953_tranche1_observational`, `test_beaaa19021_tranche1_canary`,
`test_beaaa22121_probe_expansion`, `test_canonical_test_registry_beaaa13762`,
`test_distinct_case_count_beaaa14423`.

Batch 2 modules: `test_eval_framework_capture`,
`test_founder_dev_mode_batch_promotion`, `test_founder_dev_mode_emission_boundary`,
`test_founder_dev_mode_p3_5_promotion`, `test_full_scan_queue_worker_beaaa14843`,
`test_injecagent_pilot_beaaa19103`, `test_lane_c_registration_beaaa16272`,
`test_runtime_import_closure_beaaa13329`, `test_text_case_activation`,
`test_r2_rob_families`, `test_golden_set_l2_gate`.

## 6. N=5 golden delta

Record: `tools/calibration-deltas/2026-08-17-fb2cf1ef-vs-20c1c2be.md`.

- Baseline `fb2cf1ef` → candidate `20c1c2be`, phase development
- Seeds `0, 1, 2, 3, 4`
- Roster `G1, G2, G3, M1, M2, M3, B1, B3`
- **Exclusion disclosed:** `B2` is excluded under the standing BEAAA-10199
  v1-only decision — its approved target model returned 404 through the
  approved provider path before score output. The claim is 8/9 baselined, not
  a nine-golden frozen-roster claim. This exclusion predates BEAAA-23857 and
  is not introduced by it.
- **Maximum total delta: 0. Maximum pillar delta: 0.** Every agent returns
  Δ total, Δ GOV, Δ ROB, Δ BIAS and Δ OPS of 0, with candidate seed spread 0
  on total and all four pillars.
- Gate result: **PASS**. Nothing exceeded 1 point, so no CTO route was
  triggered under acceptance item 5.

Delta 0 is the expected result and is checkable rather than merely asserted:
the golden gate replays frozen fixture inputs under `artifacts/beaaa-387/`,
so adding registry rows cannot move a golden score. Each family additionally
proves its own N=5 byte-identity — five replays of the full family envelope
collapse to a single fingerprint.

## 7. Disclosure — thirteen pre-existing red count gates, not caused by this change

Thirteen assertions across nine modules fail on the candidate. **All thirteen
already fail on canonical master `fb2cf1ef` before this change.** They are
frozen registry-count assertions, each stale by exactly +10 on master:

| Module | Frozen value | Observed on `fb2cf1ef` | Observed on `20c1c2be` |
|---|---:|---:|---:|
| `test_beaaa16647_aiuc_count_freeze` | 340 | 350 | 382 |
| `test_lane_c_registration_beaaa16272` | 340 | 350 | 382 |
| `test_full_scan_queue_worker_beaaa14843` | 340 | 350 | 382 |
| `test_founder_dev_mode_batch_promotion` (2 tests) | 340 / 428 | 350 / 438 | 382 / 470 |
| `test_founder_dev_mode_p3_5_promotion` | 340 | 350 | 382 |
| `test_text_case_activation` | 2202 | 2212 | 2244 |
| `test_canonical_test_registry_beaaa13762` | 2202 | 2212 | 2244 |
| `test_active_test_registry_beaaa13644` | 2202 | 2212 | 2244 |
| `test_beaaa18720_content_candidate_registration` (2 tests) | 2242 / 2202 | 2252 / 2212 | 2284 / 2244 |
| `test_beaaa16972_wave1a_scored_activation` | 305-basis | stale | stale |
| `test_distinct_case_count_beaaa14423` | 362 | 372 | 404 |

Root cause, located and confirmed: at `b0499f97`, the commit immediately
before BEAAA-22121, `count_active_executable` is exactly **340** — the frozen
value. BEAAA-22121 (`1a1ce3c0`) added ten cases to
`p1_1_factuality_golden` and `p1_4_refusal_golden` without refreshing these
count constants, and they have been red on canonical master since.

These constants are the frozen **published** production count. Re-freezing
them asserts a new published number, which acceptance item 6 puts out of scope
for this ticket and which is a CTO call, not a Scanner Engineer #2 call. They
are therefore left untouched and routed to the CTO as a separate item rather
than folded into this candidate.

## 8. Tamper-evidence pins refreshed, not relaxed

`tools/registered_case_executor.py` gains the scored-lane dispatch branch, so
two derived pins move:

1. `tools/tranche1_observational.EVALUATOR_SHA256` —
   `eaae8218…` → `743032f5…`. The pin still binds and
   `activate_registry_rows` still fails closed on mismatch; the rollback-refusal
   test that zeroes the pin still passes. Precedent: `e236d117` (BEAAA-21400).
2. The tranche-1 canary manifest digest in
   `tests/test_beaaa19021_tranche1_canary.py` — `ac670e51…` → `ba14910f…`.
   The manifest embeds the evaluator pin, so this digest is a function of (1).

No tamper-resistance control is removed, weakened or bypassed. Both refreshes
are annotated in place with the reason and the prior value.

## 9. Explicitly not changed

Rubric weights, pillar weights, band cuts, calibration curves, scoring math,
pricing semantics, judge models, API shape, published probe count,
tamper-resistance posture. No `reported_not_scored` row promoted. `P1.6`'s
raw-score source remains `harness/bench/eval_coverage_aggregator.py` — the new
P1.6 family emits per-case runtime evidence and deliberately carries no `raw`
field. A test asserts the four pillar weights and the three affected
dimensions' calibration curves are unchanged.

## 10. Rollback

Rollback is an explicit revert of the candidate commit:

```
git revert --no-edit 20c1c2bec11814f7adc7dd0e37736de76d970aa2
```

After the revert:

- **Regenerate the derived registry artifact.** `tools/probe-registry/`
  (`active_registry.json`, `authoritative_registry.json`) and any
  `artifacts/beaaa-*/…-active-test-registry-*.json` snapshot regenerated after
  the candidate landed must be regenerated from the reverted tree, or they
  will keep quoting 214 scored canonical rows against a registry that has 182.
  The candidate itself does not regenerate them, so if the revert happens
  before any regeneration run, there is nothing to redo.
- **Restore the two pins.** The revert restores `EVALUATOR_SHA256` to
  `eaae8218…` and the canary digest to `ac670e51…` automatically, because both
  are edits inside the candidate commit. Verify with
  `python3 -m pytest tests/test_beaaa18953_tranche1_observational.py tests/test_beaaa19021_tranche1_canary.py -q`.
- **No fixture or golden-set repair is needed.** The candidate adds no golden
  fixture and edits none, so the N=5 baseline is untouched by the revert.

## 11. Known limits

- The oracle is lexical. It detects a stated marker; it cannot read reasoning,
  follow a citation, or catch a correct identifier attached to a wrong claim.
  Each family YAML states this in its `limitation` block. These are floors on
  the behaviour under test, not ceilings.
- `p1_6_eval_coverage_breadth_golden` samples 12 of the 20 in-scope dims. The
  eight unsampled dims are named in the family's `limitation` block.
- The P1.6 family deliberately overlaps surfaces that have their own dedicated
  families. That is what a breadth sampler is, and it is not a substitute for
  those families. The duplicate-logic question is disclosed to the CTO on
  BEAAA-23857 rather than resolved unilaterally.
