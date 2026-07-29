# BEAAA-18844 (parent BEAAA-18671) — N=5 Syllable full-corpus execution plan

BLUF: The remaining execution chain is resolved and the driver is built and
validated. AC#6 (envelope path) and the pre-launch HTTP-attempt-cap item are
both closed **without any change to QA-approved or scored code**. Driver:
`scripts/beaaa18671_syllable_full_corpus_n5.py`.

- Classification: **rule-execution** (implement/execute probes within existing
  rubric points; no rubric, judge, scoring, contract-shape, or tamper rule
  touched). Contract `are-scanner-api-v1@v1.3.0`; supervision
  `scanner-implementation-supervision-v1@v1.1`. `cto_cosign_ref`: null.

## AC#6 resolution — which path emits the contract response envelope

The "current Scanner response envelope" (`are_total` / `band` / `subscores` +
`evidence_refs`) is produced by the **frozen production scoring projection**,
not by the RegisteredCaseExecutor golden-target summary (which is a verdict-count
artifact). The driver wraps — never reimplements — the exact frozen functions:

1. Per family, call the frozen `harness.bench.<family>.run(subject, judge,
   probes, ensemble_n=1, seed=…)`. Each owns its `pass_rate → raw` map
   (`pass_rate_to_raw`) and inconclusive-ceiling guard. Returns per-dim `raw` +
   per-case `trace`.
2. Assemble the per-dim `eval_results` map (P1.1/P1.3/P1.4/P1.5 → `{raw,
   evidence_uri}`).
3. Score via the frozen `rubric.scoring.score` + `harness.contract_envelope.
   to_contract_output` — the identical functions `harness.cli_runner.run_scan`
   calls internally. Uncovered active dims are marked N/A via the production
   `scanners.score_runtime._build_synthetic_applicability` helper (the pinned
   selection scores exactly the 4 golden P1 dims; the other 188 registry rows
   are out of scope for this run). The CLI's cosmetic `--corpus` semver
   flag-gate is the only thing skipped, so the envelope carries the operational
   `corpus_version = registry-baseline-f794e63` pin, matching prior vendor
   envelopes.

Validation (zero network, `--mode validate`): 5 seeds → 5 real §2.2 envelopes
(`are_total`, `band` letter, pillar `subscores`, 27 `evidence_refs`,
`contract_version`, `scanner_version=0.5.0`); N=5 spread computed over
`are_total` + every pillar subscore; reconciliation 450 = 450. The live scored
path itself is independently proven by the one-case smoke (terminal in 5.34s
through the same seam subject + pinned judge).

## HTTP-attempt-cap resolution — AC#1 preserved, no fresh seam QA

The preflight flagged that the QA-approved BEAAA-18687 seam caps HTTP attempts
at 700, while 550 target calls × 2 posts (Syllable two-phase protocol) need
~1100. The seam's `CheckpointLedger.__init__` **already exposes
`max_http_attempts` and `max_wall_seconds` as public constructor kwargs**
(`scripts/syllable_checkpoint.py:39,44`). The driver constructs the ledger with
`max_http_attempts=1300` (1100 + ~200 retry headroom) and
`max_wall_seconds=48h`. **No seam source is changed** — the preflight's proposed
source-edit is avoided entirely — so AC#1 (exact QA-approved adapter) is
preserved and no fresh exact-SHA seam QA is required. Transparency item to CTO
on the parent stands: proceeding on 550 target calls unless CTO states 700 was a
hard ceiling.

## Immutable pins (fail-closed in the driver)

- Registry `tools/probe-registry/active_registry.json` SHA-256
  `b90629da751fd07b4b986e3a82ead208a62d54a6ab8c7b24ec692f193c96b671` (278 rows).
- Selection = 90 `countable_as_executable` rows (P1.1=30, P1.3=20, P1.4=20,
  P1.5=20); 188 excluded with machine-readable reasons (109 mapping/corpus, 75
  fixture, 4 legacy smoke).
- Scanner 0.5.0; corpus `registry-baseline-f794e63`; probe-set
  `active-probe-registry@0.1.0`; seeds 0..4; subject `syllable:apichat`; pinned
  judge `anthropic/claude-haiku-4.5`; candidate adapter = BEAAA-18687 seam at
  `18d0552932955cda3eada2473cb85fe13998c8fd`.

## Hard caps (fail-closed, never silently exceeded)

450 invocations, 550 target calls (`_CappedSubject`), 1300 HTTP attempts +
48h wall (`CheckpointLedger`).

## Checkpoint / resume model

Terminal unit = family-seed (20 units); each written atomically only after its
whole `bench.run()` returns. Resume skips finalized family-seeds and re-runs
unfinalized ones **fresh** (no cached-response reuse, no double-count). Per-case
evidence is the `trace` inside each family-seed file; the 450 reconciliation
counts case rows across the 20 finalized files. The 200-attempt HTTP headroom
covers one mid-family resume without breaching 1300.

## Execution + publish

`--mode live --out artifacts/beaaa-18671/n5_run` runs all 450 invocations, then:
emits `selection_manifest.json`, `full_suite_manifest.json`, per-seed
`score/envelope.seed{0..4}.json`, per-family-seed evidence/transcripts under
`evidence/family_seed/`, the N=5 spread verdict, and the reconciliation
(expected=terminal=evidenced=450). If any `are_total` or subscore spread > 1, the
run marks `score_emittable=false`, emits **no** BackBond Score, and the prior
Syllable score remains authoritative. No production promotion; standing gates
(QA APPROVED per BEAAA-15400; Founder per BEAAA-15373) apply to any promotion.

## Rollback

Discard the run artifact directory; preserve prior Syllable score. No scoring,
rubric, corpus, contract, seam, shared, or production state is changed by this
execution.
