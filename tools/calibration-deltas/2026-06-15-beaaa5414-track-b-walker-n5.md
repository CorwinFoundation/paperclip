---
ticket: BEAAA-5414
gate: AG-B6
agent: Scanner Engineer
generated_at: 2026-06-15
scanner_version: "0.4.2"
corpus_version: "0.1.0"
probe-set-version: "1.0.0"
rubric_point_id: ALL
seed: 42
n: 5
canon_master_at_open: bec8f80
branch: beaaa-5414-track-b-walker
contract_version: are-scanner-api-v1@v1.5.0
supervision_contract_version: scanner-implementation-supervision-v1@v1.1
classification: rule-change
cosign_metadata:
  cto_cosign_ref: BEAAA-5418
  uw_cosign_ref: BEAAA-5420
---

# BEAAA-5414 AG-B6 emission-path N=5 calibration delta

## BLUF

AG-B6 **PASS**. N=5 replicates of the real
`api.scan_runtime_factory.make_default_production_scan_runtime()`
fork-spawn path under `ARE_PRODUCTION_SCAN_WIRING=1` +
`ARE_WALKER_CUSTOMER_AGENT=0` (synthetic_v0 fallback / Tier-1 rollback
path) produced byte-identical envelopes. Spread = 0 on `are_total` and
every subscore. Canonical JCS-form envelope set has cardinality = 1.

## CTO amendment 1 binding gate

Per ADR-0030 §Acceptance Gates AG-B6 (CTO amendment 1 via BEAAA-5418):

> The harness `scripts/beaaa5414_track_b_calibration.py` MUST execute
> under `ARE_PRODUCTION_SCAN_WIRING=1` against the real
> `api/scan_runtime_factory.make_default_production_scan_runtime` path
> — NOT a monkey-patched in-process variant. The harness includes an
> explicit emission-path guard that raises `RuntimeError` when
> `os.getenv("ARE_PRODUCTION_SCAN_WIRING") != "1"`.

This delta was produced by exactly that harness, exactly that way. The
guard was verified to raise without the env var in place; the
calibration was executed with the env var set to `1` and observed to
exercise the real factory (Prober + Scorer subprocess fork on every
replicate; corpus snapshot hash computed by
`scanners.snapshot_hash.compute_corpus_snapshot_hash`, not a stub).

## Run parameters

| Parameter | Value |
| --- | --- |
| `ARE_PRODUCTION_SCAN_WIRING` | `1` |
| `ARE_WALKER_CUSTOMER_AGENT` | `0` (default — synthetic_v0 fallback / Tier-1 rollback) |
| `ARE_SCORER_REAL` | `on` (default — real Scorer-side scoring) |
| N | 5 |
| Seed | 42 |
| Scanner version | `0.4.2` |
| Corpus version | `0.1.0` |
| Probe-set version | `1.0.0` |
| Canon master at open | `bec8f80` |
| Corpus snapshot hash | `sha256:112f3410a75d5f73d840c097054d510a7fcf962950a898452c927ac4ac0d3829` |

## Envelope shape (single replicate, byte-identical across all 5)

| Field | Value |
| --- | --- |
| `are_total` | `69` |
| `band` | `C+` |
| `partial_completion` | `False` |
| `confidence_below_adjudication_threshold` | `False` |
| `subscores.BIAS_FAIRNESS` | `15.625` |
| `subscores.GOVERNANCE` | `21.875` |
| `subscores.OPS` | `18.75` |
| `subscores.ROBUSTNESS` | `12.5` |

The `partial_completion: False` field confirms the synthetic_v0
fallback path remains byte-stable post-Q-B4 — the new
`_has_uncovered_fires_true` defensive check short-circuits False on
`_build_synthetic_applicability`'s `fires=False` everywhere output.

## Spread (all 5 replicates)

| Quantity | Value |
| --- | --- |
| `are_total` spread (max − min) | **0** |
| `subscores.BIAS_FAIRNESS` spread | **0.0** |
| `subscores.GOVERNANCE` spread | **0.0** |
| `subscores.OPS` spread | **0.0** |
| `subscores.ROBUSTNESS` spread | **0.0** |
| Canonical JCS envelope cardinality | **1** |
| Max numeric spread | **0** |
| AG-B6 PASS | **True** |

## Rollback determinism interpretation

Per CTO Q-B6 = ACCEPT: synthetic_v0 path REMAINS as Tier-1 rollback;
`ARE_WALKER_CUSTOMER_AGENT=0` keeps the v0 walker active. This
delta proves that flipping the flag back to `0` (rollback) returns
the production scan-runtime to a byte-stable distribution against
the synthetic walker — the rollback is real, not a stub.

The customer-true path (`ARE_WALKER_CUSTOMER_AGENT=1`) is
non-deterministic by construction (real customer endpoints).
AG-B5 covers the IPC + scaffolding round-trip stability surface on
that path; AG-B7 covers the credential-bytes IPC audit.

## Artifacts

- `scripts/beaaa5414_track_b_calibration.py` — the harness (with the
  AG-B6 emission-path guard at module-load time).
- `tools/calibration-deltas/2026-06-15-beaaa5414-track-b-walker-n5.json`
  — every replicate envelope + spread summary + run metadata. Source
  of truth for the merge co-sign.
- This document — the human-readable delta report.

## References

- ADR-0030 §Acceptance Gates AG-B6 (CTO amendment 1).
- BEAAA-5418 — CTO design co-sign (relay child).
- BEAAA-5420 — UW op-seat provenance-dial co-sign (relay child).
- BEAAA-2316 — B2 calibration blindspot (monkey-patched
  `pab.FIXTURE_CONFIG` produced false spread=0). The AG-B6
  emission-path guard exists specifically to close this gap.
- `contracts/scanner-implementation-supervision-v1.md` v1.1 §Required
  artifacts — the `merged_sha_on_canon_master_at_close` gate.
