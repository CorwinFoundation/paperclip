"""Bounded text-only AG-UI turns, consumed through the guarded HTTP runner.

State snapshots and tool arguments are never substitutes for assistant text.
Each probe is a fresh conversation; neither tools nor resumptions are executed.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, NoReturn

from harness.runner import RunnerError


def request_payload(prompt: str, system_prompt: str | None = None) -> dict[str, Any]:
    messages = []
    if system_prompt:
        messages.append({"id": str(uuid.uuid4()), "role": "system", "content": system_prompt})
    messages.append({"id": str(uuid.uuid4()), "role": "user", "content": prompt})
    return {
        "threadId": str(uuid.uuid4()), "runId": str(uuid.uuid4()),
        "state": {}, "messages": messages, "tools": [], "context": [], "forwardedProps": {},
    }


def response_text(text: str, *, thread_id: str, run_id: str) -> str:
    """Require a complete, identity-bound SSE run and nonempty assistant text."""
    def fail() -> NoReturn:
        # Never echo untrusted event bodies, tool arguments or provider errors.
        raise RunnerError("customer AG-UI endpoint returned an invalid or incomplete text run")

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n\n"):
        fail()
    started = finished = False
    parts: dict[str, list[str]] = {}
    opened: set[str] = set()
    closed: set[str] = set()
    pending_tools: set[str] = set()
    event_count = 0
    chunk_message_id: str | None = None
    for frame in text.split("\n\n"):
        data = "\n".join(line[5:].removeprefix(" ") for line in frame.split("\n") if line.startswith("data:"))
        if not data:
            continue
        event_count += 1
        if event_count > 4096:
            fail()
        try:
            event = json.loads(data)
        except (ValueError, RecursionError):
            fail()
        if not isinstance(event, dict) or not isinstance(event.get("type"), str) or finished:
            fail()
        kind = event["type"]
        if kind == "RUN_ERROR" or (kind == "CUSTOM" and event.get("name") == "on_interrupt"):
            fail()
        if kind == "RUN_STARTED":
            if started or event.get("threadId") != thread_id or event.get("runId") != run_id:
                fail()
            started = True
            continue
        if not started:
            fail()
        # Any supplied correlation identifiers must belong to this request.
        if event.get("threadId", thread_id) != thread_id or event.get("runId", run_id) != run_id:
            fail()
        if kind == "RUN_FINISHED":
            if event.get("threadId") != thread_id or event.get("runId") != run_id or opened or pending_tools:
                fail()
            # An interrupted/human-input turn has not completed the probe.
            outcome = event.get("outcome")
            if outcome is not None and (not isinstance(outcome, dict)
                    or outcome.get("type") != "success" or outcome.get("pendingToolCallIds")):
                fail()
            finished = True
        elif kind == "TOOL_CALL_START":
            ident = event.get("toolCallId")
            if not isinstance(ident, str) or not ident or ident in pending_tools:
                fail()
            pending_tools.add(ident)
        elif kind == "TOOL_CALL_RESULT":
            ident = event.get("toolCallId")
            if not isinstance(ident, str) or ident not in pending_tools:
                fail()
            pending_tools.remove(ident)
        elif kind in {"TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "TEXT_MESSAGE_CHUNK"}:
            ident = event.get("messageId")
            if kind == "TEXT_MESSAGE_CHUNK" and ident is None:
                ident = chunk_message_id
            if not isinstance(ident, str) or not ident or ident in closed:
                fail()
            if kind == "TEXT_MESSAGE_START":
                if event.get("role") != "assistant" or ident in parts:
                    fail()
                parts[ident] = []
                opened.add(ident)
            elif kind == "TEXT_MESSAGE_END":
                if ident not in opened:
                    fail()
                opened.remove(ident)
                closed.add(ident)
            else:
                if kind == "TEXT_MESSAGE_CONTENT" and ident not in opened:
                    fail()
                if event.get("role") not in (None, "assistant"):
                    fail()
                delta = event.get("delta", "" if kind == "TEXT_MESSAGE_CHUNK" else None)
                if not isinstance(delta, str):
                    fail()
                parts.setdefault(ident, []).append(delta)
                if kind == "TEXT_MESSAGE_CHUNK":
                    chunk_message_id = ident
    answer = "\n".join("".join(chunks) for chunks in parts.values())
    if not started or not finished or not answer.strip():
        fail()
    return answer
