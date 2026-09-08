<!--
owner: Legal Coordinator
artifact_type: external_asset
status: draft
updated_at: 2026-09-05
source_refs: [BEAAA-29867, BACKBOND_COMPANY_CANON.md v1.4, protection-path-status.md 2026-08-01]
claim_or_decision: Rules for publishing Agent Scan results about third-party systems.
verification: Checked against the issue's authorized work-order text and current company canon; exact external release still requires Founder approval.
approvals: [Founder approval required before publication]
version: v0.1
-->

# Third-party publication policy

BackBond does not publish scan results that identify a third-party server or its operator unless that operator has opted in to named publication.

With the operator's opt-in, BackBond may publish only:

- the decision word: `block`, `review`, or `no_blocking_finding`;
- applicable rule IDs and severity;
- the ruleset version and manifest hash;
- the scan date; and
- a correction channel.

This is a metadata match between an operator-staged manifest and a versioned ruleset. It does not certify or verify that a server, agent, operator, or tool is safe or secure. BackBond does not label a third party unsafe, malicious, vulnerable, certified, verified, safe, or secure based on this process.

BackBond may publish aggregate counts, histograms, and patterns only after removing server identifiers and tool names. Identity-free summarizer output is the maximum level of detail allowed. Raw manifests, enriched CSV files, and the raw manifest archive remain private. BackBond uses those materials only to reproduce a result or resolve a dispute.

Before any row-level data is released, every field and table is checked for rare cells and rare combinations. A cell is rare when fewer than 10 rows in the corpus share it. Rare cells and combinations are suppressed or generalized until at least 10 corpus rows share the released value. Compliance reviews and signs the release, engineering supplies the cell counts, and the Auditor checks the released rows against the threshold.

## Corrections

An operator may request a correction through the false-positive issue form. The request should identify the publication, scan date, manifest hash, disputed rule ID, requested correction, and contact details for follow-up. BackBond will provide a first response within 72 hours. If a correction is accepted, BackBond adds a separate changelog line. The original aggregate snapshot remains unchanged.

## Disclosure required on every derived publication

> This publication reports a metadata match against a versioned ruleset using operator-staged inputs. It is not a certification or verification of safety or security. Named results appear only with the operator's opt-in. Aggregate results remove server identifiers and tool names and apply a minimum cell size of 10. Raw manifests and enriched records remain private for reproducibility and dispute resolution. Corrections are recorded separately from the original aggregate snapshot. Submit a correction through the linked false-positive issue form.
