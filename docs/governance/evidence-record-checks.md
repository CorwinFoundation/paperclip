# Additional assessment evidence checks

Implementation scope: nine conditional cases, reusing the registered-case worker,
trusted diagnostic adapter, evidence store, durable manifests, standard JSON/HTML
reports and approved-plan assessor ZIP export. These cases make zero target or
judge requests, contribute zero score, and do not expand fixed packs or targeted
operations scopes. Unsupported capabilities remain unobserved in standard reports.
The previous 17 operational cases keep their identities and versions.

The original expansion used crosswalk version **@3** for both frameworks. The
[evaluation and release extension](evidence-validation-checks.md) adds nine cases
and advances new crosswalks to **@4**, while preserving these case versions.
The mapping remains an engineering
draft requiring licensed normative review. New plans must use the current @4
crosswalk; preserve previous completed assessments and original artifacts.
Mappings identify candidate evidence, and every requirement conclusion remains
`not assessed`. No case establishes certification or full conformity.

## Implemented cases and required inputs

| Case | What it checks | What the customer must supply |
| --- | --- | --- |
| OPS-LOG-INTERLEAVED | Two interleaved batches can each be retrieved without cross-batch mixing | Existing trusted diagnostic log adapter |
| OPS-LOG-EMPTY-LOOKUP | A never-emitted batch returns empty; a positive batch is also retrieved | Same adapter; this does not test authorization |
| OPS-LOG-BOUNDARY | Larger bounded typed, nested and Unicode payloads survive retrieval | Same adapter; this does not establish production size limits |
| EVID-RISK-TRACE | Unique IDs, owners, reciprocal risk/treatment links, assessment references and verified closure claims | Risk register, treatments, linked assessment/verification files and reviewed status declarations |
| EVID-DATA-LINEAGE | Artifact hashes, parent references/cycles, permission-file links and approved row-quality rules | Dataset versions, source/purpose declarations, permission records and actual JSON dataset rows |
| EVID-EVAL-RESULTS | Recomputed exact-match accuracy, source identity, case coverage and an approved minimum | Labelled dataset, actual predictions, target/model/probe version and approved threshold |
| EVID-SUBGROUP-COVERAGE | Required group sample counts and recomputed accuracy gap | Same labelled observations with appropriate group labels, approved groups/minimum size/maximum gap |
| EVID-CHANGE-REGRESSION | Comparable baseline/current dataset and probe bindings, ordered times and permitted accuracy drop | Two actual evaluation records on the same dataset and probe version, approved drop threshold |
| EVID-INCIDENT-CLOSURE | Incident/action links, owners, effective-verification references and closure chronology | Incident/action history and actual verification records; no closed incidents yields inconclusive |

The six record checks validate consistency of supplied material. Source
authenticity, completeness, permission legality, risk analysis quality, dataset
representativeness, subgroup suitability and effectiveness of actions require
human assessment. Open/accepted risks remain explicit counts. An all-open incident
register cannot demonstrate closure. There is no universal ISO/EU accuracy or
fairness threshold: thresholds are customer approved inputs. Exact-match metrics
are suitable only for tasks whose labels support exact comparison.

## Trusted adapter and normal scan flow

Use the existing `operational_log_capture_factory(context)` for the three new log
cases. It implements `emit(event)` and `read(batch_id)` on an authorized inert
diagnostic destination. Challenge reads are bounded to 20 records and 64 KiB of
canonical JSON; unrelated scope records are redacted before persistence.

For records, trusted server wiring supplies
`assessment_record_factory(context)` to `build_live_framework_workers` (or the
registered-case builder). Its adapter implements `read(case_id) -> bytes`.
`context` is exactly `{tenant_id, scan_id, agent_id}`. It must select the correct
tenant's reviewed records for this scan and return the original UTF-8 JSON bytes.
No customer-uploaded module, arbitrary URL, public raw-evidence endpoint or default
fixture is added. A missing factory excludes these families from normal selection;
a missing record from a configured adapter produces an inconclusive observation.
The customer portal/upload and native system connectors are still integration work.

Standard reports contain case/method/version, native artifact URI and digest,
verdict and integer measurements. Raw input documents, dataset rows and group
labels stay in private artifacts. The assessor importer rehashes original bytes,
recomputes measurements and preserves passes and failures. The ZIP includes the
original native artifact, whose `input_document.content_base64` decodes to the
exact original input file (also protected by its own SHA-256).

## Input contract

Each file is at most 512 KiB, including embedded artifacts, with at most 2,000 rows
per record list. JSON must have unique keys and finite numbers. These deliberate
bounds are validation limits, not claims about adequate statistical sample sizes.

```json
{
  "schema": "assessment-record-input/v1",
  "scope": {"tenant_id": "tenant", "scan_id": "scan", "agent_id": "agent"},
  "source_id": "reviewed-export-identifier",
  "recorded_at": "2026-10-27T20:00:00Z",
  "review": {"reviewer": "named-reviewer", "basis": "reviewed scope, source and policy", "reviewed_at": "2026-10-27T21:00:00Z"},
  "artifacts": {"local-reference": {"sha256": "64 lowercase hex characters", "content_base64": "original bytes encoded as base64"}},
  "data": {}
}
```

Review identities/bases are retained declarations, not authenticated attestations.
`recorded_at <= reviewed_at <= evaluated_at`; source event/evaluation/verification
times must not exceed `recorded_at`. The root scope must match the requested scope
exactly. Explicit fixture/mock/offline declarations are rejected as target evidence.
Unreviewed, wrong-scope or unreadable input, including an explicitly foreign
target inside a matching input wrapper, is not retained. Malformed scoped data
produces a failed reconciliation rather than a fabricated pass.

`artifacts` maps local reference IDs to embedded byte/digest pairs; no references
trigger fetching. Case-specific `data` contracts are:

- Risk: `risks` have `id, owner, status` (`open|accepted|closed`),
  `assessment_refs, treatment_ids`. `treatments` have `id, owner, risk_id, status`
  (`open|verified`); verified treatments also require `verification_refs`,
  `verification_outcome: effective`, `verified_at`. Closed risks link verified
  treatments. An accepted risk can explicitly have no treatment if it supplies
  `acceptance_refs, accepted_at`; the acceptance's substantive adequacy remains
  for review. All supplied treatment references must be reciprocal.
- Lineage: `datasets` have `id, version, source, purpose, permission_refs,
  parent_ids, artifact_ref, quality_rules`. The referenced JSON has `rows`.
  `quality_rules.fields` maps required column names to
  `string|integer|number|boolean|object|array`; `unique_id` selects a required
  non-null unique column. Roots explicitly declare empty `parent_ids`.
- Evaluation/group/change: `dataset_ref, current_ref, current_model_version,
  probe_version, policy`; change also uses `baseline_ref, baseline_model_version`.
  The observed model versions must match these reviewed input versions.
  Dataset JSON has `rows` with unique `id`,
  `expected`, and for subgroup checks `group`. Evaluation JSON has `target_id,
  model_version, dataset_sha256, probe_version, observed_at,
  declared_accuracy_ppm, results`; each result has unique `id, actual`.
  Predictions must cover the exact labelled case set. Accuracy is exact-match
  correct count times 1,000,000 divided by total, rounded down. Policy uses
  `minimum_accuracy_ppm`, or `required_groups, minimum_group_size,
  maximum_gap_ppm`, or `maximum_drop_ppm` respectively. PPM values are integers
  in 0–1,000,000. Baseline and current reference the same dataset digest and
  probe version; baseline time must precede current time.
- Incidents: `incidents` have `id, owner, status` (`open|closed`), `opened_at,
  action_ids`; closed entries also have `closed_at`. `actions` have `id, owner,
  incident_id, status`; actions supporting closure require `status: verified`,
  `verification_outcome: effective`, `verification_refs, verified_at` within
  incident opening/closure times. Referenced proof files must be supplied.

For operator-local record-only assessment, use an approved @3 plan containing
record cases, one trial per case (static inputs do not create independent trials):

```text
python scripts/run_operational_controls.py --records-only --records-directory reviewed-inputs --plan approved-plan.json --output new-assessment
```

Files are named `EVID-RISK-TRACE.json`, etc. In this mode their `scope` is the
approved plan's `{target_id, deployment_id, snapshot_sha256}`. The existing
`python -m tools.requirement_evidence --export-pack ...` command packages the
result after supplied documents are reviewed. Missing documents remain gaps.

## Work that still needs customer data or integrations

| Remaining evidence | Dependency |
| --- | --- |
| Meaningful real-world accuracy, bias, representativeness and regression results | Customer ground truth, appropriate outcome metrics, subgroup policy, actual target observations and comparable history; metric adapters for non-exact-match tasks |
| Complete lineage/permissions, risk-treatment effectiveness and incident closure | Authentic customer records, source-system attribution, substantive assessor review and optionally data-catalog/GRC/ticketing connectors |
| Production log retention, deletion, access restrictions, tamper resistance, clock synchronization and restore | Native logging/storage/IAM/backup integrations and longitudinal observation; diagnostic roundtrips cannot establish them |
| Real approval, emergency stop, handoff and business rollback | Customer tool/transaction integrations; existing scanner-mediated synthetic tools remain a bounded behavioral test |
| Continuous post-market monitoring and incident notification timeliness | Production telemetry, monitoring history, actual incident/delivery timestamps and approved recipients/processes |
| AIMS scope, policies, roles, competency/training, impact assessment, supplier controls, internal audit and management review | Customer organizational records and interviews; document adequacy cannot be certified by structural tests |
| Exact ISO/EN clause and harmonised-standard conformity | Licensed normative text, applicability/role/classification review and competent assessor validation; no automatic certificate |

This scope follows the distinction between organizational management-system
requirements in [ISO's overview](https://www.iso.org/standard/42001), lifecycle
performance in [EU AI Act Article 15](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-15)
and systematic monitoring in [Article 72](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-72).
Those public sources do not validate the draft clause-level crosswalk.

## Validation plan

Regression coverage exercises all nine cases with successful acquisition and
independent recomputation; reference/cycle/hash/type errors; fabricated metrics;
missing/foreign/fixture/unreviewed/future inputs; customer threshold and group
failures; native import and byte-preserving ZIP export; normal asynchronous
dispatch, database restart, public report privacy and unchanged score behavior.
Heavy tests and version-control preflight queue on the existing isolated CI VM
pool under the Founder spending policy. No routing, opt-ins or paid capacity change.
