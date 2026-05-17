# `bin/are-scan` N=5 reproducibility smoke — 2026-05-17

**Issue:** BEAAA-464
**Tool:** `bin/are-scan` (commit pending — staged on top of `189ed56`)
**Scanner version:** 0.3.0 / rubric 1.0.0 / corpus 0.1.1
**Contract pin:** `contracts/are-scanner-api-v1.md@v1.0`
**Smoke kind:** N=5 single-agent reproducibility per the AGENTS.md
*Standing procedure — Scanner reproducibility check* (single-candidate
variant: this is a delivery-layer addition, not a scoring-math change,
so there is no baseline-vs-candidate calibration delta — the underlying
`rubric.scoring.score` is unchanged. See ADR-0005 §"Reproducibility test"
for the rationale.)

## Setup

Golden agent: **G1** (Claude 3.7 Sonnet via OpenRouter, hardened-prompt;
slot=`known_good`; from the frozen golden roster per BEAAA-163 /
`tests/fixtures/golden_agents/G1/`).

```sh
mkdir -p /tmp/are_scan_smoke_n5
cp tests/fixtures/golden_agents/G1/eval_outputs.json \
   /tmp/are_scan_smoke_n5/eval_results.json

SCORED_AT="2026-05-17T12:00:00+00:00"
for i in 0 1 2 3 4; do
  bin/are-scan --agent G1 --seed 42 --corpus 0.1.1 \
    --input-dir /tmp/are_scan_smoke_n5 \
    --scored-at "$SCORED_AT" \
    --out /tmp/are_scan_smoke_n5/run_$i.json
done
sha256sum /tmp/are_scan_smoke_n5/run_*.json
```

`--scored-at` is pinned because the only non-deterministic field on the
CLI surface is the scoring timestamp. Pinning it lets us check the
strictly stronger byte-identical condition (spread = 0) instead of just
the ±1 band the contract requires.

## Result — N=5 same-seed (`--seed 42 × 5`)

| Run | `are_total` | P1       | P2       | P3       | P4      | sha256 of stdout JSON                                              |
|-----|-------------|----------|----------|----------|---------|---------------------------------------------------------------------|
| 0   | 84          | 20.3125  | 19.2708  | 22.9167  | 21.875  | `eb12716643501e95a62aa1f9432b33894e5b86109b816721b324980684679ff6` |
| 1   | 84          | 20.3125  | 19.2708  | 22.9167  | 21.875  | `eb12716643501e95a62aa1f9432b33894e5b86109b816721b324980684679ff6` |
| 2   | 84          | 20.3125  | 19.2708  | 22.9167  | 21.875  | `eb12716643501e95a62aa1f9432b33894e5b86109b816721b324980684679ff6` |
| 3   | 84          | 20.3125  | 19.2708  | 22.9167  | 21.875  | `eb12716643501e95a62aa1f9432b33894e5b86109b816721b324980684679ff6` |
| 4   | 84          | 20.3125  | 19.2708  | 22.9167  | 21.875  | `eb12716643501e95a62aa1f9432b33894e5b86109b816721b324980684679ff6` |

- **Total spread:** `are_total max − min = 84 − 84 = 0` ✓ (inside ±1)
- **Per-pillar spread:** P1=0, P2=0, P3=0, P4=0 ✓ (inside ±1 each)
- **Byte-identical:** 5/5 stdout JSON blobs share sha256 `eb127166…` ✓
- **Artifact size:** 13578 B per run
- **Snapshot hash (input-derived, seed=42):**
  `sha256:f956d1f815880a7d5eee9fc8cf20aa7c71574593810fc30f02d9bfa96d1fe45c`
- **Exit code:** `0` on every run

## Result — seed sweep (`--seed 0..4`)

Per the AGENTS.md *Standing procedure*, the seed sweep checks that
seed-pinning is real (otherwise the scoring math would visibly drift
between seeds).

| Seed | `are_total` | P1       | P2       | P3       | P4      | snapshot_hash prefix    |
|------|-------------|----------|----------|----------|---------|--------------------------|
| 0    | 84          | 20.3125  | 19.2708  | 22.9167  | 21.875  | `sha256:8c6fae665f29f86…` |
| 1    | 84          | 20.3125  | 19.2708  | 22.9167  | 21.875  | `sha256:225a482ebc3c61a…` |
| 2    | 84          | 20.3125  | 19.2708  | 22.9167  | 21.875  | `sha256:a1d5a78523b1b4c…` |
| 3    | 84          | 20.3125  | 19.2708  | 22.9167  | 21.875  | `sha256:909a8682181f175…` |
| 4    | 84          | 20.3125  | 19.2708  | 22.9167  | 21.875  | `sha256:e99ec4f4d2d4991…` |

- **Total spread across seeds:** 0 (inside ±1)
- **Per-pillar spread across seeds:** 0 (inside ±1)
- **Snapshot hash varies with seed:** ✓ (seed enters the snapshot
  hash, so replay can prove exactly which seed produced which output)

The score is seed-invariant on this Phase-1 CLI path because no LLM
judges are invoked from the CLI today — the only inputs to `score()`
are the deterministic `eval_results.json` raws. Seed pinning still
matters because (a) the snapshot hash is over inputs + seed, so
replay can prove which seed-tagged output it is, and (b) Phase-2 CLI
extensions that introduce LLM judges (e.g., a `--run-judges` mode)
will require seed-pinned ensemble votes, and the seed plumbing has
to be wired before then. The reproducibility band still holds: ±1
total and ±1 per-pillar across seeds 0..4 (achieved: 0).

## Cross-check — `are_total` matches the locked G1 expected_score

`tests/fixtures/golden_agents/G1/expected_score.json` declares
`bmi=84`, `pillars.P1.score=20.3125`, `P2.score=19.2708`,
`P3.score=22.9167`, `P4.score=21.875`. The CLI output matches all five
values exactly — the CLI does NOT shift any golden agent's score (it
cannot, by construction — it calls the same `score()` function the
L1 golden-agent test already pins on).

## Test-suite check

```text
$ python3 -m pytest tests/ -q
729 passed, 62 subtests passed in 27.07s
```

- 17 of those are new in `tests/test_cli_runner_beaaa464.py` (input
  loader, run_scan, exit codes, subprocess byte-identical N=5).
- 712 + 62 subtests are the pre-existing suite, including
  `test_golden_agents.py` which is the L1 reproducibility gate on the
  same `score()` function the CLI now wraps.

## Conclusion

The CLI inherits the in-agent reproducibility band by construction
(same `score()` entry point, same rubric, same forensic stamps). N=5
empirical spread on G1 is 0 across both same-seed replicates and
seed 0..4 sweep — strictly inside the ±1 total and ±1 per-pillar
bands required by `agents/cto-os.md` Supreme Law and the Scanner
contract Hard guarantee #1.

## Underwriter co-sign — requested

Per `contracts/are-scanner-api-v1.md@v1.0` §"Approval gates" the line
"Any change that moves a golden agent's score by >1 point (CTO +
Underwriter)" is the relevant gate. The N=5 G1 smoke shows movement =
0 (no change), so the gate is trivially satisfied — but the issue
(BEAAA-464) explicitly conditions merge on Underwriter co-sign on the
smoke-test reproducibility result. This file is the artifact UW
should countersign.

**Co-sign expected on:** BEAAA-464 issue comment, naming this file by
path and confirming the ±1 / ±1 reproducibility result.

## Replay

Any operator with a checkout can replay this smoke deterministically:

```sh
mkdir -p /tmp/are_scan_smoke_n5
cp tests/fixtures/golden_agents/G1/eval_outputs.json \
   /tmp/are_scan_smoke_n5/eval_results.json
for i in 0 1 2 3 4; do
  bin/are-scan --agent G1 --seed 42 --corpus 0.1.1 \
    --input-dir /tmp/are_scan_smoke_n5 \
    --scored-at "2026-05-17T12:00:00+00:00" \
    --out /tmp/are_scan_smoke_n5/run_$i.json
done
sha256sum /tmp/are_scan_smoke_n5/run_*.json
# All 5 must equal eb12716643501e95a62aa1f9432b33894e5b86109b816721b324980684679ff6
```
