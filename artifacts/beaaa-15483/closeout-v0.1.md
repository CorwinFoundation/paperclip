---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "Executes the frozen DoNotAnswer selection against an authenticated endpoint while preserving P2.4 as uncovered, producing audit-grade evidence without changing the BackBond Score."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-15483", "BEAAA-15482"]
proof_points_cited: []
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["No escalation: Underwriter approved evidence-only execution and rejected scoring activation; no score, rubric, judge-policy, output-shape, or tamper-resistance change was made."]
scanner_version: "0.4.1"
corpus_version: "2026.6.24+donotanswer_subset@n100_seed42-evidence-only"
probe-set-version: "donotanswer_subset@n100_seed42"
rubric_point_id: "P2.4"
n5_calibration_delta: "0 total and 0 for every pillar at seeds 0-4; evidence-only path is disconnected from scoring"
cto_cosign_ref: null
---

# BEAAA-15483 closeout

**BLUF:** PASS. The frozen 100-row DoNotAnswer corpus was executed against an authenticated target endpoint. All 100 selected cases are attempted, terminal, and evidenced; P2.4 remains unchanged and uncovered, and no promotion occurred.

## Intake and authority

- Deliverable: an evidence-only authenticated probe execution for the Underwriter that produces 100 terminal P2.4-adjacent observations within 120 target and 360 judge calls.
- Classification: rule execution under `scanner-implementation-supervision-v1@v1.1`, table row “Implement a new probe inside an existing rubric_point_id”; the Underwriter explicitly rejected scoring activation.
- Upstream revision consumed: [BEAAA-15482](/BEAAA/issues/BEAAA-15482) comment `c1fca1c5-4aa0-44d2-8057-1a35de0bf917`, created 2026-07-19T04:15:18.270Z.
- Selection authority: artifact `b650be2c`; frozen corpus SHA-256 `e83b3d3dfbd01b9501ca19a386e40080b0e0c53aefd442375fcdb4712a6bf4d8`; manifest SHA-256 `d47fe2d42f8aab64caa07061e23c5b20848b7a0489c408d558eea8d763d9583e`.

## Result

| Control | Result |
|---|---:|
| Selected / attempted / terminal / evidenced | 100 / 100 / 100 / 100 |
| Authenticated target | OpenRouter bearer-auth API |
| Target model | `meta-llama/llama-3.3-70b-instruct` |
| Judge | `anthropic/claude-sonnet-4.6` |
| Temperature / seed / ensemble | 0 / 42 / 3, strict majority |
| Recorded target / judge calls | 100 / 300 |
| Terminal ledger SHA-256 | `8f7f37609d94f04b71cbab66bd1aac80346e6ea08c4277dfb979feb0e732322f` |
| P2.4 scoring state | unchanged / uncovered |
| Promotion | prohibited; none performed |

Each terminal object contains request and response hashes, judge-vote hashes and verdicts, timestamps, Scanner/corpus/probe/model pins, seed, ensemble policy, applicability classification, and a reachable local evidence reference. Response text is not retained.

## N=5 calibration delta

| Seed | Total | GOVERNANCE | ROBUSTNESS | BIAS-FAIRNESS | OPS |
|---:|---:|---:|---:|---:|---:|
| 0 | 0 | 0 | 0 | 0 | 0 |
| 1 | 0 | 0 | 0 | 0 | 0 |
| 2 | 0 | 0 | 0 | 0 | 0 |
| 3 | 0 | 0 | 0 | 0 | 0 |
| 4 | 0 | 0 | 0 | 0 | 0 |

The delta is zero because the runner emits an evidence sidecar and neither imports nor writes the scoring path. This preserves the reproducibility, evidence-completeness, pinned-version, and tamper-resistance guarantees in `are-scanner-api-v1@v1.0`.

## Verification and rollback

- Validation: 100 JSONL rows, 100 unique case IDs, 100 terminal objects, 100 evidenced objects; all rows use the pinned judge and `unchanged_uncovered` scoring state.
- Rollback: disable or remove the evidence-only runner and candidate artifact directory; preserve the terminal ledger append-only. The preceding corpus baseline remains `2026.6.24`.
- FAL audit: pass. Facts are tied to the manifest and terminal ledger; assumptions are labeled; no conjectural scoring or coverage claim is made.
- Brand gate: not applicable; this is an internal engineering artifact.
