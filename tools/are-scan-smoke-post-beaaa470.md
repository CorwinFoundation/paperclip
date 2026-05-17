---
artifact_id: ARE-SCAN-SMOKE-POST-BEAAA470
title: `bin/are-scan` N=5 reproducibility smoke — post-rename (BEAAA-472)
issue: BEAAA-472
related_issues:
  - BEAAA-470 (pillar-name reconciliation parent — ADR-0006 ADOPTED)
  - BEAAA-464 (original CLI ship — ADR-0005 baseline smoke at `tools/are-scan-smoke-2026-05-17.md`)
date: 2026-05-17
scanner_version: 0.3.0
rubric_version: 1.0.0
corpus_version: 0.1.1
contract_pin: contracts/are-scanner-api-v1.md@v1.0
authoring_role: CTO
---

# `bin/are-scan` N=5 reproducibility smoke — post-rename

**Purpose:** prove that the BEAAA-470 pillar-name rename
(`P1 → OPS`, `P2 → BIAS_FAIRNESS`, `P3 → ROBUSTNESS`, `P4 → GOVERNANCE`)
is purely lexical — the underlying scoring math is byte-identical to
the ADR-0005 baseline, only the JSON keys change. This is the
verification artifact called out in ADR-0006 §"Reproducibility test"
and the BEAAA-472 issue body §"Verification" item (3).

**Smoke kind:** N=5 single-agent reproducibility per the AGENTS.md
*Standing procedure — Scanner reproducibility check* (single-candidate
variant: pillar-name rename is a delivery-layer / output-key change,
not a scoring-math change, so there is no baseline-vs-candidate
calibration delta — the underlying `rubric.scoring.score` arithmetic
is unchanged).

## Setup

Golden agent: **G1** (Claude 3.7 Sonnet via OpenRouter, hardened-prompt;
slot=`known_good`; from the frozen golden roster per BEAAA-163 /
`tests/fixtures/golden_agents/G1/`).

```sh
mkdir -p /tmp/are_scan_smoke_n5_postrename
cp tests/fixtures/golden_agents/G1/eval_outputs.json \
   /tmp/are_scan_smoke_n5_postrename/eval_results.json

SCORED_AT="2026-05-17T12:00:00+00:00"
for i in 0 1 2 3 4; do
  bin/are-scan --agent G1 --seed 42 --corpus 0.1.1 \
    --input-dir /tmp/are_scan_smoke_n5_postrename \
    --scored-at "$SCORED_AT" \
    --out /tmp/are_scan_smoke_n5_postrename/run_$i.json
done
sha256sum /tmp/are_scan_smoke_n5_postrename/run_*.json
```

`--scored-at` is pinned because the only non-deterministic field on
the CLI surface is the scoring timestamp. Pinning it lets us check
the strictly stronger byte-identical condition (spread = 0) instead
of just the ±1 band the contract requires.

## Result — N=5 same-seed (`--seed 42 × 5`)

| Run | `are_total` | OPS      | BIAS_FAIRNESS | ROBUSTNESS | GOVERNANCE | sha256 of stdout JSON                                              |
|-----|-------------|----------|---------------|------------|------------|---------------------------------------------------------------------|
| 0   | 84          | 20.3125  | 19.2708       | 22.9167    | 21.875     | `24361151a3b7e7ff8ba264c6977d632c8aa25a8c7abc914954ba9b1e55583762` |
| 1   | 84          | 20.3125  | 19.2708       | 22.9167    | 21.875     | `24361151a3b7e7ff8ba264c6977d632c8aa25a8c7abc914954ba9b1e55583762` |
| 2   | 84          | 20.3125  | 19.2708       | 22.9167    | 21.875     | `24361151a3b7e7ff8ba264c6977d632c8aa25a8c7abc914954ba9b1e55583762` |
| 3   | 84          | 20.3125  | 19.2708       | 22.9167    | 21.875     | `24361151a3b7e7ff8ba264c6977d632c8aa25a8c7abc914954ba9b1e55583762` |
| 4   | 84          | 20.3125  | 19.2708       | 22.9167    | 21.875     | `24361151a3b7e7ff8ba264c6977d632c8aa25a8c7abc914954ba9b1e55583762` |

- **Total spread:** `are_total max − min = 84 − 84 = 0` ✓ (strictly inside ±1)
- **Subscore spread (per pillar):** `0.0000` ✓ (strictly inside ±1)
- **Stdout sha256 spread:** all 5 identical ✓ (byte-identical
  Supreme-Law guarantee — strictly stronger than the contract's ±1)
- **Subscore values match ADR-0005 numerically:**
  - `OPS = 20.3125`           ↔ ADR-0005 `P1 = 20.3125` ✓
  - `BIAS_FAIRNESS = 19.2708` ↔ ADR-0005 `P2 = 19.2708` ✓
  - `ROBUSTNESS = 22.9167`    ↔ ADR-0005 `P3 = 22.9167` ✓
  - `GOVERNANCE = 21.875`     ↔ ADR-0005 `P4 = 21.875`  ✓
- **Stdout sha256 differs from ADR-0005's `eb127166…`** — *expected* and
  *not* calibration drift; the change is purely the JSON key strings
  (`pillars.P1 → pillars.OPS`, etc.) per ADR-0006 §"Reproducibility test"
  point (5).

## What's removed in this PR

- The ADR-0005 bridge view `subscores_contract_named` is no longer
  emitted. The `subscores` dict is now contract-named at the source,
  so the bridge view has no role. The `cli_meta.pillar_naming_note`
  was also updated to reflect post-rename semantics. See ADR-0006
  §"Merged in BEAAA-472".

## What's unchanged (load-bearing invariants held)

- Pillar weights: 25 / 25 / 25 / 25 (unchanged; Founder-locked per
  ADR-0002 / BEAAA-21).
- Dim allocation: every dim stays under its current pillar container
  (no dim moves; only the pillar-level key string changes).
- Dim ids: `P1.1`, `P1.6`, `P3.3`, `P4.6`, ... emit verbatim (the
  dim-id namespace is intentionally orthogonal to the pillar-key
  namespace per ADR-0006 §Scope).
- Rationale strings inside `dim_threshold_defaults` and `pillar_floors`
  are byte-identical (per BEAAA-472 Rider 2: "Bind-blocking semantics
  travel with content; values byte-identical").
- Contract pin: `contracts/are-scanner-api-v1.md@v1.0` — no contract
  version bump. UW R1 follow-on (v1.0.1 OPS-bullet patch) is tracked
  separately on BEAAA-471 and is intentionally NOT carried here.
- Tamper-resistance posture: regex agent_id, no shell-out, no symlink
  escape, 10MiB cap — all preserved (rename touched no input-handling
  code).

## Pinned versions

| Component         | Version                                              |
|-------------------|------------------------------------------------------|
| Scanner           | `0.3.0` (unchanged across rename)                    |
| Rubric            | `1.0.0` (unchanged — same weights, same dim allocation) |
| Corpus            | `0.1.1`                                              |
| Contract          | `are-scanner-api-v1@v1.0`                            |
| Seed              | `42`                                                 |
| Scored-at         | `2026-05-17T12:00:00+00:00`                          |

## Reversal plan

Same as ADR-0006 §"Rollback plan":

1. **Tier 1:** `git revert <refactor-commit>` restores `P1..P4` keys
   verbatim. ETA ≤5min. (The `subscores_contract_named` ADR-0005 bridge
   view is part of the same revert and re-appears under that name.)
2. **Tier 2 (forensic, not recommended):** revert only `P1 → OPS`
   while keeping the other three renames. Available as an option if
   Rider 1 (contract OPS-bullet description amendment) surfaces an
   objection.
3. **Tier 3:** escalate to ADR-0006 Option B (structural rebalance)
   via a new BEAAA ticket; treat ADR-0006 as superseded.

## Original baseline retained

`tools/are-scan-smoke-2026-05-17.md` (ADR-0005 baseline, sha256
`eb12716643…`) is **retained unchanged** as the historical pre-rename
record. The two files together constitute the byte-level audit trail
for the rename: same math, different keys, predictable new sha.
