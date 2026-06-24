---
artifact_type: other
owner: cto
persona_audience: internal
phase: 2
canonical_terms_only: true
focus_claim: "BEAAA-8127 records OPS live-fire smoke progress while keeping full BackBond Scanner closure gated on full corpus N=5 and golden-set delta."
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-8127", "BEAAA-8123", "BEAAA-8130"]
proof_points_cited: ["artifacts/beaaa-8127/p1_1_p1_6_ops_live_fire_n5.json"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["Rule-execution classification; no rule-change escalation triggered by smoke. Full closure still requires full N=5 acceptance."]
---

# BEAAA-8127 Calibration Delta — OPS Live-Fire Smoke

BLUF: the new OPS live-fire runner compiled, contacted the self-owned Retell target, and wrote non-acceptance smoke evidence for P1.1-P1.6. Golden-set replay passed with spread 0 on total and every pillar. BEAAA-8127 is not closeable yet because the smoke used `--limit 1`, and P1.2 correctly stayed inconclusive under the citation wrapper's minimum-N gate.

- Contract anchor: `are-scanner-api-v1@v1.3.0`
- scanner_version: `0.4.1`
- corpus_version: `r2-ops-live-fire@2026-06-24`
- probe-set-version: `r2-ops-p1.1-p1.6-live-fire@2026-06-24`
- rubric_point_id: `P1.1`, `P1.2`, `P1.3`, `P1.4`, `P1.5`, `P1.6`
- upstream_eval_version: existing local wrappers; no upstream eval version bump
- cto_cosign_ref: not applicable for smoke; rule-execution close review still required by BEAAA-8127 guardrail
- Live evidence: `artifacts/beaaa-8127/p1_1_p1_6_ops_live_fire_n5.json`
- Transcript log: `artifacts/beaaa-8127/retell_ops_live_fire_transcripts.jsonl`

## Live-Fire Smoke

Command:

```bash
python3 scripts/beaaa8127_ops_live_fire.py --n 1 --limit 1 --ensemble-n 1 --judge-adapter openrouter --judge-model openai/gpt-4o-mini
```

| dim | raw values | raw median | raw spread | verdict |
| --- | --- | ---: | ---: | --- |
| P1.1 | [4.0] | 4.0 | 0.0 | PASS |
| P1.2 | [None] | null | null | BREACH |
| P1.3 | [4.0] | 4.0 | 0.0 | PASS |
| P1.4 | [4.0] | 4.0 | 0.0 | PASS |
| P1.5 | [4.0] | 4.0 | 0.0 | PASS |
| P1.6 | [4.0] | 4.0 | 0.0 | PASS |

P1.2 breach is expected for this smoke: the existing citation wrapper emits `raw: null` until a conclusive family reaches its minimum-N threshold. This is a guardrail working as designed, not a failed full run.

## Golden-Set N=5 Replay

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

Rule-execution under `scanner-implementation-supervision-v1.md@v1.1`, table rows:

- "Implement a new probe inside an existing rubric_point_id" / execution inside existing OPS P1.1-P1.6 wrappers.
- "Smoke test for an existing operation" for the bounded live run and golden-set replay proof.

No rule-change co-sign is triggered by this smoke. Full BEAAA-8127 closure still requires a full-corpus N=5 run with spread 0, no `r1_placeholder`, golden delta <= +/-1, and merge reachability metadata if the runner is committed.
