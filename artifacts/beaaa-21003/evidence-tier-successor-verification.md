---
artifact_id: beaaa-21003-evidence-tier-successor-verification
issue_ref: BEAAA-21003
persona_audience: internal
scanner_version: 0.5.0
corpus_version: 0.2.0
probe_set_version: unchanged
rubric_point_id: none
contract_version: are-scanner-api-v1@v1.3.0
policy_version: evidence-tier-assignment/1.0
cto_cosign_ref: required-before-merge
n5_calibration_delta: "PASS: total 0; every pillar 0; spread 0; seeds 0-4"
---

BLUF: The successor candidate closes all four BEAAA-20247 defects through the signed receiver, append-only Log Tracker, tenant-scoped hub, and shadow-only rate boundary. It does not merge, deploy, promote, change scoring, or use live customer data.

Classification: rule-change continuation under the scanner implementation supervision classification row for changes to evidence-tier policy enforcement and audit semantics. CTO co-sign is required before merge.

Implementation evidence:

- Exact tenant-bound registered pins must be non-empty and equal; snapshot and observation windows fail closed on missing, mismatched, stale, future, or invalid input with stable basis codes.
- Assignment, downgrade, restoration, exception, and override are the only accepted audit event types. Persistence validates before/after, scope, actor, reason, approval reference, mapping version, term ID, and policy version.
- The signed receiver path rejects reserved client fields, normalizes an isolated event, assigns the tier, appends the immutable record, and produces tenant-scoped hub and fixed-current-term rate views.
- Hub output exposes current versus invalid/stale freshness and verified versus partial/missing continuity. Tenant B cannot read Tenant A state.

Verification:

- `PYTHONPATH=tools/post-bind-ingest/src:tools/log-tracker/src python3 -m pytest tools/post-bind-ingest/tests/test_evidence_tier_beaaa20234.py tools/post-bind-ingest/tests/test_receiver.py tools/post-bind-ingest/tests/test_collector_receiver_e2e.py tools/log-tracker/tests -q` -> 95 passed.
- `python3 -m pytest tests/test_beaaa398_wiring.py::test_n5_l1_zero_drift_on_every_baselined_fixture -q` -> 1 passed. Seeds 0-4 remain byte-stable; total, every pillar, and spread delta are 0.
- `git diff --check` -> clean.

FAL audit: PASS. This internal implementation makes no protection, coverage, customer, legal, or external claim. It preserves vendor-local observability-only ingestion, counts/hashes-only central output, and the fixed in-force multiplier.

Rollback: revert the BEAAA-21003 candidate commit and retain `16eb7c53b6e77622f0b37cb513922a9fa21ed613` behavior.
