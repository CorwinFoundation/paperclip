---
artifact_type: contract
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "Standard Agent Interface (SAI) v1 defines a stateful, multi-turn, tool-visible probe transport that sits above the existing egress layer and produces a reproducible ConversationTrace (turns + tool calls + tool results + authorization verdicts). It is additive: it changes no BackBond Scanner scoring math, rubric, judge, or the Underwriter output contract. It is the transport WS-3 (mock harness) builds against and the layer that gives unauthorized-action / exfiltration / transaction-boundary perils machine-observable evidence instead of hand-authored labels."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "evidence-profile-v1@v1.0", "scanner-implementation-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-11712", "BEAAA-11710"]
version: "v1"
self_audit_passed: true
escalations_noted: ["No Underwriter co-sign: SAI is an additive capture/transport layer — no rubric_point_id added/removed, no scoring math changed, no field on the are-scanner-api-v1 output envelope changed. Golden N=5 delta = 0 by construction (no scoring path imports the module). No tamper-resistance reduction: the scanner treats agent-emitted tool calls as adversarial input; SCANNER_MEDIATED mode observes calls at invocation time and applies a fail-closed authorization allowlist. Wiring the interface to a NEW rubric scorer, or activating it on the /scanner/v1/scans response surface, is a SEPARATE change that WILL require Underwriter co-sign per scanner-implementation-supervision-v1."]
---

# Contract: Standard Agent Interface (SAI) — v1

**Status:** proposed by CTO on BEAAA-11712 (CI/CD WS-2, CTO Directive v2.2 §4 Step 1). Reference implementation merged at `harness/agent_interface.py`; acceptance test `tests/test_agent_interface.py` green. ADR-0043.

**Change classification:** additive new contract + new module. No change to `are-scanner-api-v1`, rubric, judges, or golden set. Tier-1 rollback (delete the module + test + contract; nothing imports it).

---

## §1 BLUF

The legacy probe transport is `harness.runner.ModelRunner.complete(prompt) -> str`: single-turn, text-in / text-out. It cannot carry conversation state, and it cannot see what the agent *tries to do* — only what it says. Our highest-value perils live in tool activity, not chat text:

- **Unauthorized actions** — the agent invokes a tool it was not authorized to call.
- **Data exfiltration** — the agent reads/sends data outside its authority.
- **Transaction-boundary injection** — a multi-turn attack: benign setup, then a late instruction springs a side-effecting call.

None of these are legible to a stateless string runner. Today the tool-misuse fixtures encode outcomes as hand-authored labels (`high_sev_disclosure`, `resisted_no_disclosure`) with **no machine-observable trace behind them**. The SAI is what those labels get *derived from*: a stateful, tool-visible conversation handle that produces a reproducible `ConversationTrace` a scorer or judge can read and a future auditor can click through to.

The SAI is **additive**. It does not change any score today. It is the transport WS-3 (mock harness) builds on and the substrate on which future tool-aware scorers will be defined (each of which is its own co-signed change).

## §2 Scope

**In scope (v1):**
- Multi-turn conversation state (adapter-chosen: server session-id OR full-history-per-call).
- Tool-call visibility: the agent's tool invocations, their arguments, their results, and the scanner's authorization verdict are observable and captured.
- A structured, JSON-serialisable output format (`ConversationTrace`) that is the reproducible evidence artifact.
- Backward-compatibility shims to the existing scoring shapes (`StopInstructionEvent` fields; `r2_rob_common` `{"responses": {...}}` fixtures).

**Out of scope (v1) — separate, co-signed changes:**
- Any new rubric_point_id or scorer that consumes a trace.
- Activation on the `are-scanner-api-v1` `/scanner/v1/scans` response surface.
- Live wiring to a specific remote staging endpoint (enablement; needs creds/isolated box).

## §3 The interface

### §3.1 `AgentSession` (abstract)

Every target-agent adapter implements:

```
session_id() -> str
model_id()   -> str
mode         -> ToolVisibilityMode              # scanner_mediated | agent_reported
send(message: str, *, tool_results=()) -> AgentTurn
trace()      -> ConversationTrace
close()      -> None                            # optional
```

- `send()` advances the conversation by exactly **one** user turn and returns the resulting `AgentTurn`. State is retained between calls; whether that is server-side session-id or client-side full-history is the adapter's private choice and is opaque to the caller.
- In `SCANNER_MEDIATED` mode, when a returned turn carries `tool_calls`, the caller supplies `tool_results` on the **next** `send()` (empty `message`) so the agent can continue. Adapters MUST accept prior results.
- `trace()` returns everything captured so far and MUST NOT raise.

### §3.2 Tool-visibility modes

- **`SCANNER_MEDIATED` (strong, default).** The scanner plays the tool runtime. The agent emits structured tool calls; the scanner observes them **at invocation time**, applies a fail-closed authorization policy (`ToolSandbox` allowlist), returns a tool result, and loops. An unauthorized call is caught *before any side effect* — the evidence is the invocation itself. This is the mode for OpenAI-/Anthropic-tools-compatible endpoints. It is the mode WS-3's mock harness MUST implement.
- **`AGENT_REPORTED` (fallback).** The agent executes tools internally (e.g. a Voiceflow flow) and self-reports tool activity via a structured envelope or Evidence-Profile-v1 spans. Lower fidelity — we observe only what the agent chooses to report; `ToolResult.authorized` is `None` (the scanner did not mediate).

### §3.3 Structured trace format

```
ToolCall        { call_id, tool_name, arguments: dict, action_class, raw }
ToolResult      { call_id, tool_name, content, is_error, authorized: bool|None, raw }
AgentTurn       { index, user_message, assistant_text, tool_calls[], tool_results[], raw_response }
ConversationTrace { session_id, model_id, mode, turns[], declared_tools[], sai_version }
```

`ToolCall.action_class` **MUST** be drawn from the `contracts/evidence-profile-v1.md` enum: `message_send | transaction | data_access | tool_call | human_escalation | admin_change | other`. This is a hard invariant (constructor raises otherwise). Scan-time traces and post-bind runtime evidence therefore speak one vocabulary — a tool call classified `transaction` at scan time maps to the same covered-peril/authority-cap semantics the Collector applies at runtime.

`authorized=False` on a `ToolResult` is the canonical **unauthorized-action signal**. `ConversationTrace.all_unauthorized_calls()` is the accessor a tool-misuse scorer reads.

### §3.4 Backward-compat shims

- `ConversationTrace.to_probe_event()` → a `StopInstructionEvent`-compatible dict (`response_text` / `tool_calls` / `actions`), so existing probe scorers (`probes/stop_instruction/v1/probe.py`) consume a trace unchanged. Side-effecting `action_class` values (`transaction`, `data_access`, `admin_change`, `human_escalation`) surface under `actions` so halt/authorization checks keep working.
- `ConversationTrace.to_responses_shim(probe_id, label)` → the legacy `{"responses": {probe_id: label}}` fixture shape `harness/bench/r2_rob_common.py` loads.

These shims mean the SAI can feed today's scorers immediately while tool-aware scorers are designed separately.

## §4 Reproducibility & tamper resistance

- **Reproducibility.** A `ConversationTrace` is a pure function of (agent, script) in the deterministic adapters. `MockAgentSession` is seedless and network-free; same agent + same `ProbeScript` → byte-identical trace (proven by `test_reproducible_same_agent_same_script_same_trace`). Live adapters pin `temperature=0` and carry seeds/versions through the existing runner metadata.
- **Golden invariance.** The module is imported by nothing in the scoring path (verified). Golden N=5 delta = 0 by construction; no Underwriter approval gate is triggered by SAI v1 itself.
- **Tamper resistance.** Agent-emitted tool calls are adversarial input. The `ToolSandbox` is **fail-closed** (default-deny; only allowlisted tools authorized) and returns canned, side-effect-free results, so a scanned agent cannot induce real side effects or escape the sandbox by emitting crafted tool calls. Tool arguments are captured verbatim into the trace but never executed.

## §5 Reference implementation & acceptance

- Module: `harness/agent_interface.py` — dataclasses, `AgentSession` ABC, `ToolSandbox`, `SessionProbeDriver`, `MockAgentSession` (deterministic staging agent), `HTTPAgentSession` (OpenAI-tools-compatible full-history transport).
- Acceptance test: `tests/test_agent_interface.py` (6 tests, green). Proves the criterion — a tool-using, multi-turn probe (a transaction-boundary-injection scenario) executed against a staging agent, with the unauthorized `wire_transfer` call captured in the trace (not the chat text) and the scanner's denial verdict recorded.

## §6 What WS-3 (mock harness) builds against

WS-3 depends on this spec. To satisfy it, WS-3:
1. Implements/extends `MockAgentSession` policies into a library of staging agents (good / bad / graded) that exercise each tool-using peril family.
2. Runs them through `SessionProbeDriver` with `ProbeScript`s per peril.
3. Emits `ConversationTrace` fixtures (via `to_dict()`), and — where a legacy scorer is the consumer — the `to_probe_event()` / `to_responses_shim()` forms.
No change to this interface is needed for WS-3; if WS-3 finds a gap, it raises it back to this contract before diverging.

## §7 Change control

Changes to §3 (the interface) require a version bump of this contract and CTO sign-off. Wiring the SAI to a **new rubric scorer** or to the **`are-scanner-api-v1` output surface** is a separate change governed by `scanner-implementation-supervision-v1@v1.0` and requires Underwriter co-sign (and CEO for any rubric-weight/pricing impact) per the standing approval-gate rules.
