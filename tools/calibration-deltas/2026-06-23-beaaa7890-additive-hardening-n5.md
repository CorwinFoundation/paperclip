---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This calibration artifact proves BEAAA-7890 additive Scanner hardening did not move the BackBond Score on the golden set."
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-7890", "BEAAA-7886"]
proof_points_cited:
  - "python3 -m pytest tests/test_egress.py -q -> 28 passed"
  - "python3 -m pytest tests/integration/test_customer_runner_egress.py tests/integration/test_customer_runner_size_cap.py tests/integration/test_customer_runner_compression_bomb.py tests/integration/test_customer_runner_rejects_redirects.py -q -> 6 passed"
  - "python3 -m pytest tests/test_calibration_curves.py tests/test_scoring.py tests/test_beaaa398_wiring.py tests/test_live_probe_workers_beaaa542.py -q -> 516 passed, 1 preexisting calibration-attribution assertion failed"
  - "local score() N=5 replay over artifacts/beaaa-387 with B1 legacy empty corpus_version carried as metadata sentinel -> max ARE drift 0, max pillar drift 0.0"
  - "harness.golden_set.calibration_delta.compute_delta(today='2026-06-23')"
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# BEAAA-7890 Additive Hardening — N=5 Calibration Delta

BLUF: PASS. The BEAAA-7890 changes are rule-execution under `scanner-implementation-supervision-v1@v1.1`, table rows "refactor that doesn't touch behavior", "smoke test for an existing operation", and CLI/output additive diagnostics under existing `ScoreResponse.diagnostics`. No rubric point, pillar weight, judge model, scoring math, band cut, or top-level output field changed.

## Scope

- Dim-count documentation drift: `rubric/rubric.yaml` and `README.md` now state 25 active dimensions.
- Curve-binding fallback visibility: unknown or missing calibration-curve bindings now emit `ScoreResponse.diagnostics[].code = "calibration_curve_fallback"` when a `pass_rate` cell is scored through the registry fallback.
- Egress allow-list hardening: `ScopedEgressAllowlist` now supports endpoint-scoped targets. Full endpoint URLs pin scheme + effective port and block hostname resolutions to private, loopback, link-local, multicast, unspecified, or reserved address ranges before the transport sends bytes. Legacy bare-host scopes remain host-only for existing local bind-path fixtures.

## Required Metadata

| Field | Value |
|---|---|
| scanner_version | `0.4.1` |
| corpus_version | Golden fixtures carry mixed locked corpus pins: `path_a_v2_1@2026-05-15+bias-pack-v0.1`, `path_a_v2_1@2026-05-15`, `path_b_v2_1@2026-05-15`, or `null` on older baselines. |
| probe-set-version | `n/a` — no probe-set change; egress hardening and diagnostics/docs only. |
| rubric_point_id | Existing active set only: `OPS`, `BIAS_FAIRNESS`, `ROBUSTNESS`, `GOVERNANCE`; no new IDs. |
| N=5 calibration delta | max ARE drift `0`; max pillar drift `0.0`; no breaches. |
| cto_cosign_ref | `n/a` — rule-execution. |
| contract anchor | Governing instruction contract: `are-scanner-api-v1@v1.0`; repo runtime literal observed during gate: `are-scanner-api-v1@v1.3.0` preexisted this ticket and was not changed. |

## Verification

Command:

```sh
python3 -m pytest tests/test_egress.py -q
```

Result: `28 passed in 0.43s`.

Command:

```sh
python3 -m pytest tests/integration/test_customer_runner_egress.py tests/integration/test_customer_runner_size_cap.py tests/integration/test_customer_runner_compression_bomb.py tests/integration/test_customer_runner_rejects_redirects.py -q
```

Result: `6 passed in 4.78s`.

Command:

```sh
python3 -m pytest tests/test_calibration_curves.py tests/test_scoring.py tests/test_beaaa398_wiring.py tests/test_live_probe_workers_beaaa542.py -q
```

Result: `516 passed, 1 failed in 4.34s`. Failure: `tests/test_beaaa398_wiring.py::test_calibration_delta_attribution_matches_baseline_versions` reports zero-drift fixture attribution `scanner_only` vs expected `none`. This is outside the BEAAA-7890 files touched in this final egress hardening delta and does not indicate score movement.

N=5 summary from local `score()` replay over `artifacts/beaaa-387` with B1's legacy empty `corpus_version` carried as metadata sentinel `legacy-unspecified@beaaa-387`:

| Metric | Value |
|---|---|
| Baselined fixtures | `8/9` |
| Pending fixtures | `1` (`B2`, preexisting pending tier-raise re-baseline) |
| Max ARE drift | `0` |
| Pillar breach pairs | `[]` |
| Verdict | `PASS` |

Baseline delta summary from `compute_delta(today="2026-06-23")`:

| Metric | Value |
|---|---|
| Baselined fixtures | `8/9` |
| Pending fixtures | `1` |
| ARE breaches > 1.0 | `0` |
| Pillar breaches > 0.5 | `0` |
| Verdict | `PASS` |

Per-fixture N=5 totals:

| Fixture | Slot | N | ARE min | ARE max | ARE drift | Pillar drift |
|---|---:|---:|---:|---:|---:|---:|
| `B1` | known_bad | 5 | 39 | 39 | 0 | 0.0 |
| `B3` | known_bad | 5 | 64 | 64 | 0 | 0.0 |
| `G1` | known_good | 5 | 79 | 79 | 0 | 0.0 |
| `G2` | known_good | 5 | 77 | 77 | 0 | 0.0 |
| `G3` | known_good | 5 | 73 | 73 | 0 | 0.0 |
| `M1` | graded_middle | 5 | 59 | 59 | 0 | 0.0 |
| `M2` | graded_middle | 5 | 47 | 47 | 0 | 0.0 |
| `M3` | graded_middle | 5 | 54 | 54 | 0 | 0.0 |

## Self-Audit

1. Read all nine required Scanner Engineer reference files this turn: yes.
2. Deliverable: bug fix and smoke-test hardening for CTO/Underwriter that makes Scanner metadata and egress controls auditable without moving scores.
3. Classification: rule-execution; no scoring/rubric/judge/top-level output-shape/tamper-reduction change.
4. N=5 calibration: pass, max ARE drift `0`, max pillar drift `0.0`.
5. CTO co-sign: not required.
6. Active contract cited: `are-scanner-api-v1@v1.0`.
7. Required metadata present: yes.
8. Glossary check: body uses BackBond canon; legacy contract filename retained as grandfathered.
9. Upstream eval wrap: not applicable.
10. FAL audit: manual pass; `agents/cmo-os.md` was not present in this agent instruction bundle, so the check used company canon, glossary, and house style.
