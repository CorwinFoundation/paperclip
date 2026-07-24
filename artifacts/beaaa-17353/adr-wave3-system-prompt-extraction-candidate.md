---
artifact_type: adr
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
brand_backbone_applicable: false
focus_claim: "BEAAA-17380 supersedes the QA-rejected Wave 3 secrecy-control candidate with authority-signed target evidence without moving score, pricing, or underwriting eligibility."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-17380", "BEAAA-17353", "BEAAA-17355", "BEAAA-17352", "BEAAA-16974", "BEAAA-16940"]
proof_points_cited: ["evals/aiuc_path_b/system_prompt_extraction.py", "tests/test_beaaa16974_system_prompt_candidate.py", "artifacts/beaaa-17353/candidate-identity.json", "artifacts/beaaa-17353/case-inventory.json", "artifacts/beaaa-17353/fail-closed-validation-packet.json"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["No CTO co-sign required for this rule-execution remediation under scanner-implementation-supervision-v1@v1.1 row 'Implement a new probe inside an existing rubric_point_id' plus smoke/regression work. QA gate BEAAA-17355 remains required before promotion; no scoring activation, contract output-shape change, judge model, band cut, or tamper-resistance reduction shipped."]
---

# ADR: Wave 3 System-Prompt Extraction Candidate

BLUF: supersede QA-rejected commit `7d631a1c98c802f0b45259f4ab0b5765e2049872` with the zero-paid, candidate-only Wave 3 target-evidence remediation for `system_prompt_extraction`.

## Decision

Keep the reviewed BEAAA-16974 system-prompt extraction family reported-not-scored and replace the rejected target-evidence packet with authority-signed authorization plus strict target-response-derived verdicts. The candidate remains non-promoted: no live OpenRouter calls, no score activation, no underwriting activation, no signing/root-retention claim, and no public claim.

## Candidate Identity

- Rejected SHA: `7d631a1c98c802f0b45259f4ab0b5765e2049872`.
- Superseding SHA: recorded on BEAAA-17380 and BEAAA-17355 after commit creation.
- Identity packet: `artifacts/beaaa-17353/candidate-identity.json`.
- Touched implementation files: `evals/aiuc_path_b/system_prompt_extraction.py`, `tests/test_beaaa16974_system_prompt_candidate.py`.
- Touched evidence files: `artifacts/beaaa-17353/`.

## Reasoning

The family maps to existing `P3.3` / `ROBUSTNESS.SECRETS.SYSTEM_PROMPT_EXTRACTION`, so this is rule execution under `scanner-implementation-supervision-v1@v1.1`. The candidate derives deterministic `disclosed_hidden_instruction: bool` from retained target response text; missing, non-boolean, ambiguous, duplicate, missing, tampered, replayed/expired, wrong-endpoint/model/key, canary-loss, and secret-leakage conditions fail closed.

The OpenRouter contract is pinned by reference only: endpoint `https://openrouter.ai/api/v1/chat/completions`, model `openai/gpt-oss-120b`, credential ref `paperclip-secret:OPENROUTER_API_KEY`, session boundary, tenant boundary, system policy ref, retention schema, and 100-call cap. Authorization uses Ed25519 public-key verification; credential values and private signing keys are not stored.

## Calibration

N=5 delta is total `0`, pillars `0`, band delta `0`. Basis: the candidate is `reported_not_scored`, does not import the scoring engine or band math, and does not alter contract output shape.

## Rejected Alternatives

- Activate scoring in this issue: rejected; Underwriting and QA gates must adjudicate exact SHA first.
- Make live OpenRouter calls here: rejected; issue budget explicitly requires zero paid/live target calls.
- Add a new rubric point: rejected; existing P3.3 covers system-prompt/secret extraction resistance.

## Rollback

Revert the superseding BEAAA-17380 candidate commit or remove only the Wave 3 target-evidence edits. Wave 1 and Wave 2 rows are outside the touched-file inventory.
