# BEAAA-2182 Track A — Subprocess End-to-End N=5 Calibration

_Generated 2026-06-05T10:50:21.321672+00:00_

**CTO merge gate** (per comment `77974a04` 2026-06-05): **PASS**

## Pins
* scanner_version: `0.3.11`
* corpus_version: `0.1.0`
* rubric_version: `1.0.0`
* seed: `42`
* `ARE_TEST_MODE=1` (AG-A5 Scorer-startup assertion armed)
* `mp.get_context('fork')` (real fork boundary per `_fork_context`)

## Per-fixture results

| Fixture | Pass | in-proc are_total | sub are_total spread | sub vs in-proc byte-equal |
| --- | :---: | ---: | ---: | :---: |
| synthetic_v0 | ✅ | 69 | 0 | ✅ |
| B1 | ✅ | 39 | 0 | ✅ |
| G1 | ✅ | 79 | 0 | ✅ |
| G2 | ✅ | 77 | 0 | ✅ |
| M1 | ✅ | 59 | 0 | ✅ |
| M2 | ✅ | 47 | 0 | ✅ |
| M3 | ✅ | 54 | 0 | ✅ |

## Disposition

**PASS** — the fork boundary preserves byte-equality across all fixtures × N=5 replicates. CTO merge-gate condition (spread = 0 on `are_total` AND every subscore vs. golden roster) satisfied. The Scorer subprocess produces the same §2.2 envelope as the in-process projection, which itself is byte-equal to the CLI offline scoring path (AG-A2 in-process calibration at `tools/calibration-deltas/2026-06-05-beaaa2182-track-a-n5.{json,md}`).

Failure-mode coverage (per CTO comment `77974a04`):
* (a) Rubric mutation post-fork — N/A: `Rubric` is frozen dataclass; same instance across replicates.
* (b) Env-var divergence — covered: same `ARE_SCORER_REAL` / `ARE_RUBRIC_PATH` snapshot fork-inherited per replicate.
* (c) `sys.modules` stale `api.score_runtime` — covered: `ARE_TEST_MODE=1` armed the Scorer-startup assertion at `scanners/live_probe_workers.py::run_scorer`.
* (d) JCS canonicalization order — covered: byte-equality on the JCS-sorted canonical form.

