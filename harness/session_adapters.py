"""Botpress & Voiceflow AgentSession adapters — SAI v1 (BEAAA-11731).

Follow-up to BEAAA-11728 (SAI v1 live-endpoint enablement). The HTTPAgentSession
live-transport half landed on master (cdcf823, ADR-0044). This module is the
remaining half: it upgrades the single-turn, text-in/text-out
`BotpressChatRunner` / `VoiceflowRunner` (harness/runner.py) into multi-turn,
tool-visible `AgentSession` adapters that emit a `ConversationTrace`
(harness/agent_interface.py, ADR-0043).

MODE — AGENT_REPORTED (honest, not faked)
-----------------------------------------
Both target platforms execute their tools INTERNALLY and only report user-facing
messages (Botpress) or a per-turn trace array (Voiceflow) back over the wire.
That is precisely the SAI `AGENT_REPORTED` fallback tier (agent_interface.py
docstring): we surface whatever tool/action visibility the payload actually
exposes and nothing we cannot observe. Where a payload carries no tool signal,
`declared_tools=()` and `tool_calls` stay empty — an honest empty, not a
fabricated call. Because we do not mediate the tool runtime, any surfaced
ToolCall has no authorization verdict (no ToolResult is synthesised).

TRANSPORT INJECTION
-------------------
Mirrors HTTPAgentSession's `poster` seam so unit tests exercise the full
protocol with NO live endpoint: `BotpressAgentSession` takes a `client_factory`,
`VoiceflowAgentSession` takes a `poster`, both defaulting to the
`harness.egress` transport. The adapters REUSE the runners' protocol via
`complete_observed()`; they do not re-implement the 4-step Botpress flow or the
Voiceflow interact call.

Additive transport/adapter code only: no rubric, judge, corpus, or scoring
engine file is touched, so the golden N=5 delta is 0 by construction.
"""
from __future__ import annotations

import secrets
from typing import Any, Callable, Sequence

from harness.agent_interface import (
    AgentSession,
    AgentTurn,
    ConversationTrace,
    ToolCall,
    ToolResult,
    ToolVisibilityMode,
)
from harness.runner import BotpressChatRunner, VoiceflowRunner

# --------------------------------------------------------------------------- #
# Payload -> ToolCall mapping. Deliberately conservative: only payload/trace    #
# shapes that unambiguously denote an agent-reported tool/action become a       #
# ToolCall. Plain conversational text never does. All observed calls are        #
# action_class="tool_call" (generic, non-side-effecting) unless the payload     #
# self-declares a richer evidence-profile-v1 action_class.                      #
# --------------------------------------------------------------------------- #
from harness.agent_interface import ACTION_CLASSES

# Botpress public Chat API bot-message payload types that denote a tool/action
# rather than a user-facing message. Standard text/image/card payloads are NOT
# tool signals, so a normal chat reply yields an empty tool_calls tuple.
_BOTPRESS_TOOL_PAYLOAD_TYPES = frozenset({"action", "trigger", "tool", "function"})

# Voiceflow trace `type` values that denote tool/function execution the flow
# chose to report. `debug` traces are inspected for an embedded tool name.
_VOICEFLOW_TOOL_TRACE_TYPES = frozenset({"function", "tool", "action"})


def _coerce_action_class(payload: dict[str, Any]) -> str:
    ac = payload.get("action_class")
    return ac if ac in ACTION_CLASSES else "tool_call"


def _botpress_tool_calls(bot_msgs: Sequence[dict[str, Any]], turn_index: int) -> tuple[ToolCall, ...]:
    calls: list[ToolCall] = []
    for i, m in enumerate(bot_msgs):
        payload = m.get("payload", {}) or {}
        if payload.get("type") in _BOTPRESS_TOOL_PAYLOAD_TYPES:
            name = payload.get("name") or payload.get("action") or payload.get("type")
            args = payload.get("arguments")
            if not isinstance(args, dict):
                args = {k: v for k, v in payload.items() if k not in ("type",)}
            calls.append(ToolCall(
                call_id=str(m.get("id") or f"bp-{turn_index}-{i}"),
                tool_name=str(name),
                arguments=args,
                action_class=_coerce_action_class(payload),
                raw=m,
            ))
    return tuple(calls)


def _voiceflow_tool_calls(traces: Sequence[dict[str, Any]], turn_index: int) -> tuple[ToolCall, ...]:
    calls: list[ToolCall] = []
    for i, t in enumerate(traces):
        ttype = t.get("type")
        payload = t.get("payload", {}) or {}
        is_tool = ttype in _VOICEFLOW_TOOL_TRACE_TYPES
        is_tool_debug = ttype == "debug" and (
            payload.get("name") or payload.get("tool") or payload.get("type") == "function"
        )
        if not (is_tool or is_tool_debug):
            continue
        name = payload.get("name") or payload.get("tool") or ttype
        args = payload.get("arguments")
        if not isinstance(args, dict):
            args = {k: v for k, v in payload.items() if k not in ("type",)}
        calls.append(ToolCall(
            call_id=str(t.get("id") or payload.get("id") or f"vf-{turn_index}-{i}"),
            tool_name=str(name),
            arguments=args,
            action_class=_coerce_action_class(payload),
            raw=t,
        ))
    return tuple(calls)


# --------------------------------------------------------------------------- #
# Botpress adapter                                                              #
# --------------------------------------------------------------------------- #
class BotpressAgentSession(AgentSession):
    """AGENT_REPORTED AgentSession over BotpressChatRunner's 4-step Chat protocol.

    Each `send()` drives one full Botpress exchange (fresh conversation, reused
    user identity) via the wrapped runner and records an `AgentTurn`. Any bot
    message payload whose type denotes a tool/action is surfaced as an observed
    ToolCall; plain text replies yield an empty `tool_calls`.
    """

    def __init__(self, *, client_id: str, label: str | None = None,
                 timeout_s: float = 30.0, poll_max_s: float = 30.0,
                 poll_interval_s: float = 0.5,
                 declared_tools: Sequence[str] = (),
                 client_factory: Callable[..., Any] | None = None,
                 session_id: str | None = None):
        self._runner = BotpressChatRunner(
            client_id=client_id, label=label, timeout_s=timeout_s,
            poll_max_s=poll_max_s, poll_interval_s=poll_interval_s,
            client_factory=client_factory,
        )
        self._declared = tuple(declared_tools)
        self._sid = session_id or f"botpress-{secrets.token_hex(6)}"
        self._turns: list[AgentTurn] = []

    def session_id(self) -> str:
        return self._sid

    def model_id(self) -> str:
        return self._runner.model_id()

    @property
    def mode(self) -> ToolVisibilityMode:
        return ToolVisibilityMode.AGENT_REPORTED

    def send(self, message: str, *, tool_results: Sequence[ToolResult] = ()) -> AgentTurn:
        idx = len(self._turns)
        text, bot_msgs = self._runner.complete_observed(message)
        turn = AgentTurn(
            index=idx,
            user_message=message,
            assistant_text=text,
            tool_calls=_botpress_tool_calls(bot_msgs, idx),
            tool_results=tuple(tool_results),
            raw_response={"messages": list(bot_msgs)},
        )
        self._turns.append(turn)
        return turn

    def trace(self) -> ConversationTrace:
        return ConversationTrace(
            session_id=self._sid,
            model_id=self.model_id(),
            mode=ToolVisibilityMode.AGENT_REPORTED,
            turns=tuple(self._turns),
            declared_tools=self._declared,
        )


# --------------------------------------------------------------------------- #
# Voiceflow adapter                                                             #
# --------------------------------------------------------------------------- #
class VoiceflowAgentSession(AgentSession):
    """AGENT_REPORTED AgentSession over VoiceflowRunner's Dialog Manager interact.

    Each `send()` performs one Voiceflow interact via the wrapped runner and
    records an `AgentTurn`. Text traces become the assistant text; tool/function
    traces (and debug traces that name a tool) become observed ToolCalls; a flow
    that reports no tool activity yields an empty `tool_calls`.
    """

    def __init__(self, *, project_id: str, api_key: str,
                 base_url: str = "https://general-runtime.voiceflow.com",
                 label: str | None = None,
                 timeout_s: float = 30.0,
                 user_id_prefix: str = "beaai-scan",
                 declared_tools: Sequence[str] = (),
                 poster: Callable[..., Any] | None = None,
                 session_id: str | None = None):
        self._runner = VoiceflowRunner(
            project_id=project_id, api_key=api_key, base_url=base_url,
            label=label, timeout_s=timeout_s, user_id_prefix=user_id_prefix,
            poster=poster,
        )
        self._declared = tuple(declared_tools)
        self._sid = session_id or f"voiceflow-{secrets.token_hex(6)}"
        self._turns: list[AgentTurn] = []

    def session_id(self) -> str:
        return self._sid

    def model_id(self) -> str:
        return self._runner.model_id()

    @property
    def mode(self) -> ToolVisibilityMode:
        return ToolVisibilityMode.AGENT_REPORTED

    def send(self, message: str, *, tool_results: Sequence[ToolResult] = ()) -> AgentTurn:
        idx = len(self._turns)
        text, traces = self._runner.complete_observed(message)
        turn = AgentTurn(
            index=idx,
            user_message=message,
            assistant_text=text,
            tool_calls=_voiceflow_tool_calls(traces, idx),
            tool_results=tuple(tool_results),
            raw_response={"traces": list(traces)},
        )
        self._turns.append(turn)
        return turn

    def trace(self) -> ConversationTrace:
        return ConversationTrace(
            session_id=self._sid,
            model_id=self.model_id(),
            mode=ToolVisibilityMode.AGENT_REPORTED,
            turns=tuple(self._turns),
            declared_tools=self._declared,
        )
