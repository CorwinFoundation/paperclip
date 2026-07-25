<!--
artifact_id: beaaa-17907/ADR-real-scanner-evidence-assembly
artifact_type: adr
owner_role: Scanner Engineer
persona_audience: internal
scanner_version: 0.5.0
corpus_version: 0.1.0
probe-set-version: portal-synthetic-journey@0.1.0
rubric_point_id: [P1.1-P1.6, P2.1-P2.6, P3.1-P3.3, ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL, ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER, ROBUSTNESS.PII.SCAN_TIME_DETECTION, P3.5-P3.6, P4.1-P4.6, GOVERNANCE.PII.HANDLING_ATTESTATION]
n5_calibration_delta: pending
cto_cosign_ref: not-required-rule-execution
contract: are-scanner-api-v1@v1.3.0
parent_issue: BEAAA-17907
-->

# BEAAA-17907 - Real Scanner evidence assembly

**BLUF:** The portal now assembles a dimension-keyed `eval_results.json` from
verified journey envelopes and invokes the repository's real `bin/are-scan`
using its supported CLI flags. The CLI remains the sole scoring authority.

## Classification

Rule-execution under the supervision-contract row for implementing CLI plumbing
within an existing rubric. This change does not alter rubric points, weights,
judge models, scoring math, output-contract shape, or tamper resistance.

## Implementation

The portal uses Approach A from ADR-17905. Ten journey probes have stable
rubric-point-to-dimension bindings. Because the synthetic journey is a
fixed-subset integration proof and the Scanner requires a complete vector, the
verified envelope URIs are deterministically projected across the remaining
dimensions at the journey's neutral raw input. This projection is explicitly
synthetic and is not a production claim that every dimension was independently
measured.

The adapter invokes:

`bin/are-scan --agent portal-<scan_id> --seed 42 --corpus 0.1.0 --input-dir <scan evidence directory>`

`score_source` is set to `real_scanner_binary` only after exit 0 and valid JSON.
The current CLI's structured band is retained as `band_detail`; the portal's
existing rendering field receives its display value.

## Verification and rollback

The E2E test uses authenticated HTTP for invite acceptance, connector enrollment,
DP scan trigger, task reads and submissions, and JSON/HTML/PDF exports. It points
`ARE_SCAN_BIN` to the repository executable and contains no fake scanner helper.

Rollback is a revert of the BEAAA-17907 implementation commit.

## FAL audit

Focus: pass - this is internal Scanner implementation evidence.
Alignment: pass - no external promise or protection-language change.
Linkage: pass - cites ADR-17905 and `are-scanner-api-v1@v1.3.0`.
