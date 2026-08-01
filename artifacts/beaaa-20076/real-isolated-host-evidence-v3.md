---
ticket_ref: BEAAA-20076
artifact_type: test_evidence_index
persona_audience: internal
classification: rule-execution
scanner_contract: are-scanner-api-v1@v1.3.0
scanner_version: 0.5.0
corpus_version: 0.1.0
probe-set-version: unchanged
rubric_point_id: N/A-host-deployment-plumbing
N=5_calibration_delta: 0
cto_cosign_ref: N/A-rule-execution
---

# Real isolated-host activation evidence v3

BLUF: The isolated real-systemd/real-Docker gate passed success, restart/cutover, read-only state visibility, PRESENT and ABSENT rollback, and durable `ROLLBACK_FAILED` paths. The corrected PRESENT path restored a genuinely distinct prior Scanner tuple. No production or shared service was accessed or mutated.

## Immutable evidence

- BEAAA-20093 four-path raw bundle: Paperclip attachment `8abc810b-2e82-4d94-b8a8-8aec86ddb54b`; SHA-256 `a2bc7cc0e4dfaff94ead949a2dcf036600c9c81f71b32baf786a84e70b6d62a7`.
- BEAAA-20117 corrected distinct-prior PRESENT raw bundle: Paperclip attachment `55e60aea-2525-40d9-ad43-48bb7a61c637`; SHA-256 `62c723a9feb52b4a3863ea62942dba09170ee02220c2ebd71064fbecdc3ac0ae`.
- BEAAA-20117 evidence note: Paperclip attachment `310d7b3a-77c4-41fa-9d4b-616ae9a83cc3`.

The bundle hashes were independently recomputed in the BEAAA-20076 candidate workspace on 2026-08-01 and matched the CTO-accepted values exactly.

## Gate results

- Success: the transient unit executed the real Docker arguments, activated the candidate, returned readiness/SLA/release-identity HTTP 200, and exposed the root-owned state bind mount read-only inside the container.
- Restart/cutover: real systemd and nested Docker stopped the prior container and started the exact candidate image on unique loopback ports and unique ephemeral unit/container names.
- ABSENT rollback: forced public-gate failure restored absent `current_version` and identity markers; the prior service endpoints remained healthy.
- PRESENT rollback: forced public-gate failure restored prior revision `0757f835c7b9226af88340b015889fab1d8ad3bb` from candidate revision `7850289333ffc1de6eebea9c2201e0f1059c4c12`. Before and after values matched for commit, configured image, running revision, internal SHA, current version, candidate ID, and nested image ID; readiness, SLA, and release identity returned HTTP 200; the candidate identity was absent.
- Rollback failure: an invalid prior image forced a real restart failure, returned rc 90, retained a durable fsynced `ROLLBACK_FAILED` marker, and failed closed.
- Cleanup: the evidence reports zero residual fixture containers and networks.

## Scope boundary

The gate used already-local image bytes in isolated privileged fixtures. It performed no registry pull, Scanner rebuild, signing, production installation, production restart, sudo expansion, or shared-environment mutation. It does not authorize promotion. BEAAA-19984 remains blocked until the canonical QA child records `APPROVED` for the exact immutable candidate and any separate Founder governance is satisfied.

FAL audit: PASS. Focus is exact activation and rollback; alignment preserves the frozen Scanner contract and image bytes; linkage is BEAAA-20093, BEAAA-20117, BEAAA-20077, and BEAAA-19984.
