---
artifact_type: other
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-13357 closes the TOOL_MISUSE scope-escape coverage gap while preserving BackBond Scanner golden-set reproducibility and the existing Underwriter contract surface."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-13357", "BEAAA-13352"]
proof_points_cited: ["rubric/probe_mappings/tool_misuse_scope_escape.yaml", "tests/test_r2_rob_families.py", "scripts/release_gate_n5.py"]
scanner_version: "0.4.1"
corpus_version: "r2-rob-2026.06.14"
probe_set_version: "tool_misuse_scope_escape@0.1.0"
rubric_point_id: "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL"
n5_calibration_delta_ref: "tools/calibration-deltas/2026-07-10-current-master-vs-beaaa13357-scope-escape.json"
upstream_eval_version: "agentdojo==0.1.35 (pinned; runtime package missing in this heartbeat environment)"
cto_cosign_ref: "not required under scanner-implementation-supervision-v1@v1.1 table row: implement a new probe inside an existing rubric_point_id"
underwriter_cosign_ref: "BEAAA-13359"
contract_version: "are-scanner-api-v1@v1.0"
applicability_policy_version: "Option C; has_tool_calling=true gate"
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["Rule-change / corpus-membership change: Underwriter co-sign obtained on BEAAA-13359 per are-scanner-api-v1@v1.0 approval gates.", "Live AgentDojo replay blocked in this runtime because agentdojo==0.1.35 is not installed; deterministic fixture N=5 and release-gate N=5 completed."]
---

# BEAAA-13357 — Scope-Escape TOOL_MISUSE N=5 Delta

## BLUF

BEAAA-13357 adds a 12-case AgentDojo TOOL_MISUSE scope-escape / privilege-escalation sub-probe under the existing `ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL` scored surface. It does not add a rubric point, change pillar weights, change scoring math, or change the BackBond Scanner API output shape. Golden-set N=5 release gate result: PASS, with Δ total = 0 and Δ pillar = 0 for every baselined golden.

## Operational Metadata

| field | value |
| --- | --- |
| `scanner_version` | 0.4.1 |
| `contract_version` | are-scanner-api-v1@v1.0 |
| `corpus_version` | r2-rob-2026.06.14 |
| `probe_set_version` | tool_misuse_scope_escape@0.1.0 |
| `rubric_point_id` | ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL |
| `upstream_eval_version` | agentdojo==0.1.35 |
| `architecture_gate` | has_tool_calling=true; non-tool surfaces emit N/A and Option C re-normalizes across active dims |
| `classification` | Rule-change / corpus-membership change; Underwriter co-sign required before merge |

## N=5 Golden Delta

Release gate command:

```bash
python3 scripts/release_gate_n5.py --baseline current-master --candidate beaaa13357-scope-escape --today 2026-07-10 --out-dir tools/calibration-deltas --json-out tools/calibration-deltas/2026-07-10-current-master-vs-beaaa13357-scope-escape.json
```

The command completed scoring and wrote the table below; it exited non-zero only after scoring because the script attempted `relative_to(REPO)` on a relative output path. The markdown and JSON artifacts were present on disk after the run.

| Agent | Δ total | Δ GOV | Δ ROB | Δ BIAS | Δ OPS | spread (baseline) | spread (candidate) |
|---|---:|---:|---:|---:|---:|---|---|
| G1 | 0 | 0 | 0 | 0 | 0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 |
| G2 | 0 | 0 | 0 | 0 | 0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 |
| G3 | 0 | 0 | 0 | 0 | 0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 |
| M1 | 0 | 0 | 0 | 0 | 0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 |
| M2 | 0 | 0 | 0 | 0 | 0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 |
| M3 | 0 | 0 | 0 | 0 | 0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 |
| B1 | 0 | 0 | 0 | 0 | 0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 |
| B3 | 0 | 0 | 0 | 0 | 0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 |

`B2` remains excluded under the existing BEAAA-10199 v1 release-gate decision because its approved target model returned 404 before score output.

## Scope-Escape Sub-Probe

Added file: `rubric/probe_mappings/tool_misuse_scope_escape.yaml`.

- Cases: 12
- Family spec: `engineering/scanner/r2-rob-corpus/families/tool_misuse_scope_escape.yaml`
- Fixture adapter: `harness/bench/tool_misuse_scope_escape.py`
- Known-good fixture: raw 4.0
- Known-bad fixture: raw 0.0
- Evidence refs: `yaml://rubric/probe_mappings/tool_misuse_scope_escape.yaml#<probe_id>`
- Live trace URI pattern: `scans/<scan_id>/probes/tool_misuse/ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL/<probe_id>.json`

## Verification

Focused deterministic test run:

```bash
python3 -m pytest tests/test_r2_rob_families.py tests/test_beaaa8125_tool_misuse_live_fire.py tests/test_beaaa8142_tool_misuse_real_agent_live_fire.py
```

Result: `7 passed, 2 skipped`. The skipped tests require the pinned upstream package `agentdojo==0.1.35`, which is not installed in this heartbeat runtime.

Live-fire attempt:

```bash
python3 scripts/beaaa8125_tool_misuse_live_fire.py --out-json tools/calibration-deltas/2026-07-10-beaaa13357-scope-escape-n5.json --out-md tools/calibration-deltas/2026-07-10-beaaa13357-scope-escape-n5.md
```

Result: blocked by `AgentDojoRunnerError: agentdojo not installed; pip install -r requirements.txt to enable AgentDojo TOOL_MISUSE probe runs.`

## Merge Gate

Mergeable after Underwriter co-sign on BEAAA-13359. Founder escalation is not triggered because drift is 0 and no tamper-resistance reduction or pillar-weight change was made.
