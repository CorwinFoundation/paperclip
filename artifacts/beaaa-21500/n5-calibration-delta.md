---
artifact_type: calibration_delta
owner: scanner_engineer
persona_audience: internal
canonical_terms_only: true
contracts_referenced: ["are-scanner-api-v1.md@v1.0", "scanner-implementation-supervision-v1.md@v1.0"]
ticket_refs: ["BEAAA-21500", "BEAAA-21364"]
scanner_version: "0.5.0"
corpus_version: "0.1.1"
probe-set-version: "authoritative-probe-registry@1.0.0"
rubric_point_id: "P1.1 plus registry integration surface"
cto_cosign_ref: null
self_audit_passed: true
---

# BEAAA-21500 N=5 calibration delta

BLUF: PASS. The existing golden-set N=5 runner completed after the fixture and history test repairs. All six live golden agents were byte-identical across five runs with zero total or pillar spread; three roster slots remain explicitly pending baseline scans.

- Command: `python3 scripts/beaaa6190_v05_rc_n5.py`
- Result: `PASS`
- Maximum total delta: `0`
- Maximum pillar delta: `0.0000`
- Live golden agents: `B1, G1, G2, M1, M2, M3`
- Source report: `tools/calibration-deltas/2026-06-22-beaaa6190-v05-rc-n5.md`
- Classification: rule-execution; test and fixture maintenance only, with no scoring, rubric, judge, or runtime behavior change.
- FAL: internal engineering evidence; focus, alignment, and linkage pass through the cited Scanner contract and issue lineage.
