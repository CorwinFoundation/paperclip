# BEAAA-17667 - 61-family activation ledger evidence

BLUF: The canonical 61-family inventory is frozen in
`tools/probe-registry/family_activation_ledger_v0.1.json`. The zero-paid-call
normal development runner reports a truthful `0/61` target-bound baseline and
fails every unproved family closed. This artifact does not activate scoring.

## Classification and contract

- Deliverable: a machine-readable activation ledger and baseline for the CTO
  program, enabling three implementation batches within a zero-paid-call budget.
- Classification: rule execution, under the supervision-contract row for
  implementing runner/probe plumbing within existing rules.
- Scanner contract: `contracts/are-scanner-api-v1.md@v1.0` (grandfathered base
  filename; active additive patches unchanged).
- Scanner version: `0.5.0`
- Corpus version: `scanner-test-registry-baseline-2026-07-13`
- Probe-set version: `family-activation-ledger/v0.1`
- Rubric point ID: multi-family activation inventory (61 frozen rows; no scoring
  activation)
- CTO co-sign: not required for this rule-execution artifact.

## Verification

```text
python3 scripts/build_beaaa17667_ledger.py
wrote 61 rows to tools/probe-registry/family_activation_ledger_v0.1.json

python3 tools/family_activation_ledger.py \
  --write-baseline artifacts/beaaa-17667/family_activation_baseline.json
truthful_baseline: 0/61

python3 -m pytest -q tests/test_beaaa17667_family_activation_ledger.py
2 passed in 0.34s
```

The normal development runner used test-fixture posture, a hard call cap of
zero, no customer data, and no score activation. Every row without terminal
target-bound passing evidence is listed in `failures`; catalogue, fixture, and
governance-only rows cannot satisfy `family_is_working`.

## N=5 golden calibration

`python3 scripts/beaaa5528_n5_calibration.py` replayed B1, G1, G2, M1, M2, and
M3 five times each. Every fixture had spread 0, total delta 0, and pillar-max
delta 0. The ledger and baseline do not enter scoring.

## FAL audit

- Focus: pass. This internal artifact makes Scanner evidence activation
  measurable and auditable.
- Alignment: pass. It claims no protection, coverage, or score movement.
- Linkage: pass. It uses the canonical BackBond Scanner evidence and continuous
  verification language.

## Rollback

Remove only the new ledger generator, ledger validator, focused test, frozen
JSON ledger, and this `artifacts/beaaa-17667/` evidence directory.
