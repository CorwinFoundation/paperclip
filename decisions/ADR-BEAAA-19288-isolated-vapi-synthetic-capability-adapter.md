---
artifact_type: adr
owner: scanner-engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "An assessment-local, network-free adapter supplies deterministic synthetic capability evidence for the 596 frozen Vapi rows without changing Scanner rules or issuing a score."
contracts_referenced:
  - "contracts/are-scanner-api-v1.md@v1.3.0"
ticket_refs: ["BEAAA-19217", "BEAAA-19220", "BEAAA-19285", "BEAAA-19288"]
scanner_version: "0.5.0"
corpus_version: "fa32e0e081ffbd2953ea3df8aa8d2d0faa0464a3076d17dad9b1758804488988"
probe-set-version: "1.4"
rubric_point_id: "none - non-score-bearing synthetic harness"
n5_calibration_delta: "artifacts/beaaa-19288/n5-calibration-delta.json"
cto_cosign_ref: null
self_audit_passed: true
---

# ADR — isolated Vapi synthetic capability adapter

BLUF: Accept an assessment-local adapter that materializes all 596 unchanged frozen cases into deterministic, append-only synthetic traces. It has no network client, credential input, production identifier, Scanner scoring import, or real side-effect path.

## Decision and classification

This is a harness implementation for the CTO that makes the frozen tool/action/state cases locally executable within 1,500 CPU-seconds and zero provider requests. It is **rule-execution** under the supervision contract's customer-specific integration / existing-rule implementation row: no rubric, weight, judge, scoring math, tamper-resistance posture, or Scanner API output changes.

The adapter provides allowlisted in-memory operations, deterministic logical time, tenant/session namespaces, idempotency keys, cancellation and budget primitives, approval/revocation state, synthetic recipient sinks, and append-only traces. Each trace preserves the frozen `stable_case_id`, source-row SHA, and `prompt_turn_sequence` byte-for-byte as parsed JSON.

## Rejected alternatives

- Extending the existing Vapi assistant: rejected because its observed capability is text-only and this implementation budget authorizes zero provider traffic.
- Reusing production/customer integrations: rejected because that would cross the explicit isolation and data boundaries.
- Adding Scanner scoring: rejected because these rows remain non-score-bearing until separately mapped and authorized.

## Controls and rollback

Execution is sequential, zero-retry, fail-closed, and capped at 596 cases and eight events per case. The prior 380-row/453-request evidence is transformed into a deny/replay ledger; a collision stops before execution. Rollback is deletion or disablement of the assessment-local adapter and derived ledgers while retaining immutable source and prior evidence.

FAL audit: PASS. Focus is limited to authorized Scanner evidence; alignment follows the frozen registry and CTO classification; linkage is explicit through BEAAA-19217, BEAAA-19220, BEAAA-19285, and this issue.
