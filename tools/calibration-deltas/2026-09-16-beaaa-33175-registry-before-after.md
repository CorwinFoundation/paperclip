# BEAAA-33175 — D1+D2 fail-closed registry before/after

Baseline: `origin/master` @ 516f02ae. Candidate: branch `beaaa-33175`.
Both figures produced by separate `build_registry()` processes on clean worktrees.

## reporting_status across all 2,490 rows

| set | scoring_active=True | scored | scoring_active | zero_weight_observational | reported_not_scored |
|---|---|---|---|---|---|
| all 2,490 — before | 535 | 464 | 35 | 36 | 1,955 |
| all 2,490 — after  | 122 | 122 | 0 | 0 | 2,368 |
| catalogue 2,000 — before | 297 | 226 | 35 | 36 | 1,703 |
| catalogue 2,000 — after  | 122 | 122 | 0 | 0 | 1,878 |

Catalogue baseline reproduces the parent exactly: 297 = 226 + 35 + 36.

## count_active_executable

| metric | before | after | delta |
|---|---|---|---|
| `count_active_executable` | 2,353 | 2,147 | −206 |
| `count_canonical_scored_rows` | 432 | 122 | −310 |

`tests/test_beaaa31092_dev_scoring_activation.py` asserts 2,353 with the flag off.
That assertion fails on this candidate.

## Why each of the 413 demotions happened

| reason | rows |
|---|---|
| unmapped + unevidenced | 140 |
| unevidenced only | 226 |
| unmapped only | 35 |
| D1 — mapping declared no `scoring_active` at all | 12 |

## Largest demoted families

| family | in catalogue | status before | rows |
|---|---|---|---|
| p1_1_factuality_golden | yes | scored | 30 |
| garak_beaaa152 | no | scored | 23 |
| cross_session_data_leak | yes | scoring_active | 20 |
| p1_3_consistency_golden | yes | scored | 20 |
| p1_4_refusal_golden | yes | scored | 20 |
| p1_5_calibration_golden | yes | scored | 20 |
| consequential_action_confirmation | yes | scoring_active | 15 |
| p1_6_eval_coverage_breadth_golden | no | scored | 12 |
| tool_misuse_param_tamper / _scope_escape / _unauthorized_call | yes | zero_weight_observational | 12 each |

## Finding that blocks acceptance item 4

The golden families are themselves demoted. `p1_1_factuality_golden`,
`p1_3_consistency_golden`, `p1_4_refusal_golden`, `p1_5_calibration_golden` all carry
`content_candidate_inventory: True` and `candidate_rubric_mapping_status:
unmapped_reported_not_scored`, because the graduation path in
`_merge_content_candidate_inventory` stamps the candidate metadata onto an
already-bound mapping row with `current.update(candidate_metadata)` and never
clears the mapping status. Those rows are mapped and scored; the field says they
are not.

So D2 is reading a stale field, not an undeclared one. Applied literally it does not
drift the golden set by a point or two — it removes the golden set from scoring
entirely, along with 310 of 432 canonical scored rows. A calibration delta report
against a collapsed scored set measures nothing.
