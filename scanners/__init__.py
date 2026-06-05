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
SCANNER_VERSION = "0.3.9"  # BEAAA-1682 (WS-A of BEAAA-794) — AgentDojo TOOL_MISUSE wrap activation. Registers ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL + ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER rubric_point_ids (rubric.yaml 1.0.0 -> 1.0.1) under applicability_policy.yaml 0.1.0 -> 0.1.1 architecture-gate has_tool_calling=true. Wrap-only per scanner-implementation-supervision-v1.md@v1.0 (no upstream fork); scanners/agentdojo_runner.py mediates the AgentDojo unauthorized-call + parameter-tampering subsets. Rule-change classification per supervision contract row "Add a new rubric_point_id to the canonical scoring sheet" (x2). Co-sign chain: CTO comment-b4c564db-1bb0-43c6-bb30-f7cd1c7f2583 on BEAAA-794; HoUW BEAAA-1045 + _default/BEAAA-1045_houw_cosign_v0.1.md. ADR-0019 docs/adr/adr-0019-beaaa794-tool-misuse-rubric-activation.md; N=5 calibration delta tools/calibration-deltas/2026-06-01-beaaa794-tool-misuse-activation.{json,md}. Pillar weights 25/25/25/25 UNCHANGED (Founder-locked); active dim count 21 -> 23 (6 + 6 + 5 + 6). Replaces deferred P3.4 slot. On non-tool-calling surfaces both new dims emit N/A per Option C re-normalization (BEAAA-225 closed 2026-05-17). Stacks on prior 0.3.x baseline; 0.3.10/0.3.11 (BEAAA-2175 PR-B + BEAAA-2199 cosign_metadata) reverted in the immediately preceding commit BEAAA-2199-revert, leaving this 0.3.9 slot to BEAAA-1682 per CTO Path A on BEAAA-2649 cmt 289f10fb.

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
CONTRACT_VERSION = "are-scanner-api-v1@v1.2.0"  # BEAAA-1721 (sync to doc anchor; upstream cosign ADR-0017 + BEAAA-1255#56b55cb5).
