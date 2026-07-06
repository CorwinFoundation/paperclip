---
artifact_type: adr
artifact_id: "ADR-0054-beaaa12055-tgf-provenance-emission"
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
version: 0.1
prior_version: null
contracts_referenced: ["are-scanner-api-v1"]
ticket_refs: ["BEAAA-12055", "BEAAA-12051", "BEAAA-12028", "BEAAA-11816"]
proof_points_cited: ["HaluEval hallucination benchmark"]
brand_backbone_applicable: false   # internal decision record, not an external-bound asset
status: ACCEPTED
accepted_date: 2026-07-06
contract_version: "are-scanner-api-v1@v1.8.0"
scanner_version: "0.5.0"
corpus_version: "rubric.yaml@1.2.0"
focus_claim: "Scanner-side follow-up to BEAAA-12028: the tool_grounded_factuality emitter now stamps a per-submission `provenance` sub-block {source, task_set, claim_level_value_entity_binding} on every deployment_measured evidence row (verdict rows + tamper-evident artifact rows) and on the run result. The ADR-0053 ADM.R0.FACT full-limit gate (BEAAA-12051) is fail-closed: a bare or reference-sourced row is consumed as `indicative` and refers to UW, so nothing binds full-limit until provenance is stamped. This closes that emission gap. Additive metadata on the v1.8.0 evidence-row shape — no sub-block contract-version bump, import-only, reported-not-scored per ADR-0045; behavioral_integrity.scored stays false, golden N=5 delta = 0 by construction. Fail-closed default: an unspecified run stamps reference_fixture (NOT full-limit eligible); a submission run against a representative business-factual task set must opt in explicitly."
self_audit_passed: true
self_audit_note: "Pre-submission self-audit (cto-os Supreme Law). (1) Reproducibility: additive metadata on import-only pilot rows; NO Scanner scoring-path code touched. Provenance sub-block is a pure function of the run configuration; sha256 fingerprints are computed over transcript / grounding-check blobs only, so tamper-evidence bytes are unchanged (asserted by test_provenance_addition_preserves_determinism). are_total / subscores / assign_band unchanged -> golden N=5 delta = 0 by construction (same class as BEAAA-12028). (2) Pillar weights 25/25/25/25 UNCHANGED; nothing enters a pillar or a 5th weighted dimension -> NO Founder gate. (3) Evidence trail: provenance rides the already-sha256-fingerprinted evidence row; every deployment_measured row now click-through-carries source/task_set/claim_level_value_entity_binding -> more evidence, not less. (4) Tamper resistance: provenance is set by the harness from the run configuration, never read from agent free text, so injected transcript text cannot forge source=submission_agent. (5) Canonical terms: BackBond Scanner / BackBond Score; ARE appears only in grandfathered schema/code fields (are-scanner-api-v1). (6) Carrier Rule: no carrier or customer named; catalog data is synthetic fixture. (7) Internal decision record -> Brand Backbone Step-0 N/A. (8) Co-sign: additive-evidence + reported-not-scored, nothing new auto-consumes the output; the CONSUMPTION conditions were already co-signed on ADR-0053 (HUW approved BEAAA-12051, CEO pending). This ticket is the delegated EMISSION half that ADR-0053 §2.1 named as the Scanner-side owner -> no new UW co-sign required to merge the emitter; UW is notified that provenance now flows."
escalations_noted:
  - "No Founder gate: pillar weights 25/25/25/25 unchanged; are_total math unchanged; band v1.0 frozen math unchanged; behavioral_integrity stays scored:false."
  - "No new UW co-sign to merge: the consumption gate (ADR-0053 / BEAAA-12051) is HUW-approved (CEO co-sign pending on that ticket, not this one). This is the emission half ADR-0053 §2.1 delegated to Scanner Eng / CTO; it is additive and fail-closed. UW is NOTIFIED that deployment_measured rows now carry provenance."
  - "Coordination point (not a blocker): the envelope assembler that builds the reported-not-scored behavioral_integrity block must surface ToolGroundedFactualityResult.provenance at behavioral_integrity.dimensions.tool_grounded_factuality.provenance — the primary read path of the gate's `_tool_grounded_provenance` accessor; same value, no transform. The gate also accepts a UW-record fallback key `tool_grounded_factuality_provenance`."
---

# ADR-0054 — Provenance emission on `tool_grounded_factuality` `deployment_measured` rows

**Status:** ACCEPTED (2026-07-06) — CTO-authored (agent `88e68019`) under [BEAAA-12055](/BEAAA/issues/BEAAA-12055). Scanner-side emission follow-up to [BEAAA-12028](/BEAAA/issues/BEAAA-12028); pairs with the [ADR-0053](adr-0053-beaaa12051-fact-bind-consumption-conditions.md) consumption gate ([BEAAA-12051](/BEAAA/issues/BEAAA-12051)). Additive, import-only, reported-not-scored, golden delta 0.

## 1. Context

[BEAAA-12028](/BEAAA/issues/BEAAA-12028) built the tool-grounded-factuality pilot that emits `tool_grounded_factuality` evidence rows at `evidence_grade = deployment_measured` — the first probe to earn `deployment_measured` (category-proxy HaluEval stays `indicative`).

[BEAAA-12051](/BEAAA/issues/BEAAA-12051) then tightened how the Underwriter **consumes** such a row. Per ADR-0053 §2.1 the ADM.R0.FACT dial grants a **full-limit** business-factual bind only when the row carries a `provenance` sub-block proving the measurement characterizes the **submission's own agent** on a **representative** task set — not the BackBond reference fixture (which validates the verifier, not any customer). The gate is **fail-closed**: a row without matching provenance is read as `indicative`, ADM.R0.FACT fires, and the exposure refers to UW.

The consequence: **nothing binds full-limit until the emitter stamps provenance.** ADR-0053 §2.1 named the Scanner-side emitter as the owner of that stamping. This ADR closes it.

## 2. Decision

On every `tool_grounded_factuality` row carrying `evidence_grade: "deployment_measured"`, emit an additive `provenance` sub-block:

```jsonc
"provenance": {
  "source": "submission_agent" | "reference_fixture",
  "task_set": "representative_business_factual" | "reference_faithful_fabricating",
  "claim_level_value_entity_binding": false
}
```

- **`source` / `task_set`** are a property of *what is being run*, not something the verifier can infer, so they are a run-configuration input:
  - `run_tool_grounded_factuality_task(..., provenance=...)` accepts a `Provenance`.
  - `REFERENCE_FIXTURE_PROVENANCE` = `reference_fixture` / `reference_faithful_fabricating` — the reference suite; validates the verifier, characterizes no customer.
  - `SUBMISSION_AGENT_PROVENANCE` = `submission_agent` / `representative_business_factual` — a run of the insured's own agent on a representative business-factual task set; the only provenance the gate treats as full-limit eligible.
- **`claim_level_value_entity_binding`** is **`false` for the v1 verifier.** v1 grounds every numeric token but does not bind a specific value to a specific claim/entity (documented v1 scope limit). Only a v2 verifier that closes per-claim value↔entity binding may set it `true`; until then the ADR-0053 §2.1 Condition-2 misattribution/arithmetic-derived exclusion stands.

### 2.1 Fail-closed default (load-bearing)

The default `provenance` for an unspecified run is `REFERENCE_FIXTURE_PROVENANCE`, which is **not** full-limit eligible. Full-limit is **opt-in**: a caller running a real submission MUST pass `SUBMISSION_AGENT_PROVENANCE` explicitly. A caller that forgets under-grants (safe, fail-closed) rather than accidentally granting full-limit — the same safety posture as the gate.

### 2.2 Where provenance rides

- **Verdict rows** (`evidence_refs_for_run`) — one per rubric point; provenance added **only** when the row's grade is `deployment_measured` (an `indicative` row would never be full-limit eligible, so provenance there is meaningless and is suppressed).
- **Tamper-evident artifact rows** (`_artifact_refs`) — `transcript` + `grounding_check`, both `deployment_measured`, both carry provenance. The `sha256` is computed over the transcript / grounding-check blobs, not the row envelope, so the sub-block does not perturb reproducibility.
- **Run result** (`ToolGroundedFactualityResult.provenance`) — the run-level value the envelope assembler surfaces at `behavioral_integrity.dimensions.tool_grounded_factuality.provenance`, which is the primary read path of the gate's `_tool_grounded_provenance` accessor (UW-record fallback key `tool_grounded_factuality_provenance` also accepted).

## 3. Classification

Additive metadata on the existing sha256-fingerprinted v1.8.0 evidence row. **No contract-version bump** for the sub-block itself (the row shape is `additionalProperties`-tolerant; coordinate with the openapi maintainer only if we choose to schematize it). Import-only, reported-not-scored per [ADR-0045](adr-0045-beaaa11816-behavioral-integrity-scoring-wiring.md): `behavioral_integrity.scored` stays `false`, `are_total` / pillar weights / band math unchanged, **golden N=5 delta = 0 by construction**.

## 4. Alternatives rejected

- **Infer provenance in the verifier.** Rejected: whether a run is the submission agent or the reference fixture is not observable from the transcript; inferring it would be guessable and forgeable by injected text. Provenance is set by the harness from the run configuration — tamper-resistant by construction.
- **Default to `submission_agent`.** Rejected: it would fail *open* (accidentally grant full-limit on a forgotten flag). Fail-closed to the reference fixture is the only safe default.
- **Stamp `claim_level_value_entity_binding: true` in v1.** Rejected: v1 does not close per-claim value↔entity binding; asserting it would let the gate lift an exclusion the verifier has not earned.

## 5. Rollback

Revert one commit. Provenance is additive; removing it returns rows to the BEAAA-12028 shape. The consumption gate is fail-closed, so a rollback simply reverts every `deployment_measured` exposure to sub-limit / REFER — no pricing breaks, it only removes the full-limit path.

## 6. Verification

`tests/test_tgf_provenance_beaaa12055.py` (8 cases): default stamps reference-fixture provenance on every row + result; the reference fixture does NOT satisfy the gate's full-limit predicate shape; an explicit submission run earns `submission_agent` / `representative_business_factual`; v1 never claims claim-level binding; provenance is confined to `deployment_measured` rows (suppressed on `indicative`); determinism (ledger fingerprints + evidence sha256) is preserved. Full pilot suite (`test_tool_grounded_factuality_beaaa12028.py`, 11 cases) still green — 19 passed.
