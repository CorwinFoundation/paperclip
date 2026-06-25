---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-8129 records the verified P2.2 live-wiring patch and the remaining canonical-merge blocker for BackBond Scanner release discipline."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-8129", "BEAAA-8133", "BEAAA-8123"]
proof_points_cited:
  - "tools/calibration-deltas/2026-06-24-beaaa8129-p2-2-live-wiring.md"
  - "artifacts/beaaa-8129/underwriter_signoff_v0.1.md"
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted:
  - "Canonical Scanner checkout lacks the probe-sdk and equal-opportunity corpus paths touched in this execution workspace; CTO must identify the canonical merge target before this issue can close done."
---

# BEAAA-8129 Live-Wiring Handoff

## BLUF

P2.2 live wiring is implemented and N=5 verified in the execution workspace, but BEAAA-8129 cannot close as merged because the contract-pinned canonical Scanner checkout does not contain the paths changed here. CTO must identify or provide the canonical merge target for the probe SDK / P2.2 corpus mapping before `merged_sha_on_canon_master_at_close` can be populated.

## Implemented In Execution Workspace

- `corpus/bias/equal_opportunity/equal_opportunity.yaml`
  - now points to signed BEAAA-8129 JSONL substrate
  - records `underwriter_signoff_ref: BEAAA-8133`
  - removes stale `corpus.yaml` / CSV case-file mapping
- `engineering/probe-sdk/corpus/bias_equal_opportunity_v0.1.jsonl`
  - 18-row SDK pack derived from the Underwriter-signed substrate
  - SHA-256 `709102214144c1bc270d6fced93367c91f34145ead618e0a57f778019a40ab72`
- `engineering/probe-sdk/src/are_probe/probes/bias_fairness.py`
  - adds deterministic `grade_equal_opportunity`
  - computes TPR gap on `ground_truth_label=positive` rows across protected attributes
- `engineering/probe-sdk/src/are_probe/probes/__init__.py`
  - registers `bias_equal_opportunity`
- `engineering/probe-sdk/lockfile.toml`
  - pins the new P2.2 pack
  - refreshes stale existing BIAS pack SHA pins to match current on-disk bytes
  - updates SDK `build_hash` to `7bb7a54c42c28a3363b81bfdf23cb380a0912114ff3e287d25c11d7c994ebc4e`
- `engineering/probe-sdk/config/are-probe.yaml.example`
  - includes `bias_equal_opportunity`
- Tests:
  - `engineering/probe-sdk/tests/test_bias_fairness_grader.py`
  - `engineering/probe-sdk/tests/test_corpus_sha_pinning.py`
  - `engineering/probe-sdk/tests/test_equal_opportunity_mapping.py`
  - `engineering/probe-sdk/tests/test_n5_determinism.py`

## Verification

```bash
pytest engineering/probe-sdk/tests/test_bias_fairness_grader.py engineering/probe-sdk/tests/test_corpus_sha_pinning.py engineering/probe-sdk/tests/test_equal_opportunity_mapping.py
ARE_SCAN_BIN=/mnt/paperclipdata/dot-paperclip/instances/default/workspaces/d8bbc3b5-185f-43ed-bff0-d00323ddbfa3/are-scanner/bin/are-scan pytest engineering/probe-sdk/tests/test_n5_determinism.py
```

Both commands passed:

- focused tests: 17 passed
- N=5 determinism: 5 passed

Seeds `0..4` produced spread `0` on `are_total`, `GOVERNANCE`, `ROBUSTNESS`, `BIAS_FAIRNESS`, and `OPS`; P2.2 emitted measured evidence and was absent from `uncovered_dimensions`.

## Blocker

Canonical merge target is unresolved. The contract-pinned canonical checkout at `/mnt/paperclipdata/dot-paperclip/instances/default/companies/59f8876e-e729-4dda-98f9-1317c2b50492/projects/are-scanner` is Git-backed but lacks:

- `engineering/probe-sdk/`
- `corpus/bias/equal_opportunity/`

Without a canonical path containing those runtime components, no `merged_sha_on_canon_master_at_close` can be generated for this code-touching ticket.
