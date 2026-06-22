"""Retell AI capture adapter (BEAAA-7427, Done #2 — pre-built top-3).

Retell exposes per-call transcripts via the Get Call API
(``GET https://api.retellai.com/v2/get-call/{call_id}``). The response
carries ``transcript`` (flat text) and ``transcript_object`` — a list of
utterances ``{"role": "agent"|"user", "content": str, "words": [...]}`` —
plus, when the agent invokes custom functions, ``tool_call_invocation`` /
``function_call`` records. We map utterances to ``Turn`` and surface
tool/function calls as agent action turns (the tool-misuse / money-movement
surface).

Field names follow Retell's documented v2 schema. If a live payload diverges
the only change is this mapping function — the schema-v1.0 manifest, scan and
Monitor wiring downstream are untouched.
"""
from __future__ import annotations

from typing import Any

from .base import CaptureAdapter, CapturedSession
from ..scenario_kit import Turn


class RetellAdapter(CaptureAdapter):
    vendor = "retell"
    export_api_doc = "GET https://api.retellai.com/v2/get-call/{call_id}"

    def to_session(self, raw_export: dict[str, Any]) -> CapturedSession:
        call_id = str(raw_export.get("call_id") or raw_export.get("id") or "retell-call")
        agent_id = str(raw_export.get("agent_id") or "retell-agent")
        turns: list[Turn] = []
        for utt in raw_export.get("transcript_object") or []:
            if not isinstance(utt, dict):
                continue
            role = utt.get("role")
            speaker = "agent" if role in {"agent", "assistant", "bot"} else "user"
            # Tool / function call attached to this utterance.
            for call in _tool_calls(utt):
                turns.append(
                    Turn(
                        speaker="agent",
                        text=str(utt.get("content") or ""),
                        tool_name=str(call.get("name") or call.get("function_name") or "unknown"),
                        tool_input=_args(call),
                    )
                )
            content = utt.get("content")
            if isinstance(content, str) and content.strip():
                turns.append(Turn(speaker=speaker, text=content))
        # Standalone tool-call invocation records (outside transcript_object).
        for call in raw_export.get("tool_call_invocations") or raw_export.get("function_calls") or []:
            if isinstance(call, dict):
                turns.append(
                    Turn(
                        speaker="agent",
                        text="",
                        tool_name=str(call.get("name") or call.get("function_name") or "unknown"),
                        tool_input=_args(call),
                    )
                )
        return CapturedSession(
            session_id=call_id,
            agent_id=agent_id,
            vendor=self.vendor,
            turns=tuple(turns),
            metadata={"vendor_call_id": call_id, "vendor": self.vendor},
        )


def _tool_calls(utt: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("tool_calls", "function_calls", "tool_call_invocation"):
        val = utt.get(key)
        if isinstance(val, list):
            return [c for c in val if isinstance(c, dict)]
        if isinstance(val, dict):
            return [val]
    return []


def _args(call: dict[str, Any]) -> dict[str, Any]:
    args = call.get("arguments") or call.get("args") or call.get("parameters") or {}
    if isinstance(args, dict):
        return args
    return {"raw": args}
