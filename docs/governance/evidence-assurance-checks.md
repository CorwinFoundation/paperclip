# Incident, supplier and impact-assessment evidence

Three conditional checks reuse the reviewed-record adapter, normal full-scan
capability selection, durable native artifacts, standard JSON/HTML reports,
approved-plan import and private reproducible assessor ZIPs. They make zero
target/judge calls and contribute zero score. The default catalog now contains
2,417 rows: 2,373 measured tests plus 44 conditional evidence cases in 28 families.

| Case | Reconciled evidence | Boundary |
| --- | --- | --- |
| EVID-INCIDENT-NOTIFICATION | Reviewed reporting decisions, escalation, submission and original delivery receipts | Reporting applicability, recipients, deadline instants and which event meets the deadline are reviewed customer decisions |
| EVID-SUPPLIER-ASSURANCE | Component digests/versions, current supplier approvals, responsibilities and original agreements | Inventory completeness is relative to supplied manifests; agreement adequacy and supplier authenticity require review |
| EVID-IMPACT-ASSESSMENT-COVERAGE | Intended uses, affected groups, risks, supplied changes and reviewed mitigation/acceptance decisions | Structural coverage does not establish adequate impact assessment, effective mitigation or a complete production change history |

New cases use `assessment-assurance-checks@1`; all earlier methods, capture versions
and measurement contracts remain unchanged. Both candidate crosswalks advance to
**@6**. Keep completed @5 assessments and their saved mapping snapshots; obtain a
reviewed @6 plan for new scope/case selections. These are engineering-draft
associations requiring licensed normative validation. Requirement conclusions
remain `not assessed`.

## Existing acquisition and shared boundaries

Use `assessment_record_factory(context)` / `read(case_id) -> bytes`, or the existing
`--records-only --records-directory ... --plan ...` operator flow. Reuse the
`assessment-record-input/v1` envelope from [record checks](evidence-record-checks.md)
and exact `data.system = {target_id, snapshot_sha256}` from
[validation checks](evidence-validation-checks.md). There is no public upload,
URL fetch, notification sending, customer module loading, production discovery,
source signing or synthetic fallback.

Original UTF-8 JSON is bounded to 512 KiB; individual lists and total method
reference traversals are bounded to 2,000. Artifact-map values retain original
bytes and SHA-256. Structured reviewed artifacts contain matching system scope,
`reviewer, basis, reviewed_at, evidence_ref`; the latter references original
supporting material in the same private artifact map. All observed/reviewed times
must be timezone-aware and no later than root `recorded_at`, followed by root
review and capture evaluation. Contradictory identities, links, chronology,
approval states and breached customer criteria fail. Missing records/material
remain inconclusive, unless independently observed deadline/policy failures apply.
Explicitly foreign target bytes are not retained.

## Incident notification input

`policy.required_incidents` is a nonempty reviewed list of unique
`{id, aware_at, escalation_due_at, decision_ref, required_notifications}`.
Each notification requirement has unique `id`, `recipient_id`, `due_at` and
`deadline_event: submitted|delivered`. Deadlines cannot precede awareness.

`decision_ref` names a reviewed scoped artifact containing matching `incident_id,
aware_at`, a true boolean `notification_required`, and exact
`required_notification_ids`. A reviewed `false` decision requires an explicitly
empty notification list. The decision must be reviewed after awareness. This
checker never decides whether an incident legally requires notification and never
substitutes a generic number of days for customer-reviewed applicable deadlines.
Immediate-reporting duties and substantive classification remain human review.

`data.incidents` contains `{id, escalated_at, notifications}`. Each notification is
`{id, submitted_at, delivery_ref}`. Original scoped delivery receipts contain
matching `incident_id, notification_id, recipient_id, submitted_at`,
`status: delivered`, `delivered_at`, and `evidence_ref` for original delivery
material. Awareness precedes escalation and submission; submission precedes
delivery. The chosen event must meet its approved deadline, with equality allowed.
Delivery proof is still required when submission is the deadline event.

Missing notifications/escalations remain counted. Overdue missing events fail
when their deadlines precede capture `evaluated_at`; pending events remain gaps.
Saved captures replay at their original evaluation time. Diagnostics include
required/delivered/missing/overdue counts, reviewed reporting/exemption counts,
and maximum awareness-to-submission/delivery seconds, rounded up. A source date
cannot freeze an open deadline for a new scan.

## Supplier assurance input

`data.system.components` is the existing nonempty component-ID to SHA-256 map.
`policy.required_components` has unique `{id, required_responsibilities}` rows
covering exactly that map, with nonempty unique responsibility IDs per component.
`data.components` has `{id, supplier_id, version, review_ref}` rows for that same
inventory. Missing actual rows are gaps; extra or omitted policy inventory entries
are contradictions. This does not discover running components.

Each scoped original review contains matching `component_id, supplier_id,
component_version, component_sha256`, `status: approved`, `owner`, `reviewed_at,
valid_until`, exact `responsibilities: [{id, owner}]`, nonempty unique
`agreement_refs`, reviewer/basis and `evidence_ref`. Referenced agreements and
supporting material must exist as original bytes. Review validity must follow
review and remain current at capture evaluation. Reusing an approval for another
version/digest or an expired/rejected approval fails. Responsibility/owner and
agreement counts are reported; identities and agreement contents remain private.
The declared approval validity does not independently interpret contract terms.

## Impact-assessment input

`policy.required_contexts` has unique `{id, intended_use_id, required_group_ids,
required_risk_ids, change_ids, required_by, review_due_at}` rows. Risks are nonempty;
group/change lists may be explicitly empty in reviewed scope. `required_by` is a
customer-approved completed gate, such as release acceptance, no later than the
source record. This checker does not independently establish deployment time.

`data.change_manifest_ref` names a reviewed scoped original with
`changes: [{id, changed_at, context_ids}]`. Each change has nonempty unique affected
contexts from the required policy; an explicitly empty changes list is allowed.
Each context's `change_ids` must equal its inverse links in this supplied manifest.
Change time precedes manifest review and the applicable impact assessment review.

`data.contexts` has `{id, assessment_ref}` rows. Each reviewed scoped assessment
contains matching `context_id, intended_use_id`, exact `group_ids, risk_ids,
change_ids`, `reviewed_at, valid_until, evidence_ref`, and unique risk-ID
`decisions`. Assessment review must meet both `required_by` and `review_due_at`
and remain valid at capture evaluation.

Each decision has `id` equal to its risk ID and either
`decision: mitigate, mitigation_refs: [...]` or
`decision: accept, acceptance_ref: ...`, without the other decision's field.
Each referenced scoped reviewed disposition binds the same `context_id, risk_id`,
contains supporting `evidence_ref`, and is reviewed by assessment review. Mitigation
decisions require `status: approved`; residual-risk decisions require
`status: accepted, valid_until` current at capture. This checks the documented
decision and its material, not whether the treatment works or the residual risk
is acceptable. Existing native-outcome binding checks supply a separate evidence
layer for treatment effectiveness.

## Reports, assessor exports and customer dependencies

Public standard reports expose the trusted method/version, original digests,
observation boundary, verdict and allowlisted integer diagnostics. Private names,
recipients, component IDs, intended uses, groups, reviewers, document text and
original bytes remain in private artifacts. Normal scan/restart and existing
native import recompute the same records; exports retain original failures,
gaps, checksums and ZIP-only reproduction. Existing tamper rejection and
no-overwrite behavior remain in force. Static records require one trial per case.

Customers must supply incident inventories, awareness/escalation history,
reviewed reporting decisions/deadlines and real delivery receipts; complete
component inventories, supplier approvals, role ownership and agreements; and
intended-use/group/risk scope, change manifests, impact assessments and mitigation
or residual-risk decisions. Independent completeness, source/reviewer
authentication, substantive legal/contract/impact review, ticketing/GRC/vendor
connectors and native production-control verification remain dependencies.
Software fixtures are qualification inputs, never customer certification evidence.

The rationale follows [EU Article 73](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-73),
[Annex IV](https://ai-act-service-desk.ec.europa.eu/en/ai-act/annex-4) and
[ISO's AIMS overview](https://www.iso.org/standard/42001). The reconciliation
contracts above are engineering choices and customer policy, not universal
ISO/EU algorithms or automatic certification decisions.
