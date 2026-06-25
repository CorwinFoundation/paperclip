---
artifact_type: other
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-8127", "BEAAA-8123", "BEAAA-8130", "BEAAA-8043"]
proof_points_cited: ["artifacts/beaaa-8127/retell_ops_p14_live_replay_n5_summary.json", "artifacts/beaaa-8127/retell_ops_p14_live_transcripts.jsonl"]
version: 0.1
prior_version: null
self_audit_passed: false
escalations_noted: ["Golden-set delta and canonical master merge are not complete; BEAAA-8127 remains open."]
---

# BEAAA-8127 OPS P1.4 live-fire N=5 delta

## BLUF

P1.4 out-of-scope refusal accuracy now emits a measured live-fire subscore against the self-owned Retell target and replays at N=5 spread 0. This is partial O1 coverage only: P1.1, P1.2, P1.3, P1.5, and P1.6 remain unmeasured, and the canonical golden-set delta/merge gate is not complete.

## Contract anchor

- Contract: `are-scanner-api-v1@v1.0`
- Supervision contract: `scanner-implementation-supervision-v1@v1.1`
- Classification: rule-execution, table row "Implement a new probe inside an existing rubric_point_id"
- Rubric point: `P1.4`

## Metadata

| Field | Value |
|---|---|
| `scanner_version` | `0.4.2` |
| `corpus_version` | `0.1.0` |
| `probe-set-version` | `0.1.0` |
| `rubric_point_id` | `P1.4` |
| `model_versions.primary` | `retell-chat-agent-gpt-4.1-mini` |
| `model_versions.judges` | `[]` |
| `target_agent_id` | `agent_1e7015ae5f87ea825a3d1fac11` |
| `cto_cosign_ref` | `not required for rule-execution; close still requires CTO roll-up/co-sign per BEAAA-8127` |

## Live capture

| Artifact | Path |
|---|---|
| Live transcript export | `artifacts/beaaa-8127/retell_ops_p14_live_transcripts.jsonl` |
| Live seed-0 envelope | `artifacts/beaaa-8127/retell_ops_p14_live_capture_env_seed0.json` |
| Replay config | `artifacts/beaaa-8127/are-probe-retell-ops-p14-live-replay.yaml` |
| N=5 replay summary | `artifacts/beaaa-8127/retell_ops_p14_live_replay_n5_summary.json` |
| N=5 replay log | `artifacts/beaaa-8127/retell_ops_p14_live_replay_n5.log` |

## N=5 replay result

| Metric | Seed 0 | Seed 1 | Seed 2 | Seed 3 | Seed 4 | Spread |
|---|---:|---:|---:|---:|---:|---:|
| `are_total` | 64 | 64 | 64 | 64 | 64 | 0 |
| `subscores.GOVERNANCE` | 15.625 | 15.625 | 15.625 | 15.625 | 15.625 | 0 |
| `subscores.ROBUSTNESS` | 15.625 | 15.625 | 15.625 | 15.625 | 15.625 | 0 |
| `subscores.BIAS_FAIRNESS` | 15.625 | 15.625 | 15.625 | 15.625 | 15.625 | 0 |
| `subscores.OPS` | 16.6667 | 16.6667 | 16.6667 | 16.6667 | 16.6667 | 0 |

## Evidence refs

`P1.4` is absent from `uncovered_dimensions` for all five replay envelopes. The measured family evidence ref is:

`are-probe://family/ops_refusal_accuracy/refusal_robustness_v0.1/sha256:ef919312913e9e19`

No `P1.4` evidence ref uses `are-probe://placeholder/...`.

## Golden-set delta

Not complete in this heartbeat. This file is not a closeout packet and does not satisfy the final BEAAA-8127 merge gate by itself. Before BEAAA-8127 can close, the implementation must be merged to the canonical Scanner master and the golden-set N=5 delta must be attached with total and every subscore within +/-1.

## Remaining O1 dimensions

- `P1.1` TruthfulQA: not pinned/measured in this heartbeat.
- `P1.2` HaluEval QA: not pinned/measured in this heartbeat.
- `P1.3` HaluBench: not pinned/measured in this heartbeat.
- `P1.5` confidence calibration: metric and seed policy still need implementation evidence.
- `P1.6` eval coverage breadth: must be derived from actual live coverage run; not placeholder-passed.
