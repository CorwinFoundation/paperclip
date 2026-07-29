---
artifact_type: adr_contract
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
brand_backbone_applicable: false   # internal contract patch, not an external-bound asset
focus_claim: "v1.13.0 contract patch (are-scanner-api-v1 v1.12.0 -> v1.13.0) adds ONE additive, feature-gated, PII-free output object — evidence_receipt (the evidence-receipt-v1 §2 schema, frozen by ADR-0067) — to the terminal webhook (api/scan_handlers.py::_customer_result_response) and the CI plugin (api/ci_scan_ext.py::ci_plugin_result). Behind BACKBOND_EVIDENCE_RECEIPT_V1 (default OFF), a valid BackBond-minted receipt is surfaced; gate off yields a byte-identical prior response; gate on with no/invalid receipt fails closed to receipt_status:unavailable (§6, never fabricated). No scoring / rubric / corpus / band / weight change; golden N=5 delta=0 by construction. A consumer that ignores the field reads a byte-identical v1.12.0 response for every pre-existing key."
contracts_referenced: ["are-scanner-api-v1@v1.12.0 (shipped) -> v1.13.0 (proposed additive minor)", "evidence-receipt-v1@v1 (RATIFIED 2026-07-29, BEAAA-18817)", "adr-0067-beaaa18817-evidence-receipt-v1-custody"]
ticket_refs: ["BEAAA-18825", "BEAAA-18817", "BEAAA-18824", "BEAAA-18818"]
proof_points_cited:
  - "api/evidence_receipt.py: single-source project() — feature gate + evidence-receipt-v1 §2 exact-schema validation + PII allowlist (extras fail closed); both surfaces route through it (no duplicate projection logic)."
  - "api/scan_handlers.py::_customer_result_response: projects an evidence_receipt carried on the persisted kickoff row (minted by Child 2, BEAAA-18819); gate off -> byte-identical prior response."
  - "api/ci_scan_ext.py::ci_plugin_result: OPTIONAL evidence_receipt kwarg routed through the shared project(); default None + gate off -> byte-identical prior envelope; no-prompt guarantee preserved."
  - "tests/test_evidence_receipt_projection_beaaa18825.py: exact-schema projection; gate-off byte-identical (both surfaces); consumer-ignores-field byte-identical; PII allowlist fails closed; §6 fail-closed receipt_status; N=5-shaped score-envelope invariance across gate on/off."
  - "Reproducibility: evidence_receipt is an additive output projection off every scoring path; no rubric/probe/judge/corpus/scoring-math change; golden N=5 delta=0 by construction (same class as v1.8.0 / v1.10.0 / v1.12.0)."
version: "v1.13.0"
prior_version: "are-scanner-api-v1@v1.12.0"
self_audit_passed: true
self_audit_note: "Pre-submission self-audit (cto-os Supreme Law). (1) Focus claim cites the one new feature-gated PII-free output object, the exact projection sites (scan_handlers/_customer_result_response + ci_scan_ext/ci_plugin_result via the shared api/evidence_receipt.project), and the N=5 delta=0 gate. (2) Carrier Rule clean — no carrier or customer named; 'vendor' = the scanned-agent operator. (3) Canonical terms only — 'the BackBond Scanner' / 'the Scanner'; 'ARE' appears only in literal identifiers (are-scanner-api-v1, are_total). (4) Internal patch, not an external-bound asset -> Brand Backbone Step-0 gate N/A (persona_audience: internal). (5) Supreme Law / reproducibility: the four reproducibility stamps (scanner_version, corpus_version, model_versions, seed) are UNCHANGED and still required; evidence_receipt is an additive, feature-gated output projection off every scoring path; golden N=5 delta=0 verified. NO scoring math, NO rubric dim/weight/band/curve change. (6) Tamper resistance: unchanged-to-strengthened — the projection validates the ratified schema exactly and enforces a strict PII allowlist (any non-schema key fails closed), and the scanner never self-signs or fabricates a receipt (§6 fail-closed). (7) NO pillar-weight / rubric-weight / scoring-math / judge change; pillar weights stay 25/25/25/25 Founder-locked -> NO Founder gate. (8) Co-sign: this is a response/output-shape contract change; Underwriter output-surface co-sign obtained on BEAAA-18824 (done). Trust/retention language ratified by Claims (BEAAA-18822) + Compliance (BEAAA-18823) in Child 1 (ADR-0067). (9) Rollback: Tier-2 feature-gate off + projection revert; a consumer that ignores the field reads a byte-identical v1.12.0 response for all pre-existing keys."
escalations_noted:
  - "Version selection — v1.13.0 (v1.12.0 + 1 minor). Registry check: no contracts/are-scanner-api-v1-v1.1[3-9]*-patch.md on canonical master; v1.13.0 is the next unused minor. CONTRACT_VERSION runtime literal in scanners/__init__.py is UNCHANGED; additive minors since v1.5.0 are documented via patch files, not the runtime literal."
  - "Underwriter co-sign OBTAINED (output-surface change) — BEAAA-18824 (done), the additive evidence_receipt output surface on webhook + CI plugin (ADR-0067 §Output projection / contract §9). No Founder gate: evidence_receipt is a PII-free receipt object, NOT are_total input and NOT a scored evidence_ref; the Founder evidence-ref freeze and 25/25/25/25 weight lock are untouched."
  - "Receipt issuance (mint + retention-locked custody + audit) is Child 2 (BEAAA-18819), in progress. This patch ships only the Child 3 projection; until a receipt is present on the row / supplied to the CI plugin, the gate-on path fails closed to receipt_status:unavailable (§6). Contract dependency is one-directional: projection surfaces whatever the receipt service mints, no coupling to Child-2 code."
---

# Contract patch — `are-scanner-api-v1.md` v1.12.0 -> v1.13.0

**Status:** ACCEPTED for merge under [BEAAA-18825](/BEAAA/issues/BEAAA-18825) (Child 3, DoD #5 of [BEAAA-18817](/BEAAA/issues/BEAAA-18817)). Ratification gate cleared: `evidence-receipt-v1` and ADR-0067 were RATIFIED 2026-07-29 ([BEAAA-18818](/BEAAA/issues/BEAAA-18818), done) — Claims Architecture ([BEAAA-18822](/BEAAA/issues/BEAAA-18822)) + Compliance ([BEAAA-18823](/BEAAA/issues/BEAAA-18823)) trust/retention language, **Underwriter output-surface co-sign ([BEAAA-18824](/BEAAA/issues/BEAAA-18824), done)**.

**Design authority:** CTO (ADR-0067 / evidence-receipt-v1 schema). Implementation authority: Scanner Engineer (`api/evidence_receipt.py`, `api/scan_handlers.py`, `api/ci_scan_ext.py`). Approval gate: Underwriter (output-surface co-sign, BEAAA-18824) — recorded.

**Change classification:** additive minor. ONE new OPTIONAL, feature-gated output object `evidence_receipt` on the terminal webhook and the CI plugin. No existing field renamed, removed, or reshaped. A consumer that ignores it reads a **byte-identical v1.12.0 response** for every pre-existing key. Tier-2 rollback = feature-gate off + projection revert.

## §0. Why now — a portable, PII-free custody receipt on the customer surface

ADR-0067 adopts a hybrid-custody model: raw logs and the forensic bundle stay in the vendor environment; the BackBond-controlled receipt service mints a signed `evidence_receipt_v1` binding a scan to the SHA-256 digests of its forensic bundle, probe manifest, score snapshot, and scanner build, plus a BackBond-assigned `received_at`. Children 2 (issuance/custody, BEAAA-18819) and 3 (this projection) deliver it. This patch exposes the **portable, PII-free** receipt on the two customer-facing result surfaces so Claims/Underwriting can later detect whether a supplied bundle diverged from what BackBond received — **without** any raw logs, prompt/response content, customer names, or other PII leaving the vendor.

## §1. New object — `evidence_receipt` (additive, feature-gated, PII-free)

Emitted on the terminal webhook (`_customer_result_response`) and the CI plugin (`ci_plugin_result`) when the feature gate is on and a valid receipt is available. Shape = the frozen `evidence-receipt-v1` §2 schema, verbatim:

```jsonc
"evidence_receipt": {
  "version": "evidence_receipt_v1",
  "receipt_id": "<uuid>",                       // server-assigned
  "scan_id": "<string>",
  "tenant_ref": "<opaque, non-reversible>",
  "evidence_bundle_sha256": "sha256:<hex>",
  "probe_manifest_sha256": "sha256:<hex>",
  "score_snapshot_sha256": "sha256:<hex>",      // = scan_log.snapshot_hash
  "scanner_build_digest": "sha256:<hex>",
  "received_at": "<RFC3339 UTC>",               // BackBond-assigned trusted clock
  "signing_key_id": "<key id>",
  "signature_alg": "ed25519",
  "signature": "<base64>"                        // over the canonical signing body (contract §4)
}
```

- **PII allowlist enforced.** The projection emits **exactly** these 12 keys and only these keys. A receipt carrying any non-schema key (a customer name, a raw log, a bundle URI) fails closed — the malformed/leaky receipt is never surfaced. This is a whitelist copy in schema order, not a blacklist scrub.
- **No raw evidence.** Only digests and opaque references; the forensic archive and raw logs never leave the vendor (ADR-0067 §Decision).

## §2. Feature gate — `BACKBOND_EVIDENCE_RECEIPT_V1` (default OFF)

- **Gate off (default):** neither `evidence_receipt` nor `receipt_status` is added; the response is **byte-identical** to the prior v1.12.0 contract on both surfaces.
- **Gate on + valid receipt:** `evidence_receipt` is added (schema above); all pre-existing keys are byte-identical.
- **Gate on + absent/invalid receipt:** `receipt_status: "unavailable"` (§6 fail-closed) — the scanner **never** self-signs or fabricates a receipt.

Single source of truth: `api/evidence_receipt.py::project()`. Both surfaces route through it, so the gate, the schema validation, and the PII allowlist live in exactly one place (no duplicated projection logic between the webhook and the CI plugin).

## §3. Reproducibility fields — intact

The four Supreme-Law reproducibility stamps (`scanner_version`, `corpus_version`, `model_versions`, `seed`) are **unchanged and still required**. `evidence_receipt` is an additive output projection off every scoring path; no rubric / probe / judge / corpus / scoring-math change. Golden N=5 delta = **0 by construction**.

## §4. Approval-gate disposition

- **Underwriter co-sign: OBTAINED** — output-surface change ([BEAAA-18824](/BEAAA/issues/BEAAA-18824), done).
- **Ratification gate: CLEARED** — `evidence-receipt-v1` + ADR-0067 RATIFIED 2026-07-29 ([BEAAA-18818](/BEAAA/issues/BEAAA-18818)); Claims ([BEAAA-18822](/BEAAA/issues/BEAAA-18822)) + Compliance ([BEAAA-18823](/BEAAA/issues/BEAAA-18823)) confirmed the trust/retention language.
- **No Founder gate** — `evidence_receipt` is a PII-free receipt object, not a response `evidence_ref` and not an `are_total` input; the Founder evidence-ref freeze and the 25/25/25/25 weight lock are untouched.
- **CONTRACT_VERSION literal:** unchanged. Additive minors since v1.5.0 are documented via patch files, not the runtime literal.

## §5. Rollback

Tier-2 PR-revert **or** feature-gate off. Both are additive; with the gate off (the default) a consumer reads a byte-identical v1.12.0 response for all pre-existing keys. Historical receipts and existing score results are never modified.

## §6. Changelog

| Version | Date | Change | Refs |
|---|---|---|---|
| **v1.13.0** | **2026-07-29** | **additive minor — feature-gated (`BACKBOND_EVIDENCE_RECEIPT_V1`, default off), PII-free `evidence_receipt` object (evidence-receipt-v1 §2) on the terminal webhook + CI plugin; gate off -> byte-identical prior response; gate on + no/invalid receipt -> `receipt_status:unavailable` fail-closed; no scoring change** | **This patch; contract `evidence-receipt-v1` + ADR-0067 ([BEAAA-18817](/BEAAA/issues/BEAAA-18817)); ratification [BEAAA-18818](/BEAAA/issues/BEAAA-18818); Underwriter co-sign [BEAAA-18824](/BEAAA/issues/BEAAA-18824); Child 3 [BEAAA-18825](/BEAAA/issues/BEAAA-18825)** |
