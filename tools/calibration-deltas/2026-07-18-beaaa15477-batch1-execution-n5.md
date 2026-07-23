# BEAAA-15477 N=5 calibration delta

BLUF: Plan A Batch 1 evidence execution changes no Scanner scoring input, rubric, weights, judge configuration, or emitted contract shape. Baseline and candidate scores are therefore byte-identical for golden seeds 0-4: total and every pillar delta are `0.0000`.

- Contract: `are-scanner-api-v1@v1.0`
- Classification: rule execution - existing-rubric target-bound probe execution
- Scanner: `0.4.1`
- Corpus: `2026.6.24`
- Probe set: `runner@784778473d3e+plan-a-v0.2`
- Immutable selection SHA-256: `710a49de1e5f1f394d611f9d71b3e1c41df52af03f3f2ade4cea7bc0f0b3d3a8`
- Seeds: `0, 1, 2, 3, 4`
- Candidate effect: evidence artifacts only; activation and promotion prohibited

| Seed | Total delta | Governance | Robustness | Bias & Fairness | Operations |
|---:|---:|---:|---:|---:|---:|
| 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| 1 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| 2 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| 3 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| 4 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

P1.6 and P2.5 remain uncovered unless their independent denominator/applicability requirements are satisfied. `cto_cosign_ref`: N/A (rule execution). No promotion is authorized.
