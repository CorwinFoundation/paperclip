# ADR-0043 — Standard Agent Interface (SAI) v1: multi-turn + tool-call visibility

- **Status:** accepted (CTO, BEAAA-11712)
- **Date:** 2026-07-04
- **Ticket:** BEAAA-11712 (CI/CD WS-2) — parent BEAAA-11710, CTO Directive v2.2 §4 Step 1
- **Contract:** `contracts/standard-agent-interface-v1.md`

## Context

The probe transport is `ModelRunner.complete(prompt) -> str` — single-turn, text-in/text-out (`harness/runner.py`). Two structural limits block our highest-value perils:

1. **No state.** Transaction-boundary injection needs multi-turn setup; a stateless string call cannot express it.
2. **No tool visibility.** Unauthorized actions, exfiltration, and side-effecting tool misuse are invisible in chat text. Today the tool-misuse fixtures (`tests/fixtures/r2_rob/tool_misuse_*`) carry hand-authored labels (`high_sev_disclosure`) with no machine-observable trace behind them — a "trust me" gap against our Supreme Law (every point backed by a reproducible artifact).

## Decision

Add an **additive** transport layer, the Standard Agent Interface (SAI), above the existing egress path. It does **not** modify `ModelRunner`, any scorer, the rubric, judges, or the `are-scanner-api-v1` output envelope.

- `AgentSession` ABC: stateful, `send(message, tool_results=) -> AgentTurn`, `trace() -> ConversationTrace`.
- Two tool-visibility modes: `SCANNER_MEDIATED` (scanner is the tool runtime; observes calls at invocation time; fail-closed allowlist authorization) and `AGENT_REPORTED` (agent self-reports).
- Structured, JSON-serialisable trace types. `ToolCall.action_class` is constrained to the `evidence-profile-v1` enum so scan-time and post-bind runtime evidence share one vocabulary.
- Backward-compat shims to `StopInstructionEvent` and `r2_rob_common` fixture shapes so existing scorers consume traces unchanged.
- Reference impl `harness/agent_interface.py`; deterministic `MockAgentSession` is the network-free staging agent for tests and the WS-3 mock harness; `HTTPAgentSession` is the OpenAI-tools-compatible live transport.

## Alternatives considered

1. **Extend `ModelRunner` with a `complete_turn(history, session_id)` method.** Rejected: forces every one of the ~11 existing runners to grow tool semantics they don't need, and couples single-turn scoring to multi-turn state. The SAI is a *separate* abstraction that composes with the transport rather than mutating it.
2. **Reuse Evidence-Profile-v1 spans as the only trace format.** Rejected as the *primary* interface: Evidence-Profile is the post-bind runtime telemetry contract (Collector-derived, hash-anchored). Scan-time probing needs a driver-controlled, synchronous request/response loop. We *reuse its `action_class` vocabulary* for interop but keep a probe-time trace type.
3. **Parse tool calls out of chat text heuristically.** Rejected: not reproducible, not tamper-resistant, and exactly the "guess from text" failure the ticket calls out.

## Consequences

- **Reproducibility / golden invariance.** Nothing in the scoring path imports the module (verified); golden N=5 delta = 0 by construction. No Underwriter approval gate is triggered by SAI v1 itself. The standing N=5 procedure fires only on changes that *could* move scores — this cannot.
- **Approval gates deferred, not skipped.** Wiring the SAI to a new rubric scorer, or activating it on the `/scanner/v1/scans` surface, is a separate change requiring Underwriter co-sign (and CEO for any pricing/weight impact) per `scanner-implementation-supervision-v1@v1.0`.
- **Tamper resistance.** `ToolSandbox` is default-deny and side-effect-free; captured tool arguments are never executed.
- **Unblocks WS-3.** The mock harness builds on `MockAgentSession` + `SessionProbeDriver` + `ProbeScript` with no further interface change.

## Follow-up (delegated)

- **Live-endpoint enablement** — exercise `HTTPAgentSession` against a real remote staging agent (creds + isolated egress). Child issue.
- **Session-mode upgrade** of `BotpressChatRunner` / `VoiceflowRunner` into `AgentSession` adapters (they already hold per-instance session state internally).
- **Tool-aware scorer** consuming `all_unauthorized_calls()` — separate, Underwriter-co-signed.
