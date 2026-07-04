---
artifact_type: adr_contract
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "v1.6.0 contract extension adds anti_gaming.canary_flag as an Underwriting-review signal for canary/Goodhart detection. The field is additive, defaults to none, and is not an input to are_total, subscores, band, or confidence."
contracts_referenced: ["are-scanner-api-v1@v1.5.0 → v1.6.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-11721", "BEAAA-11711"]
proof_points_cited: ["scanners/canary_anti_gaming.py", "scanners/live_probe_orchestrator.py", "tests/test_canary_anti_gaming_beaaa11721.py"]
version: "v1.6.0-patch"
prior_version: "are-scanner-api-v1@v1.5.0"
self_audit_passed: true
escalations_noted: ["No fresh Underwriter co-sign required because BEAAA-11733 already co-signed the review-signal-only v1.6.0 field semantics; no scoring math, rubric weight, or customer data handling change."]
---

# Contract patch — `are-scanner-api-v1.md` v1.5.0 → v1.6.0

## §1. Additive field

Live-probe scan results MAY carry the following top-level object:

```jsonc
{
  "anti_gaming": {
    "canary_flag": "none | goodhart_suspected",
    "evidence_ref": "are-probe://anti-gaming/<scan_id>/canaries | null",
    "previously_fired_pass_rate": 0.0,
    "fresh_canary_pass_rate": 0.0,
    "threshold": 0.25
  }
}
```

`anti_gaming.canary_flag` defaults to `"none"`. `"goodhart_suspected"` means
previously fired phrasings passed at a materially higher rate than fresh
canary phrasings for the same tenant/vendor. It routes to Underwriting review.

## §2. Non-scoring invariant

This field is not a scoring input. It MUST NOT move `are_total`, any pillar
subscore, `band`, or `confidence`. It exists to preserve probe-IP integrity
and surface possible vendor memorization without adding trust-me points.

## §3. Ledger invariant

The fired-probe ledger is append-only and tenant/vendor scoped. Canary
selection MUST exclude every phrasing hash already present for that exact
tenant/vendor pair. Rows belonging to another tenant or vendor MUST NOT affect
selection.

## §4. Evidence

When `canary_flag = "goodhart_suspected"`, `evidence_ref` MUST point to a
reproducible anti-gaming evidence artifact for the scan. When
`canary_flag = "none"`, `evidence_ref` MAY be `null`.
