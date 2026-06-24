---
artifact_type: other
owner: scanner_engineer_3
persona_audience: internal
phase: 2
canonical_terms_only: true
focus_claim: "BEAAA-8138 prevents incomplete OPS live-fire smoke evidence from being mistaken for complete BackBond Scanner measurement."
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-8138", "BEAAA-8127", "BEAAA-8123", "BEAAA-8130"]
proof_points_cited:
  - "artifacts/beaaa-8127/p1_1_p1_6_ops_live_fire_n5.json"
  - "artifacts/beaaa-8127/2026-06-24-beaaa8127-ops-live-fire-smoke.md"
  - "tools/calibration-deltas/2026-06-24-beaaa8127-ops-p14-live-fire-n5.md"
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted:
  - "Rule-execution classification under scanner-implementation-supervision-v1.md v1.1: implement probes inside existing rubric_point_id values / smoke test for existing operation."
  - "Blocked, not shippable: full OPS runner and corpus pins are absent; P1.2 remains inconclusive in existing evidence."
brand_backbone_verified: "not applicable - internal artifact"
---

# BEAAA-8138 Remaining OPS live-fire disposition v0.1

## BLUF

BEAAA-8138 is not closeable as measured. The workspace contains valid non-acceptance smoke evidence for P1.1, P1.3, P1.4, P1.5, and P1.6, plus accepted P1.4 N=5 live-fire evidence, but it does not contain an executable full OPS P1.1-P1.6 runner or the pinned upstream corpus files required to convert the remaining dimensions into release-grade measurements. P1.2 is explicitly inconclusive in the only P1.1-P1.6 evidence bundle and must stay uncovered.

## Deliverable definition

This is a blocker disposition for CTO / Scanner program ownership that prevents BEAAA-8127 OPS P1.1-P1.6 smoke output from being reported as complete BackBond Scanner state under `are-scanner-api-v1@v1.0`.

## Classification

Rule-execution, not rule-change, under `scanner-implementation-supervision-v1.md@v1.1` classification rows:

- "Implement a new probe inside an existing rubric_point_id."
- "Smoke test for an existing operation."
- "Wrap an upstream eval framework as a probe" once TruthfulQA / HaluEval / HaluBench wrappers are actually pinned and executable.

No rubric point, pillar weight, judge-model choice, scoring math, tamper-resistance posture, or contract output shape is changed by this disposition.

## Current evidence state

| Rubric point | Existing evidence | Current disposition |
| --- | --- | --- |
| `P1.1` | Smoke-only, `limit: 1`, `full_acceptance_mode: false` in `p1_1_p1_6_ops_live_fire_n5.json` | Not release-grade measured |
| `P1.2` | Explicit `status: inconclusive`, `raw: null`, verdict `BREACH` in `p1_1_p1_6_ops_live_fire_n5.json` | Uncovered |
| `P1.3` | Smoke-only, `limit: 1`, `full_acceptance_mode: false` | Not release-grade measured |
| `P1.4` | Accepted P1.4 live-fire replay evidence in `tools/calibration-deltas/2026-06-24-beaaa8127-ops-p14-live-fire-n5.md`; spread 0 | Measured for P1.4 only |
| `P1.5` | Smoke-only, `limit: 1`, `full_acceptance_mode: false` | Not release-grade measured |
| `P1.6` | Derived from smoke coverage only; P1.2 uncovered | Not release-grade measured |

## Verified blockers

- `scripts/beaaa8127_ops_live_fire.py` is referenced by the smoke artifact but is absent from `scripts/` and from the workspace file tree.
- The expected BEAAA-152 dataset files cited by the public probe ledger are absent from this workspace:
  - `artifacts/beaaa-152/datasets/tqa_n100.json`
  - `artifacts/beaaa-152/datasets/halueval_qa_n300.json`
  - `artifacts/beaaa-152/datasets/halueval_dialogue_summ_general_n300.json`
- HaluBench corpus material was not found in the workspace.
- The only locally verified OPS-adjacent corpus SHA is the shared refusal corpus:
  - `engineering/probe-sdk/corpus/refusal_robustness_v0.1.jsonl`
  - `sha256: ef919312913e9e19197427faf4ba54ade3788e446dcc7fa16960ced92fb2b3a5`

## Non-actions taken

- No `r1_placeholder` evidence was converted to measured evidence.
- No fabricated P1.2 score was emitted.
- No full P1.1-P1.6 N=5 acceptance was claimed.
- No golden-set delta was claimed for the missing full runner.
- No external-bound asset was drafted.

## Required unblock action

CTO / Scanner program owner must provide or restore the executable full OPS live-fire runner and pinned corpus packs for P1.1, P1.2, P1.3, and P1.5 before BEAAA-8127 can close:

- TruthfulQA pack version, sha256, upstream commit.
- HaluEval QA pack version, sha256, upstream commit.
- HaluBench pack version, sha256, upstream commit.
- Confidence calibration metric, seed policy, and corpus pin.
- A full acceptance command that runs seeds 0-4 against the self-owned Retell target, grades via transcript replay, emits no placeholder evidence for measured dimensions, and produces N=5 spread 0 plus golden-set delta <= +/-1.

## Self-audit

- Read all required Scanner reference files this turn from `scanner-os/`.
- Canonical terms checked against `scanner-os/canon/glossary.md`.
- Contract anchor: `are-scanner-api-v1@v1.0`.
- Required metadata present where known; missing full-run metadata is named as blocker rather than invented.
- FAL / house-style pass for internal artifact.
