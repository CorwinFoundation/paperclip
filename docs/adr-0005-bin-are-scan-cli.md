# ADR-0005: `bin/are-scan` — externalised ARE Scanner CLI

**Status:** ADOPTED
**Date:** 2026-05-17
**Issue:** BEAAA-464 — "Build bin/are-scan CLI — externalize the Scanner so it's callable outside an agent run"
**Scanner version:** 0.3.0 / rubric 1.0.0
**Contract pin:** `contracts/are-scanner-api-v1.md@v1.0`
**Co-sign chain (pending):** Underwriter (smoke-test reproducibility per `are-scanner-api-v1.md` §"Approval gates")

## Decision (BLUF)

Ship `bin/are-scan` as a thin wrapper over `rubric.scoring.score` via a
new shared library `harness/cli_runner.py`. The CLI is a delivery layer
only — same rubric, same judges (not invoked in this Phase-1 CLI), same
scoring math, same forensic stamps. In-agent scanning continues to work
unchanged.

Bone-deep rule: the CLI MUST NOT duplicate rubric/judge logic. The
in-agent path through `api/handlers.py:score_handler` and the CLI both
call into the same `score()` entry point and the same `Rubric.load()`.
A behavioural divergence between the two is a contract bug, not a
maintenance burden.

## Interface

```
bin/are-scan --agent <agent-id> --seed <int>
             [--version <semver>]      # asserted scanner version; mismatch → exit 2
             [--corpus <semver>]       # corpus version pin, stamped into output
             [--input-dir <path>]      # default ./inputs/<agent-id>/
             [--out <path>]            # default stdout
             [--rubric <path>]         # default rubric/rubric.yaml
             [--scored-at <iso8601>]   # repro override; default now()
```

### Input directory convention

| File                            | Required | Shape                          |
|---------------------------------|----------|--------------------------------|
| `eval_results.json`             | yes      | `{P<n>.<m>: {raw, evidence_uri}}` |
| `system_prompt.txt`             | no       | UTF-8 text                      |
| `tool_inventory.json`           | no       | list of tool objects            |
| `policy_attestations.json`      | no       | object                          |
| `governance_attestations.json`  | no       | object                          |
| `model_versions.json`           | no       | `{primary, judges[]}`           |
| `scanner_invocation_context.json` | no     | `{purpose, requested_pillars[]}` |

Only `eval_results.json` is load-bearing for scoring math today (it's
exactly what the in-agent `score()` consumes). The other artifacts are
hashed and persisted into the output as evidence references for the
Underwriter and any later auditor — they parallel the contract's Input
section without forcing the scoring engine to grow new fields.

### Exit codes

| Exit | Meaning                                                          |
|------|------------------------------------------------------------------|
| 0    | Scan clean.                                                      |
| 1    | `evidence_refs` incomplete (refuse-to-quote per Underwriter Hard guarantee #1). |
| 2    | Invalid inputs (bad JSON, missing dim, raw out of [0,4], path traversal, oversize file, malformed agent_id, scanner-version mismatch). |
| 3    | Scanner internal error (unexpected exception in `score()`).       |

### Output shape

The CLI emits a **superset** of `rubric.scoring.score()`'s response. All
existing fields (`bmi`, `band`, `pillars`, `forensic_bundle_uri`,
`snapshot_hash`, `scored_at`, `rubric_version`, `scanner_version`,
`corpus_version`, `seed`, `model_versions`, `contract_version`,
`scan_evidence_age_days`, `pillar_floors`, optionally `diagnostics` and
`applicability_policy_version`) are preserved exactly so the existing
Underwriter pricing engine reads the CLI output without changes.

Added (additive, per contract Output section):

- `agent_id` — echo of `--agent`.
- `are_total` — alias for `bmi` (the contract uses `are_total`; scan
  callers already accept either per `scripts/scan_p4_pilot.py:145`).
- `subscores` — `{P1, P2, P3, P4}` keyed by **internal rubric pillar
  ids**, value = `pillars[Pn].score`.
- `subscores_contract_named` — same data re-grouped under the
  contract's `ROBUSTNESS / BIAS_FAIRNESS / GOVERNANCE_OPS` labels.
  See *"Known gap"* below.
- `band_letter` — convenience string (e.g. `"B+"`) for log lines.
- `confidence` — deterministic stand-in: `1 − (N/A dim count / total dim count)`.
  Hard guarantee #2 of the Underwriter side (`are-scanner-api-v1.md` §"Hard
  guarantees (Underwriter is on the hook)") is the real confidence
  authority; this CLI field is a stable scaffold so the contract field
  is never absent.
- `evidence_refs` — flat list, one per dim, derived from `pillars[*].dimensions[*]`.
- `uncovered_dimensions` — sorted N/A dim ids.
- `run_timestamp` — alias for `scored_at`.
- `input_file_sha256s` — `{filename: "sha256:..."}` for every input file
  the loader touched. The snapshot hash includes these, so any mutation
  of the input directory between runs forces a new snapshot.
- `cli_meta` — provenance: tool name, issue id, input-dir name,
  presence flags, pillar-naming note.

## Reproducibility test

Per `agents/cto-os.md` Supreme Law (and the Scanner contract Hard
guarantee #1): same agent + same `scanner_version` + same `corpus_version`
+ same `model_versions` + same seed → same `are_total` within ±1 and
same value for every subscore within ±1.

The CLI inherits this guarantee from the in-agent path mechanically:
the scoring math is the same `score()` function; nothing in the CLI
introduces non-determinism. The smoke test (`tools/are-scan-smoke-2026-05-17.md`)
runs `bin/are-scan` against the G1 golden agent N=5 with a pinned
`--scored-at`, then asserts:

1. All 5 stdout blobs are byte-identical.
2. `are_total` spread = 0.
3. Every `subscores[Pn]` spread = 0.

Byte-identical (spread = 0) is a strictly stricter check than ±1; it
holds because the scoring engine is deterministic and the CLI's
non-deterministic surface (timestamp) is overridden by `--scored-at`.

The smoke uses the locked G1 fixture under
`tests/fixtures/golden_agents/G1/eval_outputs.json`. G1 is part of the
frozen golden roster (see BEAAA-163). The smoke is reproducible by any
operator with a checkout — `tools/are-scan-smoke-2026-05-17.md` carries
the exact command.

## Tamper-resistance review

The CLI runs on attacker-controlled inputs (the scanned agent's owner
controls `system_prompt.txt`, `tool_inventory.json`, attestation
contents, etc.). Per the existing tamper-resistance posture
(`agents/cto-os.md` Supreme Law point 5), every input is treated as
adversarial:

| Attack                                | Defence                                               |
|---------------------------------------|--------------------------------------------------------|
| Shell injection via `--agent`         | `agent_id` is regex-restricted (`^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`); the CLI never shells out. |
| Path traversal via input filenames    | Filenames are fixed constants in `cli_runner._safe_join`; we never read attacker-named files. |
| Symlink escape from `--input-dir`     | Every read path is `resolve()`d and asserted under the resolved `--input-dir`. Symlinks pointing outside the dir → `CliInputError` → exit 2. |
| Oversize input → memory exhaustion    | Hard 10 MiB per-file cap on every read. |
| Malformed JSON → parser crash         | Caught as `CliInputError` → exit 2. |
| Out-of-range `raw`                    | `_validate_eval_results` rejects anything outside `[0, 4]` per the rubric contract. |
| `--scored-at` injection of past dates | `scored_at` is a stamp, not a security boundary; the snapshot hash is over inputs + seed only, so re-dating cannot manufacture a collision with an earlier scan. |
| Promp-injecting the scanner via `system_prompt.txt` | `system_prompt.txt` is read as bytes, hashed into `input_file_sha256s`, never executed by an LLM in the Phase-1 CLI. (Phase 2: if the CLI grows judge calls, judges must be pinned per `are-scanner-api-v1.md` §"Approval gates" and isolated from the scanned prompt.) |

The CLI surface adds zero new injection vectors over the in-agent path
(in fact strictly fewer — no HTTP server, no tenant header path, no
JSON-bomb intake parser).

## Rejected alternatives

1. **Rewrite scoring in a CLI-local module.** Rejected: that's the
   "duplicate rubric/judge logic" anti-pattern the issue calls out and
   would force every rubric change to land twice.
2. **Wrap `api/handlers.py:score_handler` directly.** Rejected: the
   handler couples tenant isolation, hosted-eval intake-guards, and
   forensic-bundle store wiring. The CLI doesn't have a tenant header
   or a bundle store; threading dummies into the handler is more
   coupling, not less. `cli_runner.run_scan` calls the underlying
   `score()` directly and produces a deterministic snapshot hash
   without persisting a bundle.
3. **Single binary with a `--mode in-agent | cli` flag.** Rejected:
   two delivery layers, one shared core, is exactly the right shape.
   Adding a mode flag is a hidden API surface that would re-create the
   coupling we just split.
4. **Ship without exit-code 1 / 2 / 3 separation.** Rejected: the
   Underwriter's refuse-to-quote condition (Hard guarantee #1) must be
   distinguishable from "your inputs are malformed", and both must be
   distinguishable from "the scanner crashed". CI and human operators
   need this signal.

## Known gap — contract pillar names vs internal rubric pillar ids

The contract document `contracts/are-scanner-api-v1.md@v1.0` documents
subscores keyed by `GOVERNANCE / ROBUSTNESS / BIAS_FAIRNESS / OPS`.
The internal rubric (`rubric/rubric.yaml`) uses `P1 (Failure Surface) /
P2 (Bias & Fairness) / P3 (Adversarial Robustness) / P4 (Governance &
Operations)`. The two never agreed in v1.0:

- Contract `BIAS_FAIRNESS` ↔ internal `P2` (clean 1:1).
- Contract `ROBUSTNESS` (owns prompt injection + hallucinated actions
  + adversarial input handling) ↔ internal `P1 + P3` (two pillars
  collapsed into one contract bucket).
- Contract `GOVERNANCE` + `OPS` ↔ internal `P4` (one pillar split
  across two contract buckets).

The CLI does NOT renormalise the rubric — that would be a rubric and
scoring-logic change, which requires CTO + Underwriter co-sign per
`are-scanner-api-v1.md` §"Approval gates" plus a Founder gate if it
moves any golden agent's score by >1 point. Instead, the CLI emits
**both views**:

- `subscores` (internal P1..P4 keys) — what the Underwriter pricing
  engine reads today.
- `subscores_contract_named` (contract bucket keys) — closest
  contract-shape rendering, with `P1 + P3 → ROBUSTNESS` and
  `P4 → GOVERNANCE_OPS`.

This is a documented known gap, not a CLI defect. Reconciling
contract pillar names with the internal rubric is a separate ticket
that needs CTO + Underwriter + Founder approval per the rubric-change
gate. The CLI is not the right place to make that decision.

## Rollback plan

If the CLI ships and breaks in-agent scans (it should not — the in-
agent path is byte-identical to before this ADR), or if the
externalised CLI itself misbehaves:

1. **Tier 1 — disable the CLI without touching in-agent code.**
   `git rm bin/are-scan` removes the executable; nothing else imports
   `harness/cli_runner.py` so its presence is inert. ETA: ≤2 minutes.
2. **Tier 2 — full revert of the BEAAA-464 commit.**
   `git revert <commit>` undoes `bin/are-scan`, `harness/cli_runner.py`,
   `tests/test_cli_runner_beaaa464.py`, and the README section
   simultaneously. Pre-existing 729 tests + 62 subtests should pass
   exactly as before; goldens are byte-identical (the scoring engine
   was not touched). ETA: ≤5 minutes including a smoke run of
   `python3 -m pytest tests/ -q`.
3. **In-agent path unaffected by either tier.** `api/handlers.py`,
   `rubric/scoring.py`, `harness/pipeline.py`, and every
   `scripts/scan_*.py` are untouched by this ADR. The in-agent scan
   surface continues to work whether the CLI is present, broken, or
   removed.

## Audit trail (per CTO Supreme Law)

- Code: `bin/are-scan`, `harness/cli_runner.py`, `tests/test_cli_runner_beaaa464.py`
- Smoke result: `tools/are-scan-smoke-2026-05-17.md`
- ADR: this file
- Contract pin: `contracts/are-scanner-api-v1.md@v1.0`
- Tests: 17 new + 729 pre-existing + 62 subtests all green
- Rubric/scoring touched: **none** (zero golden-set drift by construction)
- Approval gate triggered: Underwriter co-sign on smoke reproducibility
  per `are-scanner-api-v1.md` §"Approval gates" → "Any change that moves
  a golden agent's score by >1 point". The N=5 G1 smoke shows spread=0,
  inside the ±1 band; UW co-sign is the gate this ADR is presented
  against, not a CTO unilateral merge.
