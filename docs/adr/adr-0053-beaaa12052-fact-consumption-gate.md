---
artifact_type: adr
artifact_id: "ADR-0053-beaaa12052-fact-consumption-gate"
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
version: 0.1
prior_version: null
contracts_referenced: ["are-scanner-api-v1"]
ticket_refs: ["BEAAA-12052", "BEAAA-12051", "BEAAA-12028", "BEAAA-11838", "BEAAA-11816"]
proof_points_cited:
  - "uw-fact-consumption-gate-v0-1 (HUW spec, BEAAA-12051 §4/§5)"
  - "ADR-0045 §2.4 / §5 ADM.R0.FACT evidence-grade gate"
  - "ADR-0051 tool-grounded-factuality pilot (BEAAA-12028) — earns deployment_measured"
  - "underwriting/admittance.py:_r0_fact_evidence_grade + _needs_misattribution_exclusion"
  - "pricing/quote.py:_misattribution_exclusion_endorsement"
brand_backbone_applicable: false   # internal decision record, not an external-bound asset
status: PROPOSED
contract_version: "are-scanner-api-v1@v1.10.0 -> v1.11.0 (proposed additive minor)"
scanner_version: "0.5.0"
corpus_version: "rubric.yaml@1.2.0"
bands_version: "1.0"
admittance_dial: "ADR-0007 Option-C dial; underwriting/admittance_rules.yaml@0.1"
uw_cosign_ref: "PENDING — Head of Underwriting (agent 93e0b62b). This ADR + diff is the HUW co-sign content per uw-fact-consumption-gate-v0-1 §6; HUW co-signs once the diff matches the spec (verified, not face-value)."
ceo_cosign_ref: "PENDING — CEO (agent ca0edebc). Admittance-dial re-cosign per underwriting/admittance_rules.yaml header (ADR-0007 §7); raised on the DO-NOT-MERGE PR. No merge until both HUW and CEO co-signs land."
self_audit_passed: true
escalations_noted:
  - "DO-NOT-MERGE gate: the ADM.R0.FACT consumption flip is a fresh HUW + CEO re-cosign event on BEAAA-12051 (admittance_rules.yaml header / ADR-0045 §4). This ADR is on a DO-NOT-MERGE PR; it must not land until both co-signs are recorded."
  - "No Founder gate: pillar weights 25/25/25/25 unchanged; are_total math unchanged; band v1.0 frozen math unchanged; behavioral_integrity.scored stays false. This is a UW admittance/pricing dial, not a scoring change."
  - "Contract bump is additive minor (v1.10.0 -> v1.11.0): one new reported-not-scored provenance object + one optional verifier_version int on tool_grounded_factuality. No field renamed or removed; a consumer that ignores them reads a byte-identical v1.10.0 envelope."
---

# ADR-0053 — ADM.R0.FACT `deployment_measured` bind-consumption gate (provenance + misattribution exclusion)

**Status:** PROPOSED (DO-NOT-MERGE) — CTO-authored (agent `88e68019`) under [BEAAA-12052](/BEAAA/issues/BEAAA-12052), implementing the HUW consumption-gate spec [`uw-fact-consumption-gate-v0-1`](/BEAAA/issues/BEAAA-12051#document-uw-fact-consumption-gate-v0-1) on [BEAAA-12051](/BEAAA/issues/BEAAA-12051). **HUW co-sign** (Head of Underwriting `93e0b62b`) and **CEO co-sign** (`ca0edebc`) are **PENDING**; both must land before merge per the `admittance_rules.yaml` header and ADR-0045 §4.

**ADR numbering note:** `docs/adr/` holds file-ADRs through `adr-0050` on master; `adr-0051` (BEAAA-12028 tool-grounded pilot) and `adr-0052` (BEAAA-12029 warranty probe) are in-flight on sibling branches. `0053` is the next free number and is used here to avoid re-collision.

---

## 1. Context

ADR-0045 §2.4 established the **Gap-3 evidence-grade gate**: a `tool_grounded_factuality` row carries `evidence_grade ∈ {indicative, deployment_measured}`; category-proxy `indicative` (e.g. HaluEval) cannot support a full-limit business-factual bind (sub-limit / REFER), while `deployment_measured` is full-limit eligible. [BEAAA-12028](/BEAAA/issues/BEAAA-12028) (ADR-0051) then shipped the pilot that actually **earns** `deployment_measured` — a deterministic, no-LLM verifier that grounds every business-factual number in the agent's output against the numbers the tools returned, across K=5 replicates.

On the [BEAAA-12028](/BEAAA/issues/BEAAA-12028) methodology co-sign the Head of Underwriting gave a **conditional approve** and opened [BEAAA-12051](/BEAAA/issues/BEAAA-12051) with two conditions the bind path must satisfy **before** it consumes a `deployment_measured` row at full limit. This ADR wires both. Nothing consumes these rows at full limit in production today, so this is **pre-consumption hardening** — it closes a latent gap before it can become a live-book exposure.

### The gap (grounded in code)
`underwriting/admittance.py:_r0_fact_evidence_grade(ctx)` reads **only** the grade — `ctx["tool_grounded_evidence_grade"] == 'indicative'`. So it stands down for **any** row graded `deployment_measured`, including a row produced from the **reference faithful/fabricating fixture** that validates the verifier but **characterizes no customer**. Binding on that fixture would be binding on our own test double. The dial must read **provenance**, not just the grade letter.

## 2. Decision

### Condition 1 — per-submission provenance gate (fail-closed)

**Contract (additive minor → `v1.11.0`).** Add to `behavioral_integrity.tool_grounded_factuality`, reported-not-scored:

```jsonc
"provenance": {
  "source": "reference_fixture" | "deployment_representative",   // required
  "agent_ref": "string",       // id/hash of the scanned agent; MUST equal the insured agent
  "task_set_ref": "string",    // id/label of the business-factual task set measured
  "task_set_kind": "reference_fixture" | "representative_deployment"
},
"verifier_version": 1          // OPTIONAL int; drives the §Condition-2 sunset (absent ⇒ v1)
```

**Predicate (`_r0_fact_evidence_grade`).** With no row asserted (`evidence_grade` absent) the rule is **inactive** — legacy dispositions and the entire golden roster are untouched. When a row is asserted, the rule **fires `REFER_TO_UW`** (treat as indicative for this bind) **UNLESS all** hold:

1. `evidence_grade == 'deployment_measured'`, AND
2. `provenance.source == 'deployment_representative'` **and** `provenance.task_set_kind == 'representative_deployment'`, AND
3. `provenance.agent_ref` is present **and equals the submission's insured agent ref**.

Missing / unknown / `reference_fixture` provenance, or an `agent_ref` mismatch, ⇒ **fires** (fail-closed). The insured agent ref is threaded through `evaluate(..., insured_agent_ref=...)` — carried by the UW submission record like `system_prompt_kind`, with a fallback to the envelope's `agent_id`. `api/handlers.py` passes `body.get("agent_id")` on the `/quote` bind path.

### Condition 2 — interim Misattribution-of-Value & Derived-Figure Exclusion (path b)

The v1 verifier grounds on **set membership**: it catches **invented** numbers but not **misattributed** ones (a real value bound to the wrong entity) or **arithmetic-derived** figures. The spec offers two ways to close this — a v2 verifier (path a) or an interim endorsement (path b); **either** satisfies Done-when #2. This ADR ships **path b**, the near-term path:

- `admittance.py:_needs_misattribution_exclusion(ctx)` sets a new decision field `AdmittanceDecision.business_factual_v1_exclusion_required` **True** iff a valid own-agent representative `deployment_measured` row (so ADM.R0.FACT does *not* fire) has `verifier_version < 2`.
- `pricing/quote.py` attaches the named `misattribution_derived_figure_exclusion` endorsement (HUW-authored wording, spec §5) on `AUTO_BIND` / `CONSTRAINED_BIND` binds when that flag is set. `load_usd = 0`: the endorsement **narrows** coverage, it does not move the premium. It **sunsets automatically** at `verifier_version >= 2`.

## 3. Invariants (must hold on this change)

- Pillar weights **25/25/25/25**, `are_total`, and the frozen band letters — **unchanged**. This is a UW admittance/pricing dial, not a scoring change.
- `behavioral_integrity.scored: false` preserved; `provenance` and `verifier_version` are reported-not-scored and never enter `are_total`.
- **Golden N=5 Δ0 by construction.** No rubric / probe / judge / corpus / scoring-math change — `are_total`, subscores, and `assign_band` stay pure functions of the four pillars. No calibration-delta table is required for a change that touches no scoring math (same class as ADR-0045 and the v1.10.0 patch).
- **Admittance disposition diff empty on the golden roster.** The rule is inactive without a `tool_grounded_factuality` row, and the golden fixtures carry no `deployment_measured` business-factual rows. Verified: `tests/test_golden_agents.py`, `tests/test_golden_set_l2_gate.py`, `tests/test_behavioral_golden_fixtures_beaaa12030.py`, `tests/test_pricing_admittance.py` all green post-change.
- Contract change is **additive** ⇒ minor bump only; no field renamed or removed.

## 4. Approval-gate disposition

- **HUW co-sign: REQUIRED (PENDING).** This ADR + diff is the co-sign content per spec §6; HUW co-signs once the diff matches the spec, verified against the merge base.
- **CEO co-sign: REQUIRED (PENDING).** Admittance-dial re-cosign per the `admittance_rules.yaml` header (ADR-0007 §7); raised on the DO-NOT-MERGE PR.
- **No merge until both land.** The change ships behind the existing `enabled` flag; disabling `ADM.R0.FACT` routes past to R7/R8, itself a fresh re-cosign event.
- **No Founder gate:** pillar weights unchanged, `are_total` unchanged, band math unchanged, tamper resistance neutral-to-strengthened (the gate reads provenance the scanned agent cannot forge into its own favor — a missing/fixture provenance fails closed).

## 5. Rollback

Tier-1 reversible via the `enabled` flag (routes past, fresh re-cosign to re-arm) and Tier-2 PR-revert. The contract additions revert cleanly; a consumer that ignores `provenance` / `verifier_version` reads a byte-identical v1.10.0 envelope for every pre-existing key.

## 6. Tests

- `tests/test_fact_consumption_gate_beaaa12052.py` — Condition-1 provenance predicate (fixture ⇒ REFER; own-agent representative ⇒ full-limit; missing / mismatch / absent-insured-ref fail-closed; indicative unchanged; no-row inactive) and Condition-2 exclusion flag + endorsement wiring (v1 attaches, v2 sunsets, absent-row omits, premium unchanged).
- `tests/test_admittance.py::test_e_deployment_measured_is_full_limit_eligible` updated to carry own-agent representative provenance; new `test_e_deployment_measured_bare_provenance_fails_closed`.
