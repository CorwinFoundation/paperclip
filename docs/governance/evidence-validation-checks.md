# Evaluation and release evidence validation

Nine conditional cases extend the reviewed-record adapter in five areas. They
reuse normal scan dispatch, native artifacts, durable manifests, standard JSON
and HTML reports, approved-plan import and assessor ZIP export. They make zero
target/judge calls and contribute zero score. There are now 35 conditional
evidence cases; the published measured-test count remains 2,373.

The new cases use `assessment-evidence-validation@1`. The six earlier record
cases keep `assessment-record-checks@1`, their methods and exact metric shape.
Both framework crosswalks advance to **@4**. Preserve completed @3 assessments;
new scope/case selections require a reviewed @4 plan. These are engineering-draft
candidate associations, not licensed clause-level conformity determinations.
Requirement conclusions remain `not assessed`.

## Cases

| Case | Reconciled evidence | Important limit |
| --- | --- | --- |
| EVID-RISK-TEST-COVERAGE | Reviewed per-risk observation requirements versus original native results | A complete set may contain failed tests; completeness is not effectiveness |
| EVID-MITIGATION-RESULT-BINDING | Treatment/closure claims versus actual required outcomes and explicit residual-risk decisions | Accepted failed results stay failed and exceptions remain counted |
| EVID-SPLIT-OVERLAP | Content fingerprints across approved dataset partition pairs | Only supplied content is compared; unavailable vendor pretraining remains unassessed |
| EVID-SUBJECT-INDEPENDENCE | Shared subjects across the same partition pairs | Customer-approved subject keys and partition policy determine the interpretation |
| EVID-CLASS-ERROR-RATES | Binary confusion counts, abstentions, class error rates and balanced accuracy | Labelled binary tasks only; missing class denominators are inconclusive |
| EVID-STATISTICAL-SUPPORT | Wilson 95% binomial accuracy bounds and approved acceptance criteria | Requires reviewed independent Bernoulli assumptions and one record per supplied subject |
| EVID-PERFORMANCE-CLAIM-CONSISTENCY | Reviewed exact-match claims versus the same release, population, dataset and conditions | Unsupported metrics/populations are inconclusive; document adequacy is human review |
| EVID-CHANGE-IMPACT-COVERAGE | Complete declared component diff versus impact review and required observations | Completeness is relative to the supplied manifests, not discovered production state |
| EVID-RELEASE-ACCEPTANCE | Exact-snapshot release decision, outcomes, exceptions and deployment chronology | Approver identities remain declarations, not cryptographic authentication |

The registered family IDs are in `tools/assessment_test_catalog.py`. Each new case
has its own conditional family so existing one-case record identities remain
compatible. Unsupported adapters remain unobserved. A configured adapter with
unavailable or foreign input produces inconclusive output without retaining
foreign private bytes. Contradictory scoped data yields failed reconciliation.

## Shared input and acquisition

Use the existing `assessment_record_factory(context)` and `read(case_id) -> bytes`
contract from [record checks](evidence-record-checks.md). No public upload, module
path, URL fetching or synthetic fallback is introduced. Operators can also use
the existing `--records-only --records-directory ... --plan ...` CLI. Original
UTF-8 JSON is at most 512 KiB with strict keys/finite numbers and bounded lists.
Partition checks additionally cap total supplied rows across all partitions at
2,000. Embedded artifacts retain exact original bytes and SHA-256 values.

The existing input envelope contains `schema`, exact `scope`, `source_id`,
`recorded_at`, `review`, `artifacts` and `data`. New cases require
`data.system = {target_id, snapshot_sha256}`. The target must match the scan or
plan; a plan's snapshot must match too. With normal scan context, snapshot
attribution is a reviewed declaration. All bound records must agree with it.
This does not discover or authenticate actual production configuration.

Root record/review times retain their existing ordering. All source observation
times must be timezone aware and no later than `recorded_at`. Examples used by
regression tests are explicitly controlled software fixtures in
`tests/assessment_validation_fixtures.py`; they are never configured as customer
defaults or certification material.

## Risk and native observation contract

`data.observations` contains unique rows with `id, artifact_ref,
snapshot_sha256, source_sha256, job_id, agent_id, test_id, case_id, version_pin,
seed, probe_set_version, trial_index, observed_at, reviewer, basis` and, for
operational sources, the existing reviewed `capture_scope` binding. Supply the
native artifact under `artifact_ref` in the input artifact map. Alert evidence
also uses the existing approved `alert_recipient_id`.

The existing registered-source verifier rechecks original bytes, measured-source
requirements, case/version/seed, original verdict and operational captures.
Explicit fixtures, unresolved outcomes and relabelled duplicate native identities
cannot supply verification. These nine meta-validation cases cannot recursively
verify themselves as target observations. A source's snapshot/time attribution
remains the reviewer's declaration where the native source does not attest it.

`data.risks` rows have `id, status` (`open|closed|accepted`) and nonempty
`required_observation_ids`. Mitigation binding additionally requires
`treatment_ids`; `data.treatments` have `id, risk_id, status` (`open|verified`)
and `verification_observation_ids` drawn from the risk's requirements. Verified
treatments and closed risks cannot depend on failed or missing observations.
Unlinked treatments fail reconciliation. An accepted risk may have no treatment,
but requires `exception_ref`.

An exception artifact has the same `target_id, snapshot_sha256`, its `context_id`
(the risk ID, or `release`), nonempty `observation_ids`, `reviewer, basis,
approved_at, valid_until`. Risk exceptions are checked at the record time;
release exceptions must be approved by release approval and valid at deployment.
Exceptions never change original verdicts. Diagnostic counts for required/missing/
failed observations count requirement references, so the same observation can
contribute to more than one risk/treatment reference.

## Partition contract

`data.datasets` rows have `id, role` (`train|tune|validation|test`) and
`artifact_ref`. The artifact contains `rows` with unique `id`, original JSON
`content`, and a nonempty string field named by `policy.unit_field`.

`policy.comparison_pairs` contains unique unordered `{left, right}` dataset ID
pairs, including a held-out test partition. `allowed_reference_sha256` is an
explicit list of approved shared reference fingerprints, possibly empty. A
fingerprint is SHA-256 of sorted-key, compact, ASCII JSON for `content`; changing
the row ID cannot evade comparison. Approved shared references are counted
separately and do not waive shared-subject restrictions. This method does not
perform fuzzy matching or establish label-preserving preprocessing.

## Metric and claim contracts

Reuse existing `dataset_ref, current_ref, current_model_version, probe_version`
fields and their exact-match label/result contract. Current evaluation artifacts
add matching `target_id, snapshot_sha256`. Dataset coverage, original accuracy,
model/probe binding and timestamps are rechecked before further measurements.

For class errors, policy includes two distinct string `classes`, a member
`positive_label`, distinct `abstention_label`, `abstention_policy`
(`reject|count_as_incorrect`), `maximum_positive_error_ppm`,
`maximum_negative_error_ppm`, `minimum_balanced_accuracy_ppm`. Evaluation records
declare `declared_false_positive_rate_ppm`, `declared_false_negative_rate_ppm`
and `declared_balanced_accuracy_ppm` for reconciliation. Raw FP/FN rates count
the respective incorrect binary predictions; positive/negative error rates also
count abstentions for the corresponding actual class. Balanced accuracy includes
all supplied labels in the class denominators. Abstentions never disappear from
accuracy denominators. A missing class is inconclusive, with `missing_class_count`.

For statistical support, policy selects `method: wilson95-binomial`,
`sampling_assumption: independent_bernoulli`, a reviewed `sampling_basis`,
`unit_field`, `minimum_independent_count`, `minimum_accuracy_lower_ppm` and
`maximum_interval_width_ppm`. Current records declare `declared_wilson_lower_ppm`
and `declared_wilson_upper_ppm`. Lower bounds round down to PPM and upper bounds
round up. Repeated subject keys or unsupported assumptions yield inconclusive
output with dependency/assumption diagnostics. This is not a universal ISO/EU
confidence rule, sampling minimum, or proof that supplied subjects are independent.

For claims, policy supplies nonempty `required_claim_ids`, explicit
`required_limitation_refs` (possibly empty), and `maximum_overstatement_ppm`.
`data.claims` rows contain `id, document_ref, location, target_id,
snapshot_sha256, model_version, probe_version, dataset_sha256, conditions_sha256,
metric, population, value_ppm`. Supported declarations use
`metric: exact_match_accuracy_ppm` and `population: all_supplied_rows`.
`conditions_sha256` must match the original current evaluation artifact. Link
original document bytes and a reviewed extraction location; no automated prose
interpretation is performed. Claims greater than measured accuracy plus approved
tolerance fail; conservative numerical claims are permitted. Missing required
claims/limitations remain errors, and unsupported metrics/populations are gaps.

## Change and release contract

Current `system` and `data.baseline_system` declare the same target and different
snapshot digests, plus `components` maps from component IDs to original content
digests. `data.changes` rows have unique `id, component_id, before_sha256,
after_sha256, changed_at, affected_risk_ids, required_observation_ids`. Explicit
null before/after values represent added/removed components. Reconcile the full
declared component-map diff, not merely model version. Every changed component
needs an impact disposition and required observations after its change time.

`impact_review_ref` names an artifact with matching target/snapshot,
`reviewer, basis, reviewed_at, covered_change_ids, covered_risk_ids`. Impact review must cover the
declared changes and their affected risk IDs, with change time no later than review. Release acceptance adds
`release_record_ref`, naming matching target/snapshot, `approver, basis,
approved_at, released_at, required_observation_ids, exceptions`.

Approval follows impact review and all required observation times; deployment
follows approval and is no later than root record time. The release-required set
must equal the union from changed components. `exceptions` maps required
observation IDs to exception artifact references. Failed required outcomes need
those explicit decisions and remain visible in diagnostics/original evidence.
The checker does not determine whether a change is legally substantial.

## Reports, exports and customer dependencies

Standard reports reuse the trusted case catalog and integer diagnostic allowlist.
They expose methods/versions, observation scope, results, digests, counts, bounds
and applicable numerical criteria. Raw records, subject IDs, class labels,
documents and embedded native originals stay in private evidence. The assessor
importer recomputes the new measurements and exports original bytes and verdicts;
the existing ZIP-only reproduction, checksums, rejection and no-overwrite guards
apply. Missing customer inputs cannot become a technical pass or a certification
conclusion.

Customer prerequisites are a reviewed risk/test matrix and actual native results;
real split manifests/content/subject keys; appropriate labels and predictions with
approved class/statistical policies; original instructions plus reviewed claims;
and complete system/change/impact/approval/deployment records. Customer portal,
native connectors and authenticated reviewer-key infrastructure remain integration
work. Source authenticity, suitability of assumptions/thresholds, documentary
adequacy and licensed ISO/EN crosswalk review remain human assessment.

The rationale follows [EU Article 9](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-9),
[Article 10](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-10),
[Article 13](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-13),
[Article 17](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-17) and
[Annex IV](https://ai-act-service-desk.ec.europa.eu/en/ai-act/annex-4), alongside
[ISO's AIMS overview](https://www.iso.org/standard/42001). The specific algorithms
and acceptance criteria above are engineering choices and reviewed customer input.
