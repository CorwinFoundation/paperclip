---
artifact_type: adr
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-5754 creates the connector-side runtime carrier needed to collect BackBond Scanner live-probe evidence without changing scoring rules."
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-5754"]
proof_points_cited: ["BEAAA-5740 connector cap/redaction substrate", "BEAAA-5747 Tier-1 live-probe scope"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# ADR-0032 — BEAAA-5754 Live-Probe Substrate

## BLUF

Decision: implement the live-probe substrate in `tools/are-connector/src/are_connector/probe_runtime.py`, reusing the connector capped HTTP client and redaction rules, with pytest fixtures under `tools/are-connector/tests/`. This is rule-execution under `scanner-implementation-supervision-v1.md@v1.1` table rows "smoke test for an existing operation" and connector plumbing; it does not change BackBond Scanner scoring, rubric weights, judge selection, or `are-scanner-api-v1.md@v1.0` output shape.

## Reasoning

The connector is the correct implementation site because live probes fire against deployed customer endpoints through the same egress allowlist, response cap, and redaction boundary already shipped by BEAAA-5740. Putting the runtime in the connector package gives R-1, R-2, and R-5 one import path for live HTTP probe execution while keeping probe definitions and judge selection out of this ticket.

The runtime computes `transcript_sha` over canonical request/response JSON after secret-family redaction. That preserves evidence reproducibility and prevents credential-shaped material from influencing or leaking through evidence pointers.

## Verdict-Rule Dispatch

Supported dispatch:

- `regex`: pass when the extracted response text matches the pinned pattern.
- `equality`: pass when the extracted response text equals the expected string.
- `judge`: delegated callout only; no judge is selected here. Missing callout or missing judge id returns `status="error"` with `reason="error_judge_unpinnable"`.

## Failure Taxonomy

- `pass`
- `fail`
- `error_oversized`
- `error_redaction_failed`
- `error_network`
- `error_judge_unpinnable`

`ProbeVerdict.status` stays contract-simple as `pass | fail | error`; the detailed taxonomy is carried in `reason`.

## Rejected Alternatives

- Top-level `harness/` runtime: rejected because it would need a second HTTP client or a cross-package import back into the connector.
- Add the runtime to `are_connector.runtime.ConnectorRuntime`: rejected because scan-task envelope execution and direct probe execution have different lifecycles.
- Implement judge selection in this ticket: rejected because judge pinning is downstream R-tier work and would become a rule-change risk.

## Calibration Delta

N=5 golden-set delta is expected to be zero by construction because no scoring path imports `are_connector.probe_runtime`. Receipt: `tools/calibration-deltas/2026-06-16-beaaa5754-live-probe-substrate-n5.json`.

## Carry-Forward Imports

R-1 / R-2 / R-5 should import:

- `from are_connector.probe_runtime import ProbeRuntime`
- `from are_connector.probe_runtime import ProbeSpec, VerdictRule, ProbeVerdict`
- `from are_connector.probe_runtime import load_probe_set`
- pytest fixture: `probe_runtime` from `tools/are-connector/tests/conftest.py`

## Rollback

Remove `probe_runtime.py`, the connector test fixture, `test_probe_runtime_smoke.py`, and this ADR. No database migration, contract migration, or scoring rebaseline is required.
