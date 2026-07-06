---
artifact_type: adr
artifact_id: "ADR-0054-beaaa12055-tgf-provenance-emission"
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
version: 0.2
prior_version: 0.1
contracts_referenced: ["are-scanner-api-v1"]
ticket_refs: ["BEAAA-12055", "BEAAA-12051", "BEAAA-12052", "BEAAA-12028", "BEAAA-11816"]
proof_points_cited: ["HaluEval hallucination benchmark"]
brand_backbone_applicable: false   # internal decision record, not an external-bound asset
status: ACCEPTED
accepted_date: 2026-07-06
contract_version: "are-scanner-api-v1@v1.11.0"
scanner_version: "0.5.0"
corpus_version: "rubric.yaml@1.2.0"
focus_claim: "Scanner-side follow-up to BEAAA-12028: the tool_grounded_factuality emitter stamps a per-submission `provenance` object {source, task_set_kind, agent_ref, task_set_ref?} + `verifier_version` on every deployment_measured evidence row AND — load-bearing — on the `behavioral_integrity.dimensions.tool_grounded_factuality` block the ADR-0053 §4 gate actually reads. Vocabulary is pinned to the CO-SIGNED gate (`_tgf_full_limit_eligible`), not the stale issue-description draft: full-limit requires source=='deployment_representative', task_set_kind=='representative_deployment', and agent_ref present AND == the submission's insured_agent_ref. Fail-closed default is the reference fixture (source/task_set_kind=='reference_fixture', no agent_ref → consumed as indicative). verifier_version drives the Condition-2 misattribution exclusion (v1<2 ⇒ exclusion applies). Additive v1.11.0 metadata, import-only, reported-not-scored per ADR-0045; behavioral_integrity.scored stays false, golden N=5 delta = 0 by construction."
self_audit_passed: true
self_audit_note: "Pre-submission self-audit (cto-os Supreme Law), rev 0.2 after UW read-path review bounced rev 0.1 for a vocabulary mismatch. (1) Contract fidelity: emitter vocabulary VERIFIED against the live gate on both beaaa-12051-fact-consumption and beaaa-12052 (identical: source=='deployment_representative', task_set_kind=='representative_deployment', agent_ref==insured_agent_ref, verifier_version>=2 sunsets the exclusion). Test `test_submission_run_provenance_clears_the_gate` re-implements `_tgf_full_limit_eligible`'s clause byte-for-byte and asserts the emitted dimension block clears it; `test_submission_run_fails_gate_on_agent_ref_mismatch` asserts the identity pin. (2) Reproducibility: additive metadata on import-only pilot rows; NO Scanner scoring-path code touched; sha256 fingerprints over transcript/grounding blobs only, so tamper-evidence bytes unchanged (test_provenance_addition_preserves_determinism). are_total/subscores/assign_band unchanged → golden N=5 delta 0 by construction. (3) Pillar weights 25/25/25/25 UNCHANGED; behavioral_integrity.scored stays false → NO Founder gate. (4) Tamper resistance: source/task_set_kind/agent_ref are set by the harness from the trusted run configuration, NEVER read from agent free text; agent_ref (the identity pin) is required non-empty for a submission run so a run cannot silently degrade to a fail-closed identity-less block. (5) Canonical terms: BackBond Scanner/Score; ARE only in grandfathered schema fields. (6) Carrier Rule: catalog data is synthetic; no carrier/customer named (agent_ref in tests is a placeholder hash). (7) Internal decision record → Brand Backbone N/A. (8) Co-sign: consumption conditions already co-signed on ADR-0053; this is the emission half. Emitter conforms to the gate (ADR-0053 §4 authoritative), not vice versa."
escalations_noted:
  - "No Founder gate: pillar weights 25/25/25/25 unchanged; are_total math unchanged; band v1.0 frozen math unchanged; behavioral_integrity stays scored:false."
  - "Emitter conforms to the co-signed gate, not the ticket text: rev 0.1 was built to the BEAAA-12055 issue-description draft vocabulary (submission_agent / representative_business_factual / claim_level_value_entity_binding) and could NEVER clear the gate. rev 0.2 pins to ADR-0053 §4 (deployment_representative / representative_deployment / agent_ref / verifier_version). ADR-0053 §4 stays authoritative per UW; the emitter moved."
  - "MERGE COUPLING: this emitter PR (#40) and the consumption gate (BEAAA-12051 / BEAAA-12052) must land together or neither. Merging the gate without the conforming emitter leaves the full-limit path permanently fail-closed (a dead gate); merging the emitter without the gate stamps metadata nothing reads. Safe in isolation (fail-closed), but the intended full-limit path only exists when both are on master."
---

# ADR-0054 — Provenance emission on `tool_grounded_factuality` `deployment_measured` rows

**Status:** ACCEPTED (2026-07-06, rev 0.2) — CTO-authored (agent `88e68019`) under [BEAAA-12055](/BEAAA/issues/BEAAA-12055). Scanner-side emission follow-up to [BEAAA-12028](/BEAAA/issues/BEAAA-12028); conforms to the co-signed [ADR-0053](adr-0053-beaaa12051-fact-bind-consumption-conditions.md) §4 consumption gate ([BEAAA-12051](/BEAAA/issues/BEAAA-12051) / [BEAAA-12052](/BEAAA/issues/BEAAA-12052)). Additive, import-only, reported-not-scored, golden delta 0.

## 1. Context

[BEAAA-12028](/BEAAA/issues/BEAAA-12028) built the tool-grounded-factuality pilot that emits `tool_grounded_factuality` evidence rows at `evidence_grade = deployment_measured` — the first probe to earn `deployment_measured` (category-proxy HaluEval stays `indicative`).

The ADR-0053 `ADM.R0.FACT` dial grants a **full-limit** business-factual bind only when the row's provenance proves the measurement characterizes the **submission's own insured agent** on a **representative deployment** task set — not the BackBond reference fixture (which validates the verifier, characterizes no customer). The gate is **fail-closed**: a row without matching provenance is read as `indicative`, `ADM.R0.FACT` fires, and the exposure refers to UW. So **nothing binds full-limit until the emitter stamps conforming provenance.** This ADR closes it.

### 1.1 rev 0.1 → 0.2: vocabulary correction (UW read-path review)

rev 0.1 emitted the **BEAAA-12055 issue-description draft** vocabulary (`submission_agent` / `representative_business_factual` / `claim_level_value_entity_binding`). UW's read-path review traced it against the live gate `_tgf_full_limit_eligible` and found it could **never** clear full-limit — wrong field *names* and *values*, and a missing `agent_ref`. The gate's actual, co-signed contract (verified identical on `beaaa-12051-fact-consumption` and `beaaa-12052`) is the ADR-0053 §4 / v1.11.0 vocabulary below. rev 0.2 conforms the emitter to the gate; ADR-0053 §4 stays authoritative.

## 2. Decision

On every `tool_grounded_factuality` row carrying `evidence_grade: "deployment_measured"`, and on the reported-not-scored `behavioral_integrity` dimension block, emit the v1.11.0 `provenance` object plus `verifier_version`:

```jsonc
// submission run — the ONLY shape the gate treats as full-limit eligible
"provenance": {
  "source": "deployment_representative",
  "task_set_kind": "representative_deployment",
  "agent_ref": "<the submission's insured_agent_ref>",   // identity pin (id/hash)
  "task_set_ref": "<optional task-set provenance>"
}
// reference-fixture run — validates the verifier, fail-closes to indicative
"provenance": { "source": "reference_fixture", "task_set_kind": "reference_fixture" }  // NO agent_ref
```

- **`source` / `task_set_kind` / `agent_ref`** are a property of *what is being run*, not verifier-inferable, so they are a run-configuration input (same trust channel — never read from the agent-controlled transcript):
  - `REFERENCE_FIXTURE_PROVENANCE` = `reference_fixture` / `reference_fixture`, no `agent_ref` — the reference suite.
  - `deployment_representative_provenance(insured_agent_ref, task_set_ref=…)` builds the submission provenance. It **requires a non-empty `agent_ref`** so a submission run cannot silently degrade to an identity-less, fail-closed block.
- **`agent_ref` is the load-bearing field.** It is the identity pin the gate matches against the submission's `insured_agent_ref`. Without it, Condition 1 cannot bind the evidence to *this* insured agent (vs. "some real deployment"), so the gate fail-closes.
- **`verifier_version` (= `1` for v1), on the dimension block**, drives the gate's Condition-2 Misattribution-of-Value & Derived-Figure Exclusion. v1 grounds numeric tokens but does not close per-claim value↔entity binding; the exclusion sunsets only at `verifier_version >= 2`. (The old `claim_level_value_entity_binding` flag is dropped — the gate never read it.)

### 2.1 Fail-closed default (load-bearing)

The default `provenance` for an unspecified run is `REFERENCE_FIXTURE_PROVENANCE`, which is **not** full-limit eligible. Full-limit is **opt-in**: a submission run MUST build provenance via `deployment_representative_provenance(insured_agent_ref)`. A caller that forgets under-grants (safe) rather than accidentally granting full-limit — the same posture as the gate.

### 2.2 Where it rides (the load-bearing path is the dimension block)

- **Dimension block** — `ToolGroundedFactualityResult.behavioral_integrity_block()` emits the nested `{family, scored: false, dimensions: {tool_grounded_factuality: {evidence_grade, provenance, verifier_version, grounding_band, pass_k}}}` shape (mirroring `probes.stop_instruction.v1.stat_sig.emit_behavioral_integrity`). This is the path the gate's `_behavioral_dim` reads (`evidence_grade`, `provenance`, `verifier_version`). The caller merges this block into the ScoreResponse envelope. **rev 0.1 did not build this block; rev 0.2 does** — the "assembler will copy it" assumption in rev 0.1 is now realized in code.
- **Verdict rows** (`evidence_refs_for_run`) and **tamper-evident artifact rows** (`_artifact_refs`) — carry the same provenance object (informational; suppressed on `indicative` rows). `sha256` is over the transcript/grounding blobs, so the sub-block does not perturb reproducibility.

## 3. Classification

Additive `are-scanner-api-v1@v1.11.0` metadata (provenance object `{source, agent_ref, task_set_ref, task_set_kind}` + `verifier_version`) on the sha256-fingerprinted evidence row / behavioral block. Import-only, reported-not-scored per [ADR-0045](adr-0045-beaaa11816-behavioral-integrity-scoring-wiring.md): `behavioral_integrity.scored` stays `false`, `are_total` / pillar weights / band math unchanged, **golden N=5 delta = 0 by construction**.

## 4. Alternatives rejected

- **Infer provenance / agent identity from the transcript.** Rejected: identity is not observable from agent-controlled text and would be forgeable. `source`/`task_set_kind`/`agent_ref` come from the trusted run configuration.
- **Default to a submission/deployment-representative source.** Rejected: it would fail *open*. Fail-closed to the reference fixture (no `agent_ref`) is the only safe default.
- **Keep `claim_level_value_entity_binding`.** Rejected: the gate keys Condition 2 off `verifier_version`, not that flag — it was dead metadata. Emitting `verifier_version: 1` affirmatively drives the exclusion instead of relying on inference-by-absence.
- **Emit provenance only on evidence rows (rev 0.1).** Rejected: the gate reads the `behavioral_integrity` dimension block, not the rows. Emission must land there or the full-limit path stays unreachable.

## 5. Rollback

Revert one commit. Provenance/`verifier_version` are additive; removing them returns rows to the BEAAA-12028 shape. The gate is fail-closed, so a rollback reverts every `deployment_measured` exposure to sub-limit / REFER — no pricing breaks, it only removes the full-limit path. **Merge coupling:** land this with the consumption gate or neither (see escalations) — the gate without this emitter is a permanently fail-closed dead gate.

## 6. Verification

`tests/test_tgf_provenance_beaaa12055.py` (11 cases): default fail-closes to the reference fixture (no `agent_ref`, does not clear the gate); a submission run's emitted **dimension block clears a byte-for-byte reimplementation of `_tgf_full_limit_eligible`**; an `agent_ref` mismatch fails the gate (identity pin); a submission run requires a non-empty `agent_ref`; the dimension block carries `verifier_version == 1` and `evidence_grade`; `behavioral_integrity.scored` stays `false`; provenance is confined to `deployment_measured` rows; determinism (ledger fingerprints + evidence sha256) preserved. Full pilot suite (`test_tool_grounded_factuality_beaaa12028.py`, 11 cases) still green — **22 passed**.
