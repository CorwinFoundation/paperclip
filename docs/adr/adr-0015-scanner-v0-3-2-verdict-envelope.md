---
artifact_type: adr
owner: scanner-engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "v0.3.2 wires the fused `verdict` envelope + four MUST-emit field promotions at the live ScoreResponse boundary, additive and presentation-only — score math, pillar weights, judge model, and tamper-resistance posture all unchanged."
contracts_referenced: ["are-scanner-api-v1@v1.0.3", "scanner-implementation-supervision-v1@v1.0", "uw-rubric-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-796", "BEAAA-767", "BEAAA-766", "BEAAA-765", "BEAAA-763", "BEAAA-748", "BEAAA-214", "BEAAA-200"]
proof_points_cited: [
  "HoUW BEAAA-766 binding ruling (rule-change-class per uw-rubric-supervision-v1@v1.0)",
  "UW BEAAA-765 conditional co-sign + 4 named field gaps",
  "CTO design lock BEAAA-763 doc 66d0b303 rev f6997607 §3 + Appendix A",
  "CEO ack BEAAA-763 cmt f8505b4f-a91a-49eb-a2a4-46a354f2300a",
  "Patch doc contracts/are-scanner-api-v1.0.3-patch.md",
  "N=5 calibration delta tools/calibration-deltas/2026-05-27-beaaa796-v0.3.2-verdict-envelope.md (PASS, spread=0 on every golden fixture)",
  "AcmeBot v3 byte-compare artifacts/beaaa-796/acmebot_v3_byte_compare.md"
]
scanner_version: "0.3.2"
corpus_version: "path_a_v2_1@2026-05-15+bias-pack-v0.1"
probe_set_version: "n/a — presentation-only render, no probe invocation"
rubric_point_id: "n/a — envelope is post-aggregation render"
n5_calibration_delta_ref: "tools/calibration-deltas/2026-05-27-beaaa796-v0.3.2-verdict-envelope.md"
cto_cosign_ref: "Inherited from parent BEAAA-767 §Approval-gate posture: HoUW BEAAA-766 + UW BEAAA-765 + CTO BEAAA-763 + CEO ack f8505b4f. Founder NOT required."
adr_slot_note: "Original parent BEAAA-767 referenced 'ADR-0013'; that slot landed earlier under bias-pack v0.1 (BEAAA-749). This ADR takes the next free slot, 0015 (after 0014 bias-pack v0.3). Slot renumber surfaced in BEAAA-796 close-out per supervision-contract glossary-incident discipline."
self_audit_passed: true
---

# ADR-0015 — Scanner v0.3.2 verdict envelope + four MUST-emit field promotions

**Status:** proposed; awaits CTO sign on parent [BEAAA-767](/BEAAA/issues/BEAAA-767) on merge. Co-sign matrix inherited (HoUW BEAAA-766 + UW BEAAA-765 + CTO BEAAA-763 + CEO ack `f8505b4f`); Founder NOT required.
**Date:** 2026-05-27.
**Author:** Scanner Engineer ([BEAAA-796](/BEAAA/issues/BEAAA-796)).
**Implements:** `contracts/are-scanner-api-v1.0.3-patch.md` §Output, §Customer-facing adapter rule, §Hard guarantees-CTO #3 (pinned versions), §Hard guarantees-UW #2 (worst-case loading).

## BLUF

The live ScoreResponse boundary in `harness/cli_runner.py::_to_contract_output` now emits a top-level `verdict` envelope computed from existing pillar scores + the customer's P4 attestation fixture (no new judge invocation, no new corpus, no new model call, no scoring-math change). Four already-documented fields — `confidence`, `uncovered_dimensions[]`, per-row `verdict`+`weight` on `evidence_refs[]`, per-P4 `review_state`+`tier` on `evidence_refs[]` — are now MUST-emit on every output. Scanner version bumped `0.3.1 → 0.3.2`; contract reference pinned to `are-scanner-api-v1@v1.0.3`. The customer-facing JSON adapter at `report_card/customer_adapter.py` strips internal forensic surfaces (judge model identifiers, seed values, snapshot_hash, forensic_bundle_uri) per the v1.0.3 §Customer-facing adapter rule. N=5 calibration delta against the golden roster (G1, G2, G3, B1, B3) shows spread=0 on `are_total` and every pillar subscore for every fixture — verdict envelope is additive (post-aggregation render); scoring math invariant from v0.3.1.

## Context

The v1.0.3 patch ([BEAAA-767](/BEAAA/issues/BEAAA-767)) lands the contract-side surface change agreed at HoUW [BEAAA-766](/BEAAA/issues/BEAAA-766) (rule-change-class binding ruling) + UW [BEAAA-765](/BEAAA/issues/BEAAA-765) (conditional co-sign with 4 named field gaps) + CTO [BEAAA-763](/BEAAA/issues/BEAAA-763) (v0.2 evidence-packet design lock against AcmeBot v3 substrate). The contract patch is presentation-only — the pricing engine consumes one `verdict.bind_eligibility` enum at scan-time rather than computing its own fusion across `verdict.components.substrate.eligible` and `verdict.components.attestation.eligible`. Dual rendering at the pricing-engine layer is explicitly REJECTED (HoUW [BEAAA-766](/BEAAA/issues/BEAAA-766) §rejected-alternative — silent-uplift risk).

[BEAAA-796](/BEAAA/issues/BEAAA-796) is the implementation child: wire the v0.3.2 emitter to render the v1.0.3 envelope + enforce the four MUST-emit field promotions, validate with N=5 against the golden roster (expected Δ=0 since the envelope is post-aggregation render), validate byte-identical AcmeBot v3 reference-packet match against BEAAA-763 §3 + Appendix A, then merge with this ADR attached and the scanner version bumped 0.3.1 → 0.3.2.

## Decision

1. **Verdict envelope rendered at `harness/cli_runner.py::_to_contract_output`** — the live customer-facing emit boundary. New pure module `harness/verdict_envelope.py` carries the rendering logic; it is invoked AFTER `rubric.scoring.score` returns and never feeds back into scoring math. The fused enum is computed via `min(substrate.eligible, attestation.eligible)` semantics with `review_required` reserved for the edge case where both sides pass optimistically but attestation is unreviewed AND substrate leans on family-level priors that worst-case-load to ineligible.

2. **MUST-emit field promotions enforced** on every output:
   - `confidence` — already emitted by cli_runner since BEAAA-464; preserved on every output (UW guarantee 1 evidence-completeness extends to confidence presence).
   - `uncovered_dimensions[]` — already emitted by cli_runner; preserved on every output; cross-referenced by `verdict.components.substrate.worst_case_loaded.applies_uw_guarantee_2`.
   - Per-row `verdict` (enum `pass`/`partial`/`fail`/`n/a`) + `weight` on every `evidence_refs[]` entry — already emitted by cli_runner since BEAAA-464.
   - Per-P4-dim `review_state` (enum `unreviewed`/`uw_reviewed`/`uw_rejected`) + `tier` (1-4 integer per `uw-rubric-supervision-v1@v1.0`) on every P4 `evidence_refs[]` entry — NEW first-class via `harness/verdict_envelope.py::attach_p4_evidence_metadata`. Reads `review_state` off the customer-submitted attestation fixture (defaults to `unreviewed`) and per-dim claimed tier off `_metadata.expected_form_claimed_tiers`. The BEAAA-214 Q3 raw_self_report tier-2 cap is applied mechanically under `review_state=unreviewed` (previously narrated in markdown only).

3. **`substrate.worst_case_loaded` computed per v1.0.3 §Output Semantics.** `applies_uw_guarantee_2 = true` when any scored dim's `artifact_uri` resolves under `scripts/agent_priors/*.json`. On that condition, P4 dims (governance) collapse to 0 (explicit per-spec floor); other priored dims keep their raws because their pillars have no declared `min_dim_raw`. ROBUSTNESS declares `min_dim_raw=1.5` but all ROB dims on the golden roster are measured (no prior collapse applicable). Recomputed `worst_case_loaded.are_total` and `worst_case_loaded.governance_subscore` are emitted alongside the optimistic `substrate.{are_total, governance_subscore}`.

4. **BEAAA-214 Q3 raw_self_report tier-2 cap applied mechanically.** When P4 `review_state == "unreviewed"`, claimed tiers above 2 collapse to 2 (raw_self_report cap); claims at ≤2 pass through. The capped tiers feed the attestation `governance_subscore` computation that surfaces in `verdict.components.attestation.governance_subscore`. AcmeBot v3 example: claimed tiers (3, 3, 4, 3, 0, 4) → capped (2, 2, 2, 2, 0, 2) → 10/6/4*25 = 10.417.

5. **Scanner version bumped `0.3.1 → 0.3.2`** in `scanners/__init__.py`. Contract reference pinned to `are-scanner-api-v1@v1.0.3` (same file). Both pins surface on every emitted ScoreResponse via the existing `scanner_version` / `contract_version` stamps.

6. **Customer-facing JSON adapter** at `report_card/customer_adapter.py::to_customer_payload`. Drops `seed`, `model_versions`, `snapshot_hash`, `forensic_bundle_uri`, `cli_meta`, `input_file_sha256s`, `diagnostics` from the customer-visible payload per the v1.0.3 §Customer-facing adapter rule. Verdict envelope forwarded as-is (carrier-readable line is `verdict.bind_eligibility`; component `eligible` booleans MAY remain for forensic drill-down but are NEVER the carrier-readable bind decision).

## Rejected alternative

**Dual rendering** — emitting both `substrate` and `attestation` as parallel top-level verdicts and letting the pricing engine fuse them. Rejected by HoUW [BEAAA-766](/BEAAA/issues/BEAAA-766) §Q2 ruling on the following grounds (mirrored from the ruling):

1. **Contract alignment.** `are-scanner-api-v1@v1.0.2` §Hard guarantees-UW #2 forbids the silent-uplift path — an unreviewed attestation IS functionally uncovered. Dual rendering invites the substrate side to be taken as accept-on-receipt.
2. **Pricing-engine API surface.** The engine consumes one verdict-on-receipt. The strategic choice belongs at scan-time, baked into one verdict, not at the engine-routing layer.
3. **No-bind training value preserved.** Same decline outcome as the dual rendering for AcmeBot v3; the rule that produced it is now explicit.
4. **Forensic component breakdown stays.** Substrate vs. attestation breakdown lives in `verdict.components.*` for drill-down. Just not at the top-level verdict line.
5. **Carrier-readability.** A carrier reading "substrate-eligible vs. attestation-ineligible" side-by-side would ask which we'd actually bind on. The answer can only be the conservative one — so render the conservative one.

Selection-rule deviation from `min(substrate, attestation)` (e.g. weighted fusion, primary-side override) triggers fresh HoUW co-sign on a v1.0.4+ ticket per [BEAAA-766](/BEAAA/issues/BEAAA-766) forward-binding watchpoint #1.

## Rollback (Tier 1, ≤2 min)

Reversal procedure if the v0.3.2 release is rolled back to v0.3.1:

1. **Drop the `verdict` top-level block** from `harness/cli_runner.py::_to_contract_output` (remove the `compute_verdict_envelope` call + the resulting `contract_output["verdict"]` assignment).
2. **Drop the MUST-emit enforcement on per-P4-row `review_state`+`tier`** by removing the `attach_p4_evidence_metadata` call in the same function. The other three MUST-emit promotions (`confidence`, `uncovered_dimensions[]`, per-row `verdict`+`weight`) pre-date v0.3.2 and stay — they were already shipped under BEAAA-464.
3. **Revert `scanners/__init__.py`**: `SCANNER_VERSION = "0.3.2" → "0.3.1"` and `CONTRACT_VERSION = "are-scanner-api-v1@v1.0.3" → "are-scanner-api-v1@v1.0"`.
4. **Remove module files** (no callers outside cli_runner): `harness/verdict_envelope.py`, `report_card/customer_adapter.py`. Tests at `tests/test_verdict_envelope_beaaa796.py` may stay (they'll skip with ImportError) or be removed.
5. **Revert the small defensive fix** in `report_card/render.py::evaluate_bind_eligibility` that skips `applicable=False`/`raw=None` dims under bind_blocking_floor / min_dim_raw checks. This is a defensive guard (no behavior change for live evals where no N/A dim ever has `bind_blocking_floor.kind` set) so reverting is optional; recommended to keep for safety.

Existing v1.0.2-shaped responses byte-identical for the non-verdict surface. No data migration. No rebaseline (N=5 spread=0 on every golden fixture under v0.3.2 — the optimistic substrate scoring math is invariant from v0.3.1). Bound book unaffected (no policy has bound on a v1.0.3 verdict at v0.3.2 ship-time).

## N=5 calibration evidence (CTO Hard Guarantee #1 ±1 band)

Per `tools/calibration-deltas/2026-05-27-beaaa796-v0.3.2-verdict-envelope.md` — N=5 re-score of the golden roster under the v0.3.2 emitter:

| fixture | are_total | Δ are | Δ BIAS | Δ GOV | Δ OPS | Δ ROB | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| G1 | 79 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | PASS |
| G2 | 77 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | PASS |
| G3 | 73 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | PASS |
| B1 | 39 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | PASS |
| B3 | 64 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | PASS |

**Conclusion: PASS** — N=5 spread = 0 on `are_total` and every pillar subscore for every golden fixture. v0.3.2 verdict envelope is additive (post-aggregation render); scoring math invariant from v0.3.1. Supreme Law ±1 band cleared with room.

(The on-disk artifacts/beaaa-387/G1/baseline.json carries 81 from the BEAAA-758 v0.2 rebaseline while eval_outputs.json carries v0.1-era raws → the calibration_delta utility reports a -2 attribution=scanner_only "drift" against the optimistic-loaded scanner. That drift is pre-existing in master, not introduced by v0.3.2, and surfaces as a master-state inconsistency unrelated to this ticket. See BEAAA-796 close-out comment for the disposition.)

## AcmeBot v3 reference-packet byte-comparison

14 of 15 byte-comparable fields in the BEAAA-763 §3 + Appendix A reference verdict block reproduce identically under the v0.3.2 emitter. One numeric drift surfaces on `substrate.worst_case_loaded.are_total` — packet hand-renders `56`, spec-derived arithmetic gives `59`. Detail at `artifacts/beaaa-796/acmebot_v3_byte_compare.md`. Recommended resolution path: update BEAAA-763 doc `66d0b303` v0.2 §3 + Appendix A to carry `56 → 59` to match spec-derived arithmetic (lightest path; preserves the v1.0.3 design intent; no scanner code change). Surfaced to parent [BEAAA-767](/BEAAA/issues/BEAAA-767) for CTO ruling before final merge of this child.

This is NOT a HoUW BEAAA-766 watchpoint trip — the field is new in v1.0.3 (no pre-v1.0.3 golden baseline to drift against); the trigger condition `applies_uw_guarantee_2` and the selection rule are unchanged; the optimistic substrate `are_total` is unchanged at 81; N=5 spread on `are_total` + every subscore is 0 across the golden roster.

## Supreme-Law surface check (8/8 expected; verified)

| Surface | Status |
| --- | --- |
| Output shape | **ADDED** (new top-level `verdict.{…}` block + four MUST-emit field promotions; existing pillar/dim payloads byte-for-byte unchanged) |
| Field meanings | unchanged (verdict is derived from existing fields; promotions clarify but do not redefine semantics) |
| Scoring math | unchanged (`rubric/scoring.py` / `rubric/rubric.yaml` / `rubric/bands.py` not touched; verdict envelope is post-aggregation render) |
| Judge models | unchanged |
| Pillar weights | unchanged (25/25/25/25) |
| Tamper resistance | unchanged (verdict is derived, no new tamper surface) |
| Golden calibration | Δ = 0 verified at-merge (N=5 spread=0 across G1+G2+G3+B1+B3) |
| ADR attached | this file (ADR-0015) |

## Cross-references

- Parent: [BEAAA-767](/BEAAA/issues/BEAAA-767) — contract patch v1.0.3 + scanner v0.3.2 workstream (CTO closes on merge of this child).
- This issue: [BEAAA-796](/BEAAA/issues/BEAAA-796) — scanner v0.3.2 emitter implementation.
- Co-sign matrix:
  - HoUW binding ruling: [BEAAA-766](/BEAAA/issues/BEAAA-766).
  - UW conditional co-sign + 4 named field gaps: [BEAAA-765](/BEAAA/issues/BEAAA-765).
  - CTO design lock: [BEAAA-763](/BEAAA/issues/BEAAA-763) doc `66d0b303` rev `f6997607`.
  - CEO ack: [BEAAA-763 cmt f8505b4f](/BEAAA/issues/BEAAA-763#comment-f8505b4f-a91a-49eb-a2a4-46a354f2300a).
- Contract patch doc: `contracts/are-scanner-api-v1.0.3-patch.md`.
- N=5 calibration delta: `tools/calibration-deltas/2026-05-27-beaaa796-v0.3.2-verdict-envelope.md`.
- AcmeBot v3 byte-compare: `artifacts/beaaa-796/acmebot_v3_byte_compare.md`.
- Out-of-scope follow-ons under [BEAAA-748](/BEAAA/issues/BEAAA-748): full-loop tamper-resistance test against steered-judge attempt; `forensic_bundle_uri` plumbing to customer-VPC artifact store; `bmi` → `are_total` legacy-key rename in `baseline.json` writer.

## ADR slot rename note

Parent [BEAAA-767](/BEAAA/issues/BEAAA-767) and contract patch doc reference "ADR-0013". That slot landed earlier under bias-pack v0.1 via BEAAA-749 (`docs/adr/adr-0013-bias-pack-v0.1.md`); ADR-0014 also taken by bias-pack v0.3 (BEAAA-759). This ADR takes the next free slot, **0015**. Slot renumber surfaced explicitly per supervision contract glossary-incident discipline (canonical-reference discipline keeps the parent ticket's pointer recoverable).
