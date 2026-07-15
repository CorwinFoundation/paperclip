---
artifact_type: report
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This report makes the Retell real-agent BackBond Scanner evidence count auditable through canonical source control without changing scoring rules."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-14134", "BEAAA-14132", "BEAAA-13914", "BEAAA-14094", "BEAAA-14095", "BEAAA-14096", "BEAAA-14097"]
proof_points_cited: ["artifacts/beaaa-14134/reissued/row_manifest.jsonl", "artifacts/beaaa-14134/reissued/count_report.json", "scripts/beaaa14134_reissue_retell_cases.py verify"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["BEAAA-14096 remains excluded because its source rows record are-scanner-api-v1@v1.3.0 while BEAAA-14134 is anchored to are-scanner-api-v1@v1.0.", "FAL audit reference file agents/cmo-os.md was not present in this workspace; artifact is internal-bound and not external-bound."]
---

# BEAAA-14134 Retell Reissue Closeout

BLUF: canonical reissue accepts `820` Retell real-agent rows and excludes `270` rows. All `1,090` rows are attempted, terminal, and evidenced, but BEAAA-14096 remains excluded because those rows record `are-scanner-api-v1@v1.3.0` while BEAAA-14134 is anchored to `are-scanner-api-v1@v1.0`.

## Classification

Rule-execution under `scanner-implementation-supervision-v1@v1.1`: provenance reissue and verifier for existing Retell rows under existing rubric point IDs. No BackBond Score is emitted. No scoring math, rubric weight, judge model, API output shape, corpus semantics, or tamper-resistance posture changes.

## Count Report

| Source | Attempted | Terminal | Evidenced | Accepted | Excluded | Reason |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| BEAAA-14094 | 275 | 275 | 275 | 275 | 0 | Contract-clean source rows reissued through canonical manifest/verifier path |
| BEAAA-14095 | 275 | 275 | 275 | 275 | 0 | Contract-clean source rows reissued through canonical manifest/verifier path |
| BEAAA-14096 | 270 | 270 | 270 | 0 | 270 | Source rows record `are-scanner-api-v1@v1.3.0`; task anchor is `are-scanner-api-v1@v1.0` |
| BEAAA-14097 | 270 | 270 | 270 | 270 | 0 | Contract-clean source rows reissued through canonical manifest/verifier path |
| **Total** | **1,090** | **1,090** | **1,090** | **820** | **270** | Honest lower count; no contract mismatch waiver taken |

Remaining gap after this reissue: `270`.

## Committed Evidence

| Artifact | Purpose |
| --- | --- |
| `scripts/beaaa14134_reissue_retell_cases.py` | Generates and verifies the reissue manifests; no score emission |
| `tests/test_beaaa14134_retell_reissue.py` | CI-facing verifier test |
| `artifacts/beaaa-14134/source_archives/*.tar.gz` | Four prior source archives preserved as immutable inputs |
| `artifacts/beaaa-14134/reissued/case_manifest.jsonl` | Case-level manifest for 1,090 Retell cases |
| `artifacts/beaaa-14134/reissued/row_manifest.jsonl` | Row-level gate manifest with accepted/excluded disposition |
| `artifacts/beaaa-14134/reissued/run_metadata.json` | Non-secret run metadata and required Scanner metadata |
| `artifacts/beaaa-14134/reissued/source_archive_manifest.json` | SHA-256 and size for each source archive |
| `artifacts/beaaa-14134/reissued/count_report.json` | Machine-readable count report |

## Verification

Local verifier:

```bash
python3 scripts/beaaa14134_reissue_retell_cases.py verify
# {"counts": {"accepted": 820, "attempted": 1090, "evidenced": 1090, "excluded": 270, "terminal": 1090}, "status": "pass"}
```

Required CI includes:

```bash
pytest tests/test_beaaa14134_retell_reissue.py
```

## Required Metadata

| Field | Value |
| --- | --- |
| scanner_version | `1.2.0` |
| corpus_version | `scanner-test-registry-baseline-2026-07-13` |
| probe-set-version | `beaaa14134-retell-real-agent-reissue@0.1.0` |
| rubric_point_id | Existing row-level Retell rubric point IDs in `row_manifest.jsonl` |
| N=5 calibration delta | Not applicable / zero score impact: no score emitted and no scoring, rubric, judge, API behavior, corpus semantics, or tamper-resistance change |
| cto_cosign_ref | Not required; rule-execution |
| deployed_digest | Deploy not applicable: evidence-only source-control reissue, no runtime code change |

## Self-Audit

- Deliverable: report and verifier for CTO/Auditor that make Retell real-agent rows countable only when contract-clean and canonical provenance gates are met.
- Contract anchor: `are-scanner-api-v1@v1.0`.
- Canonical terms: BackBond and BackBond Scanner used; grandfathered contract identifier retained only as a contract reference.
- FAL audit: blocked by missing `agents/cmo-os.md` reference file in this workspace; no external-bound copy was produced.
- Escalation: no Underwriter or CTO co-sign gate triggered; BEAAA-14096 exclusion is recorded rather than waived.
