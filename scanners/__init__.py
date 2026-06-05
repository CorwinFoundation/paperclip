"""are-scanner runner adapters.

`promptfoo_runner` wraps the Promptfoo CLI as the canonical eval-framework
runner driving Path A (OpenRouter base-model proxy scans) and Path B
(customer-deployment harness). Garak / HarmBench / PromptBench are probe
sources executed via Promptfoo, NOT separate runners — see BEAAA-105 /
BEAAA-106.
"""

# v0.2.3 additive (BEAAA-21, UW co-signed amendment A 2026-05-09).
# ARE Scanner build version stamped on every ScoreResponse. Kept independent
# from rubric.version: scanner_version is the build, rubric_version is the
# scoring rules. Conflating them re-creates the d1832a66 retraction failure
# mode (repo-tag-vs-contract-version drift). Bump in lockstep with the API
# contract minor version per api-contract-v0 §9.5.
SCANNER_VERSION = "0.4.0"  # BEAAA-781 / ADR-0025 (spec authority ADR-0022 / BEAAA-2026) — LLM Guard Anonymize + Deanonymize PII activation atomic merge (BEAAA-2649 WS-B). Pin llm-guard==0.3.16 + presidio-analyzer==2.2.358 + presidio-anonymizer==2.2.358 + spacy==3.8.14 + transformers==4.51.3 (requirements.txt L68 / L77-78 / L87 / L97, delivered via BEAAA-2068 done 2026-06-03) + register ROBUSTNESS.PII.SCAN_TIME_DETECTION (substrate) + GOVERNANCE.PII.HANDLING_ATTESTATION (attestation pair) rubric_point_ids (rubric.yaml 1.0.1 → 1.1.0) + applicability_policy.yaml 0.1.1 → 0.1.2 (both new dims fire_when:always, no architecture gate) + pii_substrate.yaml @ 0.1.0 + pii_attestation.yaml @ 0.1.0 probe-set + harness.detectors.llmguard.{__init__, anonymize, deanonymize, scoring, envelope} wrap module (wrap-only Presidio backbone + Output.Deanonymize round-trip pure-function; no upstream fork). Composition rule: parallel-additive + attestation_substrate_divergence diagnostic flag (metadata-only; mirrors BEAAA-225 Option C; NO scoring-math change). Rule-change classification per scanner-implementation-supervision-v1.md@v1.0 row "Add a new rubric_point_id to the canonical scoring sheet" (×2). Co-sign chain: CTO (BEAAA-2026 cmt 66728f27 + ADR-0022), UW operational (BEAAA-2026 cmt e8f38143 + BEAAA-2066 done), HoUW activation (BEAAA-2026 cmt ea35a75e + BEAAA-2067 done + _default/BEAAA-2067_houw_cosign_v0.1.md), Founder authority (BEAAA-2048 Q46=A via BEAAA-2064; CEO cmt 3a432710 on BEAAA-1858). Pillar weights 25/25/25/25 UNCHANGED (Founder-locked). Active dim count 23 → 25 (6 OPS + 6 BIAS_FAIRNESS + 6 ROBUSTNESS + 7 GOVERNANCE). N=5 calibration delta: tools/calibration-deltas/2026-06-04-beaaa781-pii-activation.{json,md} (byte-identical replay on roster B1/B3/G1/G2/G3 + backstop M1/M2/M3; substrate dim emits N/A or measured per applicability gate; attestation grader deterministic by construction). Detector ensemble pinned in scanners/model_versions.json::detectors.llm-guard-anonymize (HoUW watchpoint #7 — entity_thresholds-adjacent-to-SHA). Fallback detector: presidio-standalone (presidio-analyzer 2.2.358 + en_core_web_lg 3.7.1) per ADR-0022 §4 Obs-2 (Q10=C ship-with-caveat decoupling honored). Stacks on 0.3.9 (BEAAA-1682 WS-A of BEAAA-794 — AgentDojo TOOL_MISUSE wrap; rubric.yaml 1.0.0 → 1.0.1 + applicability_policy.yaml 0.1.0 → 0.1.1 architecture-gated on has_tool_calling).

# v0.2.3 additive — default RNG seed when ScoreRequest.seed is omitted.
# Stamped into ScoreResponse.seed so byte-identical replay works for any
# downstream pricing or claims toolchain.
DEFAULT_SEED = 42

# BEAAA-390 / BEAAA-388. Customer-facing ARE Scanner API contract identifier
# the score is being computed against. Stamped on every ScoreResponse so the
# LOI-stage caveat block can name the contract version that the customer's
# downstream binder will eventually pin to. Format: "<contract-file>@<version>".
# Bumped in lockstep with contracts/are-scanner-api-v1.md. Parity enforced by
# tests/test_contract_version_sync.py (BEAAA-1721 CI guard): this literal must
# equal `"are-scanner-api-v1@" + <frontmatter version>` of the contract md, or
# CI goes red. SOP at docs/runbooks/contract-version-sync.md.
CONTRACT_VERSION = "are-scanner-api-v1@v1.3.0"  # BEAAA-2649 WS-B atomic PII activation merge — contract anchor sync per CTO ruling 289f10fb D3 first branch (Gap 2 cleared by BEAAA-2199-revert 2371d93 bringing contracts md back to v1.2.0; this commit bumps both contracts/are-scanner-api-v1.md frontmatter AND this literal to v1.3.0 in lockstep per BEAAA-1721 sync invariant + test_contract_version_sync.py guard). Minor-additive bump: rubric_point_ids ROBUSTNESS.PII.SCAN_TIME_DETECTION + GOVERNANCE.PII.HANDLING_ATTESTATION added to /scanner/v1/scans response per ADR-0022 §2 raw_mapping; envelope field-set EXTENDS via pillars[ROBUSTNESS|GOVERNANCE].dimensions[].id additions, no existing key renamed/removed. Pre-v1.3.0 consumers continue to read prior dims byte-stably (P3.1–P3.3, P4.1–P4.6); v1.3.0+ consumers SHOULD pin to read the new PII dims. Upstream cosign anchors preserved from v1.2.0 (ADR-0017 + BEAAA-1255#56b55cb5) and extended with v1.3.0 (ADR-0022 spec authority + ADR-0025 activation + BEAAA-2026 cmt 66728f27).
