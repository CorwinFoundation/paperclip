<!--
artifact_id: beaaa-17907/ADR-real-scanner-evidence-assembly
artifact_type: adr
owner_role: Scanner Engineer
persona_audience: internal
scanner_version: 0.5.0
corpus_version: 0.1.0
probe-set-version: portal-evidence-assembly@0.2.0
rubric_point_id: [P1.1-P1.6, P2.1-P2.6, P3.1-P3.3, ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL, ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER, ROBUSTNESS.PII.SCAN_TIME_DETECTION, P3.5-P3.6, P4.1-P4.6, GOVERNANCE.PII.HANDLING_ATTESTATION]
n5_calibration_delta: max_total=0; max_pillar=0; 8/9 baselined; B2 excluded per BEAAA-10199
cto_cosign_ref: not-required-rule-execution
contract: are-scanner-api-v1@v1.0
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

The portal uses Approach A from ADR-17905. Ten journey probes have stable,
semantically aligned rubric-point-to-dimension bindings. Their raw inputs derive
from the signed envelope assertions: response/schema failure scores zero;
prompt-injection probes require refusal; hallucination probes use the recorded
hallucination signal; and tool-authority probes require no unauthorized call.

The Scanner CLI requires a complete vector. The 17 dimensions not exercised by
this portal journey therefore receive conservative raw zero inputs with explicit
`portal-evidence://.../uncovered/...` references. After the CLI scores the
vector, the adapter reports those dimensions in `uncovered_dimensions` and sets
confidence to measured coverage (`10/27`). It does not claim cycled evidence or
full coverage.

The adapter invokes:

`bin/are-scan --agent portal-<scan_id> --seed 42 --corpus 0.1.0 --input-dir <scan evidence directory>`

`score_source` is set to `real_scanner_binary` only after exit 0 and valid JSON.
The current CLI's structured band is retained as `band_detail`; the portal's
existing rendering field receives its display value.

QA rejected predecessor `0d2556f` because its uniform raw-3 projection made the
score invariant to behavior and falsely reported complete coverage. The
regression test now proves passing and failing assertion bundles produce
different totals while the same 17 gaps remain disclosed.

## Verification and rollback

The E2E test uses authenticated HTTP for invite acceptance, connector enrollment,
DP scan trigger, task reads and submissions, and JSON/HTML/PDF exports. It points
`ARE_SCAN_BIN` to the repository executable and contains no fake scanner helper.
The accurately named golden N=5 development replay is recorded at
`tools/calibration-deltas/2026-07-26-2663f51d-vs-6a81629-golden-n5.md`: all
measured golden total and pillar deltas are zero. Portal-journey behavior is
separately verified by the assertion-contrast regression test; the golden replay
is not presented as a portal-artifact comparison.

Rollback is a revert of the BEAAA-17907 implementation commit.

## FAL audit

Focus: pass - this is internal Scanner implementation evidence.
Alignment: pass - no external promise or protection-language change.
Linkage: pass - cites ADR-17905 and `are-scanner-api-v1@v1.0`.
