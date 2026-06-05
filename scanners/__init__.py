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
SCANNER_VERSION = "0.3.11"  # BEAAA-2199 — auditor-readable `cosign_metadata` block added at the contract output surface via `harness.cosign_manifest.build_cosign_metadata(...)` composed in `harness.contract_envelope.to_contract_output`. Envelope-shape change (additive top-level key), not a scoring-behavior change — N=5 spread=0 on `are_total` + every pillar across B1/G1/G2/M1/M2/M3 preserved by construction. Rule-change per `scanner-implementation-supervision-v1@v1.0`. CTO design co-sign [BEAAA-2199 comment ca7071d9](/BEAAA/issues/BEAAA-2199#comment-ca7071d9); UW op-seat bump-direction co-sign on [BEAAA-2262](/BEAAA/issues/BEAAA-2262) (Option B → contract v1.2.1 → v1.3.0 minor); CTO merge co-sign pending on PR-submission comment. Stacks on 0.3.10 (BEAAA-2175 PR-B — Phase 2 envelope-content fill on POST /scanner/v1/rescans 202 response: replaces ADR-0017 addendum-1 §2.2 `_phase1_pending` placeholders with real scoring values; `snapshot_hash` now signs real envelope content per `contracts/are-scanner-api-v1@v1.2.1 §Hard guarantees (CTO) #2`) which stacks on 0.3.8 (BEAAA-1273 WS3 — `prior_scan_refs[]` 4-field shape + ADR-0017 §7 Q2 pagination cap=100 / `prior_scan_refs_truncated` sentinel) which stacks on 0.3.7 (BEAAA-1274 WS4 — purpose-aware SLA instrumentation + degradation path) which stacks on 0.3.6 (BEAAA-1271 WS2 — write-once scan_log + JCS snapshot_hash) which stacks on 0.3.5 (BEAAA-1265 WS1 — /scanner/v1/rescans shim route + ARE_RESCANS_MEASURED flag). 0.3.9 reserved for BEAAA-1721 contract-version-sync if landed separately.

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
