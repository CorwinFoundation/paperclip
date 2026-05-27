---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "v1.0.3 contract patch adds the fused bind-eligibility `verdict` envelope to the live ScoreResponse — additive, presentation-only — so the pricing engine consumes one `verdict.bind_eligibility` enum at scan-time per the HoUW BEAAA-766 ruling, and elevates four documented-but-not-emitted fields (`confidence`, `uncovered_dimensions`, per-row `verdict`+`weight`, per-P4 `review_state`+`tier`) to MUST-emit."
contracts_referenced: ["are-scanner-api-v1@v1.0.2 → v1.0.3", "uw-rubric-supervision-v1@v1.0", "scanner-implementation-supervision-v1@v1.0", "design-partner-overlap-v1@v1.0"]
ticket_refs: ["BEAAA-767", "BEAAA-766", "BEAAA-765", "BEAAA-763", "BEAAA-748", "BEAAA-754", "BEAAA-547", "BEAAA-551"]
proof_points_cited: ["HoUW Q2 ruling BEAAA-766 (binding, rule-change-class)", "UW conditional co-sign + 4 field gaps BEAAA-765", "CTO v0.2 evidence packet BEAAA-763 §3 + Appendix A (doc 66d0b303 rev f6997607)", "CEO ack BEAAA-763 comment f8505b4f", "v1.0.2 patch doc contracts/are-scanner-api-v1.0.2-patch.md", "v1.1.0 patch doc contracts/are-scanner-api-v1.md (orthogonal change set)"]
version: "v1.0.3-patch"
prior_version: "are-scanner-api-v1@v1.0.2"
self_audit_passed: true
escalations_noted:
  - "Output shape ADDED (verdict envelope + four MUST-emit promotions). Surface check 8/8: shape change but additive — existing pillar/dim payloads byte-for-byte unchanged; field meanings unchanged; scoring math unchanged (verdict computed from pillar scores + attestation, no rubric points added/removed/reweighted); judge models unchanged; pillar weights unchanged (25/25/25/25); tamper-resistance preserved (verdict is derived not new-input); ADR attached on BEAAA-767; N=5 calibration Δ expected 0 (verdict block is post-aggregation render of fields already in baseline.json + attestation fixture)."
  - "v1.0.x baseline + v1.0.2 patch + v1.0.3 patch (this) + v1.1.0 patch (BEAAA-547) compose without conflict — v1.0.3 touches §Output (verdict envelope) + §Hard guarantees-UW #2 cross-ref (worst_case_loaded.applies_uw_guarantee_2 made explicit), disjoint from v1.0.2 §Score-structure OPS-bullet-text and v1.1.0 §Input/§Output scan_mode/k_replicates/spread fields."
  - "Implementation, N=5 calibration delta, ADR-0013, and AcmeBot v3 reference-packet byte-identical match are delegated to scanner-engineer child issue. Merge of the scanner v0.3.2 release gated on N=5 Δ=0 (expected) per Supreme Law ±1 band."
---

# Contract patch — `are-scanner-api-v1.md` v1.0.2 → v1.0.3

**Status:** approved for merge as design lock. Scanner v0.3.2 release gated on N=5 calibration + ADR-0013 landing (engineer child, separate ticket).
**HoUW binding ruling:** [BEAAA-766](/BEAAA/issues/BEAAA-766) Q2 ruling 2026-05-25 (rule-change-class per `uw-rubric-supervision-v1@v1.0`).
**UW conditional co-sign + named field gaps:** [BEAAA-765](/BEAAA/issues/BEAAA-765) (4 gaps folded into this single workstream).
**CTO design lock:** [BEAAA-763 §3 + Appendix A](/BEAAA/issues/BEAAA-763) doc `66d0b303` rev `f6997607` (Bind-Grade Evidence Packet v0.2).
**CEO acknowledgement:** [BEAAA-763 cmt f8505b4f](/BEAAA/issues/BEAAA-763#comment-f8505b4f-a91a-49eb-a2a4-46a354f2300a) (no pre-emption).
**Founder gate:** NOT triggered (no pillar-weight change, no >3pt golden move, no tamper-resistance reduction, no policy-wording change). Per [BEAAA-754](/BEAAA/issues/BEAAA-754) scanner-first hold order: Phase 1 critical path landed ([BEAAA-749](/BEAAA/issues/BEAAA-749) + [BEAAA-758](/BEAAA/issues/BEAAA-758) + [BEAAA-752](/BEAAA/issues/BEAAA-752) all `done`) — this v1.0.3 patch ships post-Phase-1 per the original sequencing.
**Change classification:** additive output schema change; presentation-only. Existing pillar / dim payloads byte-for-byte unchanged. Verdict envelope is computed from fields already produced by the v0.3.1 scoring pipeline; no rubric points added/removed, no scoring-math change, no judge-model change.
**Rollback:** Tier 1, ≤2 min. Drop the `verdict` block + the four MUST-emit field promotions from the live `ScoreResponse` emitter; the existing v0.3.1-shape response is byte-identical for the non-verdict surface. No data migration. Existing bound policies under v1.0.2 unaffected. Per ADR-0013 §Rollback.

---

## §Output (additive — `verdict` envelope + four MUST-emit field promotions)

Patch the response schema at the live `ScoreResponse` surface. **Pillar / dimension payloads are byte-for-byte unchanged.** Four already-documented fields are promoted from "in contract, not consistently emitted" to **MUST emit on every output**; one new envelope is added.

### Promotions (MUST-emit on every output; not new fields, but newly enforced)

| Field | Location | Was | Now |
|---|---|---|---|
| `confidence` | top-level | documented in v1.0.x §Output line 64 | **MUST emit** on every output (live runner emit; UW guarantee 1 evidence-completeness extends to confidence presence) |
| `uncovered_dimensions[]` | top-level | documented in v1.0.x §Output line 74 | **MUST emit** on every output; cross-referenced by `verdict.components.substrate.worst_case_loaded.applies_uw_guarantee_2` |
| Per-row `verdict` (enum `pass`/`partial`/`fail`) + `weight` on `evidence_refs[]` rows | per dim | documented in v1.0.x §Output lines 65-73 | **MUST emit** on every `evidence_refs[]` entry |
| Per-P4-dim `review_state` (enum `unreviewed`/`uw_reviewed`/`uw_rejected`) + `tier` (integer 1-4 per `uw-rubric-supervision-v1@v1.0`) | per dim, P4 only | NEW first-class on the row; the markdown-narrated BEAAA-214-Q3 raw_self_report tier-2 cap is now applied mechanically | **MUST emit** on every P4 `evidence_refs[]` entry |

### New top-level `verdict` envelope (additive)

Add the following top-level block to the response schema:

```jsonc
{
  // ... existing fields unchanged (are_total, subscores, band, confidence, evidence_refs[], uncovered_dimensions, scanner_version, corpus_version, model_versions, seed, run_timestamp) ...

  "verdict": {
    "bind_eligibility": "eligible | ineligible_attestation | ineligible_substrate | review_required",
    "selection_rule": "min(substrate, attestation)",
    "primary_reasons": ["string", ...],         // ordered most-actionable-for-UW first; UW review can move attestation-driven reasons; substrate-driven reasons point to post-Phase-1 dim live-measurement
    "components": {
      "substrate": {
        "eligible": true | false,                 // optimistic-loaded view; advisory only — does NOT short-circuit the fused verdict
        "are_total": 0..100,
        "governance_subscore": 0..25,
        "worst_case_loaded": {                    // MANDATORY twin per `are-scanner-api-v1@v1.0.2` §Hard guarantees-UW #2; priors collapsed for uncovered P4 dims
          "are_total": 0..100,
          "governance_subscore": 0..25,
          "applies_uw_guarantee_2": true | false, // true ⇔ any dim's evidence_uri resolves under scripts/agent_priors/*.json
          "rationale": "string"                   // human-readable explanation of which dims collapsed and why
        },
        "reasons": ["string", ...]
      },
      "attestation": {
        "eligible": true | false,
        "are_total": null,                        // attestation does NOT stand alone; contributes only to fused verdict
        "governance_subscore": 0..25,             // raw_self_report cap applied per BEAAA-214 Q3 / `uw-rubric-supervision-v1@v1.0` review_state tiers
        "review_state": "unreviewed | uw_reviewed | uw_rejected",
        "reasons": ["string", ...]
      }
    }
  }
}
```

### Semantics

- **`verdict.bind_eligibility` is the canonical pricing-engine input.** The pricing engine MUST consume `verdict.bind_eligibility` and MUST NOT compute its own fusion across `verdict.components.substrate.eligible` and `verdict.components.attestation.eligible`. Dual-route fusion at the pricing-engine layer is explicitly REJECTED by HoUW [BEAAA-766](/BEAAA/issues/BEAAA-766) (silent-uplift path; pricing-engine surface convergence on one verdict).
- **`selection_rule: "min(substrate, attestation)"` is the only supported fusion rule in v1.0.3.** Future rules (e.g. `weighted_substrate_attestation`) require a fresh CTO + UW + HoUW co-sign on a v1.0.4+ ticket; selection-rule deviation triggers HoUW per [BEAAA-766](/BEAAA/issues/BEAAA-766) forward watchpoint #1.
- **`substrate.worst_case_loaded` is mandatory.** `worst_case_loaded.applies_uw_guarantee_2 = true` whenever any scored dim's `artifact_uri` resolves under `scripts/agent_priors/*.json` (family-level prior, not deployment-measured). On that condition, `worst_case_loaded.governance_subscore` reflects P4.1-P4.6 (and any other uncovered dims) collapsed to per-dim floor (0 for P4 dims that have no prior measurement floor; per-pillar floor per `pillar_floors[*].min_dim_raw` where defined). Semantics change to `applies_uw_guarantee_2` triggers HoUW per [BEAAA-766](/BEAAA/issues/BEAAA-766) forward watchpoint #2.
- **`attestation.review_state` enum is the bind-blocking gate on P4.** `unreviewed` ⇒ P4 dims tier-2-capped per BEAAA-214 Q3 (applied mechanically, not narrated); `uw_reviewed` ⇒ tier cap lifted to the operator-defined tier on the row; `uw_rejected` ⇒ attestation `eligible = false` regardless of substrate. Enum widening triggers HoUW per [BEAAA-766](/BEAAA/issues/BEAAA-766) forward watchpoint #3.
- **`primary_reasons[]` order is the UW-actionability heuristic.** Attestation-driven reasons (UW review can move them) come before substrate-worst-case reasons (post-Phase-1 dim live-measurement closes the gap). This order is a presentation-layer recommendation; the pricing engine MUST NOT depend on order.
- **No score-math change.** `verdict.components.substrate.are_total` ≡ top-level `are_total` (optimistic-loaded, priors as-emitted). `verdict.components.substrate.governance_subscore` ≡ top-level `subscores.GOVERNANCE`. The verdict envelope is **derived** at packet-render time from fields already produced by the v0.3.1 scoring pipeline + the customer's P4 attestation fixture; no new judge invocation, no new corpus, no new model call.

## §Hard guarantees (CTO is on the hook for these) — cross-reference patch only

No clause text change. The existing v1.0.x §Hard guarantees-CTO #1 (Reproducibility), #2 (Evidence trail completeness), #3 (Pinned versions), #4 (Discrimination), #5 (Tamper resistance) bind the v1.0.3 surface identically:

- **#1 Reproducibility (±1).** v1.0.3 N=5 calibration delta on the golden set is **expected Δ = 0** on `are_total` and every subscore (verdict envelope is post-aggregation render, doesn't fire scoring math). ±1 enforced in CI per Supreme Law; >1 = release block.
- **#2 Evidence trail completeness.** Extends to every now-MUST-emit field: `confidence`, `uncovered_dimensions`, per-row `verdict`+`weight`, per-P4 `review_state`+`tier`. Outputs missing any of these are rejected by Underwriter on receipt.
- **#3 Pinned versions.** `scanner_version` MUST equal `0.3.2` (or later) for outputs carrying the v1.0.3 `verdict` envelope. Downstream consumers reading `verdict.*` MUST pin to `contract_version: are-scanner-api-v1@v1.0.3` (or later).
- **#4 Discrimination.** Unchanged — verdict envelope is presentation-only; pillar score distribution across the calibration set is unaffected.
- **#5 Tamper resistance.** Verdict is **derived** from substrate scores + attestation fixture; the scanned agent has no input surface to the verdict envelope distinct from its substrate-side or attestation-side inputs. No new tamper surface. Adversarial input handling for substrate inputs and attestation fixtures is unchanged.

## §Hard guarantees (Underwriter is on the hook for these) — clarification patch on #2

Replace the v1.0.x clause #2 prose to make the cross-link with `verdict.components.substrate.worst_case_loaded` explicit:

> 2. **Uncovered dimensions priced at maximum risk.** `uncovered_dimensions[]` are loaded into pricing as worst-case; the Underwriter does not "trust" missing dimensions. From v1.0.3 forward, this is mechanically reflected in `verdict.components.substrate.worst_case_loaded` — when `applies_uw_guarantee_2 = true`, the priors-collapsed `worst_case_loaded.are_total` and `worst_case_loaded.governance_subscore` are the load-bearing fields for any pricing decision that would otherwise rely on the optimistic-loaded `substrate.are_total`. Pricing engines MUST NOT bind on optimistic-loaded substrate when `applies_uw_guarantee_2 = true` unless `attestation.review_state ∈ {uw_reviewed, uw_rejected}` upgrades the relevant P4 dims off the prior.

Clauses #1, #3, #4 unchanged.

## §Approval gates — posture

This v1.0.3 patch is classified under "Rubric point added or removed (CTO + Underwriter)" by analogy — the verdict envelope + the per-P4 `review_state`/`tier` first-class promotion is an output-schema change with first-class semantics. **All required co-signs are already on record:**

- HoUW binding ruling: [BEAAA-766](/BEAAA/issues/BEAAA-766) (rule-change-class).
- UW conditional co-sign + named field gaps: [BEAAA-765](/BEAAA/issues/BEAAA-765).
- CTO design lock: [BEAAA-763 §3 + Appendix A](/BEAAA/issues/BEAAA-763) doc `66d0b303` rev `f6997607`.
- CEO acknowledgement: [BEAAA-763 cmt f8505b4f](/BEAAA/issues/BEAAA-763#comment-f8505b4f-a91a-49eb-a2a4-46a354f2300a).
- Founder gate: NOT triggered (per §front-matter `escalations_noted`).

**Cleared to merge** the scanner v0.3.1 → v0.3.2 release once implementation + N=5 delta (Δ = 0 expected) + ADR-0013 + AcmeBot v3 reference-packet byte-identical match land. Implementation is delegated to scanner-engineer (Phase 1 critical-path now `done`); CTO closes BEAAA-767 on merge.

## §Customer-facing adapter rule (NEW sub-section to §Output)

Add at end of §Output:

> **Customer-facing report-card adapter (v1.0.3).** When emitting a customer-visible report card (the artifact the customer sees in their portal or the Approval Pack), the adapter:
>
> - MUST emit `verdict.bind_eligibility` and `verdict.primary_reasons[]` (these are the carrier-readable verdict line and its rationale).
> - MUST emit `verdict.components.substrate.worst_case_loaded.{are_total, governance_subscore, applies_uw_guarantee_2, rationale}` (this is the "what-if-the-priors-don't-hold" forensic line; per UW guarantee 2, customers are entitled to see the conservative load that the binding-price is based on).
> - MUST emit `verdict.components.attestation.review_state` (the UW-review surface — customers should see whether their attestation is still `unreviewed`).
> - MAY emit `verdict.components.substrate.eligible` and `verdict.components.attestation.eligible` for forensic drill-down. The carrier-readable verdict line is `verdict.bind_eligibility` — these component eligibility booleans are NEVER the carrier-readable bind decision (see HoUW [BEAAA-766](/BEAAA/issues/BEAAA-766) §rejected-alternative "dual rendering"; silent-uplift risk).
> - MUST NOT emit any internal forensic field that exposes scoring weights, judge model identifiers, seed values, or per-dim raw values absent from the v1.0.x §Output customer-facing surface. The customer-facing surface is unchanged outside the `verdict` block.

## §Versioning — bump

Update the standing version pointer.

```
v1.0.2 → v1.0.3 (2026-05-27)
```

Existing v1.0.2 consumers that do not read the `verdict` block continue to receive the v1.0.2-shaped response augmented with the new top-level `verdict` block + four MUST-emit field promotions. **Pinned-version consumers** must explicitly opt into v1.0.3 to read `verdict.*` (the Underwriter pricing engine in particular MUST pin to `are-scanner-api-v1@v1.0.3` to consume `verdict.bind_eligibility`).

## §Version history — row appended

```
| v1.0.3 | 2026-05-27 | Fused bind-eligibility verdict envelope (top-level `verdict.{bind_eligibility, selection_rule, primary_reasons, components.{substrate, attestation}}`) plus four MUST-emit promotions (`confidence`, `uncovered_dimensions`, per-row `verdict`+`weight`, per-P4 `review_state`+`tier`). Additive output schema; existing pillar/dim payloads byte-for-byte unchanged. No scoring-math change, no judge-model change, no pillar-weight change, no rubric points added/removed. N=5 calibration Δ expected 0 on every fixture; ±1 enforced in CI per Supreme Law. Per HoUW BEAAA-766 binding ruling, UW BEAAA-765 conditional co-sign, CTO BEAAA-763 v0.2 evidence packet design lock, CEO BEAAA-763 cmt `f8505b4f` ack. Founder gate NOT triggered. | BEAAA-767 (+ scanner-engineer child) |
```

---

## File-path layout note (CTO architectural disposition)

The patch-doc-per-version pattern is preserved. This file at `contracts/are-scanner-api-v1.0.3-patch.md` joins:

- `contracts/are-scanner-api-v1.md` — v1.1.0 patch doc (live-probe additive bump; [BEAAA-547](/BEAAA/issues/BEAAA-547) commit `df0b921`, orthogonal change set touching §Input/§Output `scan_mode`/`k_replicates`/`spread`).
- `contracts/are-scanner-api-v1.0.2-patch.md` — v1.0.2 patch doc (OPS-bullet description clarification; [BEAAA-551](/BEAAA/issues/BEAAA-551), orthogonal change set touching §Score-structure text).
- `contracts/are-scanner-api-v1.0.3-patch.md` — this file (v1.0.3 verdict envelope; touches §Output verdict block + §Hard guarantees-UW #2 cross-ref, disjoint from the above).

The v1.0.x canonical baseline lives in agent-instruction snapshots only (per BEAAA-510 reference doc line 20). v1.0.x baseline + v1.0.2 patch + v1.0.3 patch + v1.1.0 patch compose without conflict — the four change sets touch disjoint regions of the contract.

Cross-team agent-instruction sync (other agents' instruction snapshots regenerated to v1.0.3) is a separate downstream operation owned centrally and tracked under the scanner-engineer child for v0.3.2.

---

## Supreme-Law surface check (8/8 clean expected; verification at-merge via N=5 against golden set)

| Surface | Status |
|---|---|
| Output shape | **ADDED** (new top-level `verdict.{…}` block + four MUST-emit field promotions; existing pillar/dim payloads byte-for-byte unchanged) |
| Field meanings | unchanged (verdict is derived from existing fields; promotions clarify but do not redefine semantics) |
| Scoring math | unchanged (no `rubric/scoring.py` / `rubric/rubric.yaml` / `rubric/bands.py` touch; verdict envelope is post-aggregation render) |
| Judge models | unchanged |
| Pillar weights | unchanged (25/25/25/25) |
| Tamper resistance | unchanged (verdict is derived, no new tamper surface) |
| Golden calibration | Δ_total = Δ_subscore = 0 expected; N=5 verification at-merge by scanner-engineer child against `2026-05-26-beaaa758-bias-pack-v0.2-rebaseline.md` baseline plus the AcmeBot v3 reference packet byte-identical match per BEAAA-767 acceptance criterion 6 |
| ADR attached | ADR-0013 on BEAAA-767 (scanner-engineer child drafts; CTO signs as standing rule-change-class co-sign) |

## Approval gate row-by-row (no Founder gate, no HoUW countersign beyond the BEAAA-766 binding ruling already on record)

- Pillar weight changes (Founder) — NOT triggered.
- Rubric point added/removed (CTO + UW) — TRIGGERED under "by analogy" classification (output-schema change with first-class verdict semantics); co-signed (HoUW BEAAA-766 binding + UW BEAAA-765 + CTO this doc).
- Scoring logic change (CTO + UW) — NOT triggered (no math change; verdict is presentation-only).
- New LLM judge (CTO) — NOT triggered.
- Golden score >1pt move (CTO + UW; Founder if >3pt) — NOT triggered (Δ = 0 expected).
- Test corpus version bump >5% rubric points (CTO + UW) — NOT triggered.
- Tamper resistance reduction (Founder) — NOT triggered.

## HoUW forward-binding watchpoints (re-triggers fresh HoUW co-sign on a v1.0.4+ ticket)

Per [BEAAA-766](/BEAAA/issues/BEAAA-766) ruling §forward-binding watchpoints, any of the following triggers fresh HoUW co-sign (NOT a silent v1.0.3.x point release):

1. `verdict.selection_rule` deviation from `"min(substrate, attestation)"` (e.g. weighted fusion, primary-side override).
2. Semantics change to `verdict.components.substrate.worst_case_loaded.applies_uw_guarantee_2` (e.g. priors-not-collapsed default, partial-collapse rule).
3. Enum widening on `verdict.components.attestation.review_state` (e.g. adding `uw_pending`, `uw_conditional`).
4. ≥1pt golden drift on any fixture under the v0.3.2 emitter (would also breach CTO Hard Guarantee #1).
5. Pillar-weight plumbing leak via field addition (e.g. emitting per-pillar `weight: 25` in a way the pricing engine could re-interpret).
6. Breaking rename in `baseline.json` writer (e.g. `bmi` → `are_total` legacy-key rename without a CTO+UW co-signed migration; tracked separately under [BEAAA-748](/BEAAA/issues/BEAAA-748) follow-ons, **out of scope** for v1.0.3).

## Cross-references

- Parent: [BEAAA-748](/BEAAA/issues/BEAAA-748) — Phase 1 scanner critical path (done; meta-tracker closed 2026-05-25, operational ship landed 2026-05-27 with BEAAA-752 close).
- This issue: [BEAAA-767](/BEAAA/issues/BEAAA-767) — contract patch v1.0.3 + scanner v0.3.2 workstream.
- Engineer child: scanner-engineer ticket for emitter implementation + N=5 calibration + ADR-0013 + AcmeBot v3 reference-packet byte-identical match (filed under [BEAAA-767](/BEAAA/issues/BEAAA-767), CTO this heartbeat).
- Binding HoUW ruling: [BEAAA-766](/BEAAA/issues/BEAAA-766) §Q2 fused-vs-dual.
- UW conditional co-sign + 4 named field gaps: [BEAAA-765](/BEAAA/issues/BEAAA-765).
- CTO design lock: [BEAAA-763 §3 + Appendix A](/BEAAA/issues/BEAAA-763) doc `66d0b303` rev `f6997607`.
- CEO ack: [BEAAA-763 cmt f8505b4f](/BEAAA/issues/BEAAA-763#comment-f8505b4f-a91a-49eb-a2a4-46a354f2300a).
- Founder scanner-first hold (cleared on Phase 1 ship): [BEAAA-754](/BEAAA/issues/BEAAA-754) (done).
- Sibling complete v1.0.2 patch: `contracts/are-scanner-api-v1.0.2-patch.md` + [BEAAA-551](/BEAAA/issues/BEAAA-551).
- Sibling complete v1.1.0 patch: `contracts/are-scanner-api-v1.md` + [BEAAA-547](/BEAAA/issues/BEAAA-547) (orthogonal).
- ADR: ADR-0013 (draft on scanner-engineer child) — BLUF + rejected alternative (dual rendering, per HoUW [BEAAA-766](/BEAAA/issues/BEAAA-766)) + rollback (drop verdict envelope + four MUST-emit field promotions; no data migration).

## Rollback

Tier 1, ≤2 min. Reversal procedure:

1. Drop the `verdict` top-level block from the live `ScoreResponse` emitter.
2. Drop the MUST-emit enforcement on `confidence`, `uncovered_dimensions[]`, per-row `verdict`+`weight`, per-P4 `review_state`+`tier` (those fields are already in the v1.0.x §Output baseline; reverting drops the MUST-emit gate, not the field definitions).
3. Pin the live scanner back to `0.3.1` and the contract reference back to `are-scanner-api-v1@v1.0.2`.
4. Existing v1.0.2-shaped responses byte-identical for the non-verdict surface; no data migration; no rebaseline.
5. Bound book unaffected (no policy has bound on a v1.0.3 verdict at v1.0.3 ship-time; if any binding happened post-ship, those policies stay on their bound-time `contract_version` per existing deprecation policy).
