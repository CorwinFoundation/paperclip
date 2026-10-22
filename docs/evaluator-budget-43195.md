# Evaluator budget integration, BEAAA-43195

Ordinary starter-100@4 traffic has no new private storage dependency and does not require SCANNER_EVALUATOR_BUDGET_DB. The standalone EvaluatorBudget component is tested but is not wired into production.

Controlled one-use execution uses the shared controlled permit ledger at ARE_CONTROLLED_PERMIT_DB on owner-managed persistent storage. Its fixed USD10 cap covers preflight, target and judge calls together, with at most 100 tests. Every paid call requires an atomic reservation before HTTP; unknown pricing or insufficient remaining budget stops execution before the call. All workers for the permit must share the same protected SQLite database on a filesystem supporting SQLite locks. Missing or unsafe controlled permit storage fails closed for controlled execution.

Reservations are never refunded, including success, provider errors, malformed output, timeouts and process termination. Consumed or unknown one-use state survives restart and does not authorize a retry or reset. The standalone EvaluatorBudget tests separately exercise atomic per-scan evaluator reservations; those tests do not establish production wiring.

The pinned openai/gpt-4.1-mini request uses 512 output tokens maximum. UTF-8 byte length plus 1024 framing tokens bounds input tokens; input over 32768 tokens fails closed. OpenRouter provider max_price ceilings are USD1/million input, USD4/million output, USD0 per request, with parameter support required and fallback disabled. The reservation doubles that token-cost bound for conservative overhead. Unknown model identity is rejected. These ceilings constrain provider selection; they do not authorize another model, pack or target.

Provider contract reference: https://openrouter.ai/docs/guides/routing/provider-selection (max_price, require_parameters, allow_fallbacks). The implementation relies on the provider honoring those billing and output limits. Do not install or dispatch until review validates the exact runtime wiring and owner persistent-storage configuration. Target/evaluator dispatches during implementation: 0.

The focused workflow runs only on backbond-ci. Synthetic HTTP mocks exercise reserve-before-call, retained unknown results, restart, concurrency, exhaustion, unknown-model rejection, overflow, output and price ceilings. CI receipts must be attached before release. Archive/operator packet regeneration remains required after the reviewed head is fixed.
