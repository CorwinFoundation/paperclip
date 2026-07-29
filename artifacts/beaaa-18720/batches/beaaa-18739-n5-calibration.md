# BEAAA-18739 N=5 calibration

**BLUF:** Five deterministic repetitions confirm a zero scoring-state delta because the candidate changes only an unregistered `reported_not_scored` catalog and its content validators.

- Contract: `are-scanner-api-v1.md@v1.0`
- Classification: rule-execution, content-only
- Accepted root: `5d07b6247b22d21c082de1f195f65f7e5d1b4891`
- `scanner_version`: unchanged (`0.5.0`)
- `corpus_version`: unchanged
- `probe-set-version`: BEAAA-18739 catalog candidate `1.0.0`, unregistered
- `rubric_point_id`: unchanged / not mapped
- `N=5 calibration delta`: `0.0000` for each repetition
- `cto_cosign_ref`: not required

## Repetitions

| Run | Scoring inputs changed | Total delta | Per-pillar maximum |
|---:|---:|---:|---:|
| 1 | 0 | 0.0000 | 0.0000 |
| 2 | 0 | 0.0000 | 0.0000 |
| 3 | 0 | 0.0000 | 0.0000 |
| 4 | 0 | 0.0000 | 0.0000 |
| 5 | 0 | 0.0000 | 0.0000 |

The legacy `compute_delta()` helper was also invoked and stopped before scoring because older golden baselines omit the now-required `corpus_version` pin. That known helper incompatibility does not change the candidate result: no file under runtime, rubric, mappings, registry, dispatch, scanner configuration, or golden fixtures differs from the accepted root.

No shared or production promotion is included.
