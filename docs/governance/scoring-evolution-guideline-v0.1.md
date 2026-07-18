---
artifact_type: adr
owner: ceo
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "During the Phase 1 pilot the BackBond Score is treated as a deliberately-evolving instrument, so the team ships scoring improvements without waiting for a production-grade stability freeze."
contracts_referenced: ["are-scanner-api-v1"]
ticket_refs: ["BEAAA-14869"]
proof_points_cited: ["AIUC-1 standard"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# Scoring Evolution Guideline — Phase 1 Pilot (v0.1)

**Status:** Adopted (board directive BEAAA-14869, 2026-07-16)
**Owner:** CEO (policy) · CTO (rubric/scoring implementation)
**Applies to:** the BackBond Score, all rubric weights/bands/dimensions, calibration curves, and any output number derived from `rubric/`.

## Board directive (BEAAA-14869)

> Since we are at the pilot phase and not even close to production, changes to the score are fine. We are continuing to improve, so it just makes sense that the score will continue to evolve.

## What this means

1. **Score stability is NOT a hard constraint during Phase 1.** The BackBond Score is a pilot instrument under active calibration. A change that moves scores — reweighting a pillar, activating a dimension, retuning a calibration curve, adjusting a band boundary — is a normal, expected improvement, not a regression. It does not require backward-compatibility with a prior score value, and it must not be blocked solely because "the number moves."

2. **Traceability discipline still applies.** Loosening the *stability* constraint does not loosen the *provenance* constraint. Every score-affecting change continues to carry its normal change-control trail:
   - an ADR (or ADR addendum) stating what changed and why;
   - a rubric version bump + header note in `rubric/rubric.yaml`;
   - a calibration-delta record under `tools/calibration-deltas/` quantifying the before/after movement on the reference set;
   - the required co-sign(s) where an existing contract or activation gate demands one (e.g. Underwriter/HoUW co-sign on pillar reweights and activations).
   The point of the directive is to remove the *fear of moving the number*, not the *record of having moved it*.

3. **No silent score changes.** Because scores can move freely, the burden shifts to *communication*. Any change that would materially move a pilot customer's reported BackBond Score or band must be surfaced (release note / calibration-delta) so we, and the customer, are never surprised by a number.

4. **Production is different.** This guideline is scoped to Phase 1. Once we approach a production / bound-capacity posture — where a score feeds pricing, a warranty trigger, or a claims-defense position at bind — score stability becomes contract-grade and this permissive stance is superseded by the relevant wire-contract and relock ADRs (cf. the `applied_threshold` / `pillar_floors` bind-eligibility metadata already stamped into `ScoreResponse`). Do not cite this guideline to justify moving a number that a bound contract depends on.

## Practical effect for the team

- **CTO / scoring:** proceed with rubric and calibration improvements on their merits. Do not gate a well-justified scoring change on "but scores will shift." Keep the ADR + calibration-delta + co-sign trail. Reference this guideline in the rationale field when a change's *only* objection is score movement.
- **Underwriter:** co-sign gates on activations and reweights are unchanged; this guideline does not waive them.
- **CMO / external comms:** the BackBond Score is described externally as a pilot-stage, continuously-improving measure — never as a fixed or certified production grade. Keep the Carrier Rule and house-style intact.

## Change control for this guideline

This is a governance guideline, versioned like any artifact. Superseding it (e.g. at the Phase 1 → Phase 2 boundary, or when the first bound contract locks a score) requires a new version citing this file as `prior_version` and a board/Founder sign-off, since the originating directive is board-level.
