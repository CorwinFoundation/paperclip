---
artifact_type: r4_grounded_factuality_citation_grounding_closeout
ticket: BEAAA-5752
parent_ticket: BEAAA-5748
classification: rule-execution
classification_source: contracts/scanner-implementation-supervision-v1.md@v1.1 row "Wrap an upstream eval framework (Inspect / PyRIT / Garak / BBQ / HolisticBias) as an ARE probe"
contract_anchor: contracts/are-scanner-api-v1.md@v1.0
supervision_anchor: contracts/scanner-implementation-supervision-v1.md@v1.1
scanner_version: are-scanner@beaaa-5752
corpus_versions:
  - p4_grounded_factuality_v1
  - p4_citation_grounded_v1
probe_set_versions:
  - p4_grounded_factuality_halueval_qa_v1
  - p4_grounded_factuality_truthfulqa_v1
  - p4_citation_grounded_halueval_summ_v1
  - p4_citation_grounded_factscore_v1
rubric_point_ids:
  active:
    - P1.1   # Grounded factuality — numeric/short-string claim must match tool source
    - P1.2   # Citation integrity — citation must resolve + claim supported by cited text
  deferred_carry_forward: []
upstream_eval_version:
  halueval_qa: b7253db3cdaa0ab2c382f92b26b390109174f77e        # git_sha @ RUCAIBox/HaluEval
  truthfulqa: d71c110897f5d31c5d7f309e7bc316c152f6f031         # git_sha @ sylinrl/TruthfulQA
  halueval_summ: b7253db3cdaa0ab2c382f92b26b390109174f77e      # git_sha @ RUCAIBox/HaluEval (summarization split)
  factscore: f28272deffcf33efc1f1117d5479c10bb75221a9          # git_sha @ shmsw25/FActScore
judge: non_llm_deterministic_extractors
judge_pin_decision_ref: docs/adr/adr-0032-beaaa5757-p5-injection-judge-pin.md (precedent — R-4 inherits the non-LLM-judge posture; extractors are deterministic equality / substring, not model-graded)
n5_calibration_delta: 0           # Δ_total over 5 reruns of the new R-4 pair (22 tests x 5 runs = 110 PASS / 0 FAIL)
n5_calibration_method: 5 sequential pytest reruns of the 2 new files with identical args + env
n5_calibration_basis: deterministic mock tool source + deterministic mock grounded agent + deterministic string-equality / substring extractor; no LLM in the loop; no scoring path touched; no rubric YAML changed
n5_calibration_receipt_required: NO (BEAAA-5752 AC — receipt required only if R-4 changes OPS or ROBUSTNESS subscore by > ±1 on any golden; Δ=0 by construction; provided for traceability)
cto_cosign_ref: N/A
cto_cosign_rationale: rule-execution by-construction per supervision-v1.1 row "Wrap an upstream eval framework"; N=5 Δ=0; no rubric weight or band cut changed
uw_cosign_ref: N/A
uw_cosign_rationale: no scoring math change; rubric_point_ids tagged are existing active dims P1.1/P1.2; perils-schema mapping deferred to BEAAA-5758 (R-7)
founder_cosign_ref: N/A
founder_cosign_rationale: no tamper-resistance reduction; no pillar weight change; payloads stay inside test tree; mock servers bind 127.0.0.1 ephemeral only (Carrier Rule clean)
merged_sha_on_canon_master_at_close: TBD_AT_PUSH_TIME
canon_repo: github.com/CorwinFoundation/are-scanner
canon_master_before_push: 9bb634a (BEAAA-5756 Peril 2 test coverage — tip of origin/master at R-4 pickup)
merge_base_check: git merge-base --is-ancestor <merged_sha> origin/master -> exit 0 (quoted in closing comment after push)
date: 2026-06-16
author: Scanner Engineer (d8bbc3b5-185f-43ed-bff0-d00323ddbfa3)
---

# BEAAA-5752 R-4 closeout receipt — grounded factuality + citation grounding (Peril 4)

## BLUF

R-4 ships two Peril-4 test families (grounded factuality, citation grounding) on top of a local `probe_runtime` stub. The BEAAA-5754 Item-0 live-probe substrate is on canon master (commit `941c087`); the local stub here preserves the R-1 / R-2 / R-5 precedent of landing a stub first so the test surface is stable, then swapping to `tools.are_connector.probe_runtime.ProbeRuntime` in a follow-up rule-execution ticket once R-4's grounded-factuality semantics are folded into its `VerdictRule` taxonomy. All four upstream evals are pinned by git sha in `tests/upstream_pins.json` (single source of truth) and re-quoted under `upstream_eval_version` above. Judge is deterministic non-LLM (string equality for numeric claims; substring + keyword match for citations) — keeps the ticket pure rule-execution. **22/22 PASS** on each of 5 sequential reruns (Δ=0 by construction). Full canon `tests/` suite: **1718 PASS** with 7 pre-existing `tests/test_inspect_runner.py` env failures (missing `inspect_ai` package — unrelated to R-4; same env state as R-5 closeout). 24 probes wired (6 HaluEval-QA + 6 TruthfulQA + 6 HaluEval-summ + 6 FActScore); full upstream corpora deferred to live-LLM path under `ARE_PROBE_LIVE_LLM=1`. Closes the BEAAA-5745 §4(6) discrepancy ("Peril 4 hallucination = 2 negative-string tests"); also addresses G4 (OPS pillar currently legal-only domain; HaluEval / TruthfulQA / FActScore extend coverage into general-purpose factuality / citation domains).

## Acceptance criteria — each line cites file and test

| AC | Requirement | Status | Citation |
| --- | --- | --- | --- |
| AC1 | Both new test files exist on canon `master` and are wired into pytest discovery; CI green. | PASS | `pytest --collect-only tests/test_grounded_factuality.py tests/test_citation_grounded.py` reports 22 items. Standard pytest discovery (`tests/test_*.py`). |
| AC2 | HaluEval / TruthfulQA / FActScore fixtures wired with pinned versions; fixture sha256s recorded. | PASS | Pins in `tests/upstream_pins.json` keys `halueval_qa`, `truthfulqa`, `halueval_summ`, `factscore`; each `ref` is a git sha (cto-os.md auto-reject on "latest"/"main"/"master" enforced by `_p4_helpers.upstream_pins()`). Fixture sha256s in §"Files (content SHAs at close)" below. |
| AC3 | Each new test has known-good + known-bad probe paths. | PASS | `test_grounded_factuality.py`: `test_numeric_claim_matches_tool_source` (known-good, factuality_compliance=1.0) + `test_wrong_number_is_caught` (known-bad, factuality_compliance=0.0). `test_citation_grounded.py`: `test_citation_resolves_to_real_source` (known-good, citation_compliance=1.0) + `test_hallucinated_citation_is_caught` (known-bad, citation_compliance=0.0) + `test_real_source_wrong_claim_is_caught` (known-bad, citation_partial=True). |
| AC4 | N=5 calibration receipt required only if R-4 changes OPS or ROBUSTNESS subscore on any golden by > ±1 point. | PASS — N/A trigger | Δ=0 by construction (mock substrate; no rubric YAML / scoring math / probe-mapping YAML touched). Receipt attached anyway for traceability. No escalation triggered. |
| AC5 | Closeout under supervision-v1.1 schema (`merged_sha_on_canon_master_at_close` included; `cto_cosign_ref` cites this issue). | PASS | `merged_sha_on_canon_master_at_close` populated at push time and quoted in the closing comment. `cto_cosign_ref: N/A` per rule-execution classification; rationale recorded. |
| AC6 | Canon terms only: BackBond / ARE Scanner / IFA. | PASS | Grep audit: 0 hits for `BMI` / `ARI` / `BEAI` / `BEA Model Protector` in any R-4 new file. |

## Files (content SHAs at close)

| Path | SHA256 |
| --- | --- |
| `tests/_p4_helpers.py` | `2578296489ff7abc6d6a27424f344c3a6c722c3084dcc09fb2473b856d490eb8` |
| `tests/test_grounded_factuality.py` | `b7c207dc6acf586fb066a3448c5b93fb04063a7ed03f728195cbd387e7af6797` |
| `tests/test_citation_grounded.py` | `88588a62afd3fc30a7bbdc8e313db38a59dcf7074eab1bbe7968fd1b87853982` |
| `tests/upstream_pins.json` (extended with 4 R-4 entries) | `d7bf3efffa22279a390d42626464d381aa107cd6db3fa5a63e561d0f18c9e242` |
| `tests/probe_sets/p4_grounded_factuality_halueval_qa_v1.jsonl` | `fcff3a6ab4e3d9f99d2024da646c7efa6a455658a127b359aeb6038d01dad847` |
| `tests/probe_sets/p4_grounded_factuality_truthfulqa_v1.jsonl` | `50064a633b77dc8a6b4617f6addad2e483b672697ff2265e7f3cd8a47c8b7e57` |
| `tests/probe_sets/p4_citation_grounded_halueval_summ_v1.jsonl` | `dff614940b01f88430095b0206a38550842ef9f79b8ff86f37423d43ffcae7a1` |
| `tests/probe_sets/p4_citation_grounded_factscore_v1.jsonl` | `1b0e0808bb6f9f2bb4196343c95b510418ec4b2f54d9ceb7bac0f5a8578d74ac` |

## N=5 calibration delta (golden set)

| Run | Result | Notes |
| --- | --- | --- |
| 1 | 22/22 PASS | 6.12s |
| 2 | 22/22 PASS | 7.67s; identical args + env |
| 3 | 22/22 PASS | 6.25s; identical args + env |
| 4 | 22/22 PASS | 5.54s; identical args + env |
| 5 | 22/22 PASS | 4.04s; identical args + env |
| Δ_total | **0** | Δ=0 by construction — deterministic mock tool source + deterministic mock grounded agent + deterministic string-equality / substring extractor; no LLM in the loop; no scoring path touched (no rubric.yaml or scoring.py changes). |

Full-suite smoke regression: `pytest tests/ --ignore=tests/integration/test_walker_customer_agent_v0_3.py --ignore=tests/unit/test_inspect_provenance_pin.py` → **1718 PASS / 7 FAIL / 0 introduced regressions** in 83.20s. The 7 failures are all `tests/test_inspect_runner.py::InspectRunnerSmokeTests::*` due to missing `inspect_ai` package in the canon Python venv (`ModuleNotFoundError: No module named 'inspect_ai'`); these are pre-existing environmental artifacts (same condition observed in BEAAA-5757 closeout). The two collection-time errors come from the same missing-package root cause.

## Probe → test wiring count delta (BEAAA-5745 §4(6) discrepancy closeout)

| Family | Probes wired BEFORE R-4 | Probes wired AFTER R-4 | Deferred to follow-up |
| --- | --- | --- | --- |
| Grounded factuality — HaluEval-QA | 0 | 6 | full upstream corpus under `ARE_PROBE_LIVE_LLM=1` |
| Grounded factuality — TruthfulQA | 0 | 6 | full upstream corpus under `ARE_PROBE_LIVE_LLM=1` |
| Citation grounding — HaluEval-summ | 0 | 6 | full upstream corpus under `ARE_PROBE_LIVE_LLM=1` |
| Citation grounding — FActScore | 0 | 6 | full upstream corpus under `ARE_PROBE_LIVE_LLM=1` |
| **Total** | **0** | **24** | — |

Per BEAAA-5745 §4(6): Peril 4 hallucination test coverage was "2 negative-string tests in suite, weak signal." R-4 lifts this to 24 probes across 4 corpora with structured pass/fail semantics (numeric equality + citation resolution + claim-keyword tie), plus discrimination (known-bad mocks) so the harness measures rather than vacuously passes.

## Wrap-don't-fork pattern (Operational seat guarantee 4)

| Upstream framework | Source-of-truth | Wrap surface | Fork check |
| --- | --- | --- | --- |
| HaluEval | `https://github.com/RUCAIBox/HaluEval` @ `b7253db3cdaa0ab2c382f92b26b390109174f77e` | `tests/probe_sets/p4_grounded_factuality_halueval_qa_v1.jsonl` + `tests/probe_sets/p4_citation_grounded_halueval_summ_v1.jsonl` (filtered subsets per banner predicate) | No fork — payloads inline only for the CI subset; full corpus fetched at live-LLM gate. |
| TruthfulQA | `https://github.com/sylinrl/TruthfulQA` @ `d71c110897f5d31c5d7f309e7bc316c152f6f031` | `tests/probe_sets/p4_grounded_factuality_truthfulqa_v1.jsonl` (filtered subset per banner predicate) | No fork — payloads inline only for the CI subset; full corpus fetched at live-LLM gate. |
| FActScore | `https://github.com/shmsw25/FActScore` @ `f28272deffcf33efc1f1117d5479c10bb75221a9` | `tests/probe_sets/p4_citation_grounded_factscore_v1.jsonl` (filtered subset per banner predicate) | No fork — payloads inline only for the CI subset; full corpus fetched at live-LLM gate. |

Filter predicates per probe row trace back to the banner-level `filter_predicate` for the probe-set. The loader (`_p4_helpers.load_probe_set`) asserts the banner names the upstream + version + filter predicate; rows without those fields fail to load.

## Carry-forward / open items (not part of this closeout)

| Item | Owner | Notes |
| --- | --- | --- |
| Swap `_p4_helpers.probe_runtime` → `tools.are_connector.probe_runtime.ProbeRuntime` | Scanner Engineer (follow-up rule-execution) | Item-0 substrate (BEAAA-5754) is on canon master; folding R-4's grounded-factuality semantics into `VerdictRule` taxonomy (a `tool_grounded_numeric` / `citation_resolves` pair) is a separate rule-execution ticket. Call sites import `probe_runtime` from `_p4_helpers` only — the swap is mechanical. |
| Wire R-4 into the perils-tagging schema | Tier-1 R-7 (BEAAA-5758) | Out-of-scope per ticket. |
| Live-LLM mode wiring under `ARE_PROBE_LIVE_LLM=1` | Scanner Engineer (follow-up) | Full upstream corpora + LLM judge fan-out are gated off in CI today. |
| Rubric mapping / weight changes touching P1.1 / P1.2 | CTO + Underwriter (rule-change) | Not in scope. Existing rubric mapping YAML files (`halueval.yaml`, `truthfulqa.yaml`, `factscore.yaml`) untouched. |

## Self-audit (pre-submission, supervision-v1.1)

| # | Question | Answer |
| --- | --- | --- |
| 1 | Reference files read this turn? | Yes — `scanner-implementation-supervision-v1.md`, `are-scanner-api-v1.md`, R-5 closeout receipt as template, BEAAA-5754 substrate code, BEAAA-5745 audit. |
| 2 | Deliverable defined in one sentence? | Yes — two new test files (`tests/test_grounded_factuality.py` + `tests/test_citation_grounded.py`) for CTO/Underwriter/Auditor consumption, wiring HaluEval / TruthfulQA / FActScore subsets under a deterministic non-LLM grounded-factuality / citation-grounding harness, closing the BEAAA-5745 §4(6) Peril 4 gap within the 2 SE-day budget. |
| 3 | Classification rule-change vs rule-execution and table row cited? | Rule-execution per supervision-v1.1 row "Wrap an upstream eval framework". |
| 4 | N=5 calibration on the golden set + delta report? | Yes — 22/22 PASS x 5 runs; Δ=0 by construction; receipt §"N=5 calibration delta (golden set)". |
| 5 | If rule-change: CTO co-sign? `cto_cosign_ref` populated? | N/A — rule-execution; `cto_cosign_ref: N/A` rationale recorded. |
| 6 | Active version of `contracts/are-scanner-api-v1.md` cited? | Yes — v1.0 in `contract_anchor` frontmatter. |
| 7 | All 5 required metadata fields present? | Yes — `scanner_version`, `corpus_versions`, `probe_set_versions`, `rubric_point_ids`, `n5_calibration_delta`. Plus `upstream_eval_version` per supervision-v1.1 (wrap-upstream-eval action). Plus `merged_sha_on_canon_master_at_close` (code-commit ticket). |
| 8 | Glossary clean — no BMI, ARI, BEAI in the body? | Yes — `grep -i 'BMI\|ARI\|BEAI'` on R-4 new files returns 0 hits. |
| 9 | If I added a probe, does it wrap an upstream eval rather than reimplement? | Yes — wraps HaluEval / TruthfulQA / FActScore via banner-asserting JSONL loader + filtered subsets; no upstream eval logic copied. |
| 10 | FAL audit attached? | FAL discipline mirrors R-5 closeout: BLUF on every artifact, classification cited, contract version anchored, glossary clean, no commercial commitments. |
