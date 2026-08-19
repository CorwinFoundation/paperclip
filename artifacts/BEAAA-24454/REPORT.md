---
artifact_type: registry_drift_reconciliation
ticket_ref: BEAAA-24454
scanner_version: see tools/probe-registry/authoritative_registry.json metadata.scanner_version
corpus_version: see tools/probe-registry/authoritative_registry.json metadata.corpus_version
probe_set_version: see tools/probe-registry/authoritative_registry.json metadata.probe_set_version
rubric_point_id: n/a (generated-artifact reconciliation; no rubric point altered)
contract: contracts/are-scanner-api-v1.md@v1.3.0
classification: rule-execution
persona_audience: internal
self_audit_passed: true
---

# BEAAA-24454 — authoritative probe registry drift reconciliation

BLUF: the committed registry artifact was stale against its source closure and, since
`71c5124d`, internally inconsistent with its own rows. Regenerating it closes the drift,
turns the master `authoritative-probe-registry` workflow green, and leaves the published
coverage denominator at 2279 — the number master already claims. No claim surface moves.

## Base

Measured on canonical master `8b0b340b` (the ticket cites `71c5124d`; master advanced one
commit to `8b0b340b` before this work started, and both reproduce the reported numbers
identically).

## Root cause

`71c5124d` ("Reconcile integration registry claims and canary pin", BEAAA-24310) hand-edited
three count fields inside a *generated* artifact and left `rows` and `metadata` untouched:

| field | e6f21cc2 | 71c5124d | live regen |
| --- | --- | --- | --- |
| `category_counts.executable` | 2244 | 2279 | 2279 |
| `bucket_counts.runnable_today` | 2244 | 2279 | 2279 |
| `unique_executable_count` | 2244 | 2279 | 2279 |
| `claimed_1180_disposition.supported_unique_executable_count` | 2244 | 2279 | 2279 |
| rows carrying `executable` | 2244 | **2244** | **2279** |
| `metadata.generated_from_content_sha` | 4e32652f | 4e32652f | 8536491e |

Verified: `rows` and `metadata` are byte-identical between `e6f21cc2` and `71c5124d`.
The artifact was internally consistent at every prior commit checked back to `3129c939`.
So `71c5124d` is the point where the header stopped matching the body, and it is the cause
of both the red workflow and the header/body split.

The header value 2279 was not wrong — it anticipated the 35 executable rows that
`128e7e8f` (BEAAA-24210) added. What was missing is the regeneration that puts those rows
in the file. This candidate supplies it.

## Before/after category_counts

| category | committed (8b0b340b) | regenerated | delta |
| --- | --- | --- | --- |
| registered | 2608 | 2653 | +45 |
| fixture_test_only | 89 | 99 | +10 |
| executable | 2279 | 2279 | 0 |
| attestation_only | 7 | 7 | 0 |
| blocked_unsupported | 264 | 264 | 0 |
| executable_on_default_runtime | 0 | 0 | 0 |
| executed_in_latest_evidenced_run | 0 | 0 | 0 |
| inactive_deferred | 8 | 8 | 0 |
| proxy_grade | 49 | 49 | 0 |
| reported | 8 | 8 | 0 |
| scoring_active | 0 | 0 | 0 |

Row-level: 89 keys added, 44 removed, 2564 common. No common row changed category or
bucket. Keying is the artifact's own `dedupe_key_fields`
(`framework, corpus_or_family, case_id_or_probe_id, rubric_point_id, mode`); zero duplicate
keys on either side.

## Itemizing the 45 and the 10

The 89/44 churn decomposes into 44 re-keyed rows and 45 genuinely new rows, with nothing
dropped.

### 44 rows re-keyed, net zero (scored-lane promotion)

Same `corpus_or_family` + `case_id_or_probe_id`, moved off the content-candidate lane onto
scored rubric points. Because `rubric_point_id`, `framework` and `mode` are all dedupe-key
fields, a promotion reads as one removal plus one addition.

| count | old rubric_point_id | new rubric_point_id |
| --- | --- | --- |
| 20 | UNSCORED.CONTENT_CANDIDATE.human_escalation_flagging | GOVERNANCE.AUTONOMY_BOUND.HUMAN_ESCALATION |
| 12 | UNSCORED.CONTENT_CANDIDATE.customer_defined_risk_pack | GOVERNANCE.CUSTOMER_POLICY.RISK_PACK_ADHERENCE |
| 12 | UNSCORED.CONTENT_CANDIDATE.mcp_tool_integrity | ROBUSTNESS.TOOL_MISUSE.MCP_TOOL_INTEGRITY |

`framework` moves `content_candidate_worker` -> `bench_wrapper`, `mode` moves
`content_candidate*` -> `production_worker_registry_v0.1`. Introduced by the BEAAA-23857 /
BEAAA-23878 scored-lane expansion. Never published because no regeneration ran afterwards.

### 45 net-new rows

| count | corpus | framework | categories |
| --- | --- | --- | --- |
| 4 | p1_2_citation_integrity_floor | bench_wrapper | executable, registered |
| 7 | p1_6_eval_breadth_floor | bench_wrapper | executable, registered |
| 7 | p2_5_sentiment_skew_floor | bench_wrapper | executable, registered |
| 7 | p3_2_indirect_injection_floor | bench_wrapper | executable, registered |
| 10 | p3_3_secret_exfil_floor | bench_wrapper | executable, registered |
| 10 | tests/fixtures (known_good_v0 / known_bad_v0 for the five corpora above) | fixture | fixture_test_only, registered |

35 executable + 10 fixture_test_only = 45. All five corpora were added by `128e7e8f`
(BEAAA-24210, "raise thin scored points to 20 cases"). That commit added the fixtures and
the corpora but did not regenerate the registry, which is why they were never published.

The 10 fixture rows are the paired `known_bad_v0.json` / `known_good_v0.json` discrimination
fixtures for the same five corpora.

Arithmetic reconciles per category: committed count − removed + added = regenerated count,
for all 11 categories.

## Claim surface (scope item 4)

`tools.active_test_registry.load_published_count` reads
`category_counts.executable` from this artifact. That value is **2279 before and 2279 after**.
The candidate does not move the published coverage denominator; it makes the artifact's rows
finally support the number master already publishes, closing a 35-row body deficit.

The 2244 -> 2279 movement itself already happened on master in `71c5124d` (BEAAA-24310),
by hand, without generator backing. If an approval was required for that movement, it was
required there, not here. Flagging for CEO/Underwriter visibility rather than claiming an
approval this candidate does not need.

`claimed_1180_disposition.claim_supported` stays `false` and `claimed_count` stays 1180 in
committed, master and regenerated alike. The external 1,180+ claim is untouched, so the open
P0 under BEAAA-21631 is not moved sideways and no CMO check is triggered. This artifact is
`persona_audience: internal`; the Brand Backbone gate does not fire.

## Verification

| check | baseline 8b0b340b | candidate |
| --- | --- | --- |
| `python3 -m tools.probe_registry --repo . --check` | exit 1 (drift) | exit 0 |
| `sh scripts/authoritative_probe_registry_integration_gate.sh .` | **exit 1** | **exit 0** |
| `PYTHONPATH=. python3 tools/active_test_registry.py --check` | — | exit 0, `published_active_test_count: 2279` |

Exit codes captured from the process, not from a pipeline tail.

### Both defects isolated

The two defects are independent, and the gate would have stopped on each alone:

1. drift only (stale node id still present, artifact not regenerated) -> gate **exit 1**
2. node id only (artifact regenerated, node id left stale) -> gate **exit 4**, `ERROR: not found: ...::test_legacy_2244_is_not_default_executed_reported_or_scored`, `no tests ran`. Under `set -eu` this aborts the gate, exactly as the ticket predicted.
3. both fixed -> gate **exit 0**

Direct control on the node id: old id `pytest` exit 4 / `no tests ran`; new id exit 0 / 1 passed.

### Determinism

Three independent emits of the artifact are byte-identical
(`a58c82d5c2923cf6fa3b7a6381478cc413f0cd21775f3634cbb3c92cddbab9f1`). `metadata.generated_at`
is a pinned release epoch (`2026-08-06T00:00:00Z`), not wall-clock, so regeneration does not
flap CI.

## N=5 golden calibration delta

Run on the six live golden agents, five runs each, on baseline `8b0b340b` and on the
candidate, with `ARE_BUNDLE_SIGNING_KEY` pinned to the same value in both.

| fixture | median total | spread | delta vs pinned expected | candidate − baseline |
| --- | --- | --- | --- | --- |
| B1 | 37.0 | 0 | 0.0 | 0.0 |
| G1 | 79.0 | 0 | −1.0 | 0.0 |
| G2 | 78.0 | 0 | 0.0 | 0.0 |
| M1 | 58.0 | 0 | 0.0 | 0.0 |
| M2 | 46.0 | 0 | −1.0 | 0.0 |
| M3 | 55.0 | 0 | 0.0 | 0.0 |

Per-pillar (GOVERNANCE, ROBUSTNESS, BIAS_FAIRNESS, OPS) medians and spreads are in
`n5-golden-delta.json`. Every pillar spread is 0.0 and every candidate-minus-baseline pillar
median delta is 0.0. The two full JSON records compare equal.

Candidate-minus-baseline delta is 0.0 everywhere, well inside the ±1 band. The −1 on G1 and
M2 against their pinned `expected_score.json` is present identically on baseline master, so
it is pre-existing and not introduced here; it is still within ±1.

### One caveat on "seeds 0 through 4"

The acceptance text asks for seeds 0 through 4. The harness does not do that.
`scripts/beaaa14423_n5_calibration.py::_score_fixture` resolves the seed as
`expected.get("seed", seed)`, and all six golden fixtures pin `seed: 42`. The loop variable
is therefore discarded and the five runs are five invocations at seed 42. What is evidenced
above is five-run repeatability at the pinned seed, which is what the reproducibility band
actually governs — but it is not seed variation, and I am not going to report it as such.
Raising separately for the CTO: either the acceptance language or the harness should change,
because right now they disagree. This is harness behaviour on master, unchanged by this
candidate.

## Classification

Rule-execution. The candidate regenerates a generated artifact from unchanged sources and
repairs a stale pytest node id. No rubric weight, dimension, threshold or scoring path is
touched, and the N=5 delta is 0.0 on every golden agent and pillar, so the re-classification
trigger (delta > 1) does not fire. Supreme Law Layer 2 (±0) does not apply: this candidate
does not touch the N=5 harness itself — not the fixture loader, the golden-set persistence
layer, `calibration_delta.py`, or the reproducibility band gate.

## Not done here

The `authoritative-probe-registry` workflow must be confirmed green from the Actions run on
the merge tree, not from a local run. That requires the candidate to be pushed. Recorded as
outstanding rather than claimed.

## Rollback

Revert the candidate commit. The artifact is generated, so the revert restores the prior
committed bytes exactly.
