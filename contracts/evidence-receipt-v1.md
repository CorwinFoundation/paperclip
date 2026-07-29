---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "evidence-receipt-v1 freezes the BackBond-signed receipt schema, its canonical byte representations, the authenticated scanner->BackBond submission protocol, idempotency/conflict semantics, key lifecycle, and the optional feature-gated webhook/CI projection. Additive to are-scanner-api-v1; no scoring, rubric, corpus, band, or weight change."
contracts_referenced: ["are-scanner-api-v1@v1.0", "adr-0067-beaaa18817-evidence-receipt-v1-custody"]
ticket_refs: ["BEAAA-18817"]
version: "evidence-receipt-v1@v1 (RATIFIED 2026-07-29)"
prior_version: null
self_audit_passed: true
escalations_noted: ["Ratified 2026-07-29: Claims Architecture (BEAAA-18822) + Compliance (BEAAA-18823) trust/retention language (DoD #2); Underwriter (BEAAA-18824) output-surface co-sign. No Founder gate — no pillar-weight / scoring-math change. Prohibited terms remain: non-repudiation, irrefutable, cryptographic custody, SEC 17a-4 / regulatory-WORM compliant, any WORM claim over the vendor bundle or raw logs."]
---

# Contract: `evidence-receipt-v1` (PROPOSED — ratification-gated)

**Status:** RATIFIED 2026-07-29 under [BEAAA-18817](/BEAAA/issues/BEAAA-18817). Frozen by `docs/adr/adr-0067-beaaa18817-evidence-receipt-v1-custody.md`. Claims Architecture (BEAAA-18822) + Compliance (BEAAA-18823) confirmed the trust/retention language; the Underwriter (BEAAA-18824) co-signed the projection. Children 2/3 (BEAAA-18819 / BEAAA-18825) may now proceed; §8 custody + audit controls are Child-2 acceptance gates before issuance ships.

**Change classification:** additive to `are-scanner-api-v1`. New receipt service + optional feature-gated `evidence_receipt` object on the terminal webhook and CI plugin. No existing field renamed/removed. Tier-2 rollback (feature-gate off + projection revert).

## §1. Trust claim

`evidence_receipt_v1` proves **which digest BackBond received** for a `scan_id` and **when BackBond received it** (server-assigned time), and lets a holder **detect** whether a later vendor-supplied bundle diverged from the received digest. A match shows the supplied bundle hashes to the same digest BackBond received for that scan; it does **NOT** prove the vendor-controlled evidence was truthful or complete when collected.

**Ratified wording (Claims BEAAA-18822 + Compliance BEAAA-18823, 2026-07-29).** Permitted: "BackBond-signed", "tamper-evident against the received digest", "detects post-receipt alteration" / "detects later divergence from the received digest"; and — for the **BackBond receipt object only** — "BackBond-controlled retention-locked receipt" and "WORM-format / non-rewriteable, non-erasable object-lock control for 10 years from `received_at`". Prohibited: "non-repudiation", "irrefutable", "cryptographic custody" (of the vendor evidence), "SEC 17a-4 compliant", "regulatory WORM compliant", and any WORM/immutability claim over the vendor-held forensic bundle or raw logs (which never leave the vendor). Customer-facing external "WORM" copy beyond the exact receipt-object phrasing also requires Legal clearance (Claims condition).

## §2. Schema

```jsonc
{
  "version": "evidence_receipt_v1",
  "receipt_id": "<uuid>",                       // server-assigned
  "scan_id": "<string>",
  "tenant_ref": "<opaque, non-reversible>",     // salted keyed hash of internal tenant id
  "evidence_bundle_sha256": "sha256:<hex>",     // canonical forensic archive bytes
  "probe_manifest_sha256": "sha256:<hex>",      // exact probes/versions/config/corpus/assets
  "score_snapshot_sha256": "sha256:<hex>",      // canonical score_json (= scan_log.snapshot_hash)
  "scanner_build_digest": "sha256:<hex>",       // immutable scanner image digest
  "received_at": "<RFC3339 UTC>",               // BackBond-assigned trusted clock
  "signing_key_id": "<key id>",
  "signature_alg": "ed25519",
  "signature": "<base64>"                        // over the canonical signing body (§4)
}
```

## §3. Canonical byte representations

- **Canonical JSON** = UTF-8, sorted keys, `separators=(",", ":")`, no insignificant whitespace — reused verbatim from the existing `snapshot_hash` canonicalization.
- **evidence_bundle_sha256** = SHA-256 of a deterministic archive: entries sorted by POSIX path, normalized headers (mtime/uid/gid/mode fixed), no compression timestamps. Archive stays in the vendor environment; only the digest is submitted.
- **probe_manifest_sha256** = SHA-256 of canonical JSON array of `{probe_id, probe_version, config_sha256, corpus_snapshot_id, corpus_snapshot_sha256, execution_asset_sha256}` sorted by `(probe_id, probe_version)`. Supersedes the dimension-id-only `compute_probe_set_hash`.
- **score_snapshot_sha256** = existing `scan_log.snapshot_hash` (canonical `score_json`), carried unchanged.
- **scanner_build_digest** = the scanner container image digest.

All representations are frozen; any change is a new receipt `version`.

## §4. Signing body

The signed byte string is the canonical JSON of every schema field **except** `signature`. `signing_key_id` and `signature_alg` are inside the signed body. Verifiers reconstruct that body, resolve `signing_key_id` in the BackBond key directory, and verify. Ed25519 for v1 (reuses the scanner's existing Ed25519 verification path).

## §5. Submission protocol (scanner -> BackBond receipt service)

- Endpoint is BackBond-controlled, reachable from the scanner runtime, NOT customer CI.
- Auth = scanner-identity credential (mTLS or short-lived scanner-scoped token). BackBond private signing key never leaves the receipt service; never placed in customer code/CI.
- Request carries only: `version`, `scan_id`, `tenant_ref`, the four digests, `scanner_build_digest`, idempotency nonce. No logs, prompt/response content, customer names, tool inventory, or PII.
- Server validates identity, assigns `received_at` + `receipt_id`, applies §6, then signs and stores.

## §6. Idempotency, conflict, failure

- **Idempotent:** identical resubmission (same `scan_id` + identical four digests) returns the original receipt byte-for-byte (same `receipt_id`/`received_at`/`signature`).
- **Conflict:** same `scan_id`, any differing digest ⇒ `409 receipt_digest_conflict`, audited; original never mutated.
- **Cross-tenant:** `scan_id`/`tenant_ref` contradicting the stored pairing ⇒ rejected + audited.
- **Fail-closed:** service/key unavailable or signing failure ⇒ result still delivered, receipt absent with `receipt_status: unavailable`; scanner never self-signs.
- **Replay:** `scan_id` is inside the signed body ⇒ receipt cannot be reused for another scan.

## §7. Key lifecycle

- Ed25519 (v1). Private key KMS-backed / non-exportable, receipt-service-only.
- Rotation ⇒ new `signing_key_id`. Directory publishes `{status: active|retired|revoked, not_before, not_after}`.
- `active` signs; `retired` still verifies historical receipts but never signs; `revoked` fails verification. Historical receipts never re-signed.

## §8. Retention

Append-only receipt store + object-locked blob under BackBond control; writer identity separated from delete authority. Compliance-ratified guarantee (BEAAA-18823), receipt object only:

- **BackBond-controlled retention-locked receipt**, WORM-format / non-rewriteable, non-erasable **object-lock for 10 years from `received_at`** (`retain-until = received_at + 10y`; legal hold may extend; shortening / early deletion prohibited). Names the receipt object only — never raw logs or the vendor bundle.
- **Custody controls (Child-2 acceptance gate):** object-lock set at write time in compliance/governance mode; writer identity may create + set retention but MUST NOT delete, overwrite, bypass governance retention, or shorten; delete authority separate and unavailable to scanner runtime, customer CI, and receipt writers.
- **Audit events (Child-2 acceptance gate):** create, identical retry, digest conflict, cross-tenant rejection, signature/key lookup failure, key rotation/retirement/revocation, retention/legal-hold extension, denied overwrite/delete. Child-2 acceptance shows second-write/overwrite denied, delete denied before `retain-until`, retention metadata present, and writer IAM lacks delete/bypass.

## §9. Output projection (feature-gated)

Terminal webhook (`_customer_result_response`) and CI plugin (`ci_plugin_result`) gain an OPTIONAL `evidence_receipt` object = the §2 schema, with NO raw evidence or PII. Feature-gated; consumers that ignore it read a byte-identical prior response. Delivered under Child 3 after ratification.

## §10. Verification procedure

1. Rebuild the canonical archive from a supplied bundle; compute `sha256`.
2. Verify the receipt `signature` over the reconstructed signing body via the directory key (reject unknown/revoked).
3. Compare recomputed digest to `evidence_bundle_sha256` (and manifest/score digests). Mismatch ⇒ post-receipt alteration.
4. Trust `received_at` (BackBond clock); treat in-bundle vendor timestamps as unverified.
