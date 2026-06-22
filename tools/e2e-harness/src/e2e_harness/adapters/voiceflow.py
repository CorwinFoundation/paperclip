"""Voiceflow capture adapter (BEAAA-7427, Done #2 — pre-built top-3).

Voiceflow exposes conversation transcripts via the Transcripts API
(``GET https://api.voiceflow.com/v2/transcripts/{projectID}/{transcriptID}``),
which returns a list of dialog turns. Each turn has a ``type`` and a
``payload``:

* ``type == "request"`` with ``payload.payload.query`` / ``.label`` — the
  end-user (mimicked attacker) utterance.
* ``type in {"text", "speak"}`` with ``payload.payload.message`` — the agent
  reply.
* ``type == "trace"`` / ``"debug"`` carrying a function / API step — mapped to
  an agent tool-call turn (the action surface).

Only the mapping below is Voiceflow-specific; everything downstream is shared.
"""
from __future__ import annotations

from typing import Any

from .base import CaptureAdapter, CapturedSession
from ..scenario_kit import Turn


class VoiceflowAdapter(CaptureAdapter):
    vendor = "voiceflow"
    export_api_doc = "GET https://api.voiceflow.com/v2/transcripts/{projectID}/{transcriptID}"

    def to_session(self, raw_export: dict[str, Any]) -> CapturedSession:
        transcript_id = str(raw_export.get("_id") or raw_export.get("transcriptID") or "voiceflow-transcript")
        agent_id = str(raw_export.get("projectID") or raw_export.get("project_id") or "voiceflow-project")
        items = raw_export.get("turns") or raw_export.get("dialog") or raw_export.get("items") or []
        turns: list[Turn] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            ttype = item.get("type")
            payload = item.get("payload") or {}
            inner = payload.get("payload") if isinstance(payload, dict) else {}
            inner = inner if isinstance(inner, dict) else {}
            if ttype == "request":
                text = inner.get("query") or inner.get("label") or payload.get("query") or ""
                if text:
                    turns.append(Turn(speaker="user", text=str(text)))
            elif ttype in {"text", "speak"}:
                text = inner.get("message") or payload.get("message") or ""
                if text:
                    turns.append(Turn(speaker="agent", text=str(text)))
            elif ttype in {"trace", "debug", "function", "api"}:
                name = (
                    inner.get("name")
                    or payload.get("name")
                    or item.get("name")
                    or "voiceflow_step"
                )
                args = inner.get("body") or inner.get("arguments") or inner.get("parameters") or {}
                turns.append(
                    Turn(
                        speaker="agent",
                        text=str(inner.get("message") or ""),
                        tool_name=str(name),
                        tool_input=args if isinstance(args, dict) else {"raw": args},
                    )
                )
        return CapturedSession(
            session_id=transcript_id,
            agent_id=agent_id,
            vendor=self.vendor,
            turns=tuple(turns),
            metadata={"vendor_transcript_id": transcript_id, "vendor": self.vendor},
        )
