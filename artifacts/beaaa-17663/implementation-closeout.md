---
artifact_type: implementation_closeout
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This closeout provides a zero-call root1 r7 exact-preimage candidate so Wave 2 can proceed only through independent verification and Founder signing."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-17663", "BEAAA-17510", "BEAAA-17472"]
proof_points_cited: ["Founder root-only tuple comment f10d2df2-2d72-4cd1-9ee4-6f34d4fe7d9a", "CTO decoded 32-byte public key and reproduced fingerprint", "direct parent a63c12215c5321b08a33dff4be3f696f79b40b76"]
version: "0.1"
prior_version: "artifacts/beaaa-17530/implementation-closeout.md"
self_audit_passed: true
escalations_noted: []
---

# BEAAA-17663 Implementation Closeout

BLUF: BEAAA-17663 replaces only the rejected engineering-originated r7 public tuple and unsigned preimage with a new Founder root-only root1 tuple. Status is `FRESH_ROOT1_R7_PREIMAGE_READY_EXACT_SIGNATURE_APPROVAL_REQUIRED`; no signing, live call, scoring, underwriting, retention activation, merge, deploy, or promotion was performed.

Classification: rule execution under `scanner-implementation-supervision-v1@v1.1`, row `Refactor that doesn't touch behavior` / zero-call harness artifact replacement. The change does not alter scoring math, rubric weights, API output shape, tamper-resistance semantics, or judge model behavior.

Contract anchor: `contracts/are-scanner-api-v1.md@v1.3.0`.

Required metadata:

- `scanner_version`: `0.5.0`
- `corpus_version`: `out_of_scope_containment@0.2.0; harmful_output_golden@0.2.0; ai_disclosure@0.2.0`
- `probe-set-version`: `out_of_scope_containment@0.2.0; harmful_output_golden@0.2.0; ai_disclosure@0.2.0`
- `rubric_point_id`: `OPS.SCOPE.OUT_OF_SCOPE_CONTAINMENT; OPS.REFUSAL.HARMFUL_OUTPUT_GOLDEN; GOVERNANCE.TRANSPARENCY.AI_DISCLOSURE; P1.4; P4.1`
- `N=5 calibration delta`: `artifacts/beaaa-17663/n5-calibration-zero-delta.json`, total/pillar/band/pricing delta `0`; `openrouter_calls_made: 0`
- `cto_cosign_ref`: not required for rule execution

Root1 public tuple:

- `key_id`: `wave2-target-production-authority-r7-2026-07-root1`
- `public_key_base64url_no_pad`: `Dt0BaMLDj9DgU0FgHvcjZiZId0XCc9zhuB8norYUoOw`
- `public_key_fingerprint`: `sha256:45b1b136e040e1caf2778fffaf0a5754eecd2475de284ad32188757b9d16e3db`
- `custody_receipt_sha256`: `sha256:db8a1a79267bf0ca2a5e1ca350f4ba35820c1a0207969cdbdb677832ed356be8`
- custody state: Founder-controlled root-only private key, root:root `0700` directory, root:root `0600` key and receipt, unused and never printed/attached/exposed to Scanner Engineering.

Exact canonical preimage:

- path: `artifacts/beaaa-17663/target-authorization-preimage.bytes`
- byte length: `5214`
- SHA-256: `sha256:f4176e1fd469831396271a42cbe268f689d182615fb512fdf8293efcc2a9704d`
- packet index: `artifacts/beaaa-17663/packet-hash-index.json`

Fail-closed rejections:

- rejects all r6 key/signature/preimage/approval/partial packet/alternate lease residue before target invocation
- rejects rejected engineering r7 key id `wave2-target-production-authority-r7-2026-07`
- rejects rejected engineering r7 public key prefix `9Crbc...`
- rejects rejected engineering r7 fingerprint `sha256:66b218023b99c0e00aea594b57f9711e68dbebbc7619c930a7890b2984c36669`
- rejects rejected engineering r7 preimage `sha256:42d2f52f7fce8b9c10add1e9ff2f3a6c12b520f229910e62017c903eec8a6a59`
- rejects rejected engineering r7 commit `8adb96a78f60c95c21eda0a0fbd268ade255f626`

Pre-submission audit:

- external-bound asset: no
- Brand Backbone gate: not applicable
- FAL / house-style audit: passed for internal closeout
- deprecated terms: only grandfathered contract filename and internal historical ticket context used
- target calls: none
- OpenRouter calls: `0`
