---
artifact_id: beaaa-21319-passive-monitor-evidence-tier-successor-verification
issue_ref: BEAAA-21319
persona_audience: internal
scanner_version: 0.5.0
corpus_version: 0.2.0
probe_set_version: unchanged
rubric_point_id: none
contract_version: are-scanner-api-v1@v1.3.0
policy_version: evidence-tier-assignment/1.0
classification: rule-execution
classification_basis: existing-policy validation and regression implementation
cto_cosign_ref: not-required-rule-execution
n5_calibration_delta: "PASS: total 0; every pillar 0; spread 0; seeds 0-4"
---

BLUF: This replacement candidate closes the three QA rejection classes before any Log Tracker append. It preserves the vendor-local, observability-plane-only Passive Monitor architecture and does not change scoring, protection, the Scanner API contract, or production state.

Implementation evidence:

- Missing or incomplete tenant-pin registration rejects with the stable `tenant_pin_registration_missing` code. `mapping_version` can no longer become `unregistered`, so no T3 assignment can be emitted from an unregistered mapping.
- The verified receiver JWT customer subject and agent must match the requested tenant and agent. Each `VerifiedSource` may also carry its registered agent identity, which is checked against the requested agent.
- Log Tracker validates event-specific transitions before insertion. Assignment forbids a prior tier; downgrade requires a strictly lower after-tier; restoration requires a strictly higher after-tier; exception and override events require their respective identifiers.
- Direct regressions cover QA's empty-registration call with a populated source/current window, tenant and agent identity mismatches, exact-match success, all malformed event classes, and append-only mutation/deletion rejection.

Verification commands and results:

- `PYTHONPATH=tools/post-bind-ingest/src:tools/log-tracker/src python3 -m pytest tools/post-bind-ingest/tests/test_evidence_tier_beaaa20234.py tools/post-bind-ingest/tests/test_receiver.py tools/post-bind-ingest/tests/test_collector_receiver_e2e.py tools/log-tracker/tests -q` -> `106 passed in 2.17s`.
- `python3 -m pytest tests/test_beaaa398_wiring.py::test_n5_l1_zero_drift_on_every_baselined_fixture -q` -> `1 passed in 0.34s`.
- N=5 seeds 0-4: total delta `0`, every pillar delta `0`, spread `0`; no Underwriter gate triggered.
- `git diff --check` -> clean.

FAL audit: PASS. This is an internal technical artifact with no customer, protection, legal, pricing, or external market claim.

Rollback: revert only the BEAAA-21319 successor commit and retain approved OTel master base `16eb7c53b6e77622f0b37cb513922a9fa21ed613`.
