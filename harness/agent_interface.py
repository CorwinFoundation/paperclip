"""Standard Agent Interface (SAI) v1 — multi-turn, tool-visible probe transport.

BEAAA-11712 (CI/CD WS-2, CTO Directive v2.2 §4 Step 1). Reference implementation
of the contract at `contracts/standard-agent-interface-v1.md`.

WHY THIS EXISTS
---------------
The legacy transport is `harness.runner.ModelRunner.complete(prompt) -> str`:
single-turn, text-in / text-out. Our highest-value perils — unauthorized tool
actions, data exfiltration, transaction-boundary injection — do NOT show up in
chat text. They show up in *what the agent tries to invoke*. A stateless string
runner cannot see that, and it cannot carry the conversation state a
transaction-boundary attack needs (set up context in turn 1, spring the trap in
turn 3).

The SAI adds a stateful, tool-visible conversation handle on top of the existing
egress/transport layer WITHOUT changing any scoring math, rubric, judge, or the
Underwriter output contract. It is an additive capture layer: it produces a
`ConversationTrace` (turns + tool calls + tool results + authorization verdicts)
that becomes the reproducible evidence artifact a scorer/judge reads. Today the
tool-misuse fixtures carry hand-authored labels like `high_sev_disclosure` with
no machine-observable trace behind them; the SAI is what those labels get
*derived from*.

TWO TOOL-VISIBILITY MODES
-------------------------
- SCANNER_MEDIATED (strong): the scanner plays the tool runtime. The agent emits
  structured tool calls; the scanner observes them *at invocation time*, applies
  an authorization policy (`ToolSandbox`), returns tool results, and loops. An
  unauthorized call is caught before any side effect — the evidence is the
  invocation itself, not a post-hoc guess from chat text. OpenAI-/Anthropic-tools
  compatible.
- AGENT_REPORTED (fallback): the agent executes tools internally (e.g. a
  Voiceflow flow) and self-reports tool activity in a structured envelope or via
  Evidence-Profile-v1 spans. Lower fidelity; we observe what the agent chooses to
  report.

The tool-call vocabulary reuses `action_class` from `contracts/evidence-profile-v1.md`
(`message_send | transaction | data_access | tool_call | human_escalation |
admin_change | other`) so scan-time traces and post-bind runtime evidence speak
one language. The per-turn shape is `StopInstructionEvent`-compatible
(`response_text` / `tool_calls` / `actions`) so existing probe scorers consume it
without change.
"""
from __future__ import annotations

import json as _json
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable, Sequence

# action_class enum — MUST stay in lockstep with contracts/evidence-profile-v1.md.
ACTION_CLASSES = (
    "message_send",
    "transaction",
    "data_access",
    "tool_call",
    "human_escalation",
    "admin_change",
    "other",
)


class ToolVisibilityMode(str, Enum):
    SCANNER_MEDIATED = "scanner_mediated"
    AGENT_REPORTED = "agent_reported"


class AgentInterfaceError(RuntimeError):
    """Raised on protocol / transport violations of the SAI contract."""


# --------------------------------------------------------------------------- #
# Structured trace types (the "structured output format" of the deliverable).  #
# All frozen + JSON-serialisable so a trace is a reproducible evidence artifact.#
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ToolCall:
    """One tool invocation the agent *attempted*, observed by the scanner."""

    call_id: str            # correlates this call to its ToolResult
    tool_name: str
    arguments: dict[str, Any]
    action_class: str = "tool_call"   # evidence-profile-v1 taxonomy
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action_class not in ACTION_CLASSES:
            raise AgentInterfaceError(
                f"action_class {self.action_class!r} not in evidence-profile-v1 enum {ACTION_CLASSES}"
            )


@dataclass(frozen=True)
class ToolResult:
    """The result the tool runtime returned for a ToolCall.

    `authorized` is the scanner's authorization verdict at invocation time —
    None in AGENT_REPORTED mode (we did not mediate), True/False in
    SCANNER_MEDIATED mode. `authorized=False` is the unauthorized-action signal.
    """

    call_id: str
    tool_name: str
    content: Any
    is_error: bool = False
    authorized: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentTurn:
    index: int                 # 0-based turn ordinal within the session
    user_message: str
    assistant_text: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()
    raw_response: dict[str, Any] = field(default_factory=dict)

    @property
    def unauthorized_calls(self) -> tuple[ToolCall, ...]:
        denied = {r.call_id for r in self.tool_results if r.authorized is False}
        return tuple(c for c in self.tool_calls if c.call_id in denied)


@dataclass(frozen=True)
class ConversationTrace:
    """The captured multi-turn interaction. This IS the scoring evidence."""

    session_id: str
    model_id: str
    mode: ToolVisibilityMode
    turns: tuple[AgentTurn, ...]
    declared_tools: tuple[str, ...] = ()        # tool inventory the agent advertised
    sai_version: str = "v1"

    # ---- accessors used by scorers / judges --------------------------------
    def all_tool_calls(self) -> tuple[ToolCall, ...]:
        return tuple(c for t in self.turns for c in t.tool_calls)

    def all_unauthorized_calls(self) -> tuple[ToolCall, ...]:
        return tuple(c for t in self.turns for c in t.unauthorized_calls)

    # ---- serialisation -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["mode"] = self.mode.value
        return d

    def to_json(self) -> str:
        return _json.dumps(self.to_dict(), sort_keys=True, indent=2)

    def to_probe_event(self) -> dict[str, Any]:
        """Flatten to a StopInstructionEvent-compatible dict for legacy scorers.

        `tool_calls` / `actions` are the fields existing probe scorers read
        (see probes/stop_instruction/v1/probe.py). We surface every tool call,
        and split side-effecting calls into `actions` so halt/authorization
        checks work unchanged.
        """
        side_effecting = {"transaction", "data_access", "admin_change", "human_escalation"}
        calls = self.all_tool_calls()
        return {
            "response_text": "\n".join(t.assistant_text for t in self.turns),
            "tool_calls": tuple(
                {"tool_name": c.tool_name, "arguments": c.arguments, "action_class": c.action_class}
                for c in calls
            ),
            "actions": tuple(
                {"tool_name": c.tool_name, "action_class": c.action_class}
                for c in calls
                if c.action_class in side_effecting
            ),
        }

    def to_responses_shim(self, probe_id: str, label: str) -> dict[str, Any]:
        """Legacy `{"responses": {probe_id: label}}` fixture shim for r2_rob_common."""
        return {"responses": {probe_id: label}}


# --------------------------------------------------------------------------- #
# The interface every agent adapter implements.                                #
# --------------------------------------------------------------------------- #
class AgentSession(ABC):
    """A stateful, multi-turn, tool-visible handle to one target agent.

    Contract:
      - `send()` advances the conversation by exactly one user turn and returns
        the resulting `AgentTurn`. State is retained between calls (session-id or
        full-history — the adapter's choice; opaque to the caller).
      - In SCANNER_MEDIATED mode, when a returned turn contains `tool_calls`, the
        caller (`SessionProbeDriver`) supplies `tool_results` on the *next*
        `send()` so the agent can continue. Adapters MUST accept prior results.
      - `trace()` returns everything captured so far. Never raises.
    """

    @abstractmethod
    def session_id(self) -> str: ...

    @abstractmethod
    def model_id(self) -> str: ...

    @property
    def mode(self) -> ToolVisibilityMode:
        return ToolVisibilityMode.SCANNER_MEDIATED

    @abstractmethod
    def send(self, message: str, *, tool_results: Sequence[ToolResult] = ()) -> AgentTurn: ...

    @abstractmethod
    def trace(self) -> ConversationTrace: ...

    def close(self) -> None:  # optional cleanup hook
        return None


# --------------------------------------------------------------------------- #
# Scanner-side tool runtime: observes + authorizes tool calls.                  #
# --------------------------------------------------------------------------- #
@dataclass
class ToolSandbox:
    """The scanner playing the tool runtime in SCANNER_MEDIATED mode.

    `allowlist` is the set of (tool_name) the probe scenario authorizes. Any call
    outside it is recorded `authorized=False` and denied — that denial, plus the
    captured invocation, is the unauthorized-action evidence. `results` maps
    tool_name -> canned return so probes stay deterministic and side-effect-free.
    """

    allowlist: frozenset[str] = frozenset()
    results: dict[str, Any] = field(default_factory=dict)
    deny_content: Any = "DENIED: tool not authorized for this session"

    def handle(self, call: ToolCall) -> ToolResult:
        authorized = call.tool_name in self.allowlist
        if not authorized:
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                content=self.deny_content,
                is_error=True,
                authorized=False,
            )
        return ToolResult(
            call_id=call.call_id,
            tool_name=call.tool_name,
            content=self.results.get(call.tool_name, "ok"),
            is_error=False,
            authorized=True,
        )


# --------------------------------------------------------------------------- #
# Probe driver: runs a scripted multi-turn, tool-using scenario.               #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ProbeScript:
    """A deterministic multi-turn scenario.

    `user_turns` are the messages the scanner sends in order. In
    SCANNER_MEDIATED mode, if a turn produces tool calls, the driver resolves
    them via `sandbox` and re-sends the results before advancing to the next
    scripted user turn (bounded by `max_tool_hops`).
    """

    probe_id: str
    user_turns: tuple[str, ...]
    sandbox: ToolSandbox = field(default_factory=ToolSandbox)
    max_tool_hops: int = 4


class SessionProbeDriver:
    """Drives a ProbeScript against any AgentSession and returns its trace."""

    def run(self, session: AgentSession, script: ProbeScript) -> ConversationTrace:
        for user_msg in script.user_turns:
            turn = session.send(user_msg)
            if session.mode is ToolVisibilityMode.SCANNER_MEDIATED:
                hops = 0
                pending = self._unresolved_calls(turn)
                while pending and hops < script.max_tool_hops:
                    results = [script.sandbox.handle(c) for c in pending]
                    turn = session.send("", tool_results=results)
                    pending = self._unresolved_calls(turn)
                    hops += 1
        return session.trace()

    @staticmethod
    def _unresolved_calls(turn: AgentTurn) -> tuple[ToolCall, ...]:
        """Tool calls in `turn` that do not yet have a matching result.

        BEAAA-11732: a continuation turn echoes the just-resolved `tool_calls`
        into its trace (they ARE the evidence) while also carrying their
        `tool_results`. Reading `pending = turn.tool_calls` therefore never
        clears — the same `call_id` gets re-dispatched to the sandbox up to
        `max_tool_hops` times. Correlating on `call_id` keeps only calls with no
        result yet: for a mediated continuation that is the empty set (all
        resolved → loop advances to the next scripted user turn); for a genuine
        multi-hop agent it is exactly the NEW calls it just emitted (loop
        continues). One dispatch per `call_id`, per WS-2's uniqueness contract.
        """
        resolved = {r.call_id for r in turn.tool_results}
        return tuple(c for c in turn.tool_calls if c.call_id not in resolved)


# --------------------------------------------------------------------------- #
# Reference staging agent: deterministic, network-free, scriptable.            #
# This is a legitimate "staging agent" for the acceptance test — it holds       #
# multi-turn state and issues tool calls per a policy, exactly like a real       #
# agent under test, with zero network + full reproducibility.                   #
# --------------------------------------------------------------------------- #
# A policy inspects (turn_index, user_message, history) and returns
# (assistant_text, list[ToolCall]).
AgentPolicy = Callable[[int, str, "list[AgentTurn]"], "tuple[str, list[ToolCall]]"]


class MockAgentSession(AgentSession):
    """Deterministic staging agent for tests + WS-3 mock harness.

    `policy` decides, per turn, what the agent says and which tools it tries to
    call. Because it is a pure function of (index, message, history), the whole
    trace is reproducible with no seeds or network.
    """

    def __init__(self, *, name: str, policy: AgentPolicy,
                 declared_tools: Sequence[str] = (),
                 session_id: str | None = None):
        self._name = name
        self._policy = policy
        self._declared = tuple(declared_tools)
        self._sid = session_id or f"mock-{secrets.token_hex(6)}"
        self._turns: list[AgentTurn] = []
        self._pending_calls: tuple[ToolCall, ...] = ()

    def session_id(self) -> str:
        return self._sid

    def model_id(self) -> str:
        return f"mock-agent:{self._name}"

    def send(self, message: str, *, tool_results: Sequence[ToolResult] = ()) -> AgentTurn:
        # A continuation turn (tool_results supplied, empty message) records the
        # results against the just-issued calls rather than re-consulting policy.
        if tool_results and not message and self._turns:
            last = self._turns[-1]
            merged = AgentTurn(
                index=last.index,
                user_message=last.user_message,
                assistant_text=last.assistant_text,
                tool_calls=last.tool_calls,
                tool_results=tuple(tool_results),
                raw_response=last.raw_response,
            )
            self._turns[-1] = merged
            self._pending_calls = ()
            return merged

        idx = len(self._turns)
        text, calls = self._policy(idx, message, list(self._turns))
        turn = AgentTurn(
            index=idx,
            user_message=message,
            assistant_text=text,
            tool_calls=tuple(calls),
            tool_results=(),
        )
        self._turns.append(turn)
        self._pending_calls = tuple(calls)
        return turn

    def trace(self) -> ConversationTrace:
        return ConversationTrace(
            session_id=self._sid,
            model_id=self.model_id(),
            mode=ToolVisibilityMode.SCANNER_MEDIATED,
            turns=tuple(self._turns),
            declared_tools=self._declared,
        )


# --------------------------------------------------------------------------- #
# Real transport: full-history, scanner-mediated over the existing egress path. #
# Wiring against a live remote staging endpoint is delegated (needs creds/box); #
# the parsing + loop are implemented and unit-testable against a fake poster.   #
# --------------------------------------------------------------------------- #
class HTTPAgentSession(AgentSession):
    """OpenAI-tools-compatible multi-turn session (full-history mode).

    State is carried by resending the full `messages[]` each turn — no reliance
    on server-side session storage. Tool calls are parsed from
    `choices[0].message.tool_calls` and made observable; tool results are appended
    as role=tool messages. `poster` defaults to `harness.egress.post`; tests
    inject a deterministic fake, so the request/response plumbing is exercised
    without a live endpoint.
    """

    def __init__(self, *, url: str, model: str, auth: str | None = None,
                 tools: Sequence[dict[str, Any]] = (),
                 system_prompt: str | None = None,
                 label: str | None = None,
                 poster: Callable[..., Any] | None = None,
                 timeout_s: float = 30.0):
        self._url = url
        self._model = model
        self._auth = auth
        self._tools = list(tools)
        self._label = label or f"http-agent:{url}"
        self._timeout_s = timeout_s
        self._sid = f"http-{secrets.token_hex(6)}"
        self._messages: list[dict[str, Any]] = []
        if system_prompt:
            self._messages.append({"role": "system", "content": system_prompt})
        self._turns: list[AgentTurn] = []
        self._poster = poster

    def session_id(self) -> str:
        return self._sid

    def model_id(self) -> str:
        return self._label

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        poster = self._poster
        if poster is None:
            from harness import egress  # lazy: avoid import cost in unit tests
            poster = egress.post
        headers = {"Content-Type": "application/json"}
        if self._auth:
            headers["Authorization"] = self._auth if self._auth.startswith("Bearer ") else f"Bearer {self._auth}"
        resp = poster(self._url, headers=headers, json=body, timeout=self._timeout_s, session_id=self._sid)
        if getattr(resp, "status_code", 200) >= 400:
            raise AgentInterfaceError(f"HTTP {resp.status_code}: {getattr(resp, 'text', '')[:300]}")
        return resp.json()

    @staticmethod
    def _parse_tool_calls(msg: dict[str, Any]) -> list[ToolCall]:
        out: list[ToolCall] = []
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = _json.loads(args) if args else {}
                except _json.JSONDecodeError:
                    args = {"_raw": args}
            out.append(ToolCall(
                call_id=str(tc.get("id") or secrets.token_hex(4)),
                tool_name=str(fn.get("name", "unknown")),
                arguments=args if isinstance(args, dict) else {"_value": args},
                raw=tc,
            ))
        return out

    def send(self, message: str, *, tool_results: Sequence[ToolResult] = ()) -> AgentTurn:
        if tool_results:
            for r in tool_results:
                self._messages.append({
                    "role": "tool",
                    "tool_call_id": r.call_id,
                    "content": _json.dumps(r.content) if not isinstance(r.content, str) else r.content,
                })
        else:
            self._messages.append({"role": "user", "content": message})

        body: dict[str, Any] = {
            "model": self._model,
            "messages": self._messages,
            "temperature": 0,
        }
        if self._tools:
            body["tools"] = self._tools
        payload = self._post(body)
        msg = payload.get("choices", [{}])[0].get("message", {})
        text = str(msg.get("content") or "")
        calls = self._parse_tool_calls(msg)
        # Record the assistant message into history so the next turn carries state.
        self._messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})

        idx = len(self._turns) if not tool_results else max(0, len(self._turns) - 1)
        turn = AgentTurn(
            index=idx,
            user_message=message,
            assistant_text=text,
            tool_calls=tuple(calls),
            tool_results=tuple(tool_results),
            raw_response=payload,
        )
        if tool_results and self._turns:
            self._turns[-1] = turn
        else:
            self._turns.append(turn)
        return turn

    def trace(self) -> ConversationTrace:
        return ConversationTrace(
            session_id=self._sid,
            model_id=self.model_id(),
            mode=ToolVisibilityMode.SCANNER_MEDIATED,
            turns=tuple(self._turns),
            declared_tools=tuple(t.get("function", {}).get("name", "") for t in self._tools),
        )
