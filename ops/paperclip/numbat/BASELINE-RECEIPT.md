---
owner: Scanner Engineer
artifact_type: closeout_receipt
status: candidate
updated_at: 2026-08-02T00:00:00Z
issue: BEAAA-20300
classification: rule-execution
classification_basis: contracts/scanner-implementation-supervision-v1.md - implementation under unchanged rules
scanner_contract: contracts/are-scanner-api-v1.md@v1.0
scanner_version: "0.1.0-appliance"
corpus_version: unchanged
probe-set-version: unchanged
rubric_point_id: N/A-provenance-only
n5_calibration_delta: "0 / not applicable: no scoring, rubric, corpus, probe, or API behavior changed"
cto_cosign_ref: null
source_refs:
  - /BEAAA/issues/BEAAA-20300
  - /BEAAA/issues/BEAAA-20298
claim_or_decision: The reconciled appliance archive, unpacked packaged-source tree, and source manifest describe identical bytes on the BEAAA-20300 candidate.
verification: All declared source hashes, archive CRCs, and extracted archive member hashes pass; the exact reconciliation commit and command transcript are recorded on BEAAA-20300.
approvals: []
version: "2.0"
---

# Appliance 0.1.0 provenance-reconciliation receipt

BLUF: This receipt supersedes the stale byte-equality claim in receipt v1.0. The reconciled archive, 39-file source manifest, and unpacked packaged-source tree describe the same bytes.

## Reconciled candidate artifacts

- Immutable integration input commit: `9a05972a4671abc5cc89e7d097f93545e6be77ce`
- Integration input parent: `2227be283f62824e23187dfda83fe77ff0a78705`
- Integration input tree: `42bba725eb468bac2e275c539f415bc7ba3bac37`
- Archive: `backbond-appliance-0.1.0.zip`
- Archive SHA-256: `dbef6e73a715adda40e7697c070a96b7844398e09c7925926b8e541e5c87ef8a`
- Unpacked packaged source: `backbond-appliance-0.1.0/backbond-appliance/`
- Packaged-source file count: `39`
- Packaged-source manifest: `backbond-appliance-0.1.0.source-tree.sha256`
- Packaged-source manifest SHA-256: `e22a299fe7332d6755979fd576b74c60ae45ed2d40ba25f532d19dd6464921b1`
- Provenance record: `backbond-appliance-0.1.0.provenance.json`

The exact BEAAA-20300 reconciliation commit is recorded in the issue evidence. It is intentionally not embedded here: a tracked file cannot contain the hash of the commit whose hash depends on that file. This receipt binds the archive and manifest bytes directly and identifies the immutable integration input separately.

## Historical baseline, not a current equality claim

The original Founder-delivered archive SHA-256 `f8d283d0013312c61faf9d4e4100b22167710cec8b7cf6a4f7e46079e2399e80` and full on-box manifest SHA-256 `44ebe1ad8584050482928c3a56f2923170a70920f1a7a238b6f625af08e298de` remain preserved as historical provenance only. They do not identify the reconciled archive or assert equality with the integrated unpacked tree.

## Scanner governance

This is rule-execution under `contracts/scanner-implementation-supervision-v1.md`: it reconciles packaging under unchanged Scanner rules. No scoring, rubric, corpus, probe, judge, tamper-resistance, or API behavior changed. The N=5 calibration delta is therefore zero / not applicable by construction. FAL audit: internal evidence artifact; focus, alignment, and linkage are not external-copy surfaces.
