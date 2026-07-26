---
artifact_type: provenance_manifest
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This bundle gives the canonical QA gate one explicit evidence index for the July 31 BackBond Scanner candidate without making certification, production, pricing, or coverage claims."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-17962", "BEAAA-17386", "BEAAA-17387", "BEAAA-17956", "BEAAA-17963", "BEAAA-17383", "BEAAA-16992", "BEAAA-17795"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["Production-only monitoring, backup, restore, and rollback rows are not-evaluated and owned by BEAAA-17387 under Founder development mode."]
---

# BEAAA-17386 Acceptance Evidence Bundle

BLUF: this is the consolidated evidence index for the canonical QA gate. It does not approve QA, promote production, claim AIUC certification, or create an insurance decision.

- Scanner version: `0.5.0`
- Corpus version: `2026.6.24`
- Contract: `are-scanner-api-v1@v1.0`
- Candidate SHA: late-bound by `BEAAA-17963`
- Allowed states: `passed`, `failed`, `not-evaluated`, `not-applicable`
- Validator: `python3 tools/validate_beaaa17962_acceptance_bundle.py artifacts/beaaa-17962/beaaa-17386-acceptance-evidence-bundle.json`

## Assessment Rows

| Row | Scope | State | Owner | Evidence basis |
|---|---|---|---|---|
| `REL.IDENTITY.CANDIDATE_SHA` | core release ops | `not-evaluated` | `BEAAA-17963` | Integrated immutable SHA is late-bound by the integration child. |
| `REL.MANIFEST.AUTHORITY` | core release ops | `not-evaluated` | `BEAAA-17963` | Single release manifest must be proved after integration. |
| `CI.UBUNTU.ZERO_FAILURES` | core release ops | `not-evaluated` | `BEAAA-17963` | Clean Ubuntu CI must run on the integrated candidate. |
| `CI.LOCKS.PIP_CHECK` | core release ops | `not-evaluated` | `BEAAA-17963` | Frozen locks and isolated pip-check proofs belong to the integration child. |
| `AIUC.MAPPING.REGISTRY` | AIUC-1-mapped technical assessment | `passed` | `BEAAA-17383` | `artifacts/beaaa-17383/aiuc-mapped-coverage-ledger-v0.1.md`, `sha256:388e93f49d1e0c62f3f22d83b0c3ab02171dd75cb99323440e5ea5ea800d36de`. |
| `BBX.REGISTRY.SEPARATION` | BackBond Extension | `passed` | `BEAAA-17383` | Same ledger separates BackBond Extensions from mapped-test subtotals. |
| `API.PORTAL.JSON_PDF.PARITY` | BackBond Scanner | `passed` | `BEAAA-16992` | `artifacts/beaaa-16992/production-controls-evidence.json`, `sha256:7df179bcb3dbda21299ed2fd1c04fdc1e27d21b2b4e3ced967d897e1e755e2e5`. |
| `EVIDENCE.INTEGRITY.TAMPER` | BackBond Scanner | `passed` | `BEAAA-17795` | `artifacts/beaaa-17795-r9-bddafe24/verification.json`, `sha256:346aa74627c5d728fa980565f95d25cd0f0632771394ceaa032b426a3fd4546c`. |
| `CLAIM.BOUNDARY` | core release ops | `passed` | `BEAAA-17962` | The JSON bundle sets certification, insurance-decision, and production-promotion claims to false and is validator-gated. |
| `TENANT.ISOLATION.RETENTION` | BackBond Scanner | `passed` | `BEAAA-16992` | Development evidence packet records tenant isolation, retention, authz, and rate-limit checks. |
| `SECURITY.CRITICAL_HIGH` | BackBond Scanner | `passed` | `BEAAA-16992` | Development evidence packet records critical/high security findings absent. QA still verifies final integrated SHA. |
| `MONITORING.PRODUCTION_ONLY` | production only | `not-evaluated` | `BEAAA-17387` | Founder development mode defers production canary monitoring assessment. |
| `BACKUP_RESTORE.PRODUCTION_ONLY` | production only | `not-evaluated` | `BEAAA-17387` | Development dry-run evidence is not converted into production pass evidence. |
| `ROLLBACK.PRODUCTION_ONLY` | production only | `not-evaluated` | `BEAAA-17387` | Production rollback timing belongs to the promoted immutable image canary. |

## Validator Coverage

The validator rejects:

- blank or non-canonical assessment states;
- passed rows without a reproducible command and `sha256:<64 hex>` artifact hash;
- AIUC certification, compliance, or official-score claims;
- BackBond Extension rows that mix AIUC mapped-row wording;
- production-only monitoring, backup/restore, or rollback rows that are not anything other than `not-evaluated` under `BEAAA-17387`.

FAL audit: pass. The artifact reinforces the locked Focus by preserving the Scanner evidence chain that design-partner and QA decisions depend on; it does not broaden the promise into certification, production promotion, pricing, or insurance coverage.
