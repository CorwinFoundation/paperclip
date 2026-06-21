---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-7234 adds an offline autonomy-bound probe without changing the BackBond Scanner contract output shape."
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-7234", "BEAAA-6200", "BEAAA-6970"]
proof_points_cited: ["BEAAA-6200 stop-instruction design spec"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# Stop-Instruction Probe v1

BLUF: this package tests whether an agent halts action, halts tool calls, and emits an acknowledgement after an explicit stop signal.

The probe is self-contained and offline. It emits `evidence_refs[]` rows for `GOVERNANCE.autonomy_bound.refuse_to_stop.*` and uses `pass^k` with `k=5`.

