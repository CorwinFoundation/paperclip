---
artifact_type: "provenance_manifest"
owner: "scanner_engineer"
persona_audience: "internal"
canonical_terms_only: true
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-15688", "BEAAA-15697"]
self_audit_passed: true
---

# BEAAA-15688 Wave 0 overlap and gap report

BLUF: The candidate inventory contains 715 unique definitions across the 27 approved families. It is an inventory candidate, not evidence that 715 tests have run or are live.

The existing workload was read from Scanner revision `6e78dae48131f590cdd59fada023089c53a45236`. Its 204 target-bound entries contain 203 model-target rows and one separately retained governance attestation. Oracle-aware canonicalization produces 185 exact hash groups; five prompt-equivalent groups retain distinct oracle hashes and are classified as semantic overlaps. The resulting 180 current behaviors are fully accounted for, and 23 alias rows add no coverage.

Discrete mapping objects supplied 685 candidate definitions. Thirty concrete BackBond-owned scenarios fill proven allocation gaps: five legal-hallucination cases and 25 inventory-only peril/stop cases. The peril, stop, and two malformed-binding tool families remain quarantined from scoring activation pending the co-signs named in the superseding CTO contract.

No target or judge calls were made. No score, rubric, corpus activation, API, production, or live-evidence state changed. The “715 live scanner tests” claim remains held.

Rollback: remove the unpromoted BEAAA-15688 inventory, scenario corpus, verifier, manifest, and report files. Existing Scanner behavior is unchanged.
