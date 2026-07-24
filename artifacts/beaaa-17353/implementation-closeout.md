---
artifact_type: implementation_closeout
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
brand_backbone_applicable: false
focus_claim: "BEAAA-17380 supersedes the QA-rejected Wave 3 candidate with authority-signed target evidence without live spend, score motion, or promotion."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-17380", "BEAAA-17353", "BEAAA-17354", "BEAAA-17355"]
proof_points_cited: ["artifacts/beaaa-17353/candidate-identity.json", "artifacts/beaaa-17353/case-inventory.json", "artifacts/beaaa-17353/openrouter-authorization-contract.json", "artifacts/beaaa-17353/fail-closed-validation-packet.json", "artifacts/beaaa-17353/beaaa17353-n5-summary.json"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["Rule-execution remediation under existing reported-not-scored rubric_point_id. No scoring math, band cut, API shape, judge model, rubric weight, tamper-resistance reduction, shared-environment promotion, or production promotion changed. BEAAA-17355 exact-SHA QA APPROVED remains required before any promotion."]
---

# BEAAA-17380 Superseding Implementation Closeout

BLUF: rejected candidate `7d631a1c98c802f0b45259f4ab0b5765e2049872` is superseded by this Wave 3 target-evidence remediation packet. No live calls, merge to shared/prod, deploy, promotion, score activation, or underwriting activation occurred.

## Required Metadata

- `scanner_version`: `0.5.0`
- `corpus_version`: `system_prompt_extraction@0.1.0`
- `probe-set-version`: `system_prompt_extraction@0.1.0`
- `rubric_point_id`: `P3.3` / `ROBUSTNESS.SECRETS.SYSTEM_PROMPT_EXTRACTION`
- `N=5 calibration delta`: total `0`, pillars `0`, band `0`
- `cto_cosign_ref`: not required; rule-execution candidate-only implementation
- Contract: `are-scanner-api-v1@v1.3.0`

## Packet

- Identity: `artifacts/beaaa-17353/candidate-identity.json`
- Case inventory: `artifacts/beaaa-17353/case-inventory.json`
- OpenRouter authorization contract: `artifacts/beaaa-17353/openrouter-authorization-contract.json`
- Fail-closed validation: `artifacts/beaaa-17353/fail-closed-validation-packet.json`
- N=5 summary: `artifacts/beaaa-17353/beaaa17353-n5-summary.json`
- Full deterministic target evidence: `artifacts/beaaa-17353/system-prompt-extraction-candidate/`
- ADR: `artifacts/beaaa-17353/adr-wave3-system-prompt-extraction-candidate.md`

## Verification

- `pytest -q tests/test_beaaa16974_system_prompt_candidate.py tests/test_aiuc_path_b_lane_c_beaaa16272.py` -> `110 passed`.
- Target-evidence regeneration produced `100/100` unique case/seed rows across K=5, with `spe-20-canary` present in every seed and benign false positives `0/20`.
- Every row binds authorization ref/signature, endpoint, model, target ID, candidate identity, direct parent, tree, archive, corpus/config/evaluator/model hashes, retained target response hash, and immutable row hash.
- Authorization now uses `ed25519-wave3-target-authority-v0.2` with committed public key verification. No private key or OpenRouter secret value is committed.

## Handoff

After commit creation, comment `SUPERSEDED: 7d631a1c98c802f0b45259f4ab0b5765e2049872 -> <new SHA>` on canonical QA gate BEAAA-17355 and hand that exact SHA to QA. Promotion remains blocked until BEAAA-17355 records evidence-backed `APPROVED` for the exact superseding SHA.
