# Monitoring, evaluation and management-system evidence

Six conditional cases extend the existing reviewed-record adapter. They reuse
normal scan dispatch, durable native artifacts, standard JSON/HTML reports,
approved assessment plans, private original bytes and reproducible assessor ZIPs.
They make zero model/judge calls and contribute zero score. This extension brought
the registry to 2,414 rows: 2,373 measured and 41 conditional evidence cases.
[Assurance checks](evidence-assurance-checks.md) add three more conditional cases.

New cases use `assessment-lifecycle-checks@1`. All earlier case versions and
measurement contracts remain unchanged. Both candidate crosswalks advance to
**@5**; retain completed @4 assessments and obtain reviewed @5 plans for new
scope/case selections. These are engineering-draft associations, not licensed
normative interpretations or certification conclusions. Requirement conclusions
remain `not assessed`.

| Case | Evidence reconciled | Important boundary |
| --- | --- | --- |
| EVID-MONITORING-COVERAGE | Approved completed monitoring periods, sample coverage, result identity and freshness | Completeness is relative to the supplied schedule and dataset, not discovered production traffic |
| EVID-MONITORING-TREND | Each comparable period's exact-match accuracy versus the approved first period | An empirical regression check; it does not establish population drift or statistical significance |
| EVID-MULTICLASS-METRICS | Confusion matrix, class support, precision, recall, F1 and abstentions | Labelled classification only, with 2–16 reviewed classes and customer thresholds |
| EVID-SUBGROUP-UNCERTAINTY | Group error rates, sample coverage and Wilson 95% accuracy bounds | Independent Bernoulli assumptions and group suitability remain reviewed declarations; no general fairness determination |
| EVID-GOVERNANCE-ACTIONS | Required audit/management reviews, action links, ownership, deadlines and completion records | Record consistency does not establish an adequate audit or effective management system |
| EVID-COMPETENCE-COVERAGE | Reviewed active-role roster, required courses and current demonstrated-competence records | A reviewed outcome is required; attendance alone is insufficient and competence authenticity remains human review |

## Shared acquisition and scope

Reuse `assessment_record_factory(context)` and `read(case_id) -> bytes`, or the
existing `--records-only --records-directory ... --plan ...` CLI. There is no new
public upload, module loading, URL fetching, synthetic fallback or production
connector. Use the envelope and artifact contract in
[record checks](evidence-record-checks.md) and the `data.system` scope in
[validation checks](evidence-validation-checks.md). Every source is at most
512 KiB; lists are bounded to 2,000 rows. All embedded original bytes and digests
are retained privately and rechecked on import/export.

Source events must occur by the envelope's `recorded_at`, followed by root review
and capture evaluation. Monitoring freshness, open-action deadlines, active-role
validity and competence expiry are evaluated at capture `evaluated_at`. Replaying
an old capture uses that saved evaluation time. Running a new capture evaluates
current validity again; an old source timestamp is not a freshness exemption.

Missing inputs, monitoring windows, subgroup samples, review records or completion
evidence remain inconclusive (`unresolved` in normal scan manifests). Contradictory
identities, declarations, chronology and breached approved criteria fail. Foreign
target inputs are not retained. The original result never becomes a conformity
decision, and missing evidence never becomes a technical pass.

## Monitoring input

Reuse `dataset_ref`, `current_model_version`, `probe_version` and the current
evaluation artifact contract. Policy supplies timezone-aware `start_at, end_at`,
positive integer `cadence_seconds, maximum_age_seconds, minimum_window_count`, and
`maximum_drop_ppm`. The fully elapsed range must contain 1–366 equal, complete
periods. No partial final window or fractional count is silently rounded away.

`data.windows` contains unique `{id, index, evaluation_ref}` rows with zero-based
indices from the approved schedule. Each evaluation must match the same target,
snapshot, model, dataset hash, probe and `conditions_sha256`, cover the original
labelled dataset, reconcile declared accuracy and have `observed_at` within its
own half-open period. Reusing one original evaluation in two slots fails.

Coverage checks missing periods and minimum sample counts. Trend requires the
first approved period and at least one later period, compares every observed
period against that first period and applies the approved maximum accuracy drop.
It never substitutes the first available result for a missing approved baseline.
These periodic benchmark results do not prove representative production sampling.

## Multiclass input

Reuse the same current evaluation contract. Policy supplies ordered unique
`classes` (2–16 strings), a distinct `abstention_label`, `abstention_policy`
(`reject|count_as_incorrect`), positive `minimum_class_support`, and
`minimum_precision_ppm, minimum_recall_ppm, minimum_f1_ppm,
maximum_abstention_ppm`. Observed labels and predictions must use that vocabulary.

Current evaluations include `declared_confusion_matrix` (actual class rows,
predicted class columns, in policy order) and `declared_multiclass` in the same
order. Each class declares integer `support, predicted, true_positive,
false_positive, false_negative, abstention` and its defined
`precision_ppm, recall_ppm, f1_ppm`. Rates round down to integer PPM. F1 is computed
directly as `2*TP/(support+predicted)`, rather than from rounded precision/recall.

Abstentions remain in support, false-negative and accuracy denominators, and are
counted separately from the confusion matrix. Undefined rates must be omitted,
not declared as zero. Zero support or zero predicted count leaves a visible gap;
breached independently defined criteria still fail. Small class support is also
inconclusive. Recomputed counts and declarations must agree exactly, with actual
integers rather than boolean substitutes.

## Subgroup input

Dataset rows add `group` and a nonempty independence field selected by policy
`unit_field`. Policy supplies ordered `required_groups` (1–16 strings),
`method: wilson95-binomial`, `sampling_assumption: independent_bernoulli`, a
reviewed `sampling_basis`, positive `minimum_independent_count`, and
`minimum_group_lower_ppm, maximum_interval_width_ppm, maximum_gap_ppm`.

Current evaluations declare `declared_subgroups` in policy order, each containing
integer `count, correct` and, when count is nonzero, `error_rate_ppm,
wilson_lower_ppm, wilson_upper_ppm, interval_width_ppm`. The Wilson lower bound
rounds down and upper bound rounds up. Missing groups omit undefined numbers.
Repeated subjects or unsupported assumptions suppress intervals and remain
inconclusive. Each sufficiently sampled group is checked against the approved
lower-bound/width criteria. The reported gap is the observed point-error-rate
gap, not a confidence interval or proof of discrimination.

## Governance input

Policy supplies `required_reviews`: unique `{id, kind, due_at,
required_action_ids}` rows, where kind is `audit|management_review` and the due
date is no later than the source record time. The action list may explicitly be
empty. At most 2,000 total review/action links are accepted.

`data.reviews` supplies unique `{id, artifact_ref}` rows. Each original scoped
review artifact contains matching `id, kind`, `reviewer, basis, reviewed_at` and
`action_ids`. Required and recorded action sets must match; late reviews fail.

`data.actions` supplies unique `id, owner, status` (`open|closed`), `due_at` and
reciprocal `source_review_ids`. Open actions remain counted and fail when overdue.
Closed actions also require `closed_at, verification_ref`. The scoped original
verification contains matching `action_id`, `outcome: effective`,
`reviewer, basis, reviewed_at` and `evidence_ref` linking original completion
material. Review precedes verification, verification precedes closure, and closure
must be on time and no later than the source record. Missing verification/material
is a gap; an ineffective outcome cannot support closure. Adequacy, reviewer
independence and whether the action actually worked remain assessor decisions.

## Competence input

Policy supplies unique `required_assignments`, each with
`id, person_id, role, active_from, active_until, competence_required_by,
required_course_ids`. The roster
must describe active assignments at evaluation time, without duplicate
person/role entries. At most 2,000 total course requirements are accepted.

`data.completions` contains unique `{id, assignment_id, course_id, artifact_ref}`
rows, with one unambiguous completion per required assignment/course. Original
scoped artifacts contain matching `person_id, role, course_id`,
`outcome: demonstrated`, `completed_at, reviewed_at, valid_until`,
`reviewer, basis` and `evidence_ref` linking the original competence assessment.
Completion precedes review and both meet the explicitly approved competence
deadline; the qualification must remain valid at capture evaluation. The checker
does not invent a universal pre-employment training deadline. Missing records remain gaps; expired,
misattributed or attendance-only declarations cannot pass.

## Reporting, exports and remaining dependencies

Standard reports expose methods/versions, digests and all supported numerical
diagnostics. Period/class/group values use zero-based policy-order slots
(`window_0_accuracy_ppm`, `class_0_recall_ppm`, `confusion_0_1`,
`group_0_wilson_lower_ppm`). The corresponding private input preserves the exact
schedule and class/group mapping for the assessor. Public reports never expose
private labels, person IDs, course names, reviewer names or original documents.
Assessor packs retain originals, unchanged failures, missing evidence, checksums
and ZIP-only reproduction. Existing no-overwrite/rejection safeguards apply.

Customers must supply actual periodic results and an approved schedule; labelled
multiclass predictions; appropriate subgroup and independence keys; audit and
management-review schedules/findings/actions; active-role/course requirements;
and reviewed completion/competence material. Signed source/reviewer authentication,
GRC/LMS/monitoring connectors and native production stop/rollback, access,
retention and restore verification remain integration work. None is inferred from
record presence or these software-test fixtures.

Rationale: lifecycle monitoring and performance evidence in
[EU Article 72](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-72) and
[Article 15](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-15), plus
organizational governance and improvement in
[ISO's AIMS overview](https://www.iso.org/standard/42001). Algorithms, sample
minimums, cadences and thresholds here are engineering choices and reviewed
customer policy, not universal ISO/EU requirements. Licensed normative crosswalk
review and applicability remain assessor work.
