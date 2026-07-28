---
artifact_type: status_report
owner: scanner_engineer
persona_audience: internal
phase: development
canonical_terms_only: true
focus_claim: "BEAAA-18467 reconciles every active BackBond Scanner release-identity surface to the activated are-scanner-api-v1@v1.3.0 contract while preserving historical evidence and score behavior."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-18467", "BEAAA-18466"]
proof_points_cited: ["tests/test_contract_pin_reconciliation_beaaa18467.py", "artifacts/beaaa-18467/2026-07-28-83472cb8b127c27f816414ef5aba09b368de7c07-vs-HEAD.json", "implementation commit 72fe1fd"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# BEAAA-18467 Contract-Pin Reconciliation

BLUF: Active release identity now resolves to `are-scanner-api-v1@v1.3.0`; the focused suite passes and the canonical golden N=5 comparison reports zero change to every measured total and pillar.

## Decision and scope

This is a release-identity bug fix for QA that will eliminate the active v1.0/v1.3.0 pin contradiction within the four-hour budget.

- Classification: **rule execution**, per the CTO ruling on BEAAA-18466 and the `scanner-implementation-supervision-v1@v1.1` table row for a behavior-preserving refactor/metadata alignment.
- Contract anchor: `are-scanner-api-v1@v1.3.0`.
- Scoring, rubric, judge, corpus bytes, output shape, and tamper-resistance posture: unchanged.
- Rollback: revert implementation commit `72fe1fd`, mark this N=5 and QA evidence superseded, and do not rewrite historical evidence or tags.

The change removes stale active identity pins without changing the calculation they describe. A focused parity test now binds the activated contract document, runtime constant, release manifests, active golden fixtures, N=5 report generator, CLI consumer, and full-scan consumers to one contract identifier.

## Required operational metadata

- `scanner_version`: `0.5.0`
- `corpus_version`: `0.1.0` (Scanner invocation pin from `scanners.CORPUS_VERSION`, as required by BEAAA-18467)
- `release_gate_corpus_version`: `0.2.0` (existing canonical N=5 harness pin; unchanged)
- `probe-set-version`: `retell-consolidated-probe-set@2026-06-24` (inherited from canonical master)
- `golden_probe_set_version`: `golden_agents_v1`
- `rubric_point_id`: `none / release identity only`
- `N=5 calibration delta`: `0.0` for total, GOVERNANCE, ROBUSTNESS, BIAS_FAIRNESS, and OPS on G1, G2, G3, M1, M2, M3, B1, and B3
- `cto_cosign_ref`: not required; CTO classified BEAAA-18467 as rule execution on BEAAA-18466
- `implementation_commit`: `72fe1fd`
- `upstream_eval_version`: not applicable

B2 remains the existing explicit v1 exclusion because its approved target model returned 404 before score output. No B2 score or historical evidence was changed.

## Verification

The regression test was added first and failed twice: runtime still resolved to v1.0, and active golden fixtures still resolved to v1.0.

```text
pytest -q tests/test_contract_pin_reconciliation_beaaa18467.py
2 failed
```

After reconciliation:

```text
pytest -q tests/test_contract_pin_reconciliation_beaaa18467.py \
  tests/test_backbond_scanner_entrypoint.py \
  tests/test_verdict_envelope_beaaa796.py \
  tests/test_release_authority_beaaa16991.py \
  tests/test_release_metadata_runtime_parity_beaaa15699.py \
  tests/test_full_scan_executor_beaaa13681.py \
  tests/test_full_scan_reference_agents_beaaa13803.py
69 passed
```

Canonical N=5 command:

```text
python3 scripts/release_gate_n5.py \
  --baseline 83472cb8b127c27f816414ef5aba09b368de7c07 \
  --candidate 72fe1fd \
  --phase development \
  --today 2026-07-28 \
  --out-dir artifacts/beaaa-18467
```

The gate wrote the complete Markdown and JSON evidence and the JSON records `passed: true`. Its final CLI print then raised `ValueError` because the relative `--out-dir` could not be made relative to the absolute repository path; this occurred after evidence generation and did not affect calibration execution or results.

| Agent | Δ total | Δ GOV | Δ ROB | Δ BIAS | Δ OPS |
|---|---:|---:|---:|---:|---:|
| G1 | 0 | 0 | 0 | 0 | 0 |
| G2 | 0 | 0 | 0 | 0 | 0 |
| G3 | 0 | 0 | 0 | 0 | 0 |
| M1 | 0 | 0 | 0 | 0 | 0 |
| M2 | 0 | 0 | 0 | 0 | 0 |
| M3 | 0 | 0 | 0 | 0 | 0 |
| B1 | 0 | 0 | 0 | 0 | 0 |
| B3 | 0 | 0 | 0 | 0 | 0 |

## Changed files

- `api/full_scan_executor.py`
- `release/backbond-scanner-release.json`
- `scanner-os/release-metadata.toml`
- `scanners/__init__.py`
- `scripts/release_gate_n5.py`
- six active `tests/fixtures/golden_agents/*/expected_score.json` files
- active contract-pin assertions in five existing test modules
- `tests/test_contract_pin_reconciliation_beaaa18467.py`
- `tools/full_scan_reference_agents.py`

Historical raw evidence, score results, prior calibration reports, and tags are unchanged. The June 22 N=5 report remains byte-for-byte pinned to v1.0 and is asserted as pre-reconciliation historical evidence.

## Self-audit and FAL

- Supreme Law: reproducibility and evidence completeness pass; all N=5 score and pillar deltas are zero.
- Canonical language: clean in new prose; grandfathered contract identifiers are used only as contract identifiers.
- Carrier Rule: clean; no customer or carrier is named.
- FAL Focus: pass. This internal artifact supports continuous Scanner evidence by reconciling its release identity.
- FAL Alignment: pass. It makes no new product, coverage, scoring, or risk-transfer claim.
- FAL Linkage: pass. It links the canonical BackBond Scanner and activated contract to reproducibility evidence.
- External Brand Backbone gate: not applicable; `persona_audience` is internal.
