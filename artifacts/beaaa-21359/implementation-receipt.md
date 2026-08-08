---
artifact_type: test_report
owner: scanner_engineer
persona_audience: internal
phase: 2
canonical_terms_only: true
focus_claim: "BEAAA-21359 adds the exact OCI source label and makes the immutable-image workflow reject source or revision mismatch."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-21359", "BEAAA-21357"]
proof_points_cited: ["Dockerfile", ".github/workflows/scanner-edge-image.yml", "tests/test_oci_source_label_beaaa21359.py"]
version: 0.1
prior_version: null
self_audit_passed: false
escalations_noted: ["Canonical master N=5 aborts in rubric.scoring before producing a delta; merge is halted."]
---

# BEAAA-21359 implementation receipt

BLUF: The scoped OCI-label implementation and focused tests pass, but the mandatory canonical N=5 gate fails on the unchanged master scoring path. This candidate must not merge or promote until the canonical N=5 blocker is repaired and the gate is rerun.

- classification: `rule-execution` (existing immutable-image operation and focused smoke test)
- scanner_version: `0.5.0`
- corpus_version: `fixture-pinned per golden expected_score.json`
- probe-set-version: `golden_agents_v1`
- rubric_point_id: `release-plumbing-only; no rubric point changed`
- N=5 calibration delta: `BLOCKED — scripts/beaaa6190_v05_rc_n5.py aborts with ScoringError: raw applicability_decisions cannot authorize N/A waivers; trusted scanner validation required`
- cto_cosign_ref: `null` (rule execution)
- contract: `are-scanner-api-v1@v1.3.0`
- promotion: `not attempted`

## Verification

- `python3 -m pytest -q tests/test_oci_source_label_beaaa21359.py tests/test_scanner_edge_release_sequences_beaaa14583.py` → `4 passed`
- `git diff --check` → pass
- canonical N=5 command → failed before a report was emitted, on the pre-existing scoring/applicability mismatch

## Rollback

Revert the BEAAA-21359 commit. No image was built, published, signed, or promoted.
