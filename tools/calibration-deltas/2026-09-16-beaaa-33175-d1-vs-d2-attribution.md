# BEAAA-33175 — D1 vs D2 attribution, measured by bisect

Baseline `origin/master` @ `516f02ae`. Each row below is a separate
`build_registry()` process on a clean worktree, one variant applied at a time.
No module reloading, no shared interpreter state.

## The four variants

| variant | golden rows scored | ALL scored | scoring_active=True | count_active_executable | canonical_scored |
|---|---|---|---|---|---|
| baseline | 100 / 100 | 464 | 535 | 2,353 | 432 |
| D1 only | **0 / 100** | 122 | 193 | **2,147** | 122 |
| D2 only | 10 / 100 | 340 | 340 | 2,353 | 328 |
| D2 + field repair | **100 / 100** | 444 | 479 | **2,353** | 432 |
| D1 + D2 + repair | 0 / 100 | 122 | 157 | 2,147 | 122 |

## What this attributes

**D1 owns the entire blast radius.** Every number that moves — the 342 rows
leaving `scored`, `count_active_executable` 2,353 -> 2,147, `canonical_scored`
432 -> 122, and the whole golden set going unscored — is caused by D1 alone.
D2 contributes none of it.

**D1 also flips a field it does not write.** Under D1-only, all 100 golden rows
change `execution_evidenced` from `True` to `False`. `apply_fail_closed_scoring`
never writes that field, and D1 changes one `return` statement in
`_scoring_active`. So `execution_evidenced` on these rows is *derived from*
scoring state upstream of the predicate.

That is the defect that makes D1 unlandable today, and it is a circular
dependency, not a tuning problem:

```
scoring_active  ->  (promotion path)  ->  execution_evidenced  ->  D2  ->  scoring_active
```

D2's evidence clause is specified over a field that is itself a function of the
thing D2 is deciding. With D1 applied, the loop closes: inverting the default
un-scores a row, un-scoring it un-evidences it, and being unevidenced then makes
it permanently ineligible to be scored again. Nothing re-enters the scored set.
This is why D1+D2 lands on 122 rows — the only survivors are the 122 C10 rows
that carry a *saved* verdict (`saved_vapi_deterministic_verdict_bound`) and so
never depend on the derivation.

`tools/active_test_registry.py:~728` states the intended semantic outright:
"Prior execution evidence remains false until a scan actually runs."
`execution_evidenced` means *a scan has already produced evidence*, not *this row
can be executed*. A registry-build-time predicate that requires it can only ever
admit rows with pre-saved verdicts.

## The second defect, found and fixed here

`_merge_content_candidate_inventory` stamps the registration literal
`candidate_rubric_mapping_status: unmapped_reported_not_scored` onto rows that
have already graduated onto a bound production mapping, via
`current.update(candidate_metadata)`. Those rows carry a real rubric id and are
runtime-identity-bound; the field asserts the opposite. Only one writer in the
tree maintains it truthfully (`tools/c10_family_activation.py:178`, which writes
`mapped_scored_zero_weight`), and nothing read the field at all before D2.

D2-only demotes 90 of the 100 golden rows on that stale literal. With the writer
repaired, it demotes none of them. The repair moves 119 rows from
`unmapped_reported_not_scored` to `mapped_via_production_mapping`.

This matters for the D2 spec as written: without the repair, D2 fails rows
closed on a field no writer maintained, which is arbitrary rather than
fail-closed.

## What D2 + repair actually demotes — 56 rows, precisely the intended set

| n | prior reporting_status | trigger |
|---|---|---|
| 36 | zero_weight_observational | unmapped **and** unevidenced |
| 20 | scored | unevidenced |

By family: `garak_beaaa152` 12, `tool_misuse_param_tamper` 12,
`tool_misuse_scope_escape` 12, `tool_misuse_unauthorized_call` 12, plus
`eval_coverage_ledger`, `mitigation_evidence`, `p4_audit_log`,
`p4_eval_cadence` 1 each.

The 36 are exactly the `zero_weight_observational` rows BEAAA-33099 audited and
the CEO flagged at 16:44. They are the rows whose label was flipping away from
`reported_not_scored` under the fail-open default. D2 + repair closes that.

## Consequences for acceptance item 4

For **D2 + repair**, item 4 needs no amendment and no Underwriter co-sign: the
golden set is untouched (100/100 still scored), `count_active_executable` is
unchanged at 2,353, and `canonical_scored` is unchanged at 432. The golden
delta is 0 by construction because no golden row changes state. Item 3's 2,353
assertion holds as written.

For **D1**, item 4 cannot be run at all until the derivation is broken. The
golden set is unscored under D1, so an N=5 seeds 0-4 golden calibration would be
measuring an empty scored set. That is not a >1 point move needing co-sign; it
is an unrunnable measurement.

## Dev-tier interaction, handled

`tests/test_beaaa31092_dev_scoring_activation.py::test_flag_on_promotes_exactly_the_ledger_rows`
failed under D2 because the BEAAA-31092 dev-tier rows are unmapped and
unevidenced by construction, so D2 demoted them with the flag on.

`row_blocks_scoring_activation` now exempts `dev_scoring_active is True`. That
tier is already gated behind `BACKBOND_FOUNDER_DEV_MODE` and is non-countable
with the flag off, so it never reaches a production score; re-gating it here
would disable the dev tier without moving any production number, and
re-dispositioning rows is outside this card's scope. Flag-off numbers are
identical with and without the exemption.

## Test state on this branch

`tests/test_beaaa33175_fail_closed_scoring.py` — 5 passed, 1 xfail(strict).
The xfail is the D1 assertion (`no declaration resolves False`), which states the
target behaviour and flips to pass the moment the derivation defect is fixed.
`tests/test_beaaa31092_dev_scoring_activation.py` — 4 passed, including the
2,353 flag-off count assertion.
