---
artifact_type: blocker_packet
owner: scanner_engineer_3
persona_audience: internal
ticket_refs: ["BEAAA-8127", "BEAAA-8123"]
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
contract_anchor: "are-scanner-api-v1"
classification: "rule-execution intended; execution blocked before implementation"
canonical_terms_only: true
scanner_version: null
corpus_version: null
probe-set-version: null
rubric_point_id: ["P1.1", "P1.2", "P1.3", "P1.4", "P1.5", "P1.6"]
n5_calibration_delta: "not run - no live self-owned external target and required Scanner reference files unavailable"
cto_cosign_ref: null
brand_backbone_verified: "not applicable - internal artifact"
self_audit_passed: false
---

# BEAAA-8127 OPS live-fire blocker packet v0.1

## BLUF

BEAAA-8127 is not shippable in this heartbeat. I could not read the required Scanner OS reference files, and the issue context does not provide a self-owned external agent target/API surface for P1.1-P1.6 live-fire execution. Per the issue guardrails, I did not emit measured subscores, did not convert any `r1_placeholder`, and did not run fixture-only output as live evidence.

## Deliverable definition

This is a blocker packet for the CTO that prevents the OPS live-fire probes P1.1-P1.6 from emitting fabricated or unpinned live measurements while preserving the `are-scanner-api-v1` output shape.

## Intake classification

Intended work class: rule-execution, because the requested work wires probes inside existing `rubric_point_id` values P1.1-P1.6 without adding, retiring, reweighting, or changing scoring math.

Current disposition: blocked before implementation. If the eventual N=5 golden delta is greater than +/-1 on any golden agent, this must be reclassified as rule-change and gated to CTO + Underwriter.

## Verified local state

- Required local references missing from the checkout:
  - `canon/company-readme.md`
  - `canon/glossary.md`
  - `canon/org-chart.md`
  - `canon/house-style.md`
  - `canon/escalation-matrix.md`
  - `canon/artifact-metadata-schema.md`
  - `contracts/are-scanner-api-v1.md`
  - `contracts/scanner-implementation-supervision-v1.md`
  - `agents/cto-os.md`
- Present implementation substrate:
  - `engineering/probe-sdk/`
  - `engineering/probe-sdk/src/are_probe/`
  - `tools/calibration-deltas/`
- Present build-plan context:
  - `BEAAA-8123_coverage_build_plan_v0.1.md`
  - Parent issue BEAAA-8123 heartbeat context.

## Missing unblock inputs

1. CTO must restore or provide the canonical Scanner reference files listed above in the project workspace, or provide a traceable equivalent source that satisfies the Scanner OS read-before-task requirement.
2. CTO or Scanner program owner must provide the self-owned live external agent target for OPS execution, including target shape (`generic_prompt_output`, `botpress_webchat`, or `transcript_export`), endpoint/path, auth handling, and confirmation that functional-use-only constraints are satisfied.
3. CTO must confirm the corpus/source pins for the OPS substrates before measurement:
   - P1.1 TruthfulQA corpus pack/version/sha256/upstream commit.
   - P1.2 HaluEval QA corpus pack/version/sha256/upstream commit.
   - P1.3 HaluBench corpus pack/version/sha256/upstream commit.
   - P1.4 custom refusal corpus pack/version/sha256/upstream commit, shared with P3.1/P1.4 golden logic.
   - P1.5 confidence calibration metric and seed policy.
   - P1.6 derivation rule from actual live coverage run.

## Non-actions taken

- No `r1_placeholder` was reported as measured.
- No score was fabricated.
- No N=5 delta was claimed.
- No contract output shape was changed.
- No external-bound asset was drafted.

## Next executable step after unblock

Once the required references and self-owned target are present, wire the first OPS probe family under P1.1 against the live target, run seeds 0-4, verify spread 0 on total and every subscore, then write the first per-dim calibration delta under `tools/calibration-deltas/`.
