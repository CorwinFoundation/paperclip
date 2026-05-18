---
artifact_id: ARE-SCAN-SMOKE-POST-RENAME-BMI-TO-ARE
title: `bin/are-scan` N=5 reproducibility smoke — post project-dir rename (BEAAA-510)
issue: BEAAA-510
related_issues:
  - BEAAA-472 (pillar-name rename — established d22bf84 baseline; this rename is the next hygiene step)
  - BEAAA-477 (Scanner Engineer seat activation — this is the first SE-led rule-change-touching ticket)
  - BEAAA-496 (CEO Monday cascade — origin)
  - BEAAA-497 (CTO confirm-receipt — filed BEAAA-510)
  - BEAAA-473 (UW v1.0.1 reference text — collision noted in ADR-0008)
date: 2026-05-18
scanner_version: 0.3.0
rubric_version: 1.0.0
corpus_version: 0.1.1
contract_pin: contracts/are-scanner-api-v1.md@v1.0 (pre-rename); proposed bump to v1.0.x pending CTO co-sign per ADR-0008
authoring_role: Scanner Engineer (agent d8bbc3b5-185f-43ed-bff0-d00323ddbfa3)
pre_rename_head: 3c0ac8b6a5d18b6d65fa2433ee2a6e1cdafcf076
post_rename_head: 3c0ac8b6a5d18b6d65fa2433ee2a6e1cdafcf076 (same commit; rename is filesystem-level only)
---

# `bin/are-scan` N=5 reproducibility smoke — post project-dir rename `bmi-scanner/` → `are-scanner/`

**Purpose:** prove that renaming the project working directory `projects/bmi-scanner/` → `projects/are-scanner/` is purely a filesystem-level relabel — the underlying scoring math, output JSON shape, and per-pillar subscore values are byte-identical to the pre-rename baseline. This is the verification artifact called out in ADR-0008 §"Reproducibility test" and the BEAAA-510 issue body §DoD item (2).

**Smoke kind:** N=5 single-agent reproducibility per the AGENTS.md *Standing procedure — Scanner reproducibility check* (single-candidate variant: project-dir rename is a filesystem-layer change with no rubric / scoring / probe / judge impact, so there is no baseline-vs-candidate calibration delta — the underlying `rubric.scoring.score` arithmetic is unchanged).

## Setup

Golden agent: **G1** (Claude 3.7 Sonnet via OpenRouter, hardened-prompt; slot=`known_good`; from the frozen golden roster per BEAAA-163 / `tests/fixtures/golden_agents/G1/`).

```sh
# Run from inside projects/are-scanner/ (post-rename root)
mkdir -p /tmp/are_scan_smoke_postrename_510
cp tests/fixtures/golden_agents/G1/eval_outputs.json \
   /tmp/are_scan_smoke_postrename_510/eval_results.json

SCORED_AT="2026-05-17T12:00:00+00:00"
for i in 0 1 2 3 4; do
  bin/are-scan --agent G1 --seed 42 --corpus 0.1.1 \
    --input-dir /tmp/are_scan_smoke_postrename_510 \
    --scored-at "$SCORED_AT" \
    --out /tmp/are_scan_smoke_postrename_510/run_$i.json
done
sha256sum /tmp/are_scan_smoke_postrename_510/run_*.json
```

`--scored-at` is pinned because the only non-deterministic field on the CLI surface is the scoring timestamp. Pinning it lets us check the strictly stronger byte-identical condition (spread = 0) instead of just the ±1 band the contract requires.

## Result — N=5 same-seed (`--seed 42 × 5`), post-rename

| Run | `are_total` | OPS      | BIAS_FAIRNESS | ROBUSTNESS | GOVERNANCE | sha256 of stdout JSON                                              |
|-----|-------------|----------|---------------|------------|------------|---------------------------------------------------------------------|
| 0   | 84          | 20.3125  | 19.2708       | 22.9167    | 21.875     | `5397d381d75d17cf42217e4949f439129ffbaa926a857d17f264e1d0bf3a0907` |
| 1   | 84          | 20.3125  | 19.2708       | 22.9167    | 21.875     | `5397d381d75d17cf42217e4949f439129ffbaa926a857d17f264e1d0bf3a0907` |
| 2   | 84          | 20.3125  | 19.2708       | 22.9167    | 21.875     | `5397d381d75d17cf42217e4949f439129ffbaa926a857d17f264e1d0bf3a0907` |
| 3   | 84          | 20.3125  | 19.2708       | 22.9167    | 21.875     | `5397d381d75d17cf42217e4949f439129ffbaa926a857d17f264e1d0bf3a0907` |
| 4   | 84          | 20.3125  | 19.2708       | 22.9167    | 21.875     | `5397d381d75d17cf42217e4949f439129ffbaa926a857d17f264e1d0bf3a0907` |

- **Total spread:** `are_total max − min = 84 − 84 = 0` ✓ (strictly inside ±1)
- **Subscore spread (per pillar):** `0.0000` ✓ (strictly inside ±1)
- **Stdout sha256 spread:** all 5 identical ✓ (byte-identical Supreme-Law guarantee — strictly stronger than the contract's ±1)
- **Subscore values match BEAAA-472 baseline (commit `d22bf84` / ADR-0006) numerically:**
  - `OPS = 20.3125`           ↔ d22bf84 `OPS = 20.3125` ✓
  - `BIAS_FAIRNESS = 19.2708` ↔ d22bf84 `BIAS_FAIRNESS = 19.2708` ✓
  - `ROBUSTNESS = 22.9167`    ↔ d22bf84 `ROBUSTNESS = 22.9167` ✓
  - `GOVERNANCE = 21.875`     ↔ d22bf84 `GOVERNANCE = 21.875` ✓

## Pre-rename baseline — N=5 same-seed at HEAD `3c0ac8b` (pre-rename)

| Run | `are_total` | OPS      | BIAS_FAIRNESS | ROBUSTNESS | GOVERNANCE | sha256 of stdout JSON                                              |
|-----|-------------|----------|---------------|------------|------------|---------------------------------------------------------------------|
| 0   | 84          | 20.3125  | 19.2708       | 22.9167    | 21.875     | `cd3ec38847d16d1a287bcdbcbac0f901ece273c0ed7d8d91fe5ea6ae76407a6c` |
| 1   | 84          | 20.3125  | 19.2708       | 22.9167    | 21.875     | `cd3ec38847d16d1a287bcdbcbac0f901ece273c0ed7d8d91fe5ea6ae76407a6c` |
| 2   | 84          | 20.3125  | 19.2708       | 22.9167    | 21.875     | `cd3ec38847d16d1a287bcdbcbac0f901ece273c0ed7d8d91fe5ea6ae76407a6c` |
| 3   | 84          | 20.3125  | 19.2708       | 22.9167    | 21.875     | `cd3ec38847d16d1a287bcdbcbac0f901ece273c0ed7d8d91fe5ea6ae76407a6c` |
| 4   | 84          | 20.3125  | 19.2708       | 22.9167    | 21.875     | `cd3ec38847d16d1a287bcdbcbac0f901ece273c0ed7d8d91fe5ea6ae76407a6c` |

- Pre-rename spread `0.0000` on every pillar; sha256 stable across N=5.

## Strictly-stronger byte-identical check across the rename

The BEAAA-510 DoD allows the post-rename stdout sha256 to *differ* from the pre-rename sha (path strings in the JSON output might pick up the new dir name). To rule out any hidden behavioral diff, the smoke ran a **same-tmp-dir-name** check: re-execute the post-rename binary against the SAME tmp dir name the pre-rename baseline used. Result:

| Probe | sha256 of stdout JSON                                              |
|-------|---------------------------------------------------------------------|
| Pre-rename `run_0` (input-dir name `are_scan_smoke_baseline_510`) | `cd3ec38847d16d1a287bcdbcbac0f901ece273c0ed7d8d91fe5ea6ae76407a6c` |
| Post-rename `run_postrename_check` (input-dir name `are_scan_smoke_baseline_510`) | `cd3ec38847d16d1a287bcdbcbac0f901ece273c0ed7d8d91fe5ea6ae76407a6c` |

**`diff` = empty.** With identical inputs (including the tmp dir name), the post-rename binary produces byte-identical output to the pre-rename binary. The earlier sha difference between `cd3ec3…` (baseline) and `5397d3…` (rename) is **entirely attributable to the different input-dir names** the two N=5 runs used (`are_scan_smoke_baseline_510` vs `are_scan_smoke_postrename_510`); it has NOTHING to do with the project-dir rename. This is strictly stronger than the BEAAA-510 DoD requirement (which only asked for identicality across N=5 in each run, allowing different sha pre/post).

The `input_dir_name` field in the output JSON is the only path-string surface in `bin/are-scan`'s output that's tied to the caller's environment; the project-dir name itself does not appear anywhere in the output (`bin/are-scan` does not emit `cwd` or repo-root paths).

## Pytest

```
749 passed, 62 subtests passed in 35.99s
```

Baseline cited in BEAAA-472 close was 729+62. Current 749+62 = 20 additional tests passing — those tests landed in commits since BEAAA-472 (`cf19cec` BEAAA-397, `2802587` BEAAA-479, `2ad211a` BEAAA-480, `3c0ac8b` BEAAA-484). The rename does not break any of them.

## What's removed in this PR

Nothing. No code path removed; no fixture removed; no feature removed. Two in-repo edits:

1. `docs/runbooks/post-creds-resi-proxy.md:18` — `cd projects/bmi-scanner` → `cd projects/are-scanner`. Active runbook.
2. (New) `docs/adr-0008-project-dir-rename-bmi-to-are.md` — this ADR.
3. (New) `tools/are-scan-smoke-post-rename-bmi-to-are.md` — this smoke artifact.

External:
- Directory `projects/bmi-scanner/` renamed to `projects/are-scanner/`. The `.git` directory and all tracked content move with the parent — `git rev-parse HEAD` post-rename returns the same `3c0ac8b…`.

Deferred (working tree, not in this PR):
- `scanners/inspect_runner.py:362` has an in-tree comment `the harness already in \`bmi-scanner/harness/citation_index/\`` that should also flip to `are-scanner/...`. That file already has pre-existing uncommitted BEAAA-483 work; the comment sits inside that uncommitted block. The path-text fix lands with BEAAA-483 to keep BEAAA-510 surgical. Inventoried in this artifact for traceability.

## What's unchanged (load-bearing invariants held)

- Pillar weights: 25 / 25 / 25 / 25 (Founder-locked per ADR-0002 / BEAAA-21).
- Pillar names: `OPS / BIAS_FAIRNESS / ROBUSTNESS / GOVERNANCE` (per ADR-0006 / commit `d22bf84`).
- Dim allocation, dim ids, dim weights: all unchanged.
- Output JSON shape: unchanged (same fields, same nesting, same key spelling).
- Scoring math: unchanged (same `rubric.scoring.score()` arithmetic).
- Judge models: pinned per contract; unchanged.
- Tamper-resistance posture: regex `agent_id`, no shell-out, no symlink escape, 10MiB cap — all preserved (rename touched no input-handling code).
- Contract pin pre-rename: `contracts/are-scanner-api-v1.md@v1.0`. **Post-rename: pending CTO co-sign on v1.0.x patch bump per ADR-0008.**

## Pinned versions

| Component         | Version                                              |
|-------------------|------------------------------------------------------|
| Scanner           | `0.3.0` (unchanged across rename)                    |
| Rubric            | `1.0.0` (unchanged — same weights, same dim allocation) |
| Corpus            | `0.1.1`                                              |
| Contract          | `are-scanner-api-v1@v1.0` (pre-rename); proposed `v1.0.x` per ADR-0008 (CTO co-sign pending) |
| Seed              | `42`                                                 |
| Scored-at         | `2026-05-17T12:00:00+00:00`                          |
| Pre-rename HEAD   | `3c0ac8b6a5d18b6d65fa2433ee2a6e1cdafcf076`           |
| Post-rename HEAD  | `3c0ac8b6a5d18b6d65fa2433ee2a6e1cdafcf076` (no commit yet on the in-repo runbook + ADR + smoke artifact triplet — committed in this PR) |
| Python            | `3.12.3`                                             |

## Required-artifact block (per `canon/artifact-metadata-schema.md`, attached to ADR-0008 close)

- `scanner_version`: 0.3.0
- `corpus_version`: 0.1.1
- `probe-set-version`: current
- `rubric_point_id`: N/A (no rubric-point change)
- `N=5 calibration delta`: 0.0000 on all four pillars (G1 byte-identical with same input-dir name; stdout sha256 differs only when input-dir name differs)
- `upstream_eval_version`: N/A
- `cto_cosign_ref`: PENDING — interaction filed against BEAAA-510 on the contract-bump portion
- `head_of_uw_cosign_ref`: not-triggered
- `glossary_check`: clean (ARE canonical; `bmi-scanner` appears only in §"Out of scope" inventory + historical references)
- `Carrier Rule check`: clean (no carrier named)
