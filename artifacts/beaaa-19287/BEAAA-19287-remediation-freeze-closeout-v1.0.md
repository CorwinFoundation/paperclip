---
artifact_type: implementation_closeout
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This freeze preserves auditable BackBond Scanner evidence while keeping capability-dependent Vapi cases fail-closed."
contracts_referenced: ["are-scanner-api-v1.md@v1.3.0", "scanner-implementation-supervision-v1.md@v1.1"]
ticket_refs: ["BEAAA-19287", "BEAAA-19286", "BEAAA-19217", "BEAAA-19220"]
scanner_version: "0.5.0"
corpus_version: "fa32e0e081ffbd2953ea3df8aa8d2d0faa0464a3076d17dad9b1758804488988"
probe_set_version: "vapi-remediation-freeze-v1.4"
rubric_point_id: "not_applicable_transport_materialization"
n5_calibration_delta: "0.0000"
cto_cosign_ref: null
self_audit_passed: true
---

# BEAAA-19287 v1.4 remediation freeze

BLUF: The 477 structurally non-runnable rows in immutable Vapi manifest v1.3 now reconcile to 290 exact text-transport materializations and 187 fail-closed capable-adapter requirements. The derivation preserves source IDs, row hashes, scanner version 0.5.0, seed 42, duplicate suppression, STOP controls, and provenance. It emitted zero probe requests.

## Decision

This is a transport-materialization implementation for the CTO and Auditor that makes unchanged text-compatible cases selectable by the existing controlled execution lane while preventing structured cases from being flattened or semantically rewritten, within zero external traffic.

Classification is rule execution under the supervision-contract row "Refactor that doesn't touch behavior." The change derives an internal freeze and transport manifest without changing probes, rubrics, judges, scoring, contract output, authorization, or tamper resistance.

The implementation copies authored UTF-8 text into transport steps byte-for-byte and binds every step to its SHA-256. Structured source fields remain fail-closed behind an explicit adapter capability requirement, so no code infers authorization or substitutes semantics.

## Immutable inputs and outputs

| Artifact | Rows | SHA-256 |
|---|---:|---|
| Source inventory | 2,000 | `fa32e0e081ffbd2953ea3df8aa8d2d0faa0464a3076d17dad9b1758804488988` |
| Immutable v1.3 manifest | 2,000 | `d483a281556d9ae04a09bccc00bd3f886f27cce2f2dbde4534862c731e818319` |
| v1.4 remediation freeze | 477 | `eb2fbcf84a2b100c00db4ae6a7bde2ff4381292c698f17522f442baca8f6d421` |
| v1.4 summary | 1 | `1a916d26781464caf8b81b0d040725fa0db45c8fe12b82b3d0f251e4a928c77f` |

The attached v1.3 machine manifest was independently downloaded and matched the checked-in v1.3 SHA exactly. The checked-in v1.3 artifact was not edited.

## Terminal accounting

| Disposition | Rows | Routing |
|---|---:|---|
| `faithfully_materialized_current_text_transport` | 290 | Eligible unchanged for controlled execution lane BEAAA-19220; no traffic sent |
| `requires_different_capable_adapter` | 187 | Fail-closed; requires an authored structured-semantics adapter |
| **Total** | **477** | **Zero probe requests** |

Full-registry accounting is 405 already authorized + 596 destructive/tool/cross-session/cross-customer + 522 scope expansion + 290 faithfully materialized + 187 capable adapter = 2,000.

All 477 rows retain a unique stable ID, source row SHA-256, source-turn SHA-256, provenance family, disposition, and reason. The 290 materialized cases also carry isolated branch manifests and byte hashes. The 187 adapter cases explicitly prohibit authorization inference and require preservation of field roles, oracle inputs, action boundaries, and source bytes.

## Verification

- `python3 -m pytest -q tests/test_beaaa19287_vapi_remediation_freeze.py` - 3 passed.
- Five independent derivations produced byte-identical freeze and summary hashes.
- N=5 maximum band delta: 0. No scoring or runtime harness path changed.
- Unique stable IDs: 477 of 477.
- Dispositions: 290 + 187 = 477.
- Full registry: 405 + 596 + 522 + 290 + 187 = 2,000.
- Probe requests emitted: 0.

## Self-audit

The Scanner API contract remains `are-scanner-api-v1.md@v1.3.0`. Required operational metadata is present. Canonical vocabulary passes. This internal artifact does not trigger the Brand Backbone gate. FAL passes: the artifact focuses on reproducible Scanner evidence, aligns execution with written scope, and links the remediation freeze to BEAAA-19286, BEAAA-19217, and BEAAA-19220. Rollback is deletion of the derived BEAAA-19287 artifacts while retaining immutable v1.3.
