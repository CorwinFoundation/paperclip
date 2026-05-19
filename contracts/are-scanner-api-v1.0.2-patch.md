---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "v1.0.2 contract description patch expands the OPS pillar 'Owns' cell to enumerate output-quality discipline (hallucination, citation, refusal, calibration) — closing the customer-facing gap between the OPS bullet text and the underlying P1.1–P1.4 dim content that post-rename sits under OPS."
contracts_referenced: ["are-scanner-api-v1@v1.0.1 → v1.0.2", "uw-rubric-supervision-v1@v1.0", "scanner-implementation-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-473", "BEAAA-551", "BEAAA-470", "BEAAA-510", "BEAAA-547"]
proof_points_cited: ["UW first-touch cosign BEAAA-473 comment 3a1c16fe-2a5d-4ba3-b648-e4f5dd44bf32", "Cosign interaction 2519a4be-9eee-43cc-bc29-619635a1bdb4 accepted 2026-05-19T09:10:11.831Z", "Reference draft BEAAA-473 att bff3d295", "BEAAA-510 audit-trail line at v1.0.1 (commit be5a3c6)", "ADR-0006 §Known imperfection Mitigation #1 (docs/adr-0006-pillar-name-reconciliation.md)"]
version: "v1.0.2-patch"
prior_version: "are-scanner-api-v1@v1.0.1"
self_audit_passed: true
escalations_noted:
  - "File-path drift: contracts/are-scanner-api-v1.md was created by BEAAA-547 (commit df0b921) as the v1.1.0 additive-bump patch doc. The v1.0.x base contract lives in agent instruction snapshots only (per BEAAA-510 ref doc line 20). This v1.0.2 patch lands as a separate patch doc at contracts/are-scanner-api-v1.0.2-patch.md (mirroring the BEAAA-547 patch-doc pattern) rather than mutating the v1.1.0 patch doc. v1.0.x baseline and v1.1.0 patch surface compose without conflict (orthogonal change sets — v1.0.2 = OPS bullet description text only; v1.1.0 = scan_mode/k_replicates/spread fields additive). Surfaced to UW + CEO via BEAAA-551 close comment."
---

# Contract patch — `are-scanner-api-v1.md` v1.0.1 → v1.0.2

**Status:** approved for merge.
**UW first-touch co-sign:** [BEAAA-473 #3a1c16fe](/BEAAA/issues/BEAAA-473#comment-3a1c16fe-2a5d-4ba3-b648-e4f5dd44bf32) (Underwriter, 2026-05-19T09:15:09Z).
**Cosign interaction:** `2519a4be-9eee-43cc-bc29-619635a1bdb4` (request_confirmation, accepted 2026-05-19T09:10:11.831Z).
**CTO file commit:** this doc + the parallel edits documented below, on [BEAAA-551](/BEAAA/issues/BEAAA-551).
**Change classification:** description clarification (text-only); no output-shape change, no field-meaning shift, no scoring-math change, no judge-model change, no tamper-resistance surface change.
**Rollback:** Tier 1 `git revert` ≤2 min; contract description returns to v1.0.1 verbatim. Per BEAAA-551 §Rollback.

---

## §Score structure — OPS row "Owns" cell (diff against v1.0.1 baseline)

```diff
- | OPS | 25 | Eval reproducibility, data handling, retention, PII scrubbing, version pinning |
+ | OPS | 25 | Eval reproducibility, data handling, retention, PII scrubbing, version pinning, output-quality discipline (hallucination rate, citation integrity, refusal accuracy, confidence calibration) |
```

**Why.** The post-BEAAA-472 rename labelled the pre-rename P1.1–P1.4 dims (hallucination / citation / refusal / calibration) as OPS. ADR-0006 §Known imperfection (`docs/adr-0006-pillar-name-reconciliation.md` line 260+) flagged a gap: a customer or reinsurer reading the v1.0 OPS bullet ("Eval reproducibility, data handling, retention, PII scrubbing, version pinning") and then reading the ARE Report Card seeing "OPS: 20.3125 / 25 — six dims covering hallucination, citation, refusal, calibration, eval coverage" would see the mismatch. This patch closes the gap by expanding the bullet to match the dim content. Mitigation #1 promoted from "optional follow-on" to "shipped".

## §Status block — v1.0.2 patch line (added below the v1.0.1 BEAAA-510 line, in the agent-instruction canonical snapshot)

```
**v1.0.2 patch (2026-05-19):** OPS pillar "Owns" cell expanded to enumerate output-quality discipline (hallucination rate, citation integrity, refusal accuracy, confidence calibration). Description clarification only — no output-shape change, no field-meaning shift, no scoring-math change, no judge-model change, no tamper-resistance surface change. Approved per BEAAA-473 cosign interaction `2519a4be-9eee-43cc-bc29-619635a1bdb4` accepted 2026-05-19T09:10:11.831Z; UW first-touch co-sign on BEAAA-473 comment `3a1c16fe` (Supreme-Law surface check 8/8 clean); CTO file commit on BEAAA-551. CTO + UW co-sign per §Approval gates "Scoring logic change" read conservatively; board accept seals. UW classification: rule execution per `uw-rubric-supervision-v1@v1.0` (Δ ≤ 1pt); HUW countersign NOT triggered. ADR-0006 §Footer elevates Mitigation #1 from "optional follow-on" to "shipped".
```

## §Version history — row appended

```
| v1.0.2 | 2026-05-19 | OPS "Owns" cell expanded to enumerate output-quality discipline (hallucination rate, citation integrity, refusal accuracy, confidence calibration). Description clarification only — no output shape / field meaning / scoring math / judge model / tamper-resistance change. N=5 G1 byte-identical expected (text-only change). | BEAAA-473 + BEAAA-551 (ADR-0006 §Footer) |
```

---

## File-path layout note (CTO architectural disposition)

The task spec on [BEAAA-551](/BEAAA/issues/BEAAA-551) described three edits to `contracts/are-scanner-api-v1.md` assuming that path holds the full v1.0.x base contract. It does not. [BEAAA-547](/BEAAA/issues/BEAAA-547) (commit `df0b921`, 2026-05-18) created that path holding the v1.1.0 *patch document* (live-probe additive bump), not the v1.0.x base. The v1.0.x baseline lives in agent-instruction snapshots only (per BEAAA-510 reference doc line 20).

**Disposition.** Rather than mutate the v1.1.0 patch doc with v1.0.2 text (which would corrupt it) or rewrite the file-path layout under cosign pressure, this v1.0.2 patch lands as a **separate patch doc** at `contracts/are-scanner-api-v1.0.2-patch.md` (this file), mirroring the BEAAA-547 single-patch-doc-per-version pattern. The agent-instruction canonical snapshot (CTO's copy at `agents/88e68019-…/instructions/are-scanner-api-v1.md`) carries the full v1.0.2-baseline content. Cross-team agent-instruction sync (other agents' instruction snapshots regenerated to v1.0.2) is a separate downstream operation owned centrally.

v1.0.x baseline + v1.0.2 patch (this doc) + v1.1.0 patch (the BEAAA-547 doc at `contracts/are-scanner-api-v1.md`) compose without conflict — the v1.0.2 patch surface (OPS bullet description text) and the v1.1.0 patch surface (scan_mode / k_replicates / spread fields) are orthogonal change sets touching disjoint regions of the contract.

## Supreme-Law surface check (8/8 clean — mirrored from UW first-touch)

| Surface | Status |
|---|---|
| Output shape | unchanged |
| Field meanings | unchanged (clarifies what OPS has always covered) |
| Scoring math | unchanged (no `rubric/scoring.py` / `rubric/rubric.yaml` / `rubric/bands.py` touch) |
| Judge models | unchanged |
| Pillar weights | unchanged (25/25/25/25) |
| Tamper resistance | unchanged |
| Golden calibration | Δ_total = Δ_subscore = 0.0000 predicted; verification at-merge via G1 N=5 byte-identical sha against BEAAA-510-merge baseline (sha `cd3ec3…`) |
| ADR attached | ADR-0006 §Footer (UW preference; CTO confirmed) |

## Approval gate row-by-row (no Founder gate, no HUW countersign)

- Pillar weight changes (Founder) — NOT triggered.
- Rubric point added/removed (CTO + UW) — NOT triggered.
- Scoring logic change (CTO + UW) — TRIGGERED narrowly under conservative read; co-signed.
- New LLM judge (CTO) — NOT triggered.
- Golden score >1pt move (CTO + UW; Founder if >3pt) — NOT triggered (Δ = 0).
- Test corpus version bump >5% rubric points (CTO + UW) — NOT triggered.
- Tamper resistance reduction (Founder) — NOT triggered.

## Cross-references

- Parent: [BEAAA-473](/BEAAA/issues/BEAAA-473) — UW Rider 1 (drafted v1.0 → v1.0.1; renumbered here to v1.0.2 per BEAAA-510 v1.0.1 collision).
- This child: [BEAAA-551](/BEAAA/issues/BEAAA-551) — CTO file commit.
- Reference draft: att `bff3d295` on BEAAA-473 (v1.0 → v1.0.1 draft).
- Sibling complete: [BEAAA-510](/BEAAA/issues/BEAAA-510) — rename + v1.0.1 audit line (commit `be5a3c6`).
- Sibling complete: [BEAAA-470](/BEAAA/issues/BEAAA-470) + [BEAAA-472](/BEAAA/issues/BEAAA-472) — pillar rename (commit `d22bf84`).
- Sibling complete: [BEAAA-471](/BEAAA/issues/BEAAA-471) — UW R3 pricing-engine attestation (zero grep hits).
- Sibling complete: [BEAAA-547](/BEAAA/issues/BEAAA-547) — v1.1.0 additive bump (commit `df0b921`; orthogonal change set).
- ADR: `docs/adr-0006-pillar-name-reconciliation.md` §Footer (this commit) — Mitigation #1 promoted from "optional follow-on" to "shipped".
