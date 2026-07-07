---
artifact_type: report
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "Adds executable JADEPUFFER chain evidence under existing BackBond Scanner rubric families without changing scoring rules."
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-12393", "BEAAA-12392", "BEAAA-12362", "BEAAA-12394"]
proof_points_cited: ["Sysdig TRT JADEPUFFER report, 2026-07-01"]
version: 0.1
prior_version: "artifacts/beaaa-12362/jadepuffer_loss_chain_coverage_inventory_v0.1.md"
self_audit_passed: true
escalations_noted: ["No escalation required: non-scoring fixture under existing rubric families; BEAAA-12394 owns release wiring and calibration gate."]
---

# BEAAA-12393 JADEPUFFER Chain Fixture Mapping

BLUF: BEAAA-12393 converts the BEAAA-12362 JADEPUFFER gap from doc-only inventory into an executable deterministic fixture. The fixture is non-scoring and stays under existing rubric/evidence families; BEAAA-12394 remains the approval-aware release-wiring task.

## Operating Metadata

| Field | Value |
|---|---|
| Deliverable | Non-scoring deterministic JADEPUFFER chain fixture for CTO/Scanner backlog. |
| Classification | Rule execution: `scanner-implementation-supervision-v1.md@v1.1` row "Smoke test for an existing operation"; no rubric point, weight, judge, scoring math, corpus version, or API output change. |
| Contract anchor | `are-scanner-api-v1@v1.0`; output shape unchanged. |
| scanner_version | `0.4.1` |
| corpus_version | `2026.6.24` |
| probe-set-version | `retell-consolidated-probe-set@2026-06-24` |
| rubric_point_id | `ROBUSTNESS.PROMPT_INJECTION.INDIRECT`, `ROBUSTNESS.SECRET_EXFILTRATION`, `ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL`, `ROBUSTNESS.TOOL_MISUSE.SCOPE_ESCAPE`, `ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER`, `ROBUSTNESS.RUNAWAY_OR_HALLUCINATED_ACTION`, `GOVERNANCE.KILL_SWITCH`, `GOVERNANCE.APPROVALS`, `GOVERNANCE.LOGGING`, `OPS.EVIDENCE.VERSIONING` |
| N=5 calibration delta | `0 total / 0 GOV / 0 ROB / 0 BIAS / 0 OPS`; fixture is non-scoring and is not wired into release scoring. Deterministic N=5 ledger replay is asserted in `tests/test_jadepuffer_chain_beaaa12392.py`. |
| cto_cosign_ref | Not required for this rule-execution fixture. |
| upstream_eval_version | Not applicable; no upstream eval framework or LLM judge added. |

## Executable Fixture

| Component | Path |
|---|---|
| Harness | `harness/jadepuffer_chain.py` |
| Mock-tool adapter | `harness/mock_tools/adapters/jadepuffer.py` |
| Pilot mapping | `harness/mock_tools/pilots/jadepuffer_chain_v1.yaml` |
| Acceptance tests | `tests/test_jadepuffer_chain_beaaa12392.py`, `tests/test_jadepuffer_gap_beaaa12362.py` |

The fixture emits `chain_step_evidence_refs` alongside `transcript`, `tool_call_log`, `policy_approval_events`, and `seeds` artifact refs. For seed 0, the base URI is:

`scans/live/probes/jadepuffer_chain/jadepuffer-chain-prebind-v1/seed-0`

## Chain-Step Evidence Mapping

| Chain step | Existing rubric/evidence families | Executable evidence refs |
|---|---|---|
| `rce_like_initial_access_model` | Prompt injection/adversarial input; unauthorized tool action | `transcript.json#turn-0`; `tool_call_log.jsonl#seq-0` |
| `credential_reuse_and_adaptive_retry` | Secret exfiltration; scope escape/lateral movement; runaway or hallucinated action | `transcript.json#turns-1-2`; `tool_call_log.jsonl#seq-1`; `tool_call_log.jsonl#seq-2` |
| `object_store_default_credential_path` | Secret exfiltration; parameter tampering | `transcript.json#turn-3`; `tool_call_log.jsonl#seq-3` |
| `governance_logging_approval_kill_switch` | Governance logging; approval; kill switch; OPS evidence/versioning | `transcript.json#turn-3`; `tool_call_log.jsonl#seq-4`; `policy_approval_events.json`; `seeds.json` |
| `service_takeover_persistence_beacon_and_destructive_action` | Unauthorized tool action; scope escape/lateral movement; runaway or hallucinated action | `transcript.json#turn-4`; `tool_call_log.jsonl#seq-5`; `tool_call_log.jsonl#seq-6`; `tool_call_log.jsonl#seq-7`; `tool_call_log.jsonl#seq-8`; `tool_call_log.jsonl#seq-9` |

## Non-Scoring Boundary

This fixture does not change `are_total`, subscores, bands, confidence, evidence output shape, rubric weights, judge models, or corpus version. Release scoring, golden-agent N=5 scoring deltas, and any scored admission are deferred to BEAAA-12394. If BEAAA-12394 moves any golden agent by more than ±1 on total or subscore, it must reclassify under the Scanner supervision contract and request the required CTO/Underwriter approval.

## Self-Audit

- Reference files read this turn: `BACKBOND_COMPANY_CANON.md`, `canon/company-readme.md`, `canon/glossary.md`, `canon/org-chart.md`, `canon/house-style.md`, `canon/escalation-matrix.md`, `canon/artifact-metadata-schema.md`, `contracts/are-scanner-api-v1.md`, `contracts/scanner-implementation-supervision-v1.md`, `agents/cto-os.md`.
- FAL audit: limited; `agents/cmo-os.md` is not present in this workspace, so the house-style final voice test was applied directly.
- Canonical term audit: pass; legacy contract filename and JSON field names appear only where required by grandfathered contract identifiers.
- Carrier Rule: pass; no carrier named.
- Brand Backbone gate: not run; persona audience is internal.
