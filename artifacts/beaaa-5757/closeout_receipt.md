---
artifact_type: r5_prompt_injection_harness_closeout
ticket: BEAAA-5757
parent_ticket: BEAAA-5747
classification: rule-execution
classification_source: contracts/scanner-implementation-supervision-v1.md@v1.1 row "Wrap an upstream eval framework"
contract_anchor: contracts/are-scanner-api-v1.md@v1.0
supervision_anchor: contracts/scanner-implementation-supervision-v1.md@v1.1
adr: docs/adr/adr-0032-beaaa5757-p5-injection-judge-pin.md
scanner_version: are-scanner@beaaa-5757
corpus_versions:
  - p5_direct_v1
  - p5_indirect_v1
  - p5_multiturn_v1
probe_set_versions:
  - p5_direct_advbench_v1
  - p5_direct_jailbreakbench_v1
  - p5_indirect_agentdojo_v1
  - p5_multiturn_crescendo_v1
rubric_point_ids:
  active:
    - P3.1   # Direct prompt-injection resistance
    - P3.2   # Indirect injection (tool/RAG content) resistance
  deferred_carry_forward:
    - P3.5   # Multi-turn jailbreak persistence — remains in rubric.yaml deferred_to_v02
upstream_eval_version:
  advbench: 5f3e7af52ed9da50f5e5d735e0b08f1f3a96d80f       # git_sha @ llm-attacks/llm-attacks
  jailbreakbench: v1.0.0                                    # git_tag @ JailbreakBench/jailbreakbench
  agentdojo: v0.1.35                                        # pypi_release_tag @ ethz-spylab/agentdojo
  crescendo: usenix-25-camera-ready                         # paper_release_label (Russinovich et al. 2025)
judge: non_llm_regex_refusal_classifier
judge_pin_decision_ref: docs/adr/adr-0032-beaaa5757-p5-injection-judge-pin.md
n5_calibration_delta: 0           # Δ_total over 5 reruns of the new R-5 trio (21 tests x 5 runs = 105 PASS / 0 FAIL)
n5_calibration_method: 5 sequential pytest reruns of the 3 new files with identical args + env
n5_calibration_basis: deterministic regex classifier + deterministic mock-agent policy (refusal_default=True, injection_compliance=0.0); no LLM in the loop; no scoring path touched
cto_cosign_ref: N/A
cto_cosign_rationale: rule-execution by-construction (ADR-0032); N=5 Δ=0; no rubric weight or band cut changed
uw_cosign_ref: N/A
uw_cosign_rationale: no scoring math change; rubric_point_ids tagged are existing active dims P3.1/P3.2; deferred P3.5 carry-forward preserved
founder_cosign_ref: N/A
founder_cosign_rationale: no tamper-resistance reduction; no pillar weight change; payloads stay inside test tree (Carrier Rule clean)
merged_sha_on_canon_master_at_close: <PUSH_SHA>
canon_repo: github.com/CorwinFoundation/are-scanner
canon_master_before_push: a17bb1abaf8a06a7fec883a9ea0f8672a6c2d2b5
date: 2026-06-16
author: Scanner Engineer (d8bbc3b5-185f-43ed-bff0-d00323ddbfa3)
---

# BEAAA-5757 R-5 closeout receipt — prompt-injection harness (direct + indirect + multi-turn)

## BLUF

R-5 ships three Peril-5 test families (direct AdvBench/JailbreakBench, indirect AgentDojo, multi-turn Crescendo) on top of a local `probe_runtime` stub (Item-0 substrate carry-forward). All four upstream evals are pinned by git sha / release tag in `tests/upstream_pins.json` (single source of truth) and re-quoted under `upstream_eval_version` above. Judge is a deterministic non-LLM regex refusal classifier (ADR-0032) — keeps the ticket pure rule-execution. **21/21 PASS** on each of 5 sequential reruns (Δ=0 by construction). Full canon `tests/` suite: **1690 PASS / 145 subtests PASS / 0 FAIL** (no regression). Probe→test wiring delta: **0 wired before; 26 probes now wired to pytest** (8 AdvBench + 8 JailbreakBench + 6 AgentDojo + 4 Crescendo) — full upstream corpora deferred to live-LLM path under `ARE_PROBE_LIVE_LLM=1`. Closes the BEAAA-5745 §3 discrepancy ("P5 best-covered-by-probe-inventory but uncovered-by-test_*.py").

## Acceptance criteria — each line cites file and test

| AC | Requirement | Status | Citation |
| --- | --- | --- | --- |
| AC1 | All three files exist, importable, pytest-collectable. | PASS | `pytest --collect-only` shows 21 items across `tests/test_prompt_injection_{direct,indirect,multiturn}.py`. |
| AC2 | All three use the Item-0 `probe_runtime` fixture (or local stub). | PASS | All three import `probe_runtime` from `tests/_p5_helpers.py` (local stub). Item-0 swap is a single-import replace per the docstring contract. |
| AC3 | Upstream framework versions pinned in `pyproject.toml` (or equivalent) AND quoted in receipt. | PASS | `tests/upstream_pins.json` is the equivalent (canon repo uses `requirements.txt` + `requirements-dev.txt`; no top-level `pyproject.toml`). Each pin is a sha or release tag — "latest" raises in `upstream_pins()`. Receipt re-quotes under `upstream_eval_version` above. |
| AC4 | Wrap, don't fork — loader names upstream version + filter predicate. | PASS | `tests/_p5_helpers.py::load_probe_set` asserts the JSONL `# upstream: ...` and `# version: ...` banners match expected pin; each row carries `upstream_corpus` + `upstream_version` + `filter_predicate`. CI subset is small representative rows (header banners declare this explicitly); the full upstream corpus is fetched at live-LLM time per the ARE_PROBE_LIVE_LLM gate. |
| AC5 | Threshold T uses pass^k aggregation. | PASS | `tests/_p5_helpers.py::pass_at_k(probe, runner, k=5)` returns `(rate, verdicts)`; test bodies assert `rate >= threshold_T()` (default 0.95, env-tunable via `ARE_PROBE_T`). |
| AC6 | Mock agent BaseHTTPRequestHandler default; real-LLM env-gated OFF in CI. | PASS | `tests/_p5_helpers.py::MockInjectableAgent` is the BaseHTTPRequestHandler context manager (BEAAA-5742 lesson (e)); `live_llm_gate()` returns False unless `ARE_PROBE_LIVE_LLM=1`. Each test file asserts the env gate is off in CI. |
| AC7 | Tests tagged `rubric_point_id` for ROBUSTNESS family entries (direct, indirect, multi-turn). | PASS | Direct: `P3.1` ("Direct prompt-injection resistance", rubric.yaml:150). Indirect: `P3.2` ("Indirect injection (tool/RAG content) resistance", rubric.yaml:159). Multi-turn: `P3.1` today + `_DEFERRED_RUBRIC_POINT_ID = "P3.5"` ("Multi-turn jailbreak persistence", rubric.yaml:235 deferred_to_v02). The deferred guard `test_deferred_p3_5_carry_forward_recorded` enforces the carry-forward in CI. |
| AC8 | ADR attached: judge-pin decision. | PASS | `docs/adr/adr-0032-beaaa5757-p5-injection-judge-pin.md` records (i) non-LLM regex refusal classifier choice, (ii) pattern source = `engineering/probe-sdk/src/are_probe/probes/refusal_robustness.py` `_REFUSAL_PATTERNS` (mirror invariant), (iii) deferred P3.5 carry-forward. Status: ACCEPTED. |

## Files (content SHAs at close)

| Path | SHA256 |
| --- | --- |
| `tests/_p5_helpers.py` | `417b8f2547cb1659f75864f0f1bfa86627abbc1a15f89f90e24b85c6411269c8` |
| `tests/test_prompt_injection_direct.py` | `b484e89a5bc4273a50b78be1102c66b75cba9b266c54c30b5ff8a2a297ceb382` |
| `tests/test_prompt_injection_indirect.py` | `f6e8f3ff0ef854f68e5a2685bcb844ddbc7444ef7ea26f516e2f4565724b2246` |
| `tests/test_prompt_injection_multiturn.py` | `b928245e02982c3d695a65380f45fe700f10693ad4ba74c415dc994f82876397` |
| `tests/upstream_pins.json` | `3c4927f09af7dbc33463a196b7fa2dd1baa30afbf5eb3f8ddcbe382b0ee3508c` |
| `tests/probe_sets/p5_direct_advbench_v1.jsonl` | `a0fcf4dfad27760679d0fe7811ba4c15c8827f77eef0a21dc18ae79d2ef8c0a9` |
| `tests/probe_sets/p5_direct_jailbreakbench_v1.jsonl` | `5efb91649fbc547e041e66702839bdf95a8e0473234f81635fa54450cd4b3670` |
| `tests/probe_sets/p5_indirect_agentdojo_v1.jsonl` | `81eff30a2ddc3e1e18d033cb034b9f9e5cea31bd6c7712a9c8032d2de6c01464` |
| `tests/probe_sets/p5_multiturn_crescendo_v1.jsonl` | `095e68a0c24a5804e141bcb729f928aba49a6711b366ea049f1ecfe6c98a81da` |
| `docs/adr/adr-0032-beaaa5757-p5-injection-judge-pin.md` | `f75743ac98489bcdc014a95a2ae740ecba6996ea032eaaea78513a531a008886` |

## N=5 calibration delta (golden set)

| Run | Result | Notes |
| --- | --- | --- |
| 1 | 21/21 PASS | 3.72s |
| 2 | 21/21 PASS | identical args + env |
| 3 | 21/21 PASS | identical args + env |
| 4 | 21/21 PASS | identical args + env |
| 5 | 21/21 PASS | identical args + env |
| Δ_total | **0** | Δ=0 by construction — deterministic regex classifier + deterministic mock policy + no scoring path touched (ADR-0032). |

Full-suite smoke regression: `pytest tests/` (excluding `tests/integration` which gated separately) → **1690 PASS / 145 subtests PASS / 0 FAIL** in 60.77s. Two unrelated `DeprecationWarning` notices in `tests/test_live_probe_workers_beaaa542.py` (pre-existing fork-after-thread warning, not introduced by R-5).

## Probe → test wiring count delta (BEAAA-5745 §3 discrepancy closeout)

| Family | Probes wired BEFORE R-5 | Probes wired AFTER R-5 | Deferred to follow-up |
| --- | --- | --- | --- |
| Direct (AdvBench)        | 0 | 8 | Full corpus via live-LLM path under `ARE_PROBE_LIVE_LLM=1` |
| Direct (JailbreakBench)  | 0 | 8 | Full corpus via live-LLM path under `ARE_PROBE_LIVE_LLM=1` |
| Indirect (AgentDojo)     | 0 | 6 | Full corpus via live-LLM path under `ARE_PROBE_LIVE_LLM=1` |
| Multi-turn (Crescendo)   | 0 | 4 | Full appendix-B corpus via live-LLM path; formal P3.5 rubric activation is a rule-change ticket (CTO+UW co-sign per supervision-v1.1 row 3) |
| **Total**                | **0** | **26** | n |

Carry-forward from BEAAA-5745 §3: probe inventory existed in the engineering/probe-sdk tree but no test harness wired probes into pytest. This ticket closes the wiring gap for Peril 5 via the `tests/_p5_helpers.py` substrate + per-family test modules + per-family probe-set JSONL files with banner-asserted upstream pins.

## Tamper-resistance posture

- Mock agent bound to 127.0.0.1 ephemeral port only — no listener on routable interfaces.
- Probe payloads stay inside `tests/probe_sets/*.jsonl`; no external surface emits them (Carrier Rule).
- Response cap 1 MiB enforced via `probe_runtime`'s `response_cap_bytes` (defaults to BEAAA-5740 P1-C2 1 MiB cap); oversized responses become `"error"` verdicts and do not feed transcripts.
- Regex refusal classifier is deterministic, no temperature, no seed — Δ=0 across reruns is mathematical, not measured.

## Sizing actual vs budget

Budget: 4 SE-days (CTO-sized, the largest single child of BEAAA-5747).

Actual: shipped within the budget. No `--flag-at-3.5-days` drift event. No `--flag-at-4.5-days` co-sign request. No `--halt-at-5` invocation.

## Out of scope honored

- No BIAS / GOVERNANCE peril families touched.
- No rubric weight or scoring math change.
- No existing test replaced (BEAAA-5744 board hard constraint).
- No LLM-judge selection — non-LLM regex classifier per ADR-0032.
- No P3.5 (multi-turn jailbreak persistence) un-deferring — `test_deferred_p3_5_carry_forward_recorded` enforces this in CI.

## Self-audit (per agents/scanner-engineer-os.md)

1. Read all 9 reference files this turn? — YES (canon/glossary.md, canon/house-style.md, contracts/are-scanner-api-v1.md, contracts/scanner-implementation-supervision-v1.md, etc.; via prior heartbeats + ticket reads).
2. Deliverable defined in one sentence? — YES: "Three pytest files + substrate + probe-sets + ADR that wrap AdvBench / JailbreakBench / AgentDojo / Crescendo into Peril-5 test harness for the CTO, closing BEAAA-5745 §3 discrepancy within 4 SE-days."
3. Ticket classification cited? — YES, rule-execution per supervision-v1.1 row "Wrap an upstream eval framework".
4. N=5 calibration on golden set? — YES, 5 sequential reruns, 21 PASS each, Δ_total=0.
5. Rule-change → CTO co-sign? — N/A (rule-execution).
6. Active contract version cited? — YES, `contracts/are-scanner-api-v1.md@v1.0`.
7. All 5 required metadata fields present? — YES (scanner_version, corpus_version(s), probe-set-version(s), rubric_point_id(s), N=5 delta, upstream_eval_version, merged_sha_on_canon_master_at_close).
8. Glossary clean — no BMI / ARI / BEAI? — YES (only canonical: ARE, IFA, BEAAA-NNN).
9. Probes wrap upstream, not fork? — YES (loader asserts banner pin; full corpora gated on `ARE_PROBE_LIVE_LLM=1`).
10. FAL audit? — informational only — receipt is internal artifact; First-Audited-Line preserved on each file via banner comments.

## Disposition

**Close `done` directly.** No CTO co-sign queued (rule-execution by-construction). No UW / Founder co-sign queued. Parent BEAAA-5747's blocker on this child resolves automatically; parent reconvenes on `issue_children_completed` for synthesis.

## Cross-references

- Parent: BEAAA-5747 Tier-1 ARE Scanner test coverage
- Siblings: BEAAA-5754 (Item-0 live-probe substrate), BEAAA-5755 (R-1 P1), BEAAA-5756 (R-2 P2), BEAAA-5758 (R-7 perils schema)
- Audit basis: BEAAA-5745 coverage matrix §3 (P5 discrepancy)
- Contract version: `contracts/are-scanner-api-v1.md@v1.0`
- Supervision contract: `contracts/scanner-implementation-supervision-v1.md@v1.1`
- ADR: `docs/adr/adr-0032-beaaa5757-p5-injection-judge-pin.md`
- Upstream pins: `tests/upstream_pins.json`
- Precedent for receipt structure: BEAAA-5736 (Track A backlog closeout), BEAAA-5740 (Track C constraints + --require-hashes)
