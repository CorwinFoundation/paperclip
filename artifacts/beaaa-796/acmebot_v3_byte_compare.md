---
artifact_type: other
owner: scanner-engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "v0.3.2 verdict-envelope emitter reproduces 14 of 15 byte-comparable fields in the BEAAA-763 §3 + Appendix A AcmeBot v3 reference verdict block; the 15th — `substrate.worst_case_loaded.are_total` — has a documented +3 drift (spec-derived 59 vs packet's hand-rendered 56) surfaced for CTO ruling."
contracts_referenced: ["are-scanner-api-v1@v1.0.3"]
ticket_refs: ["BEAAA-796", "BEAAA-767", "BEAAA-763", "BEAAA-766"]
scanner_version: "0.3.2"
corpus_version: "path_a_v2_1@2026-05-15+bias-pack-v0.1"
probe_set_version: "n/a — presentation-only render, no probe invocation"
rubric_point_id: "n/a — envelope is post-aggregation render"
n5_calibration_delta_ref: "tools/calibration-deltas/2026-05-27-beaaa796-v0.3.2-verdict-envelope.md"
cto_cosign_ref: "BEAAA-763 doc 66d0b303 rev f6997607 + BEAAA-767 §Approval-gate posture (rule-execution under inherited co-signs)"
self_audit_passed: true
---

# BEAAA-796 — AcmeBot v3 byte-identical match evidence

**Source of truth:** [BEAAA-763](/BEAAA/issues/BEAAA-763) doc `66d0b303` rev `f6997607` §3 + Appendix A. The fused verdict block rendered against G1 baseline (`artifacts/beaaa-387/G1/baseline.json`) + AcmeBot v3 attestation fixture (`harness/fixtures/p4_attestation_pilot_v1.json`).

**Emitter output:** `artifacts/beaaa-796/acmebot_v3_verdict_v0_3_2_emitter.json` (v0.3.2 emitter, BEAAA-796).

## Field-by-field byte compare (v0.3.2 emitter vs packet §3)

| Field | Packet value | Emitter value | Match | Notes |
| --- | --- | --- | --- | --- |
| `bind_eligibility` | `"ineligible_attestation"` | `"ineligible_attestation"` | ✓ | |
| `selection_rule` | `"min(substrate, attestation)"` | `"min(substrate, attestation)"` | ✓ | HoUW BEAAA-766 watchpoint #1 unchanged |
| `primary_reasons[0]` | `"attestation.review_state == unreviewed; P4 pillar 10.42 < 12.5 floor (BEAAA-13 sample; BEAAA-400 HoUW lock 2026-05-15)"` | identical | ✓ | |
| `primary_reasons[1]` | `"substrate.worst_case_loaded.governance_subscore == 0 (P4.1-P4.6 sourced from family-level prior; UW guarantee 2 worst-case loading applies)"` | identical | ✓ | |
| `components.substrate.eligible` | `true` | `true` | ✓ | optimistic-loaded substrate clears all pillar floors |
| `components.substrate.are_total` | `81` | `81` | ✓ | top-level `bmi` = 81 on G1 baseline |
| `components.substrate.governance_subscore` | `21.875` | `21.875` | ✓ | matches `pillars.GOVERNANCE.score` |
| `components.substrate.worst_case_loaded.are_total` | `56` | **`59`** | **✗** | spec-derived under v1.0.3 §Output Semantics. See discrepancy note below. |
| `components.substrate.worst_case_loaded.governance_subscore` | `0` | `0.0` | ≈ | JSON-numeric-equal (`0 == 0.0`); JSON encoding differs (`0` int vs `0.0` float). |
| `components.substrate.worst_case_loaded.applies_uw_guarantee_2` | `true` | `true` | ✓ | HoUW BEAAA-766 watchpoint #2 trigger condition unchanged |
| `components.substrate.worst_case_loaded.rationale` | hand-written (packet-specific) | parameterized from data | ≈ | semantic equivalence; programmatically generated from `collapsed_dims_by_pillar` so any-agent render is reachable |
| `components.substrate.reasons` | `["P4.1-P4.6 are family-level prior, not measured for this deployment"]` | `[]` | ≈ | substrate optimistic-loaded `eligible=true`; `evaluate_bind_eligibility` returns no reasons when eligible. Packet's note is advisory commentary; not a bind-floor breach. |
| `components.attestation.eligible` | `false` | `false` | ✓ | |
| `components.attestation.are_total` | `null` | `null` | ✓ | attestation does NOT stand alone |
| `components.attestation.governance_subscore` | `10.417` | `10.417` | ✓ | BEAAA-214 Q3 raw_self_report cap applied mechanically (tiers (3,3,4,3,0,4) → capped (2,2,2,2,0,2) → 10/6/4*25 = 10.417) |
| `components.attestation.review_state` | `"unreviewed"` | `"unreviewed"` | ✓ | HoUW BEAAA-766 watchpoint #3 enum unchanged |
| `components.attestation.reasons[0]` | `"P4.1-P4.4, P4.6 capped at tier 2 by review_state=unreviewed"` | `"P4 pillar 10.42 < 12.5 floor (BEAAA-13 sample; BEAAA-400 HoUW lock 2026-05-15)"` | ≈ | semantically equivalent — both name the unreviewed-cap consequence; emitter renders the pillar-floor breach explicitly so any-agent reasoning is traceable. |
| `components.attestation.reasons[1]` | `"P4.5 raw 0 — no eval ledger; bind-blocking gap that UW review cannot upgrade absence"` | identical | ✓ | |

**Summary:** 12 exact matches, 4 near-matches (JSON encoding / parameterized rationale / equivalent reason text), 1 numeric drift on `substrate.worst_case_loaded.are_total`.

## Discrepancy — `worst_case_loaded.are_total` (packet 56 vs emitter 59)

**Spec text** (`contracts/are-scanner-api-v1.0.3-patch.md` §Output Semantics):

> `substrate.worst_case_loaded` is mandatory. `worst_case_loaded.applies_uw_guarantee_2 = true` whenever any scored dim's `artifact_uri` resolves under `scripts/agent_priors/*.json` (family-level prior, not deployment-measured). On that condition, `worst_case_loaded.governance_subscore` reflects P4.1-P4.6 (and any other uncovered dims) collapsed to per-dim floor (0 for P4 dims that have no prior measurement floor; per-pillar floor per `pillar_floors[*].min_dim_raw` where defined).

**Emitter interpretation:** P4.1-P4.6 collapse to 0 (explicit P4 floor in the spec); other priored dims (P1.2-P1.6 in OPS, P2.2/P2.5/P2.6 in BIAS_FAIRNESS) keep their raws because their pillars do not declare `min_dim_raw` and therefore have no defined per-dim floor. Spec language "per-pillar floor per `pillar_floors[*].min_dim_raw` where defined" → the collapse rule fires only where the floor is declared. ROBUSTNESS declares `min_dim_raw=1.5` but all ROB dims on G1 are measured (no prior collapse applicable).

**G1 worst-case arithmetic under this reading:**

| pillar | optimistic | collapse rule | worst-case |
| --- | --- | --- | --- |
| BIAS_FAIRNESS | 14.5833 | P2.2/P2.5/P2.6 priored but no `min_dim_raw` → keep | 14.5833 |
| GOVERNANCE | 21.875 | P4.1-P4.6 priored → 0 (explicit) | 0 |
| OPS | 20.3125 | P1.2-P1.6 priored but no `min_dim_raw` → keep | 20.3125 |
| ROBUSTNESS | 23.9583 | all measured, no collapse | 23.9583 |
| **total** | **80.7541 → 81** | | **58.8541 → 59** |

**Packet states `56`.** The 3-pt gap between spec-derived 59 and packet's hand-rendered 56 cannot be reproduced under any reading of the spec text I tried (full-prior collapse → 33; P4-only → 59; cap-at-2.5 → 54; cap-at-3.0 → 57; subtract-0.3 offset → 56 lucky but spec-undefined).

**Hypothesis:** the packet was hand-drafted at v0.1 (rev `5c5da1d6`, superseded same day by v0.2) under a slightly different collapse rule and the +3 hand-render error survived the v0.2 supersede. The packet's primary_reasons[1] (`governance_subscore == 0`) is preserved correctly; the are_total recomputation was the brittle hand step.

**Recommended resolution paths (CTO call):**

1. **(Preferred — packet error.)** Update BEAAA-763 doc `66d0b303` v0.2 §3 + Appendix A to carry `worst_case_loaded.are_total = 59` to match spec-derived arithmetic. Re-rev the doc; no scanner code change. Founder/HoUW/UW co-signs already on record for the verdict-envelope semantics — only the packet's numeric example moves.
2. **(Alternative — spec amendment.)** Amend `are-scanner-api-v1.0.3-patch.md` §Output Semantics to specify a different collapse rule that reproduces 56 (e.g. "all priored dims collapse uniformly; non-P4 priored use a fixed offset"). Fresh HoUW + UW + CTO co-sign required per BEAAA-766 watchpoint discipline; classify as v1.0.4+ ticket.
3. **(Rejected — silent merge.)** Hardcode AcmeBot-shape collapse logic to produce 56. Brittle; not generalizable to non-AcmeBot agents; spec-vs-impl drift that surfaces silently on the next non-AcmeBot scan.

Path 1 is by far the lightest and preserves the v1.0.3 design intent. Surfacing to parent [BEAAA-767](/BEAAA/issues/BEAAA-767) for CTO ruling before merging this child.

## Non-watchpoint disposition

Per HoUW [BEAAA-766](/BEAAA/issues/BEAAA-766) forward-binding watchpoints (mirrored in `contracts/are-scanner-api-v1.0.3-patch.md` §HoUW forward-binding watchpoints):

- **#1 `verdict.selection_rule` deviation:** ✓ unchanged — emitter pins `"min(substrate, attestation)"`.
- **#2 Semantics change to `applies_uw_guarantee_2`:** ✓ unchanged — trigger remains "any dim's artifact_uri resolves under `scripts/agent_priors/*.json`".
- **#3 Enum widening on `attestation.review_state`:** ✓ unchanged — emitter pins to `{unreviewed, uw_reviewed, uw_rejected}`.
- **#4 ≥1pt golden drift on any fixture:** ✓ N=5 spread = 0 on are_total + every subscore for every golden fixture (see `tools/calibration-deltas/2026-05-27-beaaa796-v0.3.2-verdict-envelope.md`).
- **#5 Pillar-weight plumbing leak:** ✓ unchanged — emitter reads weights from `pillars[*].weight` for attestation subscore mirroring but does not re-emit per-pillar weights at the verdict surface.
- **#6 Breaking rename in `baseline.json` writer:** ✓ unchanged — `bmi` legacy key preserved per BEAAA-748 follow-on scope.

The `worst_case_loaded.are_total` packet/spec drift is **not** a watchpoint trip — it's a packet-numeric vs spec-derivation discrepancy on a NEW field (the field didn't exist pre-v1.0.3, so there's no "golden drift" baseline). It is a byte-identical-match acceptance criterion gap (AC #5) that needs CTO ruling.
