---
artifact_id: BEAAA-20991-authoritative-probe-registry-closeout-v1
ticket_ref: BEAAA-20991
persona_audience: internal
artifact_type: implementation_closeout
scanner_version: "0.5.0"
corpus_version: "0.2.0"
probe_set_version: "authoritative-probe-registry@1.0.1"
rubric_point_id: "ALL_ACTIVE_DIMENSIONS"
n5_calibration_delta: "N/A - source diff contains registry generator, derived manifests, schema, CI, tests, and internal documentation only; no scoring, rubric, probe content, judge, band, threshold, or runtime-default source changed."
cto_cosign_ref: null
contract_ref: "are-scanner-api-v1@v1.3.0"
classification: "rule-execution"
classification_basis: "Metadata/registry derivation under existing rules; no contract output or scoring behavior changes."
fal_audit: "PASS - internal implementation evidence is focused on truthful Scanner authority, aligned to current product state, and linked to BEAAA-20991 and the scanner contract."
---

# BLUF

BEAAA-20991 rebases the authoritative registry onto current master as one deterministic, schema-valid, commit-bound manifest with a fail-closed CI drift check. It preserves all runtime defaults and scoring behavior.

Checked-in provenance is non-circular: `source_closure_sha` binds the deterministic source closure, and `generated_from_commit` binds the branch's parent/base commit. The exact candidate commit requires QA approval on the canonical QA child of BEAAA-20991.

## Before / after

| Claim | Before | After |
|---|---:|---:|
| Registered source-derived rows | conflated | 2,512 |
| Adapter-executable rows | labeled active/executable | 2,202 |
| Executable on default runtime | not separated | 0 |
| Executed in latest evidenced run | not separated | 0 |
| Reported | not separated | 0 |
| Scoring-active | not separated | 0 |
| Proxy-grade | mixed into aggregate | 49 |
| Attestation-only | mixed into aggregate | 7 |
| Fixture/test-only | mixed into aggregate | 82 |
| Inactive/deferred | mixed into aggregate | 8 |
| Blocked/unsupported | not separated | 220 |

Counts are non-additive. A stable-ID row may belong to multiple categories.

## Limitations

The repository does not contain an authoritative latest-run receipt that binds individual stable IDs to executed, reported, or scored outcomes. Those categories therefore fail closed at zero. The 2,202 adapter-executable rows are not described as default-runtime, latest-run, reported, or scored without that proof.

Rollback is one implementation revert. Restoring the prior readers does not alter runtime activation defaults.
