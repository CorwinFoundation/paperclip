# BEAAA-17671 canonical family activation ledger

BLUF: The canonical development integration verifies **61/61 working** families. The claim remains candidate-only until exact-SHA parent QA approval.

- Scanner version: `0.5.0`
- Corpus version: `canonical-61-family-registry@2026-07-13`
- Probe-set version: `family-activation-ledger/v0.1`
- Rubric point: `ALL_ACTIVE_DIMENSIONS`
- Classification: rule-execution
- Contract: `are-scanner-api-v1@v1.0` (output shape unchanged)
- Customer data: none
- Paid calls: 0
- Score activation: false
- N=5: every approved implementation slice records zero maximum total and pillar delta; the exact integrated candidate still requires parent QA.

## Integrated verified slices

| Slice | Families | Approval evidence |
| --- | ---: | --- |
| Batch B golden target | 12 | Existing approved Batch B evidence and regression mapping |
| Batch C | 21 | BEAAA-17700, exact SHA `335680b0665c0abed88e7260dbf390572cb3c23b` |
| Batch-A isolated frameworks | 4 | BEAAA-17707 approved candidate imported into the canonical ledger |
| Batch-A pinned JSONL | 4 | BEAAA-17714, exact integration SHA `609502f73659dabdd065fdcddd54ced159a48508` |
| Batch-A local families | 9 | BEAAA-17713, exact source SHA `418adc0a2f11afcf18a6dd2108019275c1f5879b` |
| Governance/eval families | 3 | BEAAA-17737 approved source SHA `4262cdad4a37f6b6d591c2de0d920b3adf7e1955` |
| Stateful adversarial families | 2 | BEAAA-17738 approved canonical source SHA `5c28392c5f5c7e943739f686268b4ddfc5bffd4d` |
| OPS control families | 6 | BEAAA-17736 approved source SHA `b071d60deec68c7a69f43f442863c62549a5f0ad` |

Machine-readable row classifications and immutable evidence references are in
`artifacts/beaaa-17671/integrated-family-activation-ledger.json`.

## Completion gate

- `tools/family_activation_ledger.py` exits non-zero unless all 61 unique rows satisfy the complete working predicate.
- Any missing evidence, verdict, target binding, or working state lowers the aggregate and fails the gate.
- Parent-level QA must independently approve the exact immutable integration SHA before BEAAA-17671 closes.
