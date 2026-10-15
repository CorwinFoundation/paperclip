# BackBond Scanner Quality Report v1 (BEAAA-41114)

Report-only: `score_input=false`. No Starter, rubric, BackBond Score, API, lane, pack or family change.
Outcome counts are per **unique test** (counted once). `not_applicable` and `could_not_run` are never pass.
Wilson 95% intervals use the unique tests in that row as the denominator.

**Status: complete for both targets.**

## Versions
```json
{
 "report_commit": "877da09bd7e806632c733a90203476b35115cff9",
 "master_base": "e98c300218937d58494311fa6a1cd59c424cf97a",
 "conv_en_pack_ids": [
  "full-scan-conv-en@0.1",
  "full-scan-conv-en@0.2",
  "full-scan-conv-en@0.3",
  "full-scan-conv-en@0.4",
  "full-scan-conv-en@0.5",
  "full-scan-conv-en@0.6"
 ],
 "oss_pack_ids": [
  "full-scan-oss-jbb@1.0",
  "full-scan-oss-xstest@1.0",
  "full-scan-oss-strongreject@1.0",
  "full-scan-oss-harmbench@1.0",
  "full-scan-oss-multijail@1.0",
  "full-scan-oss-cyberseceval@1.0"
 ],
 "full_scan_u_corpus": "full_scan_e_action_second_wave@0.1.0-report-only",
 "full_scan_h_slice": "beaaa39491 --offset 500 --limit 813 --k 5",
 "reference_target": "openai/gpt-4.1-mini",
 "judge_models": [
  "openai/gpt-4.1-mini",
  "anthropic/claude-sonnet-4.6"
 ],
 "conv_seeds": [
  42,
  43,
  44
 ],
 "action_seeds": [
  0,
  1,
  2,
  3,
  4
 ],
 "score_input": false
}
```

## Retained attempt rows (reference target)
```json
{
 "conv-en": 7050,
 "oss": 5203,
 "full_scan_h": 4063,
 "full_scan_u": 1300
}
```

## Notes on method
- conv-en/OSS: K=3 seeds 42/43/44; pass^k means every seed passed; mixed pass/fail is flaky; any
  seed could_not_run makes the test could_not_run; two-judge disagreement is unresolved (fail closed).
- Full Scan H: an attempt whose injection was never delivered (the H runner exposes only E tools)
  is could_not_run `injection_not_delivered`, not pass. A unique test needs all 5 seeds decided, so
  a test with any undelivered seed is could_not_run. This is a runner coverage gap, not a target result.
- Method split: a test is `judged` if any retained attempt was decided by the two-judge panel
  (including a must_include judge fallback); unrun tests use their registered check type.
- VAPI target: the assistant's stored configuration is used unchanged, so the case system prompt
  is not delivered; canary cases whose canary exists only there are could_not_run
  `target_system_prompt_fixed`. VAPI takes no seed, so K=3 there is repetition. H/U are
  not_applicable on VAPI (chat-only, BEAAA-40217).

## VAPI spend and account balance
VAPI spend is the sum of each retained chat's `vapi_cost_usd` (per row in the XLSX attempt data).

- conv-en: $31.6769
- oss: $30.7819
- full_scan_h: $0.0000
- full_scan_u: $0.0000
- total: $62.4588

Account balance before/after: The bound VAPI private key cannot read organisation or subscription data, so no before/after account balance is available via the API. VAPI spend is reported only from per-chat cost fields retained per row (vapi_cost_usd). Probe (2026-10-15T14:01Z): `GET /assistant/09aea361-04b3-4a62-bcb0-2f2582f11ec8` → 200, `GET /org` → 401, `GET /org/{orgId}` → 401, `GET /subscription` → 404, `GET /org/{orgId}/subscription` → 404, `GET /org/{orgId}/credits` → 404, `GET /credit` → 404, `GET /billing` → 404, `GET /account` → 404, `GET /analytics` → 404

## Judge HTTP 402 outage (OpenRouter credit, 2026-10-15)
From about 15:15Z every judge call returned `HTTP 402 Payment Required`. These counts are
attempt rows affected, not a verdict bucket. Before re-judging they sit in `could_not_run` (both
judges failed) or `unresolved` (one judge failed next to an answered judge). Re-judged successors
replace them in every count above; `still_pending` must be 0 for a final report.
Re-judging re-sends each retained judge prompt to the same pinned model and uses the runner's own
parser, so a judge that answers twice in one reply (e.g. `fail`, then a self-revision) still fails
closed as `could-not-run`, exactly as in the original run; those count in `judge_could_not_run`.

| target | lane | rows_with_judge_402 | of_which_could_not_run | of_which_unresolved_mixed_panel | rejudged_successors | still_pending |
|---|---|---|---|---|---|---|
| openai/gpt-4.1-mini | conv-en | 0 | 0 | 0 | 0 | 0 |
| openai/gpt-4.1-mini | oss | 1 | 0 | 1 | 1 | 0 |
| vapi:09aea361 | conv-en | 2009 | 2009 | 0 | 2009 | 0 |
| vapi:09aea361 | oss | 432 | 394 | 38 | 432 | 0 |

## By target and lane
| target | lane | unique_tests | method_deterministic | method_judged | pass | pass_wilson95 | fail | fail_wilson95 | flaky | flaky_wilson95 | unresolved | unresolved_wilson95 | could_not_run | could_not_run_wilson95 | not_applicable | not_applicable_wilson95 | not_run | attempts | judged_attempts_two_judges | judge_agree | judge_unresolved | judge_could_not_run | openrouter_usd | vapi_usd |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| openai/gpt-4.1-mini | conv-en | 2350 | 891 | 1459 | 1434 | [0.590, 0.630] | 441 | [0.172, 0.204] | 63 | [0.021, 0.034] | 399 | [0.155, 0.186] | 13 | [0.003, 0.009] | 0 | [0.000, 0.002] | 0 | 7050 | 4351 | 3590 | 761 | 17 | 11.9408 | 0.0 |
| openai/gpt-4.1-mini | oss | 1734 | 0 | 1734 | 1241 | [0.694, 0.736] | 81 | [0.038, 0.058] | 11 | [0.004, 0.011] | 395 | [0.209, 0.248] | 6 | [0.002, 0.008] | 0 | [0.000, 0.002] | 0 | 5202 | 5011 | 4317 | 694 | 185 | 11.089 | 0.0 |
| openai/gpt-4.1-mini | full_scan_h | 813 | 813 | 0 | 0 | [0.000, 0.005] | 0 | [0.000, 0.005] | 0 | [0.000, 0.005] | 0 | [0.000, 0.005] | 813 | [0.995, 1.000] | 0 | [0.000, 0.005] | 0 | 4063 | 0 | 0 | 0 | 0 | 1.3647 | 0.0 |
| openai/gpt-4.1-mini | full_scan_u | 260 | 260 | 0 | 129 | [0.436, 0.557] | 91 | [0.295, 0.410] | 39 | [0.112, 0.198] | 0 | [0.000, 0.015] | 1 | [0.001, 0.021] | 0 | [0.000, 0.015] | 0 | 1300 | 0 | 0 | 0 | 0 | 1.171 | 0.0 |
| vapi:09aea361 | conv-en | 2350 | 891 | 1459 | 803 | [0.323, 0.361] | 606 | [0.241, 0.276] | 73 | [0.025, 0.039] | 415 | [0.162, 0.193] | 453 | [0.177, 0.209] | 0 | [0.000, 0.002] | 0 | 9441 | 4370 | 3508 | 862 | 7 | 10.1975 | 31.6769 |
| vapi:09aea361 | oss | 1734 | 0 | 1734 | 1237 | [0.692, 0.734] | 79 | [0.037, 0.056] | 20 | [0.007, 0.018] | 398 | [0.210, 0.250] | 0 | [0.000, 0.002] | 0 | [0.000, 0.002] | 0 | 5202 | 5047 | 4328 | 719 | 155 | 10.1012 | 30.7819 |
| vapi:09aea361 | full_scan_h | 813 | 813 | 0 | 0 | [0.000, 0.005] | 0 | [0.000, 0.005] | 0 | [0.000, 0.005] | 0 | [0.000, 0.005] | 0 | [0.000, 0.005] | 813 | [0.995, 1.000] | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| vapi:09aea361 | full_scan_u | 260 | 260 | 0 | 0 | [0.000, 0.015] | 0 | [0.000, 0.015] | 0 | [0.000, 0.015] | 0 | [0.000, 0.015] | 0 | [0.000, 0.015] | 260 | [0.985, 1.000] | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## By target and source
| target | source | unique_tests | method_deterministic | method_judged | pass | pass_wilson95 | fail | fail_wilson95 | flaky | flaky_wilson95 | unresolved | unresolved_wilson95 | could_not_run | could_not_run_wilson95 | not_applicable | not_applicable_wilson95 | not_run | attempts | judged_attempts_two_judges | judge_agree | judge_unresolved | judge_could_not_run | openrouter_usd | vapi_usd |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| openai/gpt-4.1-mini | hand-authored | 2350 | 891 | 1459 | 1434 | [0.590, 0.630] | 441 | [0.172, 0.204] | 63 | [0.021, 0.034] | 399 | [0.155, 0.186] | 13 | [0.003, 0.009] | 0 | [0.000, 0.002] | 0 | 7050 | 4351 | 3590 | 761 | 17 | 11.9408 | 0.0 |
| openai/gpt-4.1-mini | adapted:harmbench@8e1604d1171fe8a48d8febecd22f600e462bdcdd | 272 | 0 | 272 | 238 | [0.830, 0.909] | 3 | [0.004, 0.032] | 2 | [0.002, 0.026] | 28 | [0.072, 0.145] | 1 | [0.001, 0.021] | 0 | [0.000, 0.014] | 0 | 816 | 756 | 743 | 13 | 59 | 1.656 | 0.0 |
| openai/gpt-4.1-mini | adapted:jailbreakbench@886acc352a31533ffbcf4ef22c744658688086fc | 197 | 0 | 197 | 102 | [0.448, 0.587] | 16 | [0.051, 0.128] | 2 | [0.003, 0.036] | 76 | [0.321, 0.455] | 1 | [0.001, 0.028] | 0 | [0.000, 0.019] | 0 | 591 | 585 | 418 | 167 | 5 | 1.0603 | 0.0 |
| openai/gpt-4.1-mini | adapted:strongreject@f7cad6c17e624e21d8df2278e918ae1dddb4cb56 | 309 | 0 | 309 | 295 | [0.925, 0.973] | 0 | [0.000, 0.012] | 0 | [0.000, 0.012] | 12 | [0.022, 0.067] | 2 | [0.002, 0.023] | 0 | [0.000, 0.012] | 0 | 927 | 895 | 895 | 0 | 30 | 1.4876 | 0.0 |
| openai/gpt-4.1-mini | adapted:xstest@d7bb5bd738c1fcbc36edd83d5e7d1b71a3e2d84d | 441 | 0 | 441 | 207 | [0.423, 0.516] | 55 | [0.097, 0.159] | 6 | [0.006, 0.029] | 172 | [0.346, 0.436] | 1 | [0.000, 0.013] | 0 | [0.000, 0.009] | 0 | 1323 | 1309 | 960 | 349 | 13 | 2.3499 | 0.0 |
| openai/gpt-4.1-mini | adapted:multijail@577ac511437e07a256130945ef312a99158a18ad | 310 | 0 | 310 | 292 | [0.910, 0.963] | 1 | [0.001, 0.018] | 0 | [0.000, 0.012] | 16 | [0.032, 0.082] | 1 | [0.001, 0.018] | 0 | [0.000, 0.012] | 0 | 930 | 912 | 899 | 13 | 17 | 2.6546 | 0.0 |
| openai/gpt-4.1-mini | adapted:cyberseceval@172c1074069eb88ec834124272c1b1c4f8893445 | 205 | 0 | 205 | 107 | [0.454, 0.589] | 6 | [0.013, 0.062] | 1 | [0.001, 0.027] | 91 | [0.378, 0.512] | 0 | [0.000, 0.018] | 0 | [0.000, 0.018] | 0 | 615 | 554 | 402 | 152 | 61 | 1.8808 | 0.0 |
| openai/gpt-4.1-mini | AgentDojo | 135 | 135 | 0 | 0 | [0.000, 0.028] | 0 | [0.000, 0.028] | 0 | [0.000, 0.028] | 0 | [0.000, 0.028] | 135 | [0.972, 1.000] | 0 | [0.000, 0.028] | 0 | 675 | 0 | 0 | 0 | 0 | 0.2973 | 0.0 |
| openai/gpt-4.1-mini | InjecAgent | 678 | 678 | 0 | 0 | [0.000, 0.006] | 0 | [0.000, 0.006] | 0 | [0.000, 0.006] | 0 | [0.000, 0.006] | 678 | [0.994, 1.000] | 0 | [0.000, 0.006] | 0 | 3388 | 0 | 0 | 0 | 0 | 1.0675 | 0.0 |
| openai/gpt-4.1-mini | backbond_written_sandbox | 260 | 260 | 0 | 129 | [0.436, 0.557] | 91 | [0.295, 0.410] | 39 | [0.112, 0.198] | 0 | [0.000, 0.015] | 1 | [0.001, 0.021] | 0 | [0.000, 0.015] | 0 | 1300 | 0 | 0 | 0 | 0 | 1.171 | 0.0 |
| vapi:09aea361 | hand-authored | 2350 | 891 | 1459 | 803 | [0.323, 0.361] | 606 | [0.241, 0.276] | 73 | [0.025, 0.039] | 415 | [0.162, 0.193] | 453 | [0.177, 0.209] | 0 | [0.000, 0.002] | 0 | 9441 | 4370 | 3508 | 862 | 7 | 10.1975 | 31.6769 |
| vapi:09aea361 | adapted:harmbench@8e1604d1171fe8a48d8febecd22f600e462bdcdd | 272 | 0 | 272 | 236 | [0.822, 0.903] | 4 | [0.006, 0.037] | 3 | [0.004, 0.032] | 29 | [0.075, 0.149] | 0 | [0.000, 0.014] | 0 | [0.000, 0.014] | 0 | 816 | 756 | 736 | 20 | 60 | 1.4759 | 4.1091 |
| vapi:09aea361 | adapted:jailbreakbench@886acc352a31533ffbcf4ef22c744658688086fc | 197 | 0 | 197 | 115 | [0.514, 0.650] | 28 | [0.100, 0.198] | 2 | [0.003, 0.036] | 52 | [0.207, 0.330] | 0 | [0.000, 0.019] | 0 | [0.000, 0.019] | 0 | 591 | 588 | 479 | 109 | 3 | 1.0372 | 2.9658 |
| vapi:09aea361 | adapted:strongreject@f7cad6c17e624e21d8df2278e918ae1dddb4cb56 | 309 | 0 | 309 | 300 | [0.946, 0.985] | 0 | [0.000, 0.012] | 0 | [0.000, 0.012] | 9 | [0.015, 0.054] | 0 | [0.000, 0.012] | 0 | [0.000, 0.012] | 0 | 927 | 904 | 901 | 3 | 23 | 1.3322 | 4.6436 |
| vapi:09aea361 | adapted:xstest@d7bb5bd738c1fcbc36edd83d5e7d1b71a3e2d84d | 441 | 0 | 441 | 211 | [0.432, 0.525] | 45 | [0.077, 0.134] | 6 | [0.006, 0.029] | 179 | [0.361, 0.452] | 0 | [0.000, 0.009] | 0 | [0.000, 0.009] | 0 | 1323 | 1303 | 932 | 371 | 20 | 2.238 | 6.6363 |
| vapi:09aea361 | adapted:multijail@577ac511437e07a256130945ef312a99158a18ad | 310 | 0 | 310 | 259 | [0.790, 0.873] | 1 | [0.001, 0.018] | 8 | [0.013, 0.050] | 42 | [0.102, 0.178] | 0 | [0.000, 0.012] | 0 | [0.000, 0.012] | 0 | 930 | 923 | 862 | 61 | 7 | 2.3182 | 9.3304 |
| vapi:09aea361 | adapted:cyberseceval@172c1074069eb88ec834124272c1b1c4f8893445 | 205 | 0 | 205 | 116 | [0.497, 0.632] | 1 | [0.001, 0.027] | 1 | [0.001, 0.027] | 87 | [0.359, 0.493] | 0 | [0.000, 0.018] | 0 | [0.000, 0.018] | 0 | 615 | 573 | 418 | 155 | 42 | 1.6997 | 3.0967 |
| vapi:09aea361 | AgentDojo | 135 | 135 | 0 | 0 | [0.000, 0.028] | 0 | [0.000, 0.028] | 0 | [0.000, 0.028] | 0 | [0.000, 0.028] | 0 | [0.000, 0.028] | 135 | [0.972, 1.000] | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| vapi:09aea361 | InjecAgent | 678 | 678 | 0 | 0 | [0.000, 0.006] | 0 | [0.000, 0.006] | 0 | [0.000, 0.006] | 0 | [0.000, 0.006] | 0 | [0.000, 0.006] | 678 | [0.994, 1.000] | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| vapi:09aea361 | backbond_written_sandbox | 260 | 260 | 0 | 0 | [0.000, 0.015] | 0 | [0.000, 0.015] | 0 | [0.000, 0.015] | 0 | [0.000, 0.015] | 0 | [0.000, 0.015] | 260 | [0.985, 1.000] | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## By target, lane, family and source
| target | lane | family | source | unique_tests | method_deterministic | method_judged | pass | pass_wilson95 | fail | fail_wilson95 | flaky | flaky_wilson95 | unresolved | unresolved_wilson95 | could_not_run | could_not_run_wilson95 | not_applicable | not_applicable_wilson95 | not_run | attempts | judged_attempts_two_judges | judge_agree | judge_unresolved | judge_could_not_run | openrouter_usd | vapi_usd |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| openai/gpt-4.1-mini | conv-en | authentication | hand-authored | 219 | 0 | 219 | 161 | [0.673, 0.789] | 3 | [0.005, 0.039] | 0 | [0.000, 0.017] | 53 | [0.190, 0.303] | 2 | [0.003, 0.033] | 0 | [0.000, 0.017] | 0 | 657 | 653 | 541 | 112 | 2 | 1.489017 | 0.0 |
| openai/gpt-4.1-mini | conv-en | conduct | hand-authored | 234 | 0 | 234 | 113 | [0.420, 0.547] | 48 | [0.158, 0.261] | 4 | [0.007, 0.043] | 69 | [0.240, 0.356] | 0 | [0.000, 0.016] | 0 | [0.000, 0.016] | 0 | 702 | 700 | 570 | 130 | 2 | 1.641755 | 0.0 |
| openai/gpt-4.1-mini | conv-en | data_leak | hand-authored | 219 | 219 | 0 | 196 | [0.847, 0.929] | 19 | [0.056, 0.132] | 2 | [0.003, 0.033] | 0 | [0.000, 0.017] | 2 | [0.003, 0.033] | 0 | [0.000, 0.017] | 0 | 657 | 0 | 0 | 0 | 0 | 0.082016 | 0.0 |
| openai/gpt-4.1-mini | conv-en | fairness_pair | hand-authored | 199 | 0 | 199 | 70 | [0.289, 0.420] | 18 | [0.058, 0.138] | 12 | [0.035, 0.102] | 98 | [0.424, 0.561] | 1 | [0.001, 0.028] | 0 | [0.000, 0.019] | 0 | 597 | 596 | 427 | 169 | 0 | 2.561536 | 0.0 |
| openai/gpt-4.1-mini | conv-en | grounded_policy | hand-authored | 234 | 0 | 234 | 214 | [0.872, 0.944] | 1 | [0.001, 0.024] | 4 | [0.007, 0.043] | 12 | [0.030, 0.087] | 3 | [0.004, 0.037] | 0 | [0.000, 0.016] | 0 | 702 | 691 | 680 | 11 | 8 | 1.456733 | 0.0 |
| openai/gpt-4.1-mini | conv-en | hallucinated_action | hand-authored | 187 | 0 | 187 | 109 | [0.511, 0.651] | 19 | [0.066, 0.153] | 2 | [0.003, 0.038] | 57 | [0.243, 0.374] | 0 | [0.000, 0.020] | 0 | [0.000, 0.020] | 0 | 561 | 560 | 432 | 128 | 1 | 1.198232 | 0.0 |
| openai/gpt-4.1-mini | conv-en | over_refusal | hand-authored | 199 | 0 | 199 | 134 | [0.605, 0.735] | 15 | [0.046, 0.121] | 6 | [0.014, 0.064] | 42 | [0.160, 0.273] | 2 | [0.003, 0.036] | 0 | [0.000, 0.019] | 0 | 597 | 593 | 518 | 75 | 2 | 1.876987 | 0.0 |
| openai/gpt-4.1-mini | conv-en | prompt_injection_direct | hand-authored | 219 | 219 | 0 | 62 | [0.228, 0.346] | 148 | [0.611, 0.734] | 8 | [0.019, 0.070] | 0 | [0.000, 0.017] | 1 | [0.001, 0.025] | 0 | [0.000, 0.017] | 0 | 657 | 0 | 0 | 0 | 0 | 0.062341 | 0.0 |
| openai/gpt-4.1-mini | conv-en | prompt_injection_indirect | hand-authored | 219 | 219 | 0 | 123 | [0.495, 0.626] | 85 | [0.326, 0.454] | 11 | [0.028, 0.088] | 0 | [0.000, 0.017] | 0 | [0.000, 0.017] | 0 | [0.000, 0.017] | 0 | 657 | 0 | 0 | 0 | 0 | 0.083649 | 0.0 |
| openai/gpt-4.1-mini | conv-en | system_prompt_leak | hand-authored | 234 | 234 | 0 | 137 | [0.521, 0.647] | 83 | [0.296, 0.418] | 13 | [0.033, 0.093] | 0 | [0.000, 0.016] | 1 | [0.001, 0.024] | 0 | [0.000, 0.016] | 0 | 702 | 0 | 0 | 0 | 0 | 0.108525 | 0.0 |
| openai/gpt-4.1-mini | conv-en | tool_misuse | hand-authored | 187 | 0 | 187 | 115 | [0.544, 0.682] | 2 | [0.003, 0.038] | 1 | [0.001, 0.030] | 68 | [0.298, 0.435] | 1 | [0.001, 0.030] | 0 | [0.000, 0.020] | 0 | 561 | 558 | 422 | 136 | 2 | 1.379987 | 0.0 |
| openai/gpt-4.1-mini | oss | harmful_content | adapted:harmbench@8e1604d1171fe8a48d8febecd22f600e462bdcdd | 180 | 0 | 180 | 168 | [0.887, 0.961] | 0 | [0.000, 0.021] | 0 | [0.000, 0.021] | 12 | [0.039, 0.113] | 0 | [0.000, 0.021] | 0 | [0.000, 0.021] | 0 | 540 | 508 | 508 | 0 | 32 | 0.809549 | 0.0 |
| openai/gpt-4.1-mini | oss | harmful_content | adapted:jailbreakbench@886acc352a31533ffbcf4ef22c744658688086fc | 98 | 0 | 98 | 97 | [0.944, 0.998] | 0 | [0.000, 0.038] | 0 | [0.000, 0.038] | 0 | [0.000, 0.038] | 1 | [0.002, 0.056] | 0 | [0.000, 0.038] | 0 | 294 | 293 | 293 | 0 | 0 | 0.462155 | 0.0 |
| openai/gpt-4.1-mini | oss | harmful_content | adapted:strongreject@f7cad6c17e624e21d8df2278e918ae1dddb4cb56 | 309 | 0 | 309 | 295 | [0.925, 0.973] | 0 | [0.000, 0.012] | 0 | [0.000, 0.012] | 12 | [0.022, 0.067] | 2 | [0.002, 0.023] | 0 | [0.000, 0.012] | 0 | 927 | 895 | 895 | 0 | 30 | 1.487571 | 0.0 |
| openai/gpt-4.1-mini | oss | harmful_content | adapted:xstest@d7bb5bd738c1fcbc36edd83d5e7d1b71a3e2d84d | 191 | 0 | 191 | 184 | [0.926, 0.982] | 0 | [0.000, 0.020] | 0 | [0.000, 0.020] | 6 | [0.014, 0.067] | 1 | [0.001, 0.029] | 0 | [0.000, 0.020] | 0 | 573 | 560 | 560 | 0 | 12 | 0.898752 | 0.0 |
| openai/gpt-4.1-mini | oss | harmful_content_multilingual | adapted:multijail@577ac511437e07a256130945ef312a99158a18ad | 310 | 0 | 310 | 292 | [0.910, 0.963] | 1 | [0.001, 0.018] | 0 | [0.000, 0.012] | 16 | [0.032, 0.082] | 1 | [0.001, 0.018] | 0 | [0.000, 0.012] | 0 | 930 | 912 | 899 | 13 | 17 | 2.654552 | 0.0 |
| openai/gpt-4.1-mini | oss | over_refusal | adapted:jailbreakbench@886acc352a31533ffbcf4ef22c744658688086fc | 99 | 0 | 99 | 5 | [0.022, 0.113] | 16 | [0.102, 0.247] | 2 | [0.006, 0.071] | 76 | [0.675, 0.840] | 0 | [0.000, 0.037] | 0 | [0.000, 0.037] | 0 | 297 | 292 | 125 | 167 | 5 | 0.598145 | 0.0 |
| openai/gpt-4.1-mini | oss | over_refusal | adapted:xstest@d7bb5bd738c1fcbc36edd83d5e7d1b71a3e2d84d | 250 | 0 | 250 | 23 | [0.062, 0.134] | 55 | [0.173, 0.275] | 6 | [0.011, 0.051] | 166 | [0.603, 0.720] | 0 | [0.000, 0.015] | 0 | [0.000, 0.015] | 0 | 750 | 749 | 400 | 349 | 1 | 1.451146 | 0.0 |
| openai/gpt-4.1-mini | oss | prompt_injection_direct | adapted:cyberseceval@172c1074069eb88ec834124272c1b1c4f8893445 | 178 | 0 | 178 | 104 | [0.511, 0.654] | 6 | [0.016, 0.072] | 1 | [0.001, 0.031] | 67 | [0.309, 0.449] | 0 | [0.000, 0.021] | 0 | [0.000, 0.021] | 0 | 534 | 489 | 378 | 111 | 45 | 1.546694 | 0.0 |
| openai/gpt-4.1-mini | oss | prompt_injection_indirect | adapted:cyberseceval@172c1074069eb88ec834124272c1b1c4f8893445 | 27 | 0 | 27 | 3 | [0.039, 0.281] | 0 | [0.000, 0.125] | 0 | [0.000, 0.125] | 24 | [0.719, 0.961] | 0 | [0.000, 0.125] | 0 | [0.000, 0.125] | 0 | 81 | 65 | 24 | 41 | 16 | 0.334069 | 0.0 |
| openai/gpt-4.1-mini | oss | prompt_injection_indirect | adapted:harmbench@8e1604d1171fe8a48d8febecd22f600e462bdcdd | 92 | 0 | 92 | 70 | [0.664, 0.836] | 3 | [0.011, 0.092] | 2 | [0.006, 0.076] | 16 | [0.110, 0.264] | 1 | [0.002, 0.059] | 0 | [0.000, 0.040] | 0 | 276 | 248 | 235 | 13 | 27 | 0.846411 | 0.0 |
| openai/gpt-4.1-mini | full_scan_h | banking | AgentDojo | 71 | 71 | 0 | 0 | [0.000, 0.051] | 0 | [0.000, 0.051] | 0 | [0.000, 0.051] | 0 | [0.000, 0.051] | 71 | [0.949, 1.000] | 0 | [0.000, 0.051] | 0 | 355 | 0 | 0 | 0 | 0 | 0.138536 | 0.0 |
| openai/gpt-4.1-mini | full_scan_h | data_stealing | InjecAgent | 487 | 487 | 0 | 0 | [0.000, 0.008] | 0 | [0.000, 0.008] | 0 | [0.000, 0.008] | 0 | [0.000, 0.008] | 487 | [0.992, 1.000] | 0 | [0.000, 0.008] | 0 | 2434 | 0 | 0 | 0 | 0 | 0.767133 | 0.0 |
| openai/gpt-4.1-mini | full_scan_h | direct_harm | InjecAgent | 191 | 191 | 0 | 0 | [0.000, 0.020] | 0 | [0.000, 0.020] | 0 | [0.000, 0.020] | 0 | [0.000, 0.020] | 191 | [0.980, 1.000] | 0 | [0.000, 0.020] | 0 | 954 | 0 | 0 | 0 | 0 | 0.300338 | 0.0 |
| openai/gpt-4.1-mini | full_scan_h | travel | AgentDojo | 64 | 64 | 0 | 0 | [0.000, 0.057] | 0 | [0.000, 0.057] | 0 | [0.000, 0.057] | 0 | [0.000, 0.057] | 64 | [0.943, 1.000] | 0 | [0.000, 0.057] | 0 | 320 | 0 | 0 | 0 | 0 | 0.158729 | 0.0 |
| openai/gpt-4.1-mini | full_scan_u | confirmation | backbond_written_sandbox | 120 | 120 | 0 | 27 | [0.159, 0.308] | 71 | [0.502, 0.675] | 22 | [0.124, 0.262] | 0 | [0.000, 0.031] | 0 | [0.000, 0.031] | 0 | [0.000, 0.031] | 0 | 600 | 0 | 0 | 0 | 0 | 0.472096 | 0.0 |
| openai/gpt-4.1-mini | full_scan_u | parameter_tamper | backbond_written_sandbox | 80 | 80 | 0 | 42 | [0.417, 0.631] | 20 | [0.168, 0.355] | 17 | [0.137, 0.314] | 0 | [0.000, 0.046] | 1 | [0.002, 0.067] | 0 | [0.000, 0.046] | 0 | 400 | 0 | 0 | 0 | 0 | 0.22369 | 0.0 |
| openai/gpt-4.1-mini | full_scan_u | stop | backbond_written_sandbox | 60 | 60 | 0 | 60 | [0.940, 1.000] | 0 | [0.000, 0.060] | 0 | [0.000, 0.060] | 0 | [0.000, 0.060] | 0 | [0.000, 0.060] | 0 | [0.000, 0.060] | 0 | 300 | 0 | 0 | 0 | 0 | 0.475228 | 0.0 |
| vapi:09aea361 | conv-en | authentication | hand-authored | 219 | 0 | 219 | 163 | [0.683, 0.798] | 16 | [0.045, 0.115] | 1 | [0.001, 0.025] | 39 | [0.133, 0.234] | 0 | [0.000, 0.017] | 0 | [0.000, 0.017] | 0 | 1128 | 657 | 568 | 89 | 0 | 1.269498 | 3.2935 |
| vapi:09aea361 | conv-en | conduct | hand-authored | 234 | 0 | 234 | 37 | [0.117, 0.210] | 116 | [0.432, 0.559] | 8 | [0.017, 0.066] | 73 | [0.256, 0.374] | 0 | [0.000, 0.016] | 0 | [0.000, 0.016] | 0 | 1068 | 699 | 557 | 142 | 3 | 1.521675 | 3.5283 |
| vapi:09aea361 | conv-en | data_leak | hand-authored | 219 | 219 | 0 | 0 | [0.000, 0.017] | 0 | [0.000, 0.017] | 0 | [0.000, 0.017] | 0 | [0.000, 0.017] | 219 | [0.983, 1.000] | 0 | [0.000, 0.017] | 0 | 657 | 0 | 0 | 0 | 0 | 0.0 | 0.0 |
| vapi:09aea361 | conv-en | fairness_pair | hand-authored | 199 | 0 | 199 | 39 | [0.147, 0.257] | 43 | [0.165, 0.278] | 11 | [0.031, 0.096] | 106 | [0.463, 0.601] | 0 | [0.000, 0.019] | 0 | [0.000, 0.019] | 0 | 969 | 596 | 378 | 218 | 1 | 1.877779 | 6.0434 |
| vapi:09aea361 | conv-en | grounded_policy | hand-authored | 234 | 0 | 234 | 3 | [0.004, 0.037] | 216 | [0.882, 0.951] | 6 | [0.012, 0.055] | 9 | [0.020, 0.071] | 0 | [0.000, 0.016] | 0 | [0.000, 0.016] | 0 | 1188 | 701 | 688 | 13 | 1 | 1.684473 | 3.555 |
| vapi:09aea361 | conv-en | hallucinated_action | hand-authored | 187 | 0 | 187 | 87 | [0.395, 0.537] | 13 | [0.041, 0.115] | 6 | [0.015, 0.068] | 81 | [0.364, 0.505] | 0 | [0.000, 0.020] | 0 | [0.000, 0.020] | 0 | 589 | 560 | 390 | 170 | 1 | 1.142891 | 2.8124 |
| vapi:09aea361 | conv-en | over_refusal | hand-authored | 199 | 0 | 199 | 55 | [0.219, 0.342] | 94 | [0.404, 0.542] | 14 | [0.042, 0.115] | 36 | [0.134, 0.240] | 0 | [0.000, 0.019] | 0 | [0.000, 0.019] | 0 | 802 | 597 | 523 | 74 | 0 | 1.414782 | 3.0288 |
| vapi:09aea361 | conv-en | prompt_injection_direct | hand-authored | 219 | 219 | 0 | 155 | [0.644, 0.764] | 53 | [0.190, 0.303] | 11 | [0.028, 0.088] | 0 | [0.000, 0.017] | 0 | [0.000, 0.017] | 0 | [0.000, 0.017] | 0 | 663 | 0 | 0 | 0 | 0 | 0.0 | 3.2909 |
| vapi:09aea361 | conv-en | prompt_injection_indirect | hand-authored | 219 | 219 | 0 | 176 | [0.746, 0.851] | 29 | [0.094, 0.184] | 14 | [0.038, 0.104] | 0 | [0.000, 0.017] | 0 | [0.000, 0.017] | 0 | [0.000, 0.017] | 0 | 1033 | 0 | 0 | 0 | 0 | 0.0 | 3.3096 |
| vapi:09aea361 | conv-en | system_prompt_leak | hand-authored | 234 | 234 | 0 | 0 | [0.000, 0.016] | 0 | [0.000, 0.016] | 0 | [0.000, 0.016] | 0 | [0.000, 0.016] | 234 | [0.984, 1.000] | 0 | [0.000, 0.016] | 0 | 702 | 0 | 0 | 0 | 0 | 0.0 | 0.0 |
| vapi:09aea361 | conv-en | tool_misuse | hand-authored | 187 | 0 | 187 | 88 | [0.400, 0.542] | 26 | [0.097, 0.196] | 2 | [0.003, 0.038] | 71 | [0.313, 0.451] | 0 | [0.000, 0.020] | 0 | [0.000, 0.020] | 0 | 642 | 560 | 404 | 156 | 1 | 1.286427 | 2.815 |
| vapi:09aea361 | oss | harmful_content | adapted:harmbench@8e1604d1171fe8a48d8febecd22f600e462bdcdd | 180 | 0 | 180 | 165 | [0.867, 0.949] | 0 | [0.000, 0.021] | 2 | [0.003, 0.040] | 13 | [0.043, 0.120] | 0 | [0.000, 0.021] | 0 | [0.000, 0.021] | 0 | 540 | 504 | 503 | 1 | 36 | 0.736361 | 2.7043 |
| vapi:09aea361 | oss | harmful_content | adapted:jailbreakbench@886acc352a31533ffbcf4ef22c744658688086fc | 98 | 0 | 98 | 97 | [0.944, 0.998] | 0 | [0.000, 0.038] | 0 | [0.000, 0.038] | 1 | [0.002, 0.056] | 0 | [0.000, 0.038] | 0 | [0.000, 0.038] | 0 | 294 | 294 | 293 | 1 | 0 | 0.411858 | 1.4704 |
| vapi:09aea361 | oss | harmful_content | adapted:strongreject@f7cad6c17e624e21d8df2278e918ae1dddb4cb56 | 309 | 0 | 309 | 300 | [0.946, 0.985] | 0 | [0.000, 0.012] | 0 | [0.000, 0.012] | 9 | [0.015, 0.054] | 0 | [0.000, 0.012] | 0 | [0.000, 0.012] | 0 | 927 | 904 | 901 | 3 | 23 | 1.332204 | 4.6436 |
| vapi:09aea361 | oss | harmful_content | adapted:xstest@d7bb5bd738c1fcbc36edd83d5e7d1b71a3e2d84d | 191 | 0 | 191 | 186 | [0.940, 0.989] | 0 | [0.000, 0.020] | 0 | [0.000, 0.020] | 5 | [0.011, 0.060] | 0 | [0.000, 0.020] | 0 | [0.000, 0.020] | 0 | 573 | 564 | 562 | 2 | 9 | 0.804715 | 2.8685 |
| vapi:09aea361 | oss | harmful_content_multilingual | adapted:multijail@577ac511437e07a256130945ef312a99158a18ad | 310 | 0 | 310 | 259 | [0.790, 0.873] | 1 | [0.001, 0.018] | 8 | [0.013, 0.050] | 42 | [0.102, 0.178] | 0 | [0.000, 0.012] | 0 | [0.000, 0.012] | 0 | 930 | 923 | 862 | 61 | 7 | 2.318211 | 9.3304 |
| vapi:09aea361 | oss | over_refusal | adapted:jailbreakbench@886acc352a31533ffbcf4ef22c744658688086fc | 99 | 0 | 99 | 18 | [0.118, 0.269] | 28 | [0.204, 0.378] | 2 | [0.006, 0.071] | 51 | [0.418, 0.611] | 0 | [0.000, 0.037] | 0 | [0.000, 0.037] | 0 | 297 | 294 | 186 | 108 | 3 | 0.625329 | 1.4954 |
| vapi:09aea361 | oss | over_refusal | adapted:xstest@d7bb5bd738c1fcbc36edd83d5e7d1b71a3e2d84d | 250 | 0 | 250 | 25 | [0.069, 0.143] | 45 | [0.137, 0.232] | 6 | [0.011, 0.051] | 174 | [0.636, 0.750] | 0 | [0.000, 0.015] | 0 | [0.000, 0.015] | 0 | 750 | 739 | 370 | 369 | 11 | 1.433298 | 3.7678 |
| vapi:09aea361 | oss | prompt_injection_direct | adapted:cyberseceval@172c1074069eb88ec834124272c1b1c4f8893445 | 178 | 0 | 178 | 111 | [0.551, 0.691] | 1 | [0.001, 0.031] | 1 | [0.001, 0.031] | 65 | [0.298, 0.438] | 0 | [0.000, 0.021] | 0 | [0.000, 0.021] | 0 | 534 | 505 | 394 | 111 | 29 | 1.446762 | 2.6849 |
| vapi:09aea361 | oss | prompt_injection_indirect | adapted:cyberseceval@172c1074069eb88ec834124272c1b1c4f8893445 | 27 | 0 | 27 | 5 | [0.082, 0.367] | 0 | [0.000, 0.125] | 0 | [0.000, 0.125] | 22 | [0.633, 0.918] | 0 | [0.000, 0.125] | 0 | [0.000, 0.125] | 0 | 81 | 68 | 24 | 44 | 13 | 0.252981 | 0.4118 |
| vapi:09aea361 | oss | prompt_injection_indirect | adapted:harmbench@8e1604d1171fe8a48d8febecd22f600e462bdcdd | 92 | 0 | 92 | 71 | [0.676, 0.846] | 4 | [0.017, 0.107] | 1 | [0.002, 0.059] | 16 | [0.110, 0.264] | 0 | [0.000, 0.040] | 0 | [0.000, 0.040] | 0 | 276 | 252 | 233 | 19 | 24 | 0.739501 | 1.4048 |
| vapi:09aea361 | full_scan_h | banking | AgentDojo | 71 | 71 | 0 | 0 | [0.000, 0.051] | 0 | [0.000, 0.051] | 0 | [0.000, 0.051] | 0 | [0.000, 0.051] | 0 | [0.000, 0.051] | 71 | [0.949, 1.000] | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| vapi:09aea361 | full_scan_h | data_stealing | InjecAgent | 487 | 487 | 0 | 0 | [0.000, 0.008] | 0 | [0.000, 0.008] | 0 | [0.000, 0.008] | 0 | [0.000, 0.008] | 0 | [0.000, 0.008] | 487 | [0.992, 1.000] | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| vapi:09aea361 | full_scan_h | direct_harm | InjecAgent | 191 | 191 | 0 | 0 | [0.000, 0.020] | 0 | [0.000, 0.020] | 0 | [0.000, 0.020] | 0 | [0.000, 0.020] | 0 | [0.000, 0.020] | 191 | [0.980, 1.000] | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| vapi:09aea361 | full_scan_h | travel | AgentDojo | 64 | 64 | 0 | 0 | [0.000, 0.057] | 0 | [0.000, 0.057] | 0 | [0.000, 0.057] | 0 | [0.000, 0.057] | 0 | [0.000, 0.057] | 64 | [0.943, 1.000] | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| vapi:09aea361 | full_scan_u | confirmation | backbond_written_sandbox | 120 | 120 | 0 | 0 | [0.000, 0.031] | 0 | [0.000, 0.031] | 0 | [0.000, 0.031] | 0 | [0.000, 0.031] | 0 | [0.000, 0.031] | 120 | [0.969, 1.000] | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| vapi:09aea361 | full_scan_u | parameter_tamper | backbond_written_sandbox | 80 | 80 | 0 | 0 | [0.000, 0.046] | 0 | [0.000, 0.046] | 0 | [0.000, 0.046] | 0 | [0.000, 0.046] | 0 | [0.000, 0.046] | 80 | [0.954, 1.000] | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| vapi:09aea361 | full_scan_u | stop | backbond_written_sandbox | 60 | 60 | 0 | 0 | [0.000, 0.060] | 0 | [0.000, 0.060] | 0 | [0.000, 0.060] | 0 | [0.000, 0.060] | 0 | [0.000, 0.060] | 60 | [0.940, 1.000] | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Reproduction
```sh
# reference target (OpenRouter openai/gpt-4.1-mini), retained rows resume with no repeat paid calls
python3 scripts/beaaa38922_conv_live_run.py --corpus conv-en --seeds 42 43 44 --cap-usd 16 --workers 12 --out artifacts/BEAAA-41114/full-run/ref_openai_gpt-4.1-mini/conv-en
python3 scripts/beaaa38922_conv_live_run.py --corpus oss --seeds 42 43 44 --cap-usd 12 --workers 12 --out artifacts/BEAAA-41114/full-run/ref_openai_gpt-4.1-mini/oss
PYTHONPATH=. python3 scripts/beaaa39491_full_scan_h_live_run.py --offset 500 --limit 813 --k 5 --cap-usd 8 --workers 8 --out artifacts/BEAAA-41114/full-run/ref_openai_gpt-4.1-mini/full_scan_h
PYTHONPATH=. python3 scripts/beaaa41114_full_scan_u_live_run.py --k 5 --cap-usd 4 --workers 8 --resume --out artifacts/BEAAA-41114/full-run/ref_openai_gpt-4.1-mini/full_scan_u
# VAPI target (BEAAA-39144 assistant, chat-only; H/U emitted not_applicable)
VAPI_API_KEY=... python3 scripts/beaaa38922_conv_live_run.py --adapter vapi --vapi-assistant-id <uuid> --corpus conv-en --seeds 42 43 44 --out artifacts/BEAAA-41114/full-run/vapi_<id8>/conv-en
VAPI_API_KEY=... python3 scripts/beaaa38922_conv_live_run.py --adapter vapi --vapi-assistant-id <uuid> --corpus oss --seeds 42 43 44 --out artifacts/BEAAA-41114/full-run/vapi_<id8>/oss
# report
python3 scripts/beaaa41114_quality_report.py --out artifacts/BEAAA-41114/report
```
