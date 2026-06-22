"""Vapi capture adapter (BEAAA-7427, Done #2 — pre-built top-3).

Vapi exposes per-call artifacts via the Get Call API
(``GET https://api.vapi.ai/call/{id}``). The transcript lives under
``artifact.messages`` (or top-level ``messages``) — a list of
``{"role": "bot"|"user"|"system"|"tool_calls"|"tool_call_result",
"message"|"content": str, "toolCalls": [...]}``. Bot/user messages map to
agent/user turns; ``toolCalls`` and ``tool_calls`` role messages map to agent
action turns.
"""
from __future__ import annotations

from typing import Any

from .base import CaptureAdapter, CapturedSession
from ..scenario_kit import Turn


class VapiAdapter(CaptureAdapter):
    vendor = "vapi"
    export_api_doc = "GET https://api.vapi.ai/call/{id}"

    def to_session(self, raw_export: dict[str, Any]) -> CapturedSession:
        call_id = str(raw_export.get("id") or raw_export.get("call_id") or "vapi-call")
        agent_id = str(raw_export.get("assistantId") or raw_export.get("assistant_id") or "vapi-assistant")
        artifact = raw_export.get("artifact") or {}
        messages = (
            (artifact.get("messages") if isinstance(artifact, dict) else None)
            or raw_export.get("messages")
            or []
        )
        turns: list[Turn] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            text = msg.get("message") or msg.get("content") or ""
            tool_calls = msg.get("toolCalls") or msg.get("tool_calls")
            if tool_calls:
                for call in tool_calls if isinstance(tool_calls, list) else [tool_calls]:
                    if not isinstance(call, dict):
                        continue
                    fn = call.get("function") if isinstance(call.get("function"), dict) else call
                    turns.append(
                        Turn(
                            speaker="agent",
                            text=str(text),
                            tool_name=str(fn.get("name") or "unknown"),
                            tool_input=_args(fn),
                        )
                    )
                continue
            if role in {"tool_call_result", "tool", "function_result"}:
                turns.append(Turn(speaker="tool", text=str(text), tool_result=text))
            elif role in {"bot", "assistant"}:
                if text:
                    turns.append(Turn(speaker="agent", text=str(text)))
            elif role == "user":
                if text:
                    turns.append(Turn(speaker="user", text=str(text)))
        return CapturedSession(
            session_id=call_id,
            agent_id=agent_id,
            vendor=self.vendor,
            turns=tuple(turns),
            metadata={"vendor_call_id": call_id, "vendor": self.vendor},
        )


def _args(fn: dict[str, Any]) -> dict[str, Any]:
    args = fn.get("arguments") or fn.get("parameters") or {}
    if isinstance(args, dict):
        return args
    return {"raw": args}
