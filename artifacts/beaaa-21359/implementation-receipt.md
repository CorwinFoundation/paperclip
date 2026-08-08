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
self_audit_passed: true
escalations_noted: []
---

# BEAAA-21359 implementation receipt

BLUF: The current-master candidate binds the exact OCI source label, rejects source or revision mismatch before push, and passes focused tests plus the canonical N=5 gate. No image was built, published, signed, or promoted.

- classification: `rule-execution` (existing immutable-image operation and focused smoke test)
- scanner_version: `0.5.0`
- corpus_version: `fixture-pinned per golden expected_score.json`
- probe-set-version: `golden_agents_v1`
- rubric_point_id: `release-plumbing-only; no rubric point changed`
- N=5 calibration delta: `PASS — six live golden agents; total spread 0; maximum pillar spread 0; outputs byte-identical; discrimination spread 43`
- cto_cosign_ref: `null` (rule execution)
- contract: `are-scanner-api-v1@v1.3.0`
- promotion: `not attempted`

## Verification

- `python3 -m pytest -q tests/test_oci_source_label_beaaa21359.py tests/test_beaaa21360_n5_applicability.py` → `3 passed`
- `git diff --check` → pass
- `env -u ARE_BUNDLE_SIGNING_KEY -u ARE_ALLOW_DEV_SIGNING_KEY python3 scripts/beaaa6190_v05_rc_n5.py` → pass; report: `tools/calibration-deltas/2026-06-22-beaaa6190-v05-rc-n5.md`

## FAL audit

Pass. This internal release-plumbing receipt stays focused on exact Scanner image identity, aligns to reproducible evidence and fail-closed behavior, and links every claim to the Dockerfile, workflow assertions, focused tests, or N=5 report. `agents/cmo-os.md` is absent from this workspace, so the audit used the available house-style, glossary, and artifact-metadata canon and discloses that limitation.

## Rollback

Revert the BEAAA-21359 OCI-label commit while retaining the separately approved N=5 applicability repair. No image was built, published, signed, or promoted.
