---
artifact_type: adr
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This ADR makes every BackBond Scanner release prove N=5 score stability and Phase-3 test presence before it can ship."
contracts_referenced: ["are-scanner-api-v1@v1.0"]
ticket_refs: ["BEAAA-14835", "BEAAA-14832"]
proof_points_cited: ["harness.release_gate.validate_release", "tools.full_scan_reference_agents"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["No scoring logic, rubric weight, API shape, judge, corpus, or customer-data handling change; rule-execution only."]
---

# ADR-0061: Enforce reproducibility and Phase-3 presence at release

## Decision

The BackBond Scanner release manifest must carry exactly five runs, seeds 0 through 4, for every reference agent; `release_gate.py` blocks when the total or any pillar subscore has a spread greater than 1 point. Scanner regression CI also executes the committed known-good and known-bad reference agents for every controlled family, requiring terminal evidence and the declared per-family bands.

## Reasoning

The API contract makes N=5 reproducibility a release invariant. Keeping that check only in a workflow permits another release path to bypass it. The release manifest is the common enforcement boundary, so missing, malformed, or unstable N=5 evidence is a release-gate failure.

The Phase-3 presence check uses the existing full-scan dispatcher and reconciliation manifest. That makes a silently removed family/case, absent evidence, or band failure visible as a failing CI job.

## Rejected alternatives

- Keep the checks only in `scanner-regression.yml`: rejected because the release gate could still accept an unproven candidate.
- Accept arbitrary five seeds: rejected because the standing contract fixes seeds 0 through 4.
- Check only `are_total`: rejected because the contract applies the ±1 band to every subscore too.

## Calibration and contract impact

This change does not alter scoring inputs, scoring math, rubric weights, corpus contents, judges, bands, or API output. The controlled reference replay has N=5 spread 0.0000. It is rule-execution against `are-scanner-api-v1@v1.0`; no Underwriter approval gate is triggered.

## Rollback

Revert this ADR and the scoped changes to `harness/release_gate.py`, `tests/test_release_gate_beaaa10189.py`, `tools/full_scan_reference_agents.py`, `tests/test_full_scan_reference_agents_beaaa13803.py`, and `.github/workflows/scanner-regression.yml`. Trigger: the gate rejects a release manifest that contains five valid seed 0–4 runs within ±1 on total and every pillar, or the reference harness reports a false disappearance with complete terminal evidence.

## Pre-submission self-audit

The single objective is release-bound enforcement of reproducibility and test presence. The audience is engineering and the next action is to preserve the manifest schema and keep CI green. Reproducibility, evidence completeness, calibration discipline, tamper resistance, and discrimination are preserved; every score fixture contains scanner/corpus/model/seed pins. No contract change is proposed. Rollback is concrete. No LLM judge is introduced. Glossary and Carrier Rule checks pass.
