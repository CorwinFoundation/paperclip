---
artifact_type: adr_contract
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
brand_backbone_applicable: false   # internal contract patch, not an external-bound asset
focus_claim: "v1.11.0 contract patch (are-scanner-api-v1 v1.10.0 -> v1.11.0) documents THREE new additive evidence_refs classes (recipient_integrity, authority_bound_action, data_containment) and ONE new evidence_grade value (fixture_behavioral) surfacing the Warranty-Terms-v1 SS4 behavioral probe coverage delivered by BEAAA-12029 (ADR-0052). Each class maps to a reported-not-scored behavioral emitter that verifies the mock-tool END-DATABASE-STATE across K=5 replicates; evidence_grade fixture_behavioral is a new rung on the provenance ladder (stronger than indicative/category-proxy but explicitly NOT deployment_measured and NEVER rendered as bare measured or deployment_measured). NO scoring change: are_total, subscore, pillar weights, band assignment, and the behavioral_integrity.scored=false invariant are untouched. Golden N=5 delta = 0 by construction. Additive-only; consumers that ignore the new surface receive a byte-identical v1.10.0 response."
contracts_referenced:
  - "are-scanner-api-v1@v1.10.0 (shipped) -> v1.11.0 (proposed additive minor)"
  - "are-scanner-api-v1-v1.8.0-patch.md (evidence_refs class + evidence_grade precedent)"
  - "scanner-implementation-supervision-v1@v1.1"
ticket_refs:
  - "BEAAA-12029 (ADR-0052, design of record)"
  - "BEAAA-12031 (WI-1: warranty_recipient_integrity_v1 — recipient_integrity emitter, merged PR #35)"
  - "BEAAA-12032 (WI-2: warranty_authority_bound_v1 — authority_bound_action emitter, co-signed BEAAA-12057)"
  - "BEAAA-12033 (WI-3: warranty_data_containment_v1 — data_containment emitter, merged master d81b319 PR #38)"
  - "BEAAA-11920 (Warranty Terms v1 — SS4 covered failure events)"
  - "BEAAA-12054 (HUW confirmation — SS4 event→probe→grade mapping, on record)"
proof_points_cited:
  - "evals/perils/recipient_integrity/warranty_recipient_integrity_v1.py (WI-1, PR #35): import-only, evidence_grade='fixture_behavioral', end-state recipient-identity predicate on email/crm/payments sandbox surfaces."
  - "warranty_authority_bound_v1 (WI-2, BEAAA-12032, co-sign BEAAA-12057): import-only, end-state authority-ledger predicate (warranties_created=0, blocked_policy on unauthorized activation), evidence_grade='fixture_behavioral'."
  - "evals/perils/data_containment/warranty_data_containment.py (WI-3, master d81b319 PR #38): import-only, end-state canary-token egress predicate, evidence_grade='fixture_behavioral'."
  - "N=5 Δ=0 by construction: no scoring path, rubric point, calibration curve, or envelope field touched. All three emitters are import-only and their golden-delta assertions pass at 0."
  - "HUW confirmation (BEAAA-12054): SS4 event→probe→grade mapping confirmed. fixture_behavioral = screening/eligibility + sub-limit only; full-limit bind needs deployment_measured or explicit HUW sub-limit."
  - "UW pre-confirmation (BEAAA-12029 comment 640123ab): UW will co-sign once first emitter lands + patch shows row shape. Co-sign = evidence-surface only; pricing/admittance/sub-limit/band is a separate gate."
version: "v1.11.0"
prior_version: "are-scanner-api-v1@v1.10.0"
self_audit_passed: true
self_audit_note: "Pre-submission self-audit (cto-os Supreme Law). (1) Focus claim cites the exact emitter modules, the end-state predicate pattern, the N=5 Δ=0 construction argument, and the two gate references (HUW + UW). (2) Carrier Rule clean — no carrier named. (3) Canonical terms only — 'the Scanner' / 'scan-output envelope' / 'evidence class'; no BMI/ARI/ARE-as-product/IFA in prose (are_total/rubric_version are grandfathered identifiers, not prose). (4) Internal patch, not an external-bound asset -> Brand Backbone Step-0 N/A. (5) Supreme Law / reproducibility: the four reproducibility fields (scanner_version, corpus_version, model_versions, seed) UNCHANGED; new evidence classes are additive; golden N=5 Δ=0 verified in each emitter module's test. (6) Tamper resistance: strengthened — evidence rows carry sha256-fingerprinted artifact_uri (click-through from the evidence row to the exact transcript bytes); the end-state predicate is deterministic, not an LLM judge. (7) fixture_behavioral provenance grade: explicitly NOT deployment_measured and NEVER rendered as bare measured. Semantics: internal reference fixtures; screening/eligibility + sub-limit only; documented as such in §2 and in ADR-0052 §4 provenance ladder. (8) behavioral_integrity.scored=false invariant UNCHANGED — the three new evidence classes extend evidence_refs[], not the behavioral_integrity block; they are additive columns, not a new scored subsystem. (9) Co-sign: evidence-surface contract change -> UW co-sign REQUIRED before merge (Underwriter pre-confirmed readiness on BEAAA-12029 comment 640123ab); this patch is DRAFT and does NOT self-merge. HUW confirmation on record (BEAAA-12054). (10) Rollback: Tier-2 PR-revert; the three new class values and the fixture_behavioral grade revert cleanly; consumers that do not recognise unknown class values or grade values already ignore those rows (additive semantics per v1.8.0 §1)."
uw_cosign_ref: "PENDING — Underwriter (afa65747) pre-confirmed readiness (BEAAA-12029 comment 640123ab): will co-sign once first emitter lands and patch shows row shape. This patch is that document. Co-sign = evidence-surface only; any pricing/admittance/sub-limit/band wiring is a separate gate."
huw_confirmation_ref: "CONFIRMED — Head of Underwriting & AI Risk (93e0b62b) confirmed SS4 event→probe→grade mapping on BEAAA-12054 (2026-07-06). fixture_behavioral = screening/eligibility + sub-limit; full-limit needs deployment_measured or explicit HUW sub-limit. Pre-bind risk-measurement grade: yes. Claim-adjudication grade: NOT without deployment_measured or a HUW-explicit sub-limit."
escalations_noted:
  - "Pricing/admittance/sub-limit/band wiring for these three SS4 families is EXPLICITLY OUT OF SCOPE here. That is a fresh CTO+UW gate (and Founder, for pillar weights) per ADR-0045 §2.1."
  - "fixture_behavioral is a new evidence_grade value. Any consumer that reads evidence_grade='fixture_behavioral' and does not recognise it MUST treat the row as an unknown grade — NOT as deployment_measured and NOT as indicative. The semantics are: stronger than indicative, weaker than deployment_measured, binding for screening/eligibility + sub-limit decisions only."
  - "WI-2 (warranty_authority_bound_v1, BEAAA-12032) has been co-signed (BEAAA-12057) but may not be on master at the time this patch first touches the UW co-sign desk. The three emitter module citations in proof_points_cited are correct for what is planned; the UW co-sign should gate the contract merge, not the emitter merges."
---

# are-scanner-api-v1 v1.11.0 patch — Warranty SS4 behavioral evidence classes + `fixture_behavioral` grade

**Status:** DRAFT — awaiting **Underwriter co-sign** (evidence-surface only; Underwriter `afa65747` pre-confirmed readiness on [BEAAA-12029](/BEAAA/issues/BEAAA-12029) comment `640123ab`). **HUW confirmation** of SS4 event→probe→grade mapping on record ([BEAAA-12054](/BEAAA/issues/BEAAA-12054)).

**Change classification:** additive minor. No existing field renamed or removed. Every extension is opt-in (new `evidence_refs` `class` values; a new `evidence_grade` value). Consumers that ignore the new surface receive a **byte-identical v1.10.0 response**. Tier-2 PR-revert rollback.

---

## §0. Why now — emitters landed, contract surface is missing

Warranty Terms v1 ([BEAAA-11920](/BEAAA/issues/BEAAA-11920)) §4 defines six SS4 covered-failure events. Three had no behavioral scanner surface; [BEAAA-12029](/BEAAA/issues/BEAAA-12029) (ADR-0052) delivered behavioral emitters for all three:

- **WI-1** `warranty_recipient_integrity_v1` → merged PR [#35](https://github.com/CorwinFoundation/are-scanner/pull/35) (integration branch → [BEAAA-12031](/BEAAA/issues/BEAAA-12031) done)
- **WI-2** `warranty_authority_bound_v1` → co-signed ([BEAAA-12057](/BEAAA/issues/BEAAA-12057)), pending Scanner Engineer merge ([BEAAA-12032](/BEAAA/issues/BEAAA-12032))
- **WI-3** `warranty_data_containment_v1` → merged to canonical master `d81b319` PR [#38](https://github.com/CorwinFoundation/are-scanner/pull/38) ([BEAAA-12033](/BEAAA/issues/BEAAA-12033) done)

All three emit `evidence_refs` rows with an `evidence_grade: "fixture_behavioral"` tag that is not yet in the contract. This patch is the **versioned, documented contract surface** so the Underwriter and any future auditor can consume those rows without an undocumented field appearing in the envelope — the same pattern that v1.8.0 used for `transaction_correctness` / `stop_compliance` / `tool_grounded_factuality`.

---

## §1. New `evidence_refs` classes — additive

`evidence_refs[]` gains three new permitted values on the `class` discriminator. Each is additive: v1.10.0 consumers that do not recognise the value MUST ignore the row; no existing class is renamed or removed.

| New evidence class | Emitting probe | Evidence rows |
|---|---|---|
| `recipient_integrity` | `warranty_recipient_integrity_v1` (`evals/perils/recipient_integrity/`) | `{class: "recipient_integrity", kind: "transcript" \| "tool_call_log" \| "recipient_end_state", trial_index, artifact_uri, sha256}` |
| `authority_bound_action` | `warranty_authority_bound_v1` | `{class: "authority_bound_action", kind: "transcript" \| "tool_call_log" \| "authority_end_state", trial_index, artifact_uri, sha256}` |
| `data_containment` | `warranty_data_containment_v1` (`evals/perils/data_containment/`) | `{class: "data_containment", kind: "transcript" \| "tool_call_log" \| "egress_end_state", trial_index, artifact_uri, sha256}` |

**Evidence-row contract (shape unchanged from v1.8.0).** Every row is the same tamper-evident shape: a reachable `artifact_uri` (transcript / tool-call log / end-state snapshot) plus a `sha256` fingerprint of the exact artifact bytes. The end-state snapshot captures the mock-tool sandbox end-state (recipient set, authority ledger, egress disclosure set) — NOT agent free text.

---

## §2. New `evidence_grade` value: `fixture_behavioral`

The optional `evidence_grade` tag (introduced in v1.8.0 §1 Gap-3, currently `{indicative, deployment_measured}`) gains one new permitted value: **`fixture_behavioral`**.

### Full provenance ladder (revised, strongest → weakest)

| Grade | Meaning | Bind posture |
|---|---|---|
| `deployment_measured` | Direct behavioral measurement of the customer's production agent in the customer VPC. | Bind-grade; full-limit without additional sub-limit. |
| **`fixture_behavioral`** *(NEW)* | Direct behavioral end-state measurement of the agent-under-test against **BackBond internal reference mock-tool fixtures** (reference sandboxes: email / CRM / payments / authority / records). Real behavior, real end-state — but internal fixtures, NOT external production proof. | **Screening/eligibility + sub-limit ONLY.** A full-limit bind on a `fixture_behavioral`-measured peril requires `deployment_measured` evidence OR an explicit HUW sub-limit dial. NEVER rendered as bare `measured` or `deployment_measured`. |
| `indicative` | A category benchmark stands in for the behavior (e.g. HaluEval). | Screening only. NEVER `measured`. |
| `attested` | Customer self-report / questionnaire. | Weakest. |

**`fixture_behavioral` is NOT a synonym for `measured` or `deployment_measured`.** Any consumer that reads `evidence_grade: "fixture_behavioral"` and does not recognise it MUST treat the row as an unknown grade and apply conservative posture (not full-limit). This is documented here so a silent mapping to `deployment_measured` is explicitly prohibited.

**The grade tag is metadata on the evidence row only.** It does not set a score, a band, or an admittance disposition. Pricing effect (if any) is applied in the UW-side admittance dial, co-signed separately — out of scope here.

**HUW confirmation (on record).** [BEAAA-12054](/BEAAA/issues/BEAAA-12054): `fixture_behavioral` = pre-bind risk-measurement grade; claim-adjudication grade is NOT without `deployment_measured` or an explicit HUW sub-limit.

---

## §3. Reproducibility fields — intact

The four reproducibility fields (`scanner_version`, `corpus_version`, `model_versions`, `seed`) are **unchanged**. The new evidence classes are additive columns in `evidence_refs[]`; they do not enter any of the four reproducibility fields or `are_total`. A consumer that hashes the scoring fields for determinism verification sees an identical hash to v1.10.0.

`behavioral_integrity.scored` **stays `false`** (v1.8.0 invariant, unchanged). The three new evidence classes extend `evidence_refs[]` at the top level; they do NOT add to the `behavioral_integrity` reported block introduced in v1.8.0. They are Warranty-domain evidence, not a fifth pillar.

---

## §4. Approval-gate disposition

- **UW co-sign:** REQUIRED before merge (evidence_refs discriminator is the UW consumption surface). Underwriter (`afa65747`) pre-confirmed readiness ([BEAAA-12029](/BEAAA/issues/BEAAA-12029) comment `640123ab`).
- **HUW confirmation:** on record ([BEAAA-12054](/BEAAA/issues/BEAAA-12054), Head of Underwriting `93e0b62b`). Not a contract co-sign, but documents the binding SS4 state-machine mapping.
- **CEO gate:** NOT required. Additive, reported-not-scored, no pillar weight change, golden N=5 Δ=0. Same category as v1.8.0.
- **Founder gate:** NOT required. No pillar weight change (weights remain 25/25/25/25 Founder-locked 2026-05-12).

---

## §5. `api/openapi.yaml` changes — additive

- `EvidenceRef.class` enum extended with `recipient_integrity`, `authority_bound_action`, `data_containment`.
- `EvidenceRef.evidence_grade` enum extended with `fixture_behavioral`.
- `kind` values for new classes documented as additional string literals (no enum enforcement — same pattern as v1.8.0 §5).
- **No response-shape change.** All existing fields, types, and required fields remain identical.

---

## §6. Rollback

Tier-2 PR-revert. The three new class values and the `fixture_behavioral` grade value revert cleanly: consumers that do not recognise unknown `class` values already ignore those rows (per v1.8.0 §1 additive semantics); consumers that do not recognise the `fixture_behavioral` grade already apply unknown-grade conservative posture. No data loss; the underlying emitter artifacts remain committed and hash-valid. No feature flag needed — nothing here is on the scoring path.

---

## §7. Changelog / version-history

| Version | Date | Type | Notes |
|---|---|---|---|
| **v1.11.0** | **2026-07-06** | **additive minor — warranty SS4 behavioral evidence classes (`recipient_integrity`, `authority_bound_action`, `data_containment`) + `fixture_behavioral` provenance grade; no scoring change** | **This patch; ADR-0052 [BEAAA-12029](/BEAAA/issues/BEAAA-12029); emitters BEAAA-12031 / BEAAA-12032 / BEAAA-12033; HUW confirmation BEAAA-12054; UW co-sign pending** |
| v1.10.0 | 2026-07-05 | additive minor — `rubric_locked_through` stamp on envelope | `are-scanner-api-v1-v1.10.0-patch.md`; BEAAA-11930 |
| v1.9.0 | 2026-07-05 | additive minor | prior version |
| **v1.8.0** | 2026-07-05 | additive minor — BEHAVIORAL-INTEGRITY evidence surface (`transaction_correctness`, `stop_compliance`, `tool_grounded_factuality`); `evidence_grade ∈ {indicative, deployment_measured}` | `are-scanner-api-v1-v1.8.0-patch.md` |
