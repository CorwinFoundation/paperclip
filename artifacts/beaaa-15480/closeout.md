---
artifact_type: other
owner: scanner_engineer_2
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-15480 ingests and executes the approved 100-case Plan A Batch 2 candidate without promotion."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-15480", "BEAAA-15479", "BEAAA-15478"]
version: 0.1
prior_version: null
self_audit_passed: true
---

# BEAAA-15480 closeout

BLUF: the approved 100-case deterministic roster was ingested as immutable candidate corpus `0.1.2` and executed against the authenticated vendor endpoint. All 100 cases are terminal and evidenced; no promotion occurred.

## Operational metadata

- Classification: rule execution — existing rubric points, deterministic oracles, unchanged scoring behavior.
- `scanner_version`: `0.5.0`
- Baseline `corpus_version`: `0.1.1`
- Candidate `corpus_version`: `0.1.2`
- Candidate corpus SHA-256: `b96fbbf0dc7133f3ed0d6ac287eddfed86b0cf6d049c1d108e39cd9822505485`
- `probe-set-version`: `active-probe-registry@0.1.0+beaaa15480.candidate`
- `rubric_point_id`: `P1.1` (86), `P3.2` (14)
- N=5 calibration delta: `0.0000` total and every pillar for seeds 0–4; see `tools/calibration-deltas/2026-07-18-beaaa15480-batch2-n5.md`.
- `cto_cosign_ref`: not required; no rule-change threshold crossed.
- Contract: `are-scanner-api-v1@v1.0`.

## Result

- Approved input manifest SHA-256: `badbc0e652ff952fc5517597f5d6b137c9d540be4ce38d57418c7ab61889139b`.
- Execution manifest SHA-256: `1a3fec6e7042e396450d7cbe4487cb6b4eaf3ad18dbf572dc39167c9946df0c1`.
- Attempted / terminal / evidenced: `100 / 100 / 100`.
- Calls: `100` authenticated target, `0` judge.
- Deterministic verdicts: `4 pass / 96 fail / 0 incomplete`.
- Evidence is PII-scrubbed and includes request/response hashes, verdict, timestamps, version pins, seed, target identity, and reachable local reference.
- `promotion_authorized`: `false`.

## Verification

```bash
python3 artifacts/beaaa-15478/build_batch2_manifest.py
sha256sum -c artifacts/beaaa-15478/batch2_selection_manifest.sha256
python3 artifacts/beaaa-15480/validate_execution.py
python3 -m compileall -q artifacts/beaaa-15480/run_batch2.py artifacts/beaaa-15480/validate_execution.py
```

Observed: all commands pass. Exact-candidate QA `APPROVED` remains mandatory before any later promotion request; public or production promotion also remains Founder-gated.
