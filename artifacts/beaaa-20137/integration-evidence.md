---
ticket_ref: BEAAA-20137
artifact_type: implementation_evidence
persona_audience: internal
classification: rule-execution
classification_row: host deployment plumbing under existing Scanner rules
scanner_version: 0.5.0
corpus_version: 0.2.0
probe-set-version: artifacts/beaaa-387/manifest.json@3561a38e73746731734b4daa011a28663c4edc9e333ae88b138657b232db5e8d
rubric_point_id: N/A-host-deployment-plumbing
N=5_calibration_delta: 0
cto_cosign_ref: N/A-rule-execution
scanner_contract: are-scanner-api-v1@v1.3.0
supervision_contract: scanner-implementation-supervision-v1@v1.1
self_audit_passed: true
---

# BEAAA-20137 atomic integration evidence

BLUF: The functional candidate `cd464001ebb4e0c34ad4d0174fc55a6e7a539d22` is rooted directly at frozen master `f9a60012fa84e3905add75132ef9cb717b4236bd` and contains only the approved BEAAA-20076 helper/config file states plus the single service-unit line authorized by that delta. No production or shared environment was mutated.

## Lineage and scope proof

- Parent chain: `f9a60012fa84e3905add75132ef9cb717b4236bd -> cd464001ebb4e0c34ad4d0174fc55a6e7a539d22`.
- Approved source lineage replayed: `be8080c0`, `f5688520`, `fda3f349`, `318059cb`.
- Excluded stale predecessors: `02579dfa`, `790c4a8d`, `8527ea16`, `91fc53c7`; none is an ancestor of the integration candidate.
- Approved source patch IDs, in order: `e506545fb0a2e59eb4446ef5ffaada0012ec198e`, `848f81252eb2c31bc577e200013dab3dce66f3d0`, `b2b58539fae53a46bcd5d51d8be9986fe4adfd5c`, `cf41b6415c5fcbe7b985000a8be6bb7a54344b41`.
- Integrated aggregate patch ID: `ccfb72b2383eed210714c097ce694bce57b3d9fa`.
- Six replayed paths are byte-identical to approved source `318059cb`: the four `artifacts/beaaa-20076/*` files, `scripts/deploy/scanner-deploy.sh`, and `tests/test_scanner_deploy_beaaa16598.py`.
- `infra/live-edge/backbond-scanner-live.service` preserves frozen master's networking dependency, egress flag, and preflight while adding only the approved read-only `/var/lib/backbond-deploy-agent` mount. This is the sole necessary conflict resolution.
- File-level delta: four added evidence files; one service-unit line added; approved helper and focused test replace the older same-path master implementations. No other path changed.

Commands used for the audit included `git diff --name-status f9a60012... cd464001...`, `git show <source-commit> --pretty=format: | git patch-id --stable`, `git diff f9a60012... cd464001... | git patch-id --stable`, and byte comparisons against `318059cb` for all non-conflicting replay paths.

## Verification

- `python3 -m pytest -q tests/test_scanner_deploy_beaaa16598.py`: PASS, 20 tests.
- Canonical deterministic golden regression suite: PASS, 75 tests and 20 subtests.
- `python3 scripts/beaaa6190_v05_rc_n5.py`: PASS.
- Release/reference gate tests: PASS, 17 tests.
- `python3 -m tools.full_scan_reference_agents --check ...`: PASS; known-good A/A, known-bad D/D, all spreads 0, discrimination and evidence complete.
- Candidate-specific release N=5: PASS; all total/pillar deltas and baseline/candidate spreads are 0. See `tools/calibration-deltas/2026-08-01-f9a60012-vs-cd464001.md`.

FAL audit: PASS. Focus is the authorized atomic integration; alignment preserves Scanner scoring and evidence semantics; linkage is BEAAA-20136, BEAAA-20076, BEAAA-19983, and BEAAA-20077.

Rollback: abandon/delete the isolated branch. Canonical master stays at `f9a60012...`; production stays at `b6ae5e29...`.
