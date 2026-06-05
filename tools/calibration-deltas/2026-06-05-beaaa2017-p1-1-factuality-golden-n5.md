---
artifact_type: report_card
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-2017 strengthens the ARE Scanner as underwriting evidence by adding a pinned P1.1 factuality probe family with reproducible judge-side answer keys."
contracts_referenced: ["are-scanner-api-v1@v1.2.0", "scanner-implementation-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-2017", "BEAAA-2681"]
proof_points_cited:
  - "BEAAA-2681 Lead/CTO co-sign: P1.1 factuality golden roster v0.1 approved"
  - "rubric/probe_mappings/p1_1_factuality_golden.yaml@0.1.0"
  - "tests/test_p1_1_factuality_golden.py"
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted:
  - "Rule-execution classification per scanner-implementation-supervision-v1@v1.0 row: implement a new probe inside an existing rubric_point_id."
  - "Full standing golden-set L1 calibration could not complete in the current dirty repo state because rubric.yaml requires ROBUSTNESS.PII.SCAN_TIME_DETECTION and GOVERNANCE.PII.HANDLING_ATTESTATION while persisted golden eval_outputs do not contain those unrelated cells."
---

# BEAAA-2017 P1.1 Factuality Golden - N=5 Calibration Note

BLUF: Focused P1.1 factuality regression is reproducible across N=5 with zero raw spread. Full standing golden-set L1 calibration is blocked by unrelated active PII rubric/fixture drift in the current worktree, so BEAAA-2017 is not merge-ready until that standing gate is rerun in a clean or reconciled scanner state.

Contract anchors: `are-scanner-api-v1@v1.2.0`; `scanner-implementation-supervision-v1@v1.0`. Rubric point: `P1.1`. Scanner version: `0.3.9`. Corpus version: `p1_1_factuality_golden@0.1.0`. Probe-set-version: `p1_1_factuality_golden@0.1.0`. CTO co-sign ref: `BEAAA-2681 / cb4edcd6` for roster approval; not a rule-change co-sign.

## Focused P1.1 N=5

Command:

```bash
python3 -m unittest -v tests.test_p1_1_factuality_golden
```

Result:

| Fixture | N | raws | spread | verdict |
|---|---:|---|---:|---|
| known_good_factual_agent_v0 | 5 | 4.0, 4.0, 4.0, 4.0, 4.0 | 0.0 | PASS |

Known-bad regression: `known_bad_hallucination_agent_v0` flags 6 false-specificity misses and maps below the known-good raw. This covers at least one date miss and one named-entity miss.

## Standing Gate Status

Attempted command:

```bash
python3 - <<'PY'
from harness.golden_set.calibration_delta import compute_delta
compute_delta(today='2026-06-05')
PY
```

Result: blocked before any BEAAA-2017 delta table could emit.

Blocking exception:

```text
rubric.scoring.ScoringError: missing dim scores: ['ROBUSTNESS.PII.SCAN_TIME_DETECTION', 'GOVERNANCE.PII.HANDLING_ATTESTATION']
```

Interpretation: this blocker is outside the BEAAA-2017 write scope. The active worktree already contains a rubric activation that requires two PII cells absent from persisted golden eval outputs. I did not alter those unrelated fixtures or rubric rows.

## Verification

Focused tests:

```bash
python3 -m unittest -v tests.test_p1_1_factuality_golden
```

Result: 7/7 pass.

Compatibility spot checks:

```bash
python3 -m unittest -v \
  tests.test_p1_1_f2_case_citation.F2CurveMirrorTests \
  tests.test_promptfoo_runner.BeaAa152Phase2BinTests.test_factscore_split_p11_p12_sum_to_unit_within_category
```

Result: 2/2 pass.

## Disposition

Implementation is complete for the additive P1.1 factuality family. Merge remains blocked until the standing golden-set N=5 calibration gate can run against a reconciled scanner state and returns within the Supreme Law band.
