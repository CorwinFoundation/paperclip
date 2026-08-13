---
artifact_type: implementation_closeout
owner: scanner_engineer
persona_audience: internal
canonical_terms_only: true
focus_claim: "The BackBond Scanner now exercises two existing robustness points through the customer endpoint real adapter."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-22774", "BEAAA-5414"]
proof_points_cited: ["tests/test_real_adapter_asi_beaaa22774.py", "artifacts/beaaa-22774/n5-calibration.json"]
scanner_version: "0.5.0"
corpus_version: "0.1.0"
probe-set-version: "real_adapter_asi_v0@0.1.0"
rubric_point_id: ["P3.2", "P3.3"]
n5_calibration_delta: {max_total: 0, max_pillar: 0, seeds: [0, 1, 2, 3, 4]}
cto_cosign_ref: null
self_audit_passed: true
fal_audit: {focus: pass, alignment: pass, linkage: pass}
---

# BEAAA-22774 closeout

BLUF: The customer endpoint walker now dispatches real single-turn probes for P3.2 indirect-injection resistance and P3.3 secret-exfiltration resistance. Each family carries closed ASI01-ASI10 metadata and produces a response-bound evidence URI and SHA-256 reference.

Classification: rule-execution under the supervision-contract row for implementing a probe against an existing rubric point. The change adds no rubric point, weight, judge, scoring constant, band cut, API field, or tamper-control change.

Inventory result: P3.2 and P3.3 were applicable to the existing single-turn real adapter. Tool-misuse points remain dependent on a tool-call harness. P3.6 remains reported-not-scored. Neither class was activated by this ticket.

Verification:

- Focused registry, walker, pillar-mapping, evidence, and surface-rotation suite: 36 passed.
- Frozen live golden roster: six agents, seeds 0 through 4, maximum total delta 0, maximum pillar delta 0.
- Contract: `are-scanner-api-v1@v1.0`; output envelope unchanged.

Rollback: revert the candidate commit. This removes the new wrap, ASI enum, registry entry, tests, and calibration runner as one unit while retaining the prior BEAAA-5414 walker.
