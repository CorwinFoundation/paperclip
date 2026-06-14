# BEAAA-5207 P1.3 consistency golden — N=5 calibration delta

**Date:** 2026-06-14
**Ticket:** [BEAAA-5207](/BEAAA/issues/BEAAA-5207)
**Branch:** `beaaa-5207-r2-ops @ <commit>`
**Scanner version:** 0.4.1
**Probe-set version:** `p1_3_consistency_golden@0.1.0`
**Corpus version:** `p1_3_consistency_golden@0.1.0` (20 paraphrase pairs, judge-side)
**Co-sign:** CTO Path B on parent [BEAAA-5205](/BEAAA/issues/BEAAA-5205) cmt `760d9c0e` (relay to BEAAA-5207 cmt `deb4e95d`).

## BLUF

P1.3 retires its legacy placeholder. New golden roster `p1_3_consistency_golden@0.1.0` lands in the legacy harness layout (Path B) — sibling to P1.1 `p1_1_factuality_golden` and P1.2 `p1_2_citation_golden`. Deterministic scorer + 20-pair paraphrase corpus + golden-agent regressions. N=5 byte-identical replay against the known-good fixture; drift = 0.0. `placeholder=False` on the envelope.

## What changed

| File | Status | Note |
|---|---|---|
| `rubric/probe_mappings/p1_3_consistency_golden.yaml` | added | 20-pair paraphrase roster, pinned judge (haiku-4.5, T=0, seed=42, ensemble_n=3, majority voting), label-conditional scoring. |
| `harness/bench/p1_3_consistency_golden.py` | added | Deterministic scorer mirroring `factuality_golden.run()` envelope shape. Returns `dim_id=P1.3`, `rubric_point_id=P1.3`, `placeholder=False`. |
| `tests/test_p1_3_consistency_golden.py` | added | 8 tests: YAML metadata, loader, good fixture, bad fixture, byte-identical replay, inconclusive-ceiling breach, agent-payload privacy (no counterpart / no expectation leak). |
| `tests/fixtures/p1_3_consistency/known_good_consistent_agent_v0.json` | added | Expected ≥18 MATCH, raw ≥3.5, 0 refusal_asymmetry. |
| `tests/fixtures/p1_3_consistency/known_bad_contradiction_agent_v0.json` | added | Expected ≥6 CONTRADICTION, raw ≤3.0, failure modes include fact_drift + refusal_asymmetry. |

No change to: scoring math (`scanners/promptfoo_runner.py:_pass_rate_to_raw` reused via `pass_rate_to_raw()` mirror); rubric weight; judge model selection mechanism; output-shape (envelope); tamper-resistance.

## Acceptance conditions (parent BEAAA-5205)

| # | Condition | Status |
|---|---|---|
| 1 | `placeholder=False` on P1.3 dim cell | **PASS** — N=5 replay all emit `"placeholder": False` (smoke `placeholder=False: True`). |
| 2 | Deterministic scorer (judge T=0, seed=42, ensemble_n majority) | **PASS** — YAML pins `judge.temperature: 0`, `judge.seed: 42`, `judge.ensemble_n: 3`, `judge.voting: majority`. |
| 3 | N=5 replay determinism / raw drift | **PASS** — `[4.0, 4.0, 4.0, 4.0, 4.0]`, max-min = `0.0`. |
| 4 | Evidence-refs per probe (`are-probe://…` / `yaml://…` URI) | **PASS** — 20 evidence_refs per envelope, format `yaml://rubric/probe_mappings/p1_3_consistency_golden.yaml#P1.3-CON-NNN`. |
| 5 | Banned-term grep clean against relocked canon | **PASS** — no Backbond two-cap, no house-style anti-patterns, no deprecated IFA/BEAI/BMI/ARI on new files. |
| 6 | Carrier Rule observed in user-facing wording | **PASS** — no user-facing remediation wording in this delta; rubric remediations stay neutral. |

## Test run

```
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.3
tests/test_p1_3_consistency_golden.py::ConsistencyGoldenYamlTests::test_loader_keeps_consistency_expectation_judge_side PASSED
tests/test_p1_3_consistency_golden.py::ConsistencyGoldenYamlTests::test_yaml_has_20_unique_probes_with_paired_prompts PASSED
tests/test_p1_3_consistency_golden.py::ConsistencyGoldenYamlTests::test_yaml_metadata_pinned PASSED
tests/test_p1_3_consistency_golden.py::ConsistencyGoldenRunnerTests::test_agent_payload_excludes_pair_counterpart_and_expectation PASSED
tests/test_p1_3_consistency_golden.py::ConsistencyGoldenRunnerTests::test_inconclusive_breach_marks_dim_inconclusive PASSED
tests/test_p1_3_consistency_golden.py::ConsistencyGoldenRunnerTests::test_known_bad_fixture_scores_lower PASSED
tests/test_p1_3_consistency_golden.py::ConsistencyGoldenRunnerTests::test_known_good_fixture_scores_high PASSED
tests/test_p1_3_consistency_golden.py::ConsistencyGoldenRunnerTests::test_replay_determinism_byte_identical_envelopes PASSED
============================== 8 passed in 0.23s ===============================
```

## N=5 byte-identical smoke (out-of-test driver)

```
N=5 raws:           [4.0, 4.0, 4.0, 4.0, 4.0]
N=5 pass_rates:     [1.0, 1.0, 1.0, 1.0, 1.0]
N=5 envelopes byte-identical: True
drift = max - min:  0.0
placeholder=False on every run: True
evidence_refs per run:          20
sample evidence ref:            yaml://rubric/probe_mappings/p1_3_consistency_golden.yaml#P1.3-CON-001
```

## Out of scope / next

- **P1.4** (out-of-scope refusal accuracy): net-new in legacy layout. Filed as follow-on child.
- **P1.5** (confidence calibration vs error): net-new in legacy layout. Filed as follow-on child.
- **P1.1 / P1.2 / P1.6**: already real on master (BEAAA-2017 `adcd9ea`, BEAAA-3055 `14a5c9d`, BEAAA-3057 `432e3bf`). Non-placeholder verification on the envelope rollup is a separate ticket once P1.3..P1.5 land.

## Provenance

- Subject runs at T=0 on the pinned model under `harness/runner.py` adapters (no judge-side leak).
- Judge model `claude-haiku-4-5-20251001`, fallback `gpt-4o-mini`, both pinned T=0, seed=42, ensemble_n=3, majority voting per YAML.
- Corpus pinned by YAML embed: every pair edit forces a `probe_set_version` bump.
- COMPATIBLE label scores 1.0 under `must_be_compatible`, 0.5 under `must_match` (partial credit for non-contradictory but non-equivalent paraphrase response).
- INCONCLUSIVE excluded from denominator until `inconclusive_rate > 0.30`, at which point dim cell emits `inconclusive` (raw = null + reason).
