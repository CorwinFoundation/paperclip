"""are-scanner runner adapters.

`promptfoo_runner` wraps the Promptfoo CLI as the canonical eval-framework
runner driving Path A (OpenRouter base-model proxy scans) and Path B
(customer-deployment harness). Garak / HarmBench / PromptBench are probe
sources executed via Promptfoo, NOT separate runners — see BEAAA-105 /
BEAAA-106.
"""

# v0.2.3 additive (BEAAA-21, UW co-signed amendment A 2026-05-09).
# BackBond Scanner build version stamped on every ScoreResponse. Kept independent
# from rubric.version: scanner_version is the build, rubric_version is the
# scoring rules. Conflating them re-creates the d1832a66 retraction failure
# mode (repo-tag-vs-contract-version drift). Bump in lockstep with the API
# contract minor version per api-contract-v0 §9.5.
SCANNER_VERSION = "0.5.0"  # BEAAA-6190 v0.5 RC cut. Release-cut stamp only: rubric/rubric.yaml remains 1.2.0 because BEAAA-6188 codified the 40-point discrimination spread as a CI gate, not a rubric-side gate; CONTRACT_VERSION remains are-scanner-api-v1@v1.3.0 because BEAAA-6187 customer-VPC artifact URI projection is feature-flagged evidence custody with no default score-output field-shape change. N=5 all-golden RC gate recorded at tools/calibration-deltas/2026-06-22-beaaa6190-v05-rc-n5.md. CTO + Underwriter co-sign required before merge per BEAAA-6190.

# v0.2.3 additive — default RNG seed when ScoreRequest.seed is omitted.
# Stamped into ScoreResponse.seed so byte-identical replay works for any
# downstream pricing or claims toolchain.
DEFAULT_SEED = 42

# BEAAA-390 / BEAAA-388. Customer-facing BackBond Scanner API contract identifier
# the score is being computed against. Stamped on every ScoreResponse so the
# LOI-stage caveat block can name the contract version that the customer's
# downstream binder will eventually pin to. Format: "<contract-file>@<version>".
# Bumped in lockstep with contracts/are-scanner-api-v1.md. Parity enforced by
# tests/test_contract_version_sync.py (BEAAA-1721 CI guard): this literal must
# equal `"are-scanner-api-v1@" + <frontmatter version>` of the contract md, or
# CI goes red. SOP at docs/runbooks/contract-version-sync.md.
CONTRACT_VERSION = "are-scanner-api-v1@v1.3.0"  # BEAAA-2649 WS-B atomic PII activation merge — contract anchor sync per CTO ruling 289f10fb D3 first branch (Gap 2 cleared by BEAAA-2199-revert 2371d93 bringing contracts md back to v1.2.0; this commit bumps both contracts/are-scanner-api-v1.md frontmatter AND this literal to v1.3.0 in lockstep per BEAAA-1721 sync invariant + test_contract_version_sync.py guard). Minor-additive bump: rubric_point_ids ROBUSTNESS.PII.SCAN_TIME_DETECTION + GOVERNANCE.PII.HANDLING_ATTESTATION added to /scanner/v1/scans response per ADR-0022 §2 raw_mapping; envelope field-set EXTENDS via pillars[ROBUSTNESS|GOVERNANCE].dimensions[].id additions, no existing key renamed/removed. Pre-v1.3.0 consumers continue to read prior dims byte-stably (P3.1–P3.3, P4.1–P4.6); v1.3.0+ consumers SHOULD pin to read the new PII dims. Upstream cosign anchors preserved from v1.2.0 (ADR-0017 + BEAAA-1255#56b55cb5) and extended with v1.3.0 (ADR-0022 spec authority + ADR-0025 activation + BEAAA-2026 cmt 66728f27).
