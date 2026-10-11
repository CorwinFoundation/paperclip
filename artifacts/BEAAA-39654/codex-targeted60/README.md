# Codex Kaltura targeted-60 evidence (redacted)

Input for BEAAA-39654 (Kaltura combined mini report). Run on 9 Oct 2026 by Codex (founder-directed, outside Paperclip) against the public Kaltura Genie browser adapter, fresh-thread reset, same target record as the 8 Oct Starter run (intake 491e0abd7709498c7b365c88f3cabe66 / scanner 522e64f9-785c-4be4-8249-52ad60571db3).

- `kaltura-findings-review-2026-10-09.md` - review of the original 10 Starter failures and the design of the 20 scenarios (KFR-S/I/U/P/F) with oracles.
- `kaltura-targeted-results-2026-10-09.md` - all 60 captured prompts, replies, verdicts and manual judgments; per-scenario table.
- `targeted60-redacted.json` - the 60 executions (start, finish, tests[]), canaries and emails replaced by placeholders.
- `targeted-adjudications.json`, `targeted-findings.json` - per-execution verdicts and the headline findings.

Totals: 37 pass, 11 fail, 9 inconclusive, 3 unverified. Treat as pack `codex-targeted-20@1`, K=3. Inconclusive and unverified are could-not-run, never passes. Synthetic canaries only; nothing here is a live secret.
