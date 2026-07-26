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
SCANNER_VERSION = "0.5.0"
# Design-partner invocation pin. This is release metadata only; corpus bytes and
# scoring behavior remain owned by their existing manifests.
CORPUS_VERSION = "0.1.0"

# v0.2.3 additive — default RNG seed when ScoreRequest.seed is omitted.
# Stamped into ScoreResponse.seed so byte-identical replay works for any
# downstream pricing or claims toolchain.
DEFAULT_SEED = 42

# BEAAA-390 / BEAAA-388. Customer-facing BackBond Scanner API contract identifier
# the score is being computed against. Stamped on every ScoreResponse so the
# LOI-stage caveat block can name the contract version that the customer's
# downstream binder will eventually pin to. Format: "<contract-file>@<version>".
# Candidate identity is governed by scanner-os/release-metadata.toml. The
# repository contract document can retain later implementation history, but it
# cannot silently supersede the locked cross-team release identifier.
CONTRACT_VERSION = "are-scanner-api-v1@v1.0"
