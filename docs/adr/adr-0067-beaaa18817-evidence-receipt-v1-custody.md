---
artifact_type: adr
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "evidence_receipt_v1 is a BackBond-signed, retention-stored receipt that binds a scan to the SHA-256 digests of its forensic bundle, probe manifest, score snapshot, and scanner build, plus a BackBond-assigned received_at. It proves WHICH digest BackBond received and WHEN, and makes any later divergence of a vendor-supplied bundle detectable. It does NOT prove the vendor-controlled evidence was truthful when collected, and a detached digest is NOT storage-level WORM."
contracts_referenced: ["are-scanner-api-v1@v1.0", "evidence-receipt-v1@v1 (this ADR freezes the schema)"]
ticket_refs: ["BEAAA-18817"]
proof_points_cited: ["api/ci_scan_ext.py::ci_plugin_result / compute_probe_set_hash", "api/scan_handlers.py::_customer_result_response", "api/scan_log.py::snapshot_hash"]
version: 0.2
prior_version: 0.1
self_audit_passed: true
ratified: "2026-07-29 — Claims Architecture (BEAAA-18822), Compliance (BEAAA-18823), Underwriter (BEAAA-18824)"
escalations_noted: ["No rubric, corpus, pillar-weight, scoring-math, band, or LLM-judge change -> no Founder gate and no N=5 calibration delta (DoD #7 / Do-not-do #5). NEW output-surface fields on terminal webhook + CI plugin -> Underwriter co-sign obtained (BEAAA-18824). Trust/retention LANGUAGE ratified by Claims Architecture (BEAAA-18822) + Compliance (BEAAA-18823) on 2026-07-29 (DoD #2); this v0.2 folds their exact wording guards. Still-prohibited terms: non-repudiation, irrefutable, cryptographic custody, SEC 17a-4 compliant, regulatory WORM compliant, and any WORM claim over the vendor-held bundle or raw logs."]
---

# ADR-0067: `evidence_receipt_v1` — signed evidence receipt and hybrid custody

**Status:** RATIFIED (2026-07-29). This ADR is Child 1 of BEAAA-18817. Claims Architecture (BEAAA-18822) and Compliance (BEAAA-18823) confirmed the trust-claim and retention language (DoD #2), and the Underwriter co-signed the output-surface change (BEAAA-18824). This v0.2 folds their exact wording guards. Implementation (Child 2, BEAAA-18819) and integration (Child 3, BEAAA-18825) may now proceed; the Compliance custody/audit conditions in §Retention are acceptance gates on Child 2 before receipt issuance ships.

## Decision

BackBond adopts a **hybrid custody model**. Raw logs and the forensic bundle stay in the vendor environment. The scanner finalizes a canonical evidence archive and an exact probe manifest, computes their SHA-256 digests, and submits **only digests plus opaque references** over an authenticated scanner identity to a **BackBond-controlled receipt service**. That service — not the scanner, not customer CI — assigns a trusted `received_at`, signs an `evidence_receipt_v1` object with a BackBond-held asymmetric key, and writes it to retention-locked storage under BackBond control. The terminal webhook and CI plugin then return the portable, PII-free receipt. Claims can later compare a supplied bundle against the receipt and detect divergence.

## Trust claim (exact, load-bearing)

The receipt proves **two** things and only two:

1. **Which digest BackBond received** — the `evidence_bundle_sha256`, `probe_manifest_sha256`, `score_snapshot_sha256`, and `scanner_build_digest` that a BackBond-authenticated scanner identity submitted for a given `scan_id`.
2. **When BackBond received it** — the BackBond-service-assigned `received_at`, not a vendor-supplied clock.

From these, Claims can **detect** whether a later vendor-supplied bundle differs from what was digested at scan finalization: recompute the digest, compare to the signed receipt. A mismatch is dispositive that the bundle changed; a match shows the supplied bundle hashes to the same digest BackBond received for that scan; it does not prove the vendor-controlled evidence was truthful or complete when collected (Claims Architecture wording guard, BEAAA-18822).

The receipt explicitly does **NOT** prove:

- that the vendor-controlled evidence was **truthful or complete when collected** (garbage-in is still garbage-in-with-a-receipt);
- that the vendor could not have **prepared** a favorable bundle before finalization;
- storage-level immutability of the underlying bundle, which never leaves the vendor.

**Ratified wording rules (Claims Architecture BEAAA-18822 + Compliance BEAAA-18823, 2026-07-29).**

Permitted present-tense claims: *"BackBond-signed"*, *"tamper-evident against the received digest"*, *"detects post-receipt alteration"* / *"detects later divergence from the received digest"*, *"retention-stored under BackBond control"*, and — for the **BackBond receipt object only** — *"BackBond-controlled retention-locked receipt"* and *"WORM-format / non-rewriteable, non-erasable object-lock control for 10 years from `received_at`"*.

Prohibited (NOT ratified; a detached digest and a signed receipt do not carry these properties): "non-repudiation", "irrefutable", "cryptographic custody" (of the vendor evidence), "SEC 17a-4 compliant", and "regulatory WORM compliant". The object-lock/WORM-format claim applies to the **BackBond receipt object only** — never to the vendor-held forensic bundle or raw logs, which never leave the vendor and are not immutable under BackBond control. Customer-facing external copy that uses "WORM" beyond this exact Compliance-ratified receipt-object phrasing additionally requires Legal to clear the storage claim (Claims condition).

## Why the current proposal is insufficient

- `ci_plugin_result()` (`api/ci_scan_ext.py:323`) is an optional CI GET projection, not the terminal webhook assembly path; patching only it does not create custody (Do-not-do #1).
- `_customer_result_response()` (`api/scan_handlers.py`) intentionally strips `snapshot_hash` and `forensic_bundle_uri` from the ratified customer adapter.
- `scan_log.snapshot_hash` covers canonicalized `score_json`, **not** the forensic bundle bytes.
- `compute_probe_set_hash()` covers sorted **dimension identifiers**, not the exact probes, versions, configuration, corpus, or execution assets.
- SQLite write-once triggers stop ordinary application UPDATEs but do not bind the database owner; they are not storage-level WORM.

`evidence_receipt_v1` therefore adds three digests the existing surface does not carry (`evidence_bundle_sha256`, `probe_manifest_sha256`, `scanner_build_digest`), a BackBond-assigned time, and a BackBond signature — none of which exist on the current path.

## Canonical byte representations (frozen by this ADR)

All digests are `sha256:<64-hex-lowercase>`.

1. **Evidence bundle** (`evidence_bundle_sha256`): the scanner writes a deterministic archive — entries sorted by POSIX path, fixed `0` mtime/uid/gid/mode-normalized headers, no compression timestamps — and hashes the **archive bytes**. The archive builder is pinned; two runs over identical inputs yield identical bytes. The archive stays in the vendor environment; only its digest is submitted.
2. **Probe manifest** (`probe_manifest_sha256`): SHA-256 of the canonical JSON manifest listing, for every probe that ran, `{probe_id, probe_version, config_sha256, corpus_snapshot_id, corpus_snapshot_sha256, execution_asset_sha256}`, array sorted by `(probe_id, probe_version)`. This supersedes `compute_probe_set_hash`'s dimension-id-only hash. Canonical JSON = UTF-8, sorted keys, no insignificant whitespace, `separators=(",", ":")` — the same canonicalization `snapshot_hash` already uses (`api/ci_scan_ext.py:77`), reused verbatim so the bytes are byte-stable.
3. **Score snapshot** (`score_snapshot_sha256`): the existing `scan_log.snapshot_hash` value (canonical `score_json`), carried through unchanged. This binds the receipt to the exact scored result.
4. **Scanner build** (`scanner_build_digest`): the immutable container image digest (`sha256:...`) of the scanner that produced the scan, so a receipt names the exact build.

Canonicalization for all four, plus the receipt-signing byte string (below), is frozen here and may only change under a new receipt `version`.

## Authenticated submission protocol

1. Submission endpoint is **BackBond-controlled** (receipt service), reachable from the scanner runtime, not from customer CI.
2. The scanner authenticates with a **scanner-identity credential** (mTLS client cert or a short-lived scanner-scoped token issued to the scanner runtime, not to the tenant). The BackBond private signing key is **never** placed in customer-controlled code or CI (Do-not-do #3).
3. Request body carries ONLY: `version`, `scan_id`, opaque `tenant_ref`, the four digests, `scanner_build_digest`, and a scanner-side idempotency nonce. No logs, prompt/response content, customer names, tool inventories, or other PII (Do-not-do #4).
4. `tenant_ref` is an opaque, non-reversible reference (salted keyed hash of the internal tenant id); it partitions receipts for conflict/substitution checks without carrying customer identity.
5. The service validates the scanner identity, assigns `received_at` from the BackBond trusted clock, assigns `receipt_id`, and applies the idempotency/conflict rules below before signing.

## Receipt schema — `evidence_receipt_v1`

Frozen fields (full schema in `contracts/evidence-receipt-v1.md`):

| field | type | source |
|---|---|---|
| `version` | `"evidence_receipt_v1"` | constant |
| `receipt_id` | uuid | server-assigned |
| `scan_id` | string | submission |
| `tenant_ref` | opaque string | submission |
| `evidence_bundle_sha256` | `sha256:<hex>` | submission |
| `probe_manifest_sha256` | `sha256:<hex>` | submission |
| `score_snapshot_sha256` | `sha256:<hex>` | submission |
| `scanner_build_digest` | `sha256:<hex>` | submission |
| `received_at` | RFC 3339 UTC | **server-assigned** |
| `signing_key_id` | string | server (active key) |
| `signature_alg` | `"ed25519"` (v1) | server |
| `signature` | base64 | server, over canonical signing body |

The **signing body** is the canonical JSON of every field above except `signature`, with `signing_key_id` and `signature_alg` included so the key/alg are bound into the signature. Verifiers reconstruct that body, look up `signing_key_id` in the published BackBond key directory, and check the signature.

## Key lifecycle

- **Algorithm:** Ed25519 (v1), matching the existing scanner Ed25519 certificate/signing semantics (ADR-0064 §Implementation) so we reuse one verification path.
- **Custody:** private key held only by the BackBond receipt service (KMS-backed / non-exportable). Rotation issues a new `signing_key_id`; the directory publishes each key's `{status: active|retired|revoked, not_before, not_after}`.
- **Verification vs. issuance:** a **retired** key still **verifies** historical receipts (they remain valid) but is **never** used to sign new ones; a **revoked** key fails verification (DoD #6 retired/unknown-key test). Signing always uses the single `active` key.
- Historical receipts are never re-signed on rotation.

## Retention semantics

Receipts are written to **retention-locked storage under BackBond control**. Compliance (BEAAA-18823) ratified the following, which is now binding on this ADR and on Child 2 (BEAAA-18819) acceptance:

- **Nameable guarantee (receipt object only):** a **BackBond-controlled retention-locked receipt**, stored under a **WORM-format / non-rewriteable, non-erasable object-lock control for 10 years from `received_at`**. This names the BackBond receipt object only — not the raw logs or the vendor-held forensic bundle.
- **Retention duration:** minimum `retain-until = received_at + 10 years` (7-year claims run-off + 3-year policy-term buffer). Legal hold MUST be able to **extend** retention; shortening or early deletion is prohibited.
- **Custody controls (Child 2 acceptance gate before issuance ships):** object-lock retention is set **at write time** in compliance/governance mode (or an equivalent provider-native immutability mode). The receipt-writer identity may create objects and set retention but MUST NOT delete, overwrite, bypass governance retention, or shorten retention. Delete authority is **separate** and unavailable to scanner runtime, customer CI, and receipt writers.
- **Audit evidence (Child 2 acceptance gate before issuance ships):** audit events for receipt create, identical retry, digest conflict, cross-tenant rejection, signature/key lookup failure, key rotation/retirement/revocation, retention/legal-hold extension, and any denied overwrite/delete attempt. Child 2 acceptance must include a test or provider evidence showing a second write/overwrite is denied, delete is denied before `retain-until`, retention metadata is present, and the writer IAM lacks delete/bypass permissions.

This ADR freezes the *architecture* (append-only receipt table + object-locked blob, writer cannot delete); Child 2 proves the controls above with independently testable evidence.

## Failure behavior, idempotency, conflict handling

- **Idempotent retry:** identical resubmission (same `scan_id` + identical four digests) returns the **same** `receipt_id`, `received_at`, and `signature` — the first receipt, byte-for-byte (DoD #4). Keyed on `(scan_id, evidence_bundle_sha256, probe_manifest_sha256, score_snapshot_sha256, scanner_build_digest)`.
- **Conflict:** a submission for an existing `scan_id` with **any differing digest** is **rejected** (`409 receipt_digest_conflict`) and written to an audit log; the original receipt is never mutated or overwritten (DoD #4).
- **Cross-tenant substitution:** a `scan_id`/`tenant_ref` pairing that contradicts the stored pairing is rejected and audited (DoD #6).
- **Fail-closed:** if the receipt service is unreachable, the signing key is unavailable, or signing fails, the scan result is still delivered but the receipt block is **absent** with `receipt_status: unavailable` — the scanner never fabricates or self-signs a receipt (mirrors the `ci_plugin_result` `infra_retry`-not-fabricate posture).
- **Replay:** a receipt replayed for a different `scan_id` fails signature/body binding because `scan_id` is inside the signed body (DoD #6).

## Versioning and output surface

- Receipt schema is versioned by the `version` literal; any canonicalization or field change is a new version. Contract lives at `contracts/evidence-receipt-v1.md` and is bumped via the `are-scanner-api-v1` patch chain when the webhook/CI projection changes.
- Terminal webhook (`_customer_result_response`) and CI plugin (`ci_plugin_result`) gain an **optional, feature-gated** `evidence_receipt` object carrying exactly the schema above — no raw evidence, no PII (DoD #5). A consumer that ignores it reads a byte-identical prior response.

## Verification procedure (Claims / auditor)

1. Take a later vendor-supplied bundle; rebuild the canonical archive; compute `sha256`.
2. Fetch the receipt for the `scan_id`; verify `signature` over the reconstructed signing body using the `signing_key_id` from the BackBond key directory (reject unknown/revoked keys).
3. Compare the recomputed digest to `evidence_bundle_sha256` (and manifest/score digests as needed). Match ⇒ the supplied bundle hashes to the same digest BackBond received (not proof the evidence was truthful/complete when collected); mismatch ⇒ post-receipt alteration.
4. `received_at` is the BackBond-assigned receipt time; treat vendor timestamps inside the bundle as unverified.

## Rollback

Feature-gated. Rollback disables receipt issuance and delivery and reverts the webhook/CI projection to the prior contract version. Historical receipts and existing score results are never modified (Rollback §). Tier-2 PR-revert.

## Rejected alternatives

- **Patch `ci_plugin_result()` to emit `snapshot_hash` + `probe_set_hash` and call it done** — rejected: not the terminal path, wrong hash coverage, no BackBond time, no signature, no custody (Do-not-do #1).
- **Sign in the scanner / customer CI** — rejected: puts a BackBond private key in customer-controlled code (Do-not-do #3).
- **Ship raw bundle to BackBond** — rejected: violates raw-evidence locality and PII rules; raw logs stay in-VPC.
- **Call the SQLite write-once trigger WORM** — rejected: it does not bind the DB owner; it is not storage-level WORM.

## Pre-submission self-audit

1. One outcome: a BackBond-signed, retention-stored receipt that detects post-receipt bundle alteration without moving raw evidence or PII.
2. Audience/action: Claims Architecture + Compliance ratify the trust/retention language; Underwriter co-signs the output-surface change; Scanner Engineer implements Children 2/3 after acceptance.
3. Supreme Law: reproducibility, calibration, discrimination, tamper resistance unchanged; NO score, rubric, corpus, band, or weight touched (DoD #7).
4. No score shipped in this artifact; digests carry scanner_build_digest + corpus/probe provenance.
5. No contract scoring-math change ⇒ N=5 calibration delta NOT triggered; the receipt is an additive output surface, not a scoring path.
6. Rollback is feature-gate off + projection revert; historical receipts and score results untouched.
7. No LLM judge added or changed.
8. Canonical terminology: "the BackBond Scanner", "the Scanner"; "ARE" appears only in literal identifiers (`are-scanner-api-v1`).
9. Carrier Rule: no carrier framing; "vendor" = the scanned-agent operator, not an adversary.
10. Pre-empted critique: the trust claim is deliberately narrow (detect alteration ≠ prove truthfulness). Trust/retention wording ratified 2026-07-29 by Claims (BEAAA-18822) + Compliance (BEAAA-18823); the still-prohibited terms (non-repudiation, irrefutable, cryptographic custody, SEC 17a-4 / regulatory-WORM compliant, any WORM claim over the vendor bundle) are enumerated in §Trust claim and remain withheld.
