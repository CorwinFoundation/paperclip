---
artifact_type: closeout_receipt
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This rollback receipt preserves Wave 2 as an inactive reported-not-scored gate while replacing only the rejected r7 public tuple and unsigned preimage."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-17663", "BEAAA-17510", "BEAAA-17472"]
proof_points_cited: ["Founder root-only tuple comment f10d2df2-2d72-4cd1-9ee4-6f34d4fe7d9a", "direct parent a63c12215c5321b08a33dff4be3f696f79b40b76"]
version: "0.1"
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# BEAAA-17663 Rollback Receipt

BLUF: rollback is discard-only for the BEAAA-17663 root1 direct child and `artifacts/beaaa-17663/*`; preserve `a63c12215c5321b08a33dff4be3f696f79b40b76`, rejected `8adb96a78f60c95c21eda0a0fbd268ade255f626`, r6 history, and Wave 2 inactive/reported-not-scored state.

Rollback scope:

- Remove the BEAAA-17663 root1 child commit if it is not approved for exact signing.
- Remove `tools/beaaa17663_wave2_root1_r7_preimage.py`, `tests/test_beaaa17663_wave2_root1_r7_preimage.py`, and `artifacts/beaaa-17663/*`.
- Restore `evals/aiuc_path_b/lane_a_depth.py` to parent `a63c12215c5321b08a33dff4be3f696f79b40b76`.

Non-rollback scope:

- Do not rewrite or delete rejected commit `8adb96a78f60c95c21eda0a0fbd268ade255f626`; it remains permanent rejected history.
- Do not restore r6 key, signature, preimage, approval, partial packet, or alternate lease residue.
- Do not run target calls, scoring, underwriting, retention activation, signing, merge, deploy, or promotion.
