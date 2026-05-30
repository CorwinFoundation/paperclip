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
SCANNER_VERSION = "0.3.8"  # BEAAA-1273 (WS3 of BEAAA-1255) — `prior_scan_refs[]` 4-field shape (scan_id, run_timestamp, are_total, band) + top-level `forensic_bundle_uri` + `snapshot_hash` + ADR-0017 §7 Q2 pagination cap=100 / `prior_scan_refs_truncated` sentinel on POST /scanner/v1/rescans (rule-change toward are-scanner-api-v1 v1.2.0 additive minor; no scoring path touched — read-only chain query + write-time pin propagation only). Stacks on 0.3.7 (BEAAA-1274 WS4 — purpose-aware SLA instrumentation + degradation path) which stacks on 0.3.6 (BEAAA-1271 WS2 — write-once scan_log + JCS snapshot_hash) which stacks on 0.3.5 (BEAAA-1265 WS1 — /scanner/v1/rescans shim route + ARE_RESCANS_MEASURED flag).

# v0.2.3 additive — default RNG seed when ScoreRequest.seed is omitted.
# Stamped into ScoreResponse.seed so byte-identical replay works for any
# downstream pricing or claims toolchain.
DEFAULT_SEED = 42

# BEAAA-390 / BEAAA-388. Customer-facing ARE Scanner API contract identifier
# the score is being computed against. Stamped on every ScoreResponse so the
# LOI-stage caveat block can name the contract version that the customer's
# downstream binder will eventually pin to. Format: "<contract-file>@<version>".
# Bumped in lockstep with contracts/are-scanner-api-v1.md.
CONTRACT_VERSION = "are-scanner-api-v1@v1.0.3"
