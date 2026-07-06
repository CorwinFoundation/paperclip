---
artifact_type: adr_contract
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
brand_backbone_applicable: false   # internal contract patch, not an external-bound asset
focus_claim: "v1.11.0 contract patch (are-scanner-api-v1 v1.10.0 -> v1.11.0) adds TWO additive, reported-not-scored fields to behavioral_integrity.tool_grounded_factuality: (1) a `provenance` object {source, agent_ref, task_set_ref, task_set_kind} that says WHOSE agent and WHICH task set a tool-grounded-factuality grade was measured on, so the Underwriter's ADM.R0.FACT admittance gate can require that a `deployment_measured` row characterizes the submission's OWN insured agent on a REPRESENTATIVE deployment task set — never the reference verifier fixture; and (2) an OPTIONAL `verifier_version` int that lets the interim Misattribution-of-Value & Derived-Figure Exclusion sunset automatically at verifier_version>=2. Both are metadata on the reported-not-scored behavioral_integrity block; neither enters are_total, any pillar weight, or a band. scored:false is preserved. Golden N=5 delta=0 by construction. A consumer that ignores both reads a byte-identical v1.10.0 envelope."
contracts_referenced: ["are-scanner-api-v1@v1.10.0 (shipped) -> v1.11.0 (proposed additive minor)", "uw-fact-consumption-gate-v0-1 (HUW spec, BEAAA-12051)"]
ticket_refs: ["BEAAA-12052", "BEAAA-12051", "BEAAA-12028", "BEAAA-11838"]
proof_points_cited:
  - "underwriting/admittance.py: _tool_grounded_provenance / _tool_grounded_verifier_version accessors + _r0_fact_evidence_grade provenance predicate + _needs_misattribution_exclusion."
  - "pricing/quote.py: _misattribution_exclusion_endorsement attached on AUTO_BIND/CONSTRAINED_BIND when the decision carries business_factual_v1_exclusion_required (v1 row)."
  - "tests/test_fact_consumption_gate_beaaa12052.py: fixture-provenance REFER; own-agent representative full-limit; fail-closed on missing/mismatch/absent-insured-ref; verifier_version sunset; endorsement wiring + premium-unchanged."
  - "Reproducibility: both fields are reported-not-scored metadata off every scoring path; behavioral_integrity.scored stays false; golden N=5 delta=0 by construction (same class as v1.8.0 / v1.10.0)."
version: "v1.11.0"
prior_version: "are-scanner-api-v1@v1.10.0"
self_audit_passed: true
self_audit_note: "Pre-submission self-audit (cto-os Supreme Law). (1) Focus claim cites the two new reported-not-scored fields, the exact consumer sites (admittance.py provenance predicate + quote.py endorsement), and the N=5 delta=0 gate. (2) Carrier Rule clean — no carrier or customer named. (3) Canonical terms only — 'the Scanner' / 'tool-grounded-factuality' / 'admittance gate'; ARE appears only in literal identifiers (are_total, are-scanner-api-v1). (4) Internal patch, not an external-bound asset -> Brand Backbone Step-0 gate N/A (persona_audience: internal). (5) Supreme Law / reproducibility: the four reproducibility fields (scanner_version, corpus_version, model_versions, seed) are UNCHANGED and remain required; provenance/verifier_version are ADDITIVE reported-not-scored metadata; behavioral_integrity.scored stays false; golden N=5 delta=0 verified. NO scoring math, NO rubric dim/weight/band/curve change. (6) Tamper resistance: strengthened — the provenance gate is fail-closed, so a scanned agent cannot walk the dial past our own reference fixture, and a missing/forged provenance refers to UW rather than binding. (7) NO pillar-weight / rubric-weight / scoring-math / judge change; pillar weights stay 25/25/25/25 Founder-locked -> NO Founder gate. (8) Co-sign: this is a response/output-shape contract change AND it wires the ADM.R0.FACT admittance dial -> Underwriter co-sign REQUIRED (HUW spec author) + CEO admittance-dial re-cosign REQUIRED; this patch is DRAFT on a DO-NOT-MERGE PR and does NOT self-merge. (9) Rollback: Tier-2 PR-revert; a consumer that ignores the two fields reads a byte-identical v1.10.0 response for all pre-existing keys."
escalations_noted:
  - "Version selection — v1.11.0 (v1.10.0 + 1 minor). Registry check: contracts/ carries patch files through are-scanner-api-v1-v1.10.0-patch.md; this is the next unused minor. CONTRACT_VERSION literal in scanners/__init__.py stays are-scanner-api-v1@v1.3.0 (pinned by tests/test_verdict_envelope_beaaa796.py); additive minors since v1.5.0 are documented via patch files, not the literal — unchanged here."
  - "Underwriter co-sign REQUIRED (output-shape change + admittance-dial consumption). CEO admittance-dial re-cosign REQUIRED per underwriting/admittance_rules.yaml header. Both gated on BEAAA-12051; DO-NOT-MERGE until both land."
  - "NO Founder gate: provenance/verifier_version are reported-not-scored metadata on behavioral_integrity, NOT response evidence_refs and NOT are_total inputs; the Founder evidence-ref freeze and 25/25/25/25 weight lock are untouched."
---

# Contract patch — `are-scanner-api-v1.md` v1.10.0 -> v1.11.0

**Status:** DRAFT authored + CTO-reviewed (agent `88e68019`) under [BEAAA-12052](/BEAAA/issues/BEAAA-12052), implementing the HUW consumption-gate spec [`uw-fact-consumption-gate-v0-1`](/BEAAA/issues/BEAAA-12051#document-uw-fact-consumption-gate-v0-1) on [BEAAA-12051](/BEAAA/issues/BEAAA-12051). **Output-shape change + admittance-dial consumption ⇒ Underwriter co-sign + CEO admittance-dial re-cosign REQUIRED before merge.** DO-NOT-MERGE until both land.

**Design authority:** CTO (contract design) + HUW (consumption-gate spec author). Implementation authority: CTO (`underwriting/admittance.py`, `pricing/quote.py`). Approval gates: Head of Underwriting (spec + output-shape) and CEO (admittance-dial re-cosign per `admittance_rules.yaml` header).

**Change classification:** additive minor. TWO new OPTIONAL fields on `behavioral_integrity.tool_grounded_factuality`. No existing field renamed, removed, or reshaped. A consumer that ignores both reads a **byte-identical v1.10.0 response** for every pre-existing key. Tier-2 PR-revert rollback.

## §0. Why now — the grade letter is not self-describing about WHOSE agent it measured

The v1.8.0 `tool_grounded_factuality` block carries `evidence_grade ∈ {indicative, deployment_measured}` but **no provenance**. [BEAAA-12028](/BEAAA/issues/BEAAA-12028) then shipped the pilot that earns `deployment_measured`. On the methodology co-sign, the Head of Underwriting flagged that a `deployment_measured` row from the **reference faithful/fabricating fixture** — which validates the verifier but characterizes no customer — would, under the grade-only ADM.R0.FACT gate, grant a **full-limit** business-factual bind. Binding on the fixture is binding on our own test double. Closing that requires the envelope to say **whose** agent and **which** task set produced the grade. This patch adds exactly that, so the Underwriter's admittance gate can read provenance rather than the grade letter alone.

## §1. New field — `provenance` (additive, reported-not-scored)

Emitted inside `behavioral_integrity.tool_grounded_factuality`:

```jsonc
"tool_grounded_factuality": {
  "reported_raw": 0.0,
  "evidence_grade": "indicative | deployment_measured",   // v1.8.0 §1 Gap-3 tag
  "provenance": {                                          // NEW v1.11.0 — reported-not-scored
    "source": "reference_fixture | deployment_representative",   // required
    "agent_ref": "string",        // id/hash of the scanned agent; MUST equal the insured agent
    "task_set_ref": "string",     // id/label of the business-factual task set measured
    "task_set_kind": "reference_fixture | representative_deployment"
  },
  "verifier_version": 1,                                   // NEW v1.11.0 — OPTIONAL int (see §2)
  "evidence_refs": [ /* class: tool_grounded_factuality rows */ ]
}
```

- **`source` / `task_set_kind` == `reference_fixture`** is the fixture that validates the verifier; it **never** characterizes a customer.
- **`source` == `deployment_representative`** + **`task_set_kind` == `representative_deployment`** + **`agent_ref` == the submission's insured agent** is the only combination that lets a `deployment_measured` row grant a **full-limit** business-factual bind (ADM.R0.FACT, ADR-0053).
- **Semantics are UW-consumed, not scored.** The Scanner reports provenance; the Underwriter's admittance dial decides eligibility. Missing / unknown / `reference_fixture` provenance is **fail-closed** at consumption (the row is treated as indicative → sub-limit / REFER).

## §2. New field — `verifier_version` (additive, OPTIONAL, reported-not-scored)

An OPTIONAL integer naming the major version of the verifier that produced the row. The v1 verifier grounds on **set membership** (catches invented numbers, not misattributed or arithmetic-derived ones). The Underwriter attaches an interim **Misattribution-of-Value & Derived-Figure Exclusion** to any full-limit business-factual bind resting on a `verifier_version < 2` row; a v2+ verifier that closes per-claim value↔entity binding **retires** the exclusion automatically. Absent ⇒ treated as v1 (the only verifier that exists today), which conservatively keeps the exclusion attached.

## §3. Reproducibility fields — intact

The four Supreme-Law reproducibility stamps (`scanner_version`, `corpus_version`, `model_versions`, `seed`) are **unchanged and still required**. `provenance` and `verifier_version` are reported-not-scored metadata off every scoring path; `behavioral_integrity.scored` stays **false**. Golden N=5 delta = **0 by construction** — no rubric / probe / judge / corpus / scoring-math change.

## §4. Approval-gate disposition

- **Underwriter co-sign: REQUIRED** — output-shape change AND it wires the ADM.R0.FACT admittance dial the Underwriter authored the spec for ([BEAAA-12051](/BEAAA/issues/BEAAA-12051)).
- **CEO admittance-dial re-cosign: REQUIRED** — per the `underwriting/admittance_rules.yaml` header (ADR-0007 §7 / ADR-0045 §4); raised on the DO-NOT-MERGE PR.
- **No Founder gate** — `provenance` / `verifier_version` are reported-not-scored metadata on `behavioral_integrity`, not response `evidence_refs` and not `are_total` inputs; the Founder evidence-ref freeze and the 25/25/25/25 weight lock are untouched.
- **CONTRACT_VERSION literal:** unchanged (`are-scanner-api-v1@v1.3.0`). Additive minors since v1.5.0 are documented via patch files, not the runtime literal.

## §5. Rollback

Tier-2 PR-revert. Both fields are additive; a consumer that has not upgraded reads a byte-identical v1.10.0 response for all pre-existing keys until the revert lands. The consumption logic additionally ships behind the `ADM.R0.FACT` `enabled` flag (ADR-0007 dial), so the gate can be routed-past without a code revert (itself a fresh HUW+CEO re-cosign).

## §6. Changelog

| Version | Date | Change | Refs |
|---|---|---|---|
| **v1.11.0** | **2026-07-06** | **additive minor — reported-not-scored `provenance` object + optional `verifier_version` on `tool_grounded_factuality`, consumed by the ADM.R0.FACT bind-consumption gate (provenance) and the interim misattribution exclusion sunset (verifier_version); no scoring change** | **This patch; spec `uw-fact-consumption-gate-v0-1` ([BEAAA-12051](/BEAAA/issues/BEAAA-12051)); design ADR-0053 ([BEAAA-12052](/BEAAA/issues/BEAAA-12052)); grade producer [BEAAA-12028](/BEAAA/issues/BEAAA-12028)** |
