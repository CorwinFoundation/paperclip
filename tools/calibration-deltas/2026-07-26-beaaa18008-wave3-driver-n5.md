---
artifact_type: report
owner: scanner_engineer
persona_audience: internal
phase: development
canonical_terms_only: true
focus_claim: "BEAAA-18008 makes the Wave 3 execution harness live and verifiable without changing BackBond Scanner scoring."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-18008", "BEAAA-18009", "BEAAA-17981"]
proof_points_cited: ["tools/wave3_executor.py", "tests/test_beaaa18004_wave3_candidate.py", "2026-07-26-beaaa18008-wave3-driver-n5.json"]
version: 1.0
prior_version: null
self_audit_passed: true
escalations_noted: ["Exact candidate requires QA Engineer APPROVED on canonical gate BEAAA-17981 before any live or shared execution."]
---

# BEAAA-18008 Wave 3 driver N=5 calibration delta

BLUF: The immutable implementation candidate `922ab953dac66b35eb01a80a254878d1b0fee1b6` passes the development N=5 release gate against baseline `ca6e481`; all eight baselined golden agents have total and pillar delta 0 and candidate spread 0.

- `scanner_version`: `1.2.0`
- `corpus_version`: `scanner-test-registry-baseline-2026-07-13`
- `probe-set-version`: `wave3-first100/v1`
- `rubric_point_id`: `ALL_ACTIVE_DIMENSIONS` (execution only; no scoring activation)
- contract: `are-scanner-api-v1@v1.0`
- classification: rule execution — customer onboarding harness / smoke execution under existing rules
- `cto_cosign_ref`: not required; BEAAA-18009 is the operational budget ruling
- N=5 result: PASS
- golden total delta: 0 on G1, G2, G3, M1, M2, M3, B1, B3
- maximum pillar delta: 0
- maximum candidate reproducibility spread: 0
- behavioral golden gate: PASS
- B2: explicitly excluded by the inherited gate because its approved target model returns 404 before score output

Command:

```sh
python3 scripts/release_gate_n5.py \
  --baseline ca6e481 \
  --candidate 922ab953dac66b35eb01a80a254878d1b0fee1b6 \
  --phase development \
  --today 2026-07-26 \
  --out-dir tools/calibration-deltas \
  --json-out tools/calibration-deltas/2026-07-26-beaaa18008-wave3-driver-n5.json
```

The helper emitted its complete JSON receipt successfully with `passed: true`, then its CLI wrapper returned non-zero while trying to call `Path.relative_to(REPO)` on an already-relative report path. That post-write display defect did not affect the completed N=5 calculations or receipt.

FAL: Focus passes because the driver turns frozen Scanner cases into evidence-backed live execution. Alignment passes because the implementation preserves pinned models, fixed seeds, budget stops, append-only evidence, and score neutrality. Linkage passes through the signed reconciliation manifest and the existing canonical QA gate BEAAA-17981.
