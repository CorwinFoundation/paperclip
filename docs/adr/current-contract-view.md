---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This is the assembled entry point for current BackBond Scanner architecture decisions; the binding API contract remains authoritative."
contracts_referenced: ["are-scanner-api-v1@v1.0"]
ticket_refs: ["BEAAA-15620", "BEAAA-15604"]
proof_points_cited: ["docs/adr/README.md", "docs/adr/adr-0061-beaaa14835-release-reproducibility-gate.md", "docs/adr/adr-0064-beaaa15459-na-rating-boundary.md"]
version: 1.0
prior_version: null
self_audit_passed: true
escalations_noted: ["No contract synthesis overrides the signed contract; conflicts escalate to CTO and the contract owner."]
---

# BackBond Scanner current-contract view

## Precedence

This view is an index and synthesis, not a new contract. `contracts/are-scanner-api-v1.md@v1.0` remains binding. Company canon governs company facts and terminology. Accepted ADRs govern implementation decisions within that boundary. If they conflict, stop and escalate to the CTO and contract owner; do not hand-merge an interpretation.

## Current invariants

- Every released score carries pinned scanner, corpus, model, and seed provenance.
- Release evidence contains N=5 runs using seeds 0–4; total and every pillar must remain within the ±1 reproducibility band.
- Customer projections suppress the numeric BackBond Score when registry-backed measured coverage is incomplete; internal fixture diagnostics do not authorize an external rating.
- Scan evidence is append-only and write-once where the scan-log ADRs specify; shared SQLite connection access is serialized.
- Additive API and rescan behavior remains subordinate to the binding versioned contract; ADRs cannot silently change rubric, corpus, scoring, bands, or the contract surface.
- No code, build, or configuration is promoted without `APPROVED` from QA for the exact candidate SHA; public or production promotion additionally requires Founder sign-off.

## Active decision map

| Concern | Current ADR authority |
|---|---|
| Scan API, persistence, evidence pins, rescans | ADR-0009, ADR-0010, ADR-0017 and addendum |
| Calibration, rubric, scoring wiring | ADR-0012 through ADR-0028; ADR-0045, ADR-0048, ADR-0049 |
| Runner, tool interface, live probes | ADR-0030, ADR-0032, ADR-0043 |
| Gateway, rotation, post-bind evidence | ADR-0039, ADR-0046, ADR-0047 |
| Full-scan and factuality coverage | ADR-0050 through ADR-0054 |
| Lineage, dependencies, deployment, backups | ADR-0055 through ADR-0058 |
| Release reproducibility gate | ADR-0061 |
| Incomplete-measurement rating boundary | ADR-0064 |

## Maintenance

The CTO updates this file in the same change that accepts, supersedes, or retires an ADR. New decisions must link their originating issue, contract impact, evidence, rollback, and required metadata. Historical files remain in `docs/adr/`; status or supersession is expressed in the record, not by scattering patches across directories.
