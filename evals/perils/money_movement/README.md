---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-7234 adds offline money-movement evidence rows without changing the BackBond Scanner contract output shape."
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-7234", "BEAAA-6200", "BEAAA-6970"]
proof_points_cited: ["tau-bench upstream pin", "tau2-bench upstream pin"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# Money-Movement Offline Wrapper

BLUF: this package verifies value-transfer traces using pass^k (`k=5`) and emits `evidence_refs[]` rows for `ROBUSTNESS.tool_misuse.money_movement.*`.

Pins:

- `sierra-research/tau-bench@59a200c6d575d595120f1cb70fea53cef0632f6b`
- `sierra-research/tau2-bench@5ebebbe827b455b3ed04fcb9294235c6ef4e5fd6`

The wrapper consumes structured tool calls and final ledger state only. Agent free text is ignored by the verifier.

