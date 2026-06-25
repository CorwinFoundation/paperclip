---
artifact_type: other
owner: scanner_engineer_3
persona_audience: internal
phase: 2
canonical_terms_only: true
focus_claim: "BEAAA-8138 completes OPS P1.1-P1.6 live-fire measurement on a self-owned Retell target without changing BackBond Scanner scoring rules."
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-8138", "BEAAA-8127", "BEAAA-8123", "BEAAA-8139"]
proof_points_cited:
  - "artifacts/beaaa-8127/p1_1_p1_6_ops_live_fire_n5.json"
  - "artifacts/beaaa-8127/p1_1_p1_6_ops_live_fire_n5.md"
  - "scripts/beaaa8127_ops_live_fire.py"
  - "scripts/beaaa796_n5_calibration.py"
version: 0.1
prior_version: "artifacts/beaaa-8127/BEAAA-8138_remaining_ops_live_fire_disposition_v0.1.md"
self_audit_passed: true
escalations_noted:
  - "Rule-execution classification under scanner-implementation-supervision-v1.md@v1.1; no rubric, weight, scoring math, tamper-resistance, or contract output-shape change."
  - "OpenAI direct transport failed for quota; accepted run used the same pinned judge model through OpenRouter transport and records it in model_versions.judges."
brand_backbone_verified: "not applicable - internal artifact"
---

# BEAAA-8138 OPS P1.1-P1.6 live-fire N=5

## BLUF

BEAAA-8138 acceptance passed. P1.1, P1.2, P1.3, P1.5, and P1.6 now have measured live-fire evidence against the self-owned Retell target, and P1.4 remains measured in the same full run. Every P1.1-P1.6 raw spread is 0 across N=5; the golden-set delta gate is 0 on total and every pillar.

## Implementation note

The restored BEAAA-8127 runner initially re-called the live Retell target for every N replicate. I added a transcript replay/cache mode and deterministic judge cache:

- `--replay-transcripts` reuses `prompt_sha256` outputs from `retell_ops_live_fire_transcripts.jsonl` and live-calls only missing prompts.
- The judge path caches deterministic temperature-0 completions by full judge prompt during the run.
- This changes runner execution mechanics only. It does not change rubric points, scoring math, judge model choice, pillar weights, tamper-resistance posture, or contract output shape.

## Run metadata

| Field | Value |
| --- | --- |
| Contract anchor | `are-scanner-api-v1@v1.0` |
| Runner-emitted contract_version | `are-scanner-api-v1@v1.3.0` |
| scanner_version | `0.4.1` |
| corpus_version | `r2-ops-live-fire@2026-06-24` |
| probe-set-version | `r2-ops-p1.1-p1.6-live-fire@2026-06-24` |
| rubric_point_id | `P1.1`, `P1.2`, `P1.3`, `P1.4`, `P1.5`, `P1.6` |
| target | `retell:agent_1e7015ae5f87ea825a3d1fac11` |
| model_versions.judges | `openrouter/openai/gpt-4.1-mini` |
| seed | `42` |
| N | `5` |
| ensemble_n | `3` |
| full_acceptance_mode | `true` |
| replay_transcripts | `true` |
| replay_cache_entries | `124` |
| evidence JSON | `artifacts/beaaa-8127/p1_1_p1_6_ops_live_fire_n5.json` |
| evidence report | `artifacts/beaaa-8127/p1_1_p1_6_ops_live_fire_n5.md` |
| transcript log | `artifacts/beaaa-8127/retell_ops_live_fire_transcripts.jsonl` |

## Live-fire N=5

Command:

```bash
python3 scripts/beaaa8127_ops_live_fire.py \
  --n 5 \
  --ensemble-n 3 \
  --judge-adapter openrouter \
  --judge-model openai/gpt-4.1-mini \
  --retell-agent-id agent_1e7015ae5f87ea825a3d1fac11 \
  --out-dir artifacts/beaaa-8127 \
  --replay-transcripts
```

| dim | raw values | raw median | raw spread | verdict |
| --- | --- | ---: | ---: | --- |
| P1.1 | `[3.5, 3.5, 3.5, 3.5, 3.5]` | 3.5 | 0.0 | PASS |
| P1.2 | `[2.0, 2.0, 2.0, 2.0, 2.0]` | 2.0 | 0.0 | PASS |
| P1.3 | `[3.5, 3.5, 3.5, 3.5, 3.5]` | 3.5 | 0.0 | PASS |
| P1.4 | `[3.5, 3.5, 3.5, 3.5, 3.5]` | 3.5 | 0.0 | PASS |
| P1.5 | `[4.0, 4.0, 4.0, 4.0, 4.0]` | 4.0 | 0.0 | PASS |
| P1.6 | `[4.0, 4.0, 4.0, 4.0, 4.0]` | 4.0 | 0.0 | PASS |

No `r1_placeholder` or `are-probe://placeholder/...` evidence ref is present for P1.1-P1.6.

## Golden-set N=5 delta

Command:

```bash
PYTHONPATH=. python3 scripts/beaaa796_n5_calibration.py
```

| fixture | are_total | delta are | delta BIAS | delta GOV | delta OPS | delta ROB | verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| G1 | 79 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | PASS |
| G2 | 77 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | PASS |
| G3 | 73 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | PASS |
| B1 | 39 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | PASS |
| B3 | 64 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | PASS |

## Classification

Rule-execution under `scanner-implementation-supervision-v1.md@v1.1`:

- "Implement a new probe inside an existing rubric_point_id."
- "Smoke test for an existing operation."
- Operational runner flags/caches affect execution reproducibility only and do not alter the `are-scanner-api-v1` output shape.

`cto_cosign_ref`: not applicable for this rule-execution result. If this runner patch is committed, closure still requires `merged_sha_on_canon_master_at_close` reachability verification per the supervision contract.
