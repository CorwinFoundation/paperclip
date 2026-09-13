# BEAAA-30935: local Codex saved-response judge comparison

BLUF: Keep PR #417 as the current scheduled-judge candidate. Do not replace the scheduled judge with `codex_local` yet. In this offline challenger run, Codex agreed with 18 of 28 prior consensus labels (64.3%) and converted 7 of 10 prior `partial` labels to `pass`. That is too much label movement for a scoring-path switch.

## Scope and method

- Offline saved responses only; no target scans and no Anthropic or OpenRouter API calls.
- Source: `artifacts/beaaa-19422/final-adjudication-candidate-v1.json`, SHA-256 `383636dbe5a791f841bac4a286aa813f7c69cdfdfbdc44243c4117e6ae3abf70`.
- Deterministic label-balanced sample: 28 rows (10 prior pass, 10 partial, 8 fail). The source only contained 8 fail rows, so the sample stopped at 28.
- Challenger: Paperclip's installed `codex_local` path, Codex CLI 0.144.4, model `gpt-5.6-sol`, one vote, structured JSON output, read-only sandbox, no tools or web.
- Input SHA-256: `00dfd93ce762f70ae000e5ba8489c79f2d04c91006ef22e26b3925feefc2a6e7`.
- Output SHA-256: `51a376c62c5ce64a9d079277c18e9c0ef7c79275d867a26508ea8d5164280849`.
- CLI-reported usage: 39,996 tokens for 28 rows. This includes agent/session overhead and repeated rubric bytes, so it is an observed upper-bound implementation datum, not normalized API billing.

## Results

| Prior label | Codex pass | Codex partial | Codex fail | Agreement |
|---|---:|---:|---:|---:|
| pass | 10 | 0 | 0 | 10/10 |
| partial | 7 | 2 | 1 | 2/10 |
| fail | 2 | 0 | 6 | 6/8 |
| total | 19 | 2 | 7 | 18/28 (64.3%) |

Codex produced valid structured output for all 28 rows. The dominant error is leniency around partial credit. Ten disagreements were observed: eight prior partial rows moved (seven to pass and one to fail), and two prior fail rows moved to pass.

## Cost and routing recommendation

The existing 2026-09-08 OpenRouter bill is USD 19.60 for roughly 10,500 three-vote judge requests. PR #417's Anthropic-direct Batch API plus prompt caching estimate is USD 6-8 per weekly run. This run cannot establish a comparable `codex_local` dollar figure: Paperclip/Codex did not expose a per-run invoice price, and the agent CLI is not yet a scanner-grade batch endpoint with an evidenced cache/batch contract.

Recommendation:

1. Continue PR #417 as the scheduled-judge candidate, subject to CTO ownership reconciliation and the already-requested provider A/B evidence.
2. Keep `codex_local` as an offline challenger only. Do not merge, switch the schedule, or change scoring from this comparison.
3. If CTO selects Codex for a second evaluation, first build a judge-only adapter that supplies the exact materialized prompt, disables repository/tool context, records input/output/cache tokens, and runs the same three-vote semantics. Require agreement analysis by label, especially `partial`, before any cost comparison or route change.

This is evaluation evidence under `contracts/are-scanner-api-v1.md@v1.3.0`; it changes no Scanner state, corpus, probe set, rubric point, score, or scheduled provider.
