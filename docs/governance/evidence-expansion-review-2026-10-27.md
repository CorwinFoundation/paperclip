# Assessment evidence review — 27 October 2026

The largest immediate gain is to expose existing scanner results and preserve
their original evidence. The scanner already registers retry/idempotency,
partial-failure, checkpoint recovery, cancellation, human takeover, isolation,
data containment and provenance tests. Several were absent from the original
EU/ISO candidate associations. Adding duplicate prompts would provide less value
than making those observations usable in an assessment file.

ISO/IEC 42001 concerns an organization's AI management system. The EU AI Act
conformity route depends on the system, role and applicable risk requirements.
The Commission now reports EN 18286:2026 referenced in the Official Journal for
the Article 17 provisions it covers. An ISO certificate or a collection of scan
passes cannot establish the entire EU conformity case. This extension supplies
supporting evidence; applicability, documentary adequacy and normative mapping
remain assessor decisions.

Sources checked: [ISO's official overview](https://www.iso.org/standard/42001),
[Commission standardisation guidance](https://digital-strategy.ec.europa.eu/en/policies/ai-act-standardisation),
[Commission EN 18286 publication notice](https://digital-strategy.ec.europa.eu/en/library/first-harmonised-standard-supporting-ai-act-published-official-journal),
[CEN-CENELEC's publication](https://www.cencenelec.eu/news-events/news/2026/en-in-the-spotlight/2026-07-30-ai-quality-management),
[Article 15](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-15) and
[Article 72](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-72).
The licensed standards and their exact clause/Annex ZA crosswalk still require
review; no clause-level EN 18286 conformity declaration is added here.

## Review and implemented work

| Priority / gap | Evidence added or reused | Practical limit |
| --- | --- | --- |
| Existing findings not reaching the crosswalk | Explicit associations for actual registered grounding/factuality, retry/idempotency, schema/partial-failure, checkpoint, concurrent-isolation, cancellation, takeover, approval-freshness, containment, fairness and provenance families | Each result retains its tested domain, adapter and acquisition basis; it does not prove a whole control |
| Log payload corruption invisible to ID-only checks | Five new scoped diagnostic cases below | Requires trusted server-side log capture; never enabled by a client module path |
| Reports lacked an artifact inventory | Unique case/observation counts, original artifact fingerprints, version/method/diagnostic measurements and a document-review register | Public report metadata does not independently verify source bytes |
| Zero-model log captures rejected by the older importer | Schema-specific receipt/log reconciliation, explicit reviewed capture scope, original verdict and byte verification | Reviewed attribution is an operator declaration, not authentication of collection |
| Evidence scattered across files | Protected local ZIP export with original artifacts, input plan/bundle, assessment, checksums and CSV worksheets | No automatic documentary acceptance, certification, or public raw-transcript endpoint |

Mapping versions advance to `@2`. Existing completed reports retain their saved
mapping snapshots. New assessment plans must use the new mapping versions;
previous approvals are not silently carried over. Earlier generic concepts
(`grounded_policy` and `hallucination`) remain
usable for explicitly reviewed historical imports, but are labelled unresolved
legacy mapping concepts instead of appearing as physical unrun test families.
Actual registered case families, including authentication and delegated-authority
tests, are listed explicitly alongside them.

## Five new evidence-only logging cases

| Case | Measurement / positive control | Negative controls |
| --- | --- | --- |
| OPS-LOG-PAYLOAD | Typed nested payload, Unicode, booleans, numbers and null values survive persistence | Truncation, type coercion and changed content |
| OPS-LOG-TIME | Timezone-aware source event instants survive capture | Missing, naive, invalid and changed timestamps; equivalent UTC spellings remain valid |
| OPS-LOG-BATCH | Two independently identified batches can each be retrieved intact | A reader returning records from the other batch |
| OPS-LOG-REPEAT | Two immediate reads return the same event records, independent of delivery order | Destructive reads, omissions and changed second snapshots |
| OPS-ALERT-SELECTIVITY | Failure produces a correlated delivered alert at the configured recipient; success-only batch produces no delivered alerts | Silent, noisy and wrong-recipient alert paths |

The cases emit at most ten synthetic events and make at most two event reads
per case. Alert selectivity additionally reads two delivery captures. Snapshot
size and record count are bounded. They make no target-model or judge calls.
They run automatically in the normal full-registry flow only when the existing
trusted scoped logging factory is configured. Fixed packs and persisted scopes
are preserved. All seventeen operational cases remain report-only and excluded
from the public measured-test count and underwriting math.

These checks establish behavior only along the selected diagnostic path.
Immediate repeat reads do not establish statutory retention. Source-timestamp
preservation does not establish clock synchronization. Batch separation does
not establish tenant authentication. The report keeps those distinctions.

## Assessor evidence export

Use the existing approved-plan/import workflow, then export:

```sh
python -m tools.requirement_evidence --export-pack \
  --plan /secure/approved-plan.json --bundle /secure/bundle.json \
  --artifacts /secure/hash-addressed-artifacts --output /secure/assessment.zip
```

The exporter reuses the evaluator, includes only integrity-checked scoped
observations and reviewed document bytes, and preserves native originals
alongside their wrappers. Rejected/tampered bytes are omitted; rejection metadata,
failures, missing trials and missing documents remain visible. Control decisions
and accepted references are reproduced against the packaged bytes before the
ZIP is published. `CHECKSUMS.json` covers the members; it is not a digital
signature or independent collection attestation. Existing output files are
never overwritten. Treat the ZIP as private assessment material.

## Further evidence requiring customer inputs or another trusted adapter

| Candidate next step | Input needed before a reliable test can be run |
| --- | --- |
| Intended-use accuracy and robustness acceptance | Customer-approved labelled evaluation set, domain boundaries, subgroup definitions and acceptance thresholds; a generic math score cannot substitute |
| Dataset provenance and quality | Actual dataset/lineage manifests, collection permissions and a reviewed data-quality specification |
| Retention, access controls and recovery | Read-only scoped retention/access/restore interfaces, approved retention periods and independently recorded checkpoints |
| Longitudinal monitoring / corrective-action verification | Comparable deployments, pinned test versions, observation periods and approved change/incident records |
| Management-system evidence | AI scope/roles, policy approvals, training, impact/risk records, internal audits, management reviews and improvement actions supplied by the organization |
| Native oversight/stop/rollback | Provider-specific observable native controls; synthetic scanner tools do not establish customer business side effects |

These remain explicit gaps instead of being converted into superficial
document-presence passes. The next product layer can collect and route these
records for review using the document register and protected evidence pack.

## Validation plan

Exercise real local JSONL persistence, each corruption/negative control, missing
adapters, scope/recipient substitutions, native zero-model evidence import,
failed-source preservation, tampered-source omission, checksum verification,
no-overwrite behavior, normal async dispatch and durable restart round trips.
Verify public metadata filtering and HTML escaping, existing mapping snapshots,
all registry/count pins and unchanged score/cost boundaries. Heavy regression
and preflight queue on the existing isolated `backbond-ci` VM; routing,
local-CI opt-ins and paid execution capacity are unchanged.
