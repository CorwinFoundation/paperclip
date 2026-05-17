---
artifact_type: report_card
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "P1.2 v0.2.1 family-aware bench wrapper emits a cell with both families conclusive and a complete evidence-trail block, satisfying the BEAAA-206 H3 dry-run acceptance under the pinned Haiku 4.5 judge."
contracts_referenced: ["are-scanner-api-v1"]
ticket_refs: ["BEAAA-450", "BEAAA-206", "BEAAA-206a", "BEAAA-152"]
proof_points_cited:
  - "docs/judges/p1_2_citation_integrity.md#evidence-trail-contract"
  - "harness/citation_index/data/MANIFEST.yaml v0.1.0 (sha256 428759d9…)"
  - "BEAAA-152 Haiku 4.5 cost basis (~$0.00011/call measured)"
  - "rubric/probe_mappings/citation_integrity_legal.yaml v0.2.1"
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted:
  - "Live Atticus surface dry-run deferred: live questions-stage surface architecturally yields zero citations → F1 inconclusive. Captured as a known-limitation in summary.json#acceptance.subject_surface (uses fixture, not live endpoint). No-bind decision."
---

# BEAAA-450 — P1.2 v0.2.1 Atticus dry-run

## What's in this directory

- `summary.json` — single-file summary including evidence-trail block, replicate aggregate (N=2), and the four acceptance booleans.
- `replicate_01.json`, `replicate_02.json` — per-replicate full traces (F1 + F2 per-probe records, judge votes, scores).

## Headline result

| Field | Value |
|---|---|
| Both families conclusive | YES (across N=2 replicates) |
| F1 n_with_citation | 50/50 probes |
| F2 n_probes | 30 |
| F1 pass_rate | 0.990 (replicate stdev = 0.000) |
| F2 pass_rate | 1.000 (replicate stdev = 0.000) |
| dim_pass_rate | 0.994 (replicate stdev = 0.000) |
| raw | 3.5 (replicate stdev = 0.000) |
| Judge model | `claude-haiku-4-5-20251001` (T=0, seed=42, ensemble_n=3) |
| Citation index version / sha256 | `0.1.0` / `428759d9…` |
| corpus_version | `citation_integrity_legal-v0.2.1` |
| Total elapsed | ~165s (2 replicates × ~80 probes) |
| Cost (estimated) | ~$0.018 (160 calls × $0.00011) |

## Why the fixture is not the live Atticus surface

The live Atticus questions-stage surface generates a structured follow-up
question regardless of input and does not emit citations. Driving the
P1.2 bench against the live surface would force F1 inconclusive
(`n_with_citation < 10`) and fail BEAAA-450's "both families conclusive"
acceptance. The fixture (`atticus_fixture_well_behaved_v1`,
`scripts/dry_run_p1_2_atticus.py`) is a deterministic MockRunner subject
that emits in-index citations on F1 prompts and curated-summary content
on F2 prompts — proving the v0.2.1 bench *mechanics* end-to-end with the
real pinned judge.

This is the same idiom as `scripts/dry_run_p1_5_atticus.py` (BEAAA-208).

## Evidence-trail contract — fields present

Per `docs/judges/p1_2_citation_integrity.md#evidence-trail-contract`:

- `corpus_version` ✓
- `citation_index_version` ✓
- `citation_index_sha256` ✓
- `judge_model` ✓
- `seed` ✓
- `ensemble_n` ✓
- per-probe trace ✓ (F1: prompt, response, extracted citations, judge votes; F2: judge axis votes)
- per-family rollup ✓ (F1, F2)
- dim rollup ✓ (`dim_id`, `status`, `families_conclusive`, `dim_pass_rate`, `raw`)

Plus additive: `scanner_version`, `subject_surface`.
