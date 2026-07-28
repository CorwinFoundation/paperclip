---
artifact_type: ADR
persona_audience: internal
ticket_refs: [BEAAA-18684]
scanner_version: "0.5.0"
corpus_version: "credential_secret_output_log_handling@0.1.0"
probe-set-version: "0.1.0"
rubric_point_id: ["P3.3", "ROBUSTNESS.SECRETS"]
contract_referenced: "are-scanner-api-v1@v1.0; no delta"
classification: "rule-execution: new probe for an existing rubric point"
cto_cosign_ref: null
canonical_terms_only: true
self_audit_passed: true
---
# Decision

Add 12 deterministic A008 credential/secret output-log cases under P3.3 / ROBUSTNESS.SECRETS. Keep the family reported-not-scored with zero weight and no BackBond Score, band, API-envelope, or published-count effect.

# Insight and rollback

Exact inert-canary matching covers response and log fixtures, generated code/configuration, redaction, references, and false-positive controls without an LLM judge. Roll back by disabling the family and reverting its manifest row, runner, mapping, cases, and tests together.

# FAL audit

Focus, Alignment, and Linkage pass: this adds reproducible BackBond Scanner evidence under an existing contract surface and changes no pricing or protection rule.
