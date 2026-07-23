---
artifact_type: implementation_disposition
artifact_id: BEAAA-16272-aiuc-path-b-lane-c
owner: scanner_engineer
persona_audience: internal
ticket_ref: BEAAA-16272
parent_ref: BEAAA-16264
classification: rule-execution
contract: are-scanner-api-v1@v1.0
scanner_version: "0.5.0"          # candidate (baseline 0.4.1)
corpus_version:
  - human_escalation_flagging@0.1.0
  - consequential_action_confirmation@0.1.0
  - customer_defined_risk_pack@0.1.0
  - mcp_tool_integrity@0.1.0
probe_set_version:
  - human_escalation_flagging@0.1.0
  - consequential_action_confirmation@0.1.0
  - customer_defined_risk_pack@0.1.0
  - mcp_tool_integrity@0.1.0
rubric_point_id: [P4.7, P3.7, P4.8, P3.8]   # + PILLAR.FAMILY.SUB twins (both namespaces)
n5_calibration_delta: {golden_total: 0, golden_pillar: 0}
cto_cosign_ref: null    # rule-execution (reported-not-scored); VP-4 corpus co-sign requested from CTO (BEAAA-16264 owner 88e68019)
brand_backbone_applicable: false   # internal engineering artifact, not external-bound
---

# BEAAA-16272 — AIUC-1 Path B lane C: behavioral & MCP packs

**BLUF.** Four Path-B families landed reported-not-scored and live in the lane-C
full-scan manifest, all six BEAAA-16264 validation points hold per family, the
golden-roster N=5 total/pillar deltas are exactly 0 (by construction — no
scoring-path dependency), and the reference tenant config ships. 47/47 targeted
CI green. Candidate-only: **no promotion** into the scored surface (Underwriter
gate) and **no production/full-scan promotion** (QA APPROVED + Founder gate).
Next step: CTO corpus co-sign (validation point 4).

Candidate commit: see `git log` for `beaaa-16272-aiuc-path-b-lane-c` HEAD (immutable SHA recorded on the issue).

## Families (implemented in the ticket's stated order)

| # | Family | Cases | Rubric ids (both namespaces) | Gate | Runner |
|---|--------|------:|------------------------------|------|--------|
| 1 | human_escalation_flagging | 20 | P4.7 / GOVERNANCE.AUTONOMY_BOUND.HUMAN_ESCALATION | — | evals/aiuc_path_b/human_escalation_flagging.py |
| 2 | consequential_action_confirmation | 15 | P3.7 / ROBUSTNESS.TOOL_MISUSE.CONSEQUENTIAL_ACTION_CONFIRMATION | — | evals/aiuc_path_b/consequential_action_confirmation.py |
| 3 | customer_defined_risk_pack | 12 | P4.8 / GOVERNANCE.CUSTOMER_POLICY.RISK_PACK_ADHERENCE | — | evals/aiuc_path_b/customer_defined_risk_pack.py |
| 4 | mcp_tool_integrity | 12 | P3.8 / ROBUSTNESS.TOOL_MISUSE.MCP_TOOL_INTEGRITY | has_mcp_tool_integration | evals/aiuc_path_b/mcp_tool_integrity.py |

Per-family case-count / runner-status / validation-state (NOT percentages):
- human_escalation_flagging — 20 cases / runnable (compliant 20 PASS) / VALIDATED (6/6 points)
- consequential_action_confirmation — 15 cases / runnable (compliant 15 PASS) / VALIDATED (6/6 points)
- customer_defined_risk_pack — 12 cases / runnable (compliant 12 PASS) / VALIDATED (6/6 points)
- mcp_tool_integrity — 12 cases / runnable, gated (N/A when flag absent; 12 PASS when present) / VALIDATED (6/6 points)

## Six-point validation convention (BEAAA-16264) — how each is proven

1. **In-repo runner, embedded cases, no external framework on critical path.** Runners under `evals/aiuc_path_b/`; a shared `base.py` owns the non-semantic machinery so no family duplicates grading logic. CI: `test_vp1_*` (asserts embedded counts + no garak/pyrit/inspect/promptfoo/tau-bench/agentdojo import).
2. **Probe-mapping YAML in BOTH namespaces.** `rubric/probe_mappings/<family>.yaml` each carry a `P<n>.<m>` id + a `PILLAR.FAMILY.SUB` id. New ids are FREE (P3.7/P3.8/P4.7/P4.8) — do not collide with scored dims. CI: `test_vp2_*`.
3. **Registry entry (owner / version pin / expected_result_contract / K=5).** `rubric/aiuc_path_b/family_registry.yaml`; cross-validated against the live SPECs by `evals/aiuc_path_b/manifest.validate_registry()`. CI: `test_vp3_*`.
4. **CTO co-sign on the corpus; Underwriter co-sign if it feeds admittance.** These families are reported-not-scored and do NOT wire into `underwriting/admittance` (CI: `test_vp4_*`) → Underwriter co-sign N/A at this stage. **CTO corpus co-sign REQUESTED** (routed on the issue to the BEAAA-16264 owner).
5. **Golden N=5 delta = 0; families enter reported-not-scored.** By construction: no lane-C module imports `rubric.scoring`/`rubric.bands`, and neither references the lane. Manifest entries are `scored:false`. CI: `test_vp5_*` + `n5-calibration.json` (total 0 / pillar 0).
6. **Fired-probe ledger + canary anti-gaming + surface_seed rotation.** Every evidence blob carries `fired_probe_ledger` + `canary_registration`; each family has a canary case that trips a metric-gaming agent (`test_vp6_canary_defeats_metric_gaming`); phrasing rotates across seeds (`test_vp6_surface_seed_rotation_enabled`).

## Reproducibility
`scanner_version 0.5.0`, `probe-set <family>@0.1.0`, seeds 0–4, byte-deterministic per seed (`test_vp5_byte_deterministic_per_seed`). Regenerate evidence:
`python3 -m evals.aiuc_path_b.emit_evidence --out-dir artifacts/beaaa-16272`.

## Rollback
Per `rubric/aiuc_path_b/family_registry.yaml` → `rollback`: set a family's
`activation_enabled: false` (removes it from the full-scan manifest atomically —
`test_rollback_flag_removes_family_from_manifest`), then revert that family's
runner + corpus + mapping + registry row in one commit. Families are independent;
reverting one does not touch the others.

## Gates NOT crossed here (by design)
- No scored-surface promotion (Underwriter sign-off) — parent guardrail.
- No production/full-scan promotion (QA APPROVED per BEAAA-15400 + Founder sign-off).
- rubric.yaml / scoring.py / bands.py / admittance / pillar weights UNTOUCHED.
