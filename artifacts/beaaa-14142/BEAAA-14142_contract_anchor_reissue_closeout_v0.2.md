---
artifact_type: closeout_receipt
owner: cto
persona_audience: internal
phase: 2
canonical_terms_only: true
focus_claim: "BEAAA-14142 restores the remaining BackBond Scanner real-agent evidence only through canonical GitHub PR, CI, and merge reachability."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-14142", "BEAAA-14096", "BEAAA-13914", "BEAAA-14143"]
proof_points_cited: ["artifacts/beaaa-14142/reissued/row_manifest.jsonl", "artifacts/beaaa-14142/reissued/count_report.json", "scripts/beaaa14142_verify_contract_anchor.py verify", "tests/test_beaaa14142_contract_anchor.py"]
version: 0.2
prior_version: "artifacts/beaaa-14142/BEAAA-14142_contract_anchor_reissue_closeout_v0.1.md"
self_audit_passed: true
escalations_noted: ["Board correction accepted: artifact-only and deploy-not-applicable do not waive BEAAA-13914 gate 7. This closeout requires PR/CI/merge reachability before the 270 rows count."]
---

# BEAAA-14142 Contract-Anchor Reissue Closeout v0.2

BLUF: v0.1 is superseded. The 270 Retell executions are real and gates 1-6 are supported, but they count only after this branch lands through a clean GitHub PR, passing CI, and merge to `master`. This packet adds the sanitized 270-row manifest/index and verifier to the authoritative `are-scanner` repo.

## Result

| Field | Value |
| --- | --- |
| Parent ticket | `BEAAA-13914` |
| Source excluded ticket | `BEAAA-14096` |
| Reissue ticket | `BEAAA-14142` |
| Target identity | `retell:agent_1e7015ae5f87ea825a3d1fac11` |
| Contract version | `are-scanner-api-v1@v1.0` |
| scanner_version | `1.2.0` |
| corpus_version | `scanner-test-registry-baseline-2026-07-13` |
| seed | `0` |
| Accepted rows after PR/CI/merge | `270` |
| Excluded rows | `0` |
| Source archive SHA-256 | `c8ac1fb7190d12ab8f72326c0caf0efeafa73abac73a8f0cae110bf874c44ce1` |
| Source manifest SHA-256 | `7a57dc5eeecf34cda605d74b1dd566ac0cfa995a864c991213b46596dd88fb3d` |

## Committed Evidence

| Artifact | Purpose |
| --- | --- |
| `artifacts/beaaa-14142/source_archives/BEAAA-14142_contract_anchor_reissue_bundle_v0.1.tar.gz` | Immutable source archive containing the signed manifest, closeout, and 270 raw Retell traces |
| `artifacts/beaaa-14142/reissued/row_manifest.jsonl` | Sanitized row-level manifest with contract anchor, target identity, evidence hashes, flags, and accepted disposition |
| `artifacts/beaaa-14142/reissued/case_manifest.jsonl` | Sanitized case index |
| `artifacts/beaaa-14142/reissued/source_archive_manifest.json` | Source archive SHA and source manifest SHA |
| `artifacts/beaaa-14142/reissued/run_metadata.json` | Non-secret run metadata and BackBond Scanner metadata |
| `artifacts/beaaa-14142/reissued/count_report.json` | Machine-readable count report |
| `scripts/beaaa14142_verify_contract_anchor.py` | Verifier for archive reachability, contract consistency, flags, target identity, and counts |
| `tests/test_beaaa14142_contract_anchor.py` | CI-facing pytest wrapper |

## Count Report

| Family | Accepted |
| --- | ---: |
| `autonomous_decision_consistency` | 45 |
| `direct_prompt_injection` | 45 |
| `restricted_action_refusal` | 90 |
| `synthetic_support_scope` | 90 |

| Verdict | Count |
| --- | ---: |
| `pass` | 177 |
| `fail` | 93 |

## Verification

Local verifier:

```sh
python3 scripts/beaaa14142_verify_contract_anchor.py verify
# {"counts": {"accepted": 270, "attempted": 270, "evidenced": 270, "excluded": 0, "terminal": 270}, "status": "pass"}
```

CI-facing test:

```sh
python -m pytest tests/test_beaaa14142_contract_anchor.py -q
```

## Gate 7 Position

Board correction is binding: artifact-only and deploy-not-applicable do not waive gate 7. The gate-7 cure for these rows is the GitHub PR itself, its passing CI, and merge to `master`. Until then, BEAAA-13914 remains at `910` verified and these `270` remain pending.

## Fixture / Mock Non-Counts

None counted. The sanitized manifest rejects localhost, `127.0.0.1`, mock, echo, fixture, replay, `LOCAL_TARGET_RESPONSE`, prewritten, or metadata-only markers and requires target identity `retell:agent_1e7015ae5f87ea825a3d1fac11`.

## Rollback

Revert this PR. BEAAA-13914 returns to `910` verified and the 270 BEAAA-14142 rows remain excluded.
