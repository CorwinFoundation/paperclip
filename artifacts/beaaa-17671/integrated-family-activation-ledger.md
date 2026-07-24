# BEAAA-17671 canonical family activation ledger

BLUF: The canonical development integration truthfully verifies **41/61 working** families. Nine additional local Batch-A rows are QA-unverified and excluded; 11 genuine gaps remain fail-closed.

- Scanner version: `0.5.0`
- Corpus version: `canonical-61-family-registry@2026-07-13`
- Probe-set version: `family-activation-ledger/v0.1`
- Rubric point: `ALL_ACTIVE_DIMENSIONS`
- Classification: rule-execution
- Contract: `are-scanner-api-v1@v1.0` (output shape unchanged)
- Customer data: none
- Paid calls: 0
- Score activation: false
- N=5: approved slice receipts record zero delta; the integrated candidate still requires exact-SHA QA.

## Integrated verified slices

| Slice | Families | Approval evidence |
| --- | ---: | --- |
| Batch B golden target | 12 | Existing approved Batch B evidence and regression mapping |
| Batch C | 21 | BEAAA-17700, exact SHA `335680b0665c0abed88e7260dbf390572cb3c23b` |
| Batch-A isolated frameworks | 4 | BEAAA-17707 approved candidate imported into the canonical ledger |
| Batch-A pinned JSONL | 4 | BEAAA-17714, exact integration SHA `609502f73659dabdd065fdcddd54ced159a48508` |

Machine-readable row classifications and immutable evidence references are in
`artifacts/beaaa-17671/integrated-family-activation-ledger.json`.

## Excluded from the verified aggregate

- Nine local Batch-A rows (ordinals 3-9, 15, and 18) have source evidence but are excluded pending QA verification for this integration scope. They are not fail-closed gaps.

## Remaining

- Eleven genuine gaps (ordinals 10, 11, 20, and 33-40) still require configured-target runners/evaluators. They remain fail-closed.
- The complete-working CI predicate must continue to reject `61/61 working` until every row passes and the exact integrated candidate receives QA approval.
