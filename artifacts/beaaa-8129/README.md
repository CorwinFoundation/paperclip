---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-8129 packages the P2.2 labeled-outcome candidate corpus for Underwriter sign-off before any BackBond Scanner pricing use."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-8129", "BEAAA-8123"]
proof_points_cited:
  - "artifacts/beaaa-8129/p2_2_equal_opportunity_labeled_outcome_corpus_v0.1.jsonl"
  - "artifacts/beaaa-8129/measurement_spec.md"
  - "artifacts/beaaa-8129/manifest.json"
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted:
  - "Underwriter sign-off required before score-bearing use."
---

# BEAAA-8129 P2.2 Candidate Corpus Packet

## BLUF

P2.2 now has a candidate labeled-outcome corpus packet ready for Underwriter review. The packet is synthetic, functional-use only, hash-pinned, judge-free, and not valid for pricing until signed.

## Files

| File | Purpose |
|---|---|
| `p2_2_equal_opportunity_labeled_outcome_corpus_v0.1.jsonl` | Row-level prompts, protected attributes, ground-truth labels, and expected outcome labels. |
| `measurement_spec.md` | True-positive-rate parity method and scanner wiring constraints. |
| `manifest.json` | Version pins, provenance, corpus SHA-256, and approval posture. |
| `SHA256SUMS` | Reproducible file hashes for the packet. |

## Operational Metadata

| Field | Value |
|---|---|
| `scanner_version` | `0.4.1` target, unchanged by this packet |
| `corpus_version` | `p2_2_equal_opportunity_labeled_outcome_v0.1_candidate` |
| `probe-set-version` | `bbq-holisticbias-phase1-v0.1.0 + p2_2_equal_opportunity_candidate_v0.1` |
| `rubric_point_id` | `P2.2` |
| `N=5 calibration delta` | Not run; corpus candidate only, no scanner score path wired in this heartbeat. Required before any merge that makes P2.2 measured. |
| `cto_cosign_ref` | Not required for this rule-execution corpus packet; required if later N=5 delta exceeds `±1` or scoring math changes. |

## Review Ask

Underwriter should review whether the labeled outcomes and functional-use scenarios are acceptable as a pricing substrate for P2.2. If accepted, the next Scanner Engineer step is live probe wiring plus N=5 golden calibration; if rejected, the corpus remains non-score-bearing and P2.2 stays uncovered.

## FAL Audit

- Focus: pass. The packet supports the BackBond rating-and-insurance operating model by making P2.2 measurable only after signed evidence substrate exists.
- Alignment: pass. The packet does not position BackBond as an evaluation-only or governance-advice product, does not name customers or carriers, and does not create a commercial commitment.
- Linkage: pass. The packet anchors to BEAAA-8129 / BEAAA-8123, `are-scanner-api-v1@v1.3.0`, `scanner-implementation-supervision-v1@v1.1`, and the required P2.2 operational metadata.
