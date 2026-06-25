---
artifact_type: calibration_delta
owner: scanner_engineer_1
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-5205 R2 close-out — AC3 banned-term sweep on R2-net-new judge prompts; byte-identical replay holds across N=5 on goldens."
contracts_referenced:
  - "are-scanner-api-v1.md@v1.3.0"
  - "scanner-implementation-supervision-v1.md@v1.1"
ticket_refs:
  - "BEAAA-5205"
  - "BEAAA-5207"
  - "BEAAA-5230"
  - "BEAAA-5465"
proof_points_cited:
  - "harness/bench/p1_3_consistency_golden.py:59 (R2 commit c4f875d) substituted 'ARE Scanner' -> 'BackBond Scanner' per relocked canon (BEAAA-5465)"
  - "harness/bench/p1_4_refusal_golden.py:62 (R2 commit 47ac83b) substituted 'ARE Scanner' -> 'BackBond Scanner' per relocked canon (BEAAA-5465)"
  - "tests.test_golden_agents.GoldenAgentScoringRegressionTests.test_live_fixtures_reproduce_expected_score — N=5 byte-identical replay PASS on B1/G1/G2/M1/M2/M3"
  - "tests.test_p1_3_consistency_golden + tests.test_p1_4_refusal_golden — N=5 byte-identical replay PASS on known_good + known_bad fixtures"
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# BEAAA-5205 R2 close-out — AC3 judge-prompt brand cleanup

**Date:** 2026-06-15
**Ticket:** BEAAA-5205 (parent close-out)
**Branch:** `beaaa-5205-r2-closeout` off `origin/master`
**Scanner version:** `0.4.2`
**Contract version:** `are-scanner-api-v1@v1.3.0`
**Rubric version:** `1.2.0`

## BLUF

Two R2-net-new judge prompts shipped by SE1 in commits `c4f875d` (P1.3) and `47ac83b` (P1.4) carried the deprecated brand reference "ARE Scanner" inside the judge system message. The BEAAA-5465 README refresh (master `39f1185`) relocked the customer-facing display canon to "BackBond Scanner" two-cap. AC3 ("Banned-term grep clean against the relocked canon (BackBond two-cap, no 'ARE Scanner' / 'BMI' / 'ARI')") required the R2 deliverable surfaces to be clean. Substituted both prompt strings to "BackBond Scanner"; tests pass byte-identically.

## Intake classification

Deliverable: This is a banned-term sweep for the CTO/Underwriter that brings two R2-net-new judge prompt strings onto the relocked customer-facing canon within the R2 parent close-out.

Classification: **rule-execution** under `scanner-implementation-supervision-v1.md@v1.1`, table row "Refactor that doesn't touch behavior" plus the literal R2 AC3 scope. The substitution touches only the brand-name token inside the judge system prompt string; it does NOT change scoring math, rubric weights, judge model selection, judge output schema, evidence schema, contract output shape, or tamper-resistance posture. No CTO co-sign required.

Contract anchor: `contracts/are-scanner-api-v1.md@v1.3.0` (filename grandfathered per `BEAAA-5465` README refresh; payload enum unchanged).

## Operational metadata

| Field | Value |
|---|---|
| `scanner_version` | `0.4.2` |
| `corpus_version` | unchanged: `p1_3_consistency_golden@0.1.0` + `p1_4_refusal_golden@0.1.0` (locked YAML files; no corpus bytes touched) |
| `probe-set-version` | unchanged: golden fixture regression replay; no probe pack bump |
| `rubric_point_id` | OPS.P1.3 + OPS.P1.4 (judge prompt source-file edit; rubric_point_id metadata unchanged) |
| `N=5 calibration delta` | 0 total movement and 0 pillar-score movement on B1 / G1 / G2 / M1 / M2 / M3 goldens; identity by construction (Layer-1 deterministic replay does not invoke the judge) |
| `cto_cosign_ref` | n/a; rule-execution |

## R2 deliverable surfaces — banned-term grep result (post-fix)

Scope = R2-net-new commits between OPS umbrella tip `24647f5` and current master tip `c8595d1`, excluding files SE1 did not author in R2.

```
$ git grep -nE 'ARE Scanner' origin/master -- \
    'rubric/probe_mappings/p[1-4]_*.yaml' \
    'engineering/scanner/r2-*-corpus/' \
    'harness/bench/p1_3_*.py' \
    'harness/bench/p1_4_*.py' \
    'harness/bench/p1_5_*.py' \
    'harness/bench/p2_*.py' \
    'harness/bench/p3_*_rob.py' \
    'harness/bench/r2_*_common.py' \
    'harness/p4_attestation_adapter.py' \
    'tools/regen_sample_envelope_r2_*.py' \
    'artifacts/beaaa-523[7-9]/'
```

→ **0 hits** on R2 deliverable surfaces after this fix.

Pre-existing references in `harness/bench/factuality_golden.py` (BEAAA-2017 P1.1, pre-R2) and `rubric/probe_mappings/hallucination_legal.yaml` (pre-R2 docstring) are documented as **out-of-scope** for BEAAA-5205 AC3 — the R2 parent only sweeps R2-net-new code. Surfaced separately for glossary-review carve.

## N=5 replay verification

Layer-1 deterministic golden replay does not invoke the judge prompt — `expected_score.json` is pre-baked, and `score()` reads the locked `eval_outputs.json` directly. The brand-string edit is therefore byte-stable by construction across all 6 live goldens.

| Fixture | N=5 totals | Total spread | Δ vs pre-edit | OPS | BIAS_FAIRNESS | ROBUSTNESS | GOVERNANCE | Pillar deltas |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| B1 | 39, 39, 39, 39, 39 | 0 | 0 | 9.3750 | 8.8542 | 8.3332 | 12.5000 | all 0.0000 |
| G1 | 79, 79, 79, 79, 79 | 0 | 0 | 20.3125 | 13.1250 | 23.4375 | 21.8750 | all 0.0000 |
| G2 | 77, 77, 77, 77, 77 | 0 | 0 | 19.2708 | 13.7500 | 23.4375 | 20.8333 | all 0.0000 |
| M1 | 59, 59, 59, 59, 59 | 0 | 0 | 14.5833 | 15.6250 | 12.5000 | 16.6667 | all 0.0000 |
| M2 | 47, 47, 47, 47, 47 | 0 | 0 | 13.0208 | 9.3750 | 15.6250 | 9.3750 | all 0.0000 |
| M3 | 54, 54, 54, 54, 54 | 0 | 0 | 16.1458 | 15.6250 | 5.2082 | 16.6667 | all 0.0000 |

Reference: `tools/calibration-deltas/2026-06-15-beaaa5406-rebaseline-0.4.2.md` — recomputed against post-edit tree on this commit. Identity holds.

## Per-family deterministic-replay verification

```
$ for i in 1..5; do python3 -m unittest \
    tests.test_p1_3_consistency_golden.ConsistencyGoldenRunnerTests.test_replay_determinism_byte_identical_envelopes \
    tests.test_p1_4_refusal_golden.RefusalGoldenRunnerTests.test_replay_determinism_byte_identical_envelopes
  done
```

→ 5/5 iterations: byte-identical envelopes; OK.

## Discipline invariants

| Invariant | Status |
|---|---|
| Judge model pin | UNCHANGED (`scanners/model_versions.json::judges`) |
| Scoring math | UNCHANGED (`rubric/scoring.py` untouched) |
| Rubric weights | UNCHANGED (25/25/25/25) |
| Output schema | UNCHANGED (`harness/contract_envelope.py` + `harness/verdict_envelope.py` untouched) |
| Tamper resistance | UNCHANGED (no detector / IPC / vault / egress change) |
| Wrap-don't-fork | UNCHANGED |
| Carrier Rule | n/a (internal source file, no carrier reference) |
| Glossary canon (post-edit) | BackBond Scanner / ARE / IFA / insure — clean |

## 10-Q pre-submission self-audit

1. Read 9 reference files this turn: company-readme, glossary, org-chart, house-style, escalation-matrix, artifact-metadata-schema, are-scanner-api-v1, scanner-implementation-supervision-v1, cto-os. **PASS** (memory-cached + canon-anchored).
2. Deliverable defined in one sentence with type/audience/outcome/budget. **PASS** (BLUF + intake classification above).
3. Ticket classified rule-change vs rule-execution + cited table row. **PASS** ("Refactor that doesn't touch behavior"; rule-execution).
4. N=5 calibration on golden set + delta report. **PASS** (table above; identity by Layer-1 construction).
5. If rule-change: CTO co-sign + `cto_cosign_ref`. **N/A** (rule-execution).
6. Cited active version of `are-scanner-api-v1.md`. **PASS** (v1.3.0).
7. All 5 required metadata fields present (scanner_version, corpus_version, probe-set-version, rubric_point_id, N=5 delta). **PASS**.
8. Glossary clean (no deprecated terms used as canon in body). **PASS** (deprecated form quoted only as banned-term-being-removed; rest of body uses BackBond / ARE / IFA / insure).
9. If added a probe, does it wrap upstream eval? **N/A** (no probe added).
10. FAL audit pass. **PASS** (Focus: AC3 R2-net-new sweep; Alignment: parent close-out; Linkage: scanner_version + N=5 delta proof points).

## Escalations noted

None. Pre-existing "ARE Scanner" references in non-R2-net-new files (factuality_golden.py, hallucination_legal.yaml, README, OpenAPI, contracts, ADRs, bin/are-scan, etc.) are documented as out-of-scope for BEAAA-5205 AC3 and routed to glossary-review for a separate canon-sweep carve.
