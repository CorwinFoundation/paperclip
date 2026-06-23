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

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .base import CaptureAdapter, CapturedSession
from ..scenario_kit import Turn


RETELL_API_BASE = "https://api.retellai.com"
MAX_FREE_CONCURRENT_CALLS = 20
MAX_FREE_QA_MINUTES = 100
DEFAULT_MAX_CALLS = 30
DEFAULT_BATCH_SIZE = 10


class RetellAdapter(CaptureAdapter):
    vendor = "retell"
    export_api_doc = (
        "call_analyzed webhook payload; REST fallback "
        "GET https://api.retellai.com/v2/get-call/{call_id}, "
        "POST /v2/list-calls and POST /v2/create-web-call"
    )

    def fetch(self, credentials: dict[str, Any], **params: Any) -> list[dict[str, Any]]:  # pragma: no cover - live path
        """Fetch Retell call exports without exceeding the free test envelope.

        Required credential: ``api_key`` / ``RETELL_API_KEY`` in the creds file,
        ``.secrets/retell.env``, or process environment. The caller supplies
        either ``call_id`` for one known call or ``agent_id`` for a bounded
        list-calls backfill.
        """

        creds = _retell_creds(credentials)
        api_key = creds["api_key"]
        if not api_key:
            raise NotImplementedError("Retell live run requires api_key or RETELL_API_KEY")
        base_url = str(creds.get("base_url") or RETELL_API_BASE).rstrip("/")

        call_id = params.get("call_id") or creds.get("call_id")
        agent_id = params.get("agent_id") or creds.get("agent_id")
        max_calls = int(params.get("max_calls") or creds.get("max_calls") or DEFAULT_MAX_CALLS)
        max_qa_minutes = int(
            params.get("max_qa_minutes")
            or creds.get("max_qa_minutes")
            or min(MAX_FREE_QA_MINUTES, 20)
        )
        _assert_live_volume(total_calls=max_calls, batch_size=DEFAULT_BATCH_SIZE, max_qa_minutes=max_qa_minutes)

        if call_id:
            return [_retell_request(api_key, "GET", f"{base_url}/v2/get-call/{call_id}")]
        if not agent_id:
            raise ValueError("Retell live run requires --retell-call-id or --retell-agent-id")

        listed = _retell_request(
            api_key,
            "POST",
            f"{base_url}/v2/list-calls",
            {
                "filter_criteria": {"agent_id": [str(agent_id)]},
                "sort_order": "descending",
                "limit": max_calls,
            },
        )
        calls = _coerce_call_list(listed)[:max_calls]
        if not calls:
            raise ValueError("Retell list-calls returned no calls for the supplied agent_id")
        return [_retell_request(api_key, "GET", f"{base_url}/v2/get-call/{_call_id(c)}") for c in calls if _call_id(c)]

    def drive_scenarios(
        self,
        credentials: dict[str, Any],
        scenarios: tuple[Any, ...],
        **params: Any,
    ) -> list[dict[str, Any]]:  # pragma: no cover - live path
        """Create one Retell web call per scripted scenario and fetch results.

        Retell's server-side ``create-web-call`` registers the call and returns
        an ``access_token`` for a web client. This helper writes scenario
        metadata and script text into per-call metadata/dynamic variables, then
        polls until Retell reports the call ended/analyzed. If the scoped Retell
        setup cannot auto-drive audio from the harness, operators can place the
        registered calls from the Retell console and re-run ``fetch`` by
        ``agent_id`` or ``call_id``.
        """

        creds = _retell_creds(credentials)
        api_key = creds["api_key"]
        if not api_key:
            raise NotImplementedError("Retell call-driver requires api_key or RETELL_API_KEY")
        agent_id = str(params.get("agent_id") or creds.get("agent_id") or "")
        if not agent_id:
            raise ValueError("Retell call-driver requires --retell-agent-id")
        base_url = str(creds.get("base_url") or RETELL_API_BASE).rstrip("/")
        total_calls = int(params.get("max_calls") or creds.get("max_calls") or DEFAULT_MAX_CALLS)
        batch_size = int(params.get("batch_size") or creds.get("batch_size") or DEFAULT_BATCH_SIZE)
        max_qa_minutes = int(
            params.get("max_qa_minutes")
            or creds.get("max_qa_minutes")
            or min(MAX_FREE_QA_MINUTES, 20)
        )
        _assert_live_volume(total_calls=total_calls, batch_size=batch_size, max_qa_minutes=max_qa_minutes)
        selected = tuple(scenarios[:total_calls])

        call_ids: list[str] = []
        for idx, scenario in enumerate(selected, 1):
            created = _retell_request(
                api_key,
                "POST",
                f"{base_url}/v2/create-web-call",
                _create_web_call_payload(agent_id, scenario),
            )
            call_id = _call_id(created)
            if not call_id:
                raise ValueError(f"Retell create-web-call returned no call_id for {scenario.scenario_id}")
            call_ids.append(call_id)
            # Keep registration volume under the Retell free concurrent envelope.
            if idx % batch_size == 0:
                time.sleep(float(params.get("batch_pause_seconds") or creds.get("batch_pause_seconds") or 0))

        poll_seconds = float(params.get("poll_seconds") or creds.get("poll_seconds") or 5)
        poll_attempts = int(params.get("poll_attempts") or creds.get("poll_attempts") or 24)
        return [
            _poll_retell_call(api_key, base_url, call_id, poll_seconds=poll_seconds, poll_attempts=poll_attempts)
            for call_id in call_ids
        ]

    def to_session(self, raw_export: dict[str, Any]) -> CapturedSession:
        call = _retell_call_payload(raw_export)
        call_id = str(call.get("call_id") or call.get("chat_id") or call.get("id") or "retell-call")
        agent_id = str(call.get("agent_id") or "retell-agent")
        metadata = _session_metadata(call)
        turns: list[Turn] = []
        transcript_object = (
            call.get("transcript_object")
            or call.get("transcript_with_tool_calls")
            or call.get("scrubbed_transcript_with_tool_calls")
            or call.get("message_with_tool_calls")
            or []
        )
        for utt in transcript_object:
            if not isinstance(utt, dict):
                continue
            role = utt.get("role")
            if role == "tool_call_invocation":
                turns.append(
                    Turn(
                        speaker="agent",
                        text=str(utt.get("content") or ""),
                        tool_name=_tool_name(utt),
                        tool_input=_args(utt),
                    )
                )
                continue
            if role == "tool_call_result":
                turns.append(
                    Turn(
                        speaker="tool",
                        text=str(utt.get("content") or ""),
                        tool_name=str(utt.get("name") or "tool_call_result"),
                        tool_result=utt.get("content"),
                    )
                )
                continue
            speaker = "agent" if role in {"agent", "assistant", "bot"} else "user"
            # Tool / function call attached to this utterance.
            for call in _tool_calls(utt):
                turns.append(
                    Turn(
                        speaker="agent",
                        text=str(utt.get("content") or ""),
                        tool_name=_tool_name(call),
                        tool_input=_args(call),
                    )
                )
            content = utt.get("content")
            if isinstance(content, str) and content.strip():
                turns.append(Turn(speaker=speaker, text=content))
        if not turns and isinstance(call.get("transcript"), str) and call["transcript"].strip():
            turns.extend(_turns_from_flat_transcript(call["transcript"]))
        # Standalone tool-call invocation records (outside transcript_object).
        for tool_call in call.get("tool_call_invocations") or call.get("function_calls") or []:
            if isinstance(tool_call, dict):
                turns.append(
                    Turn(
                        speaker="agent",
                        text="",
                        tool_name=_tool_name(tool_call),
                        tool_input=_args(tool_call),
                    )
                )
        return CapturedSession(
            session_id=call_id,
            agent_id=agent_id,
            vendor=self.vendor,
            turns=tuple(turns),
            metadata=metadata,
        )


def _tool_calls(utt: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("tool_calls", "function_calls", "tool_call_invocations", "tool_call_invocation"):
        val = utt.get(key)
        if isinstance(val, list):
            return [c for c in val if isinstance(c, dict)]
        if isinstance(val, dict):
            return [val]
    return []


def _args(call: dict[str, Any]) -> dict[str, Any]:
    fn = call.get("function")
    if isinstance(fn, dict):
        args = fn.get("arguments") or fn.get("args") or fn.get("parameters") or {}
    else:
        args = call.get("arguments") or call.get("args") or call.get("parameters") or {}
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except json.JSONDecodeError:
            return {"raw": args}
        if isinstance(parsed, dict):
            return parsed
    if isinstance(args, dict):
        return args
    return {"raw": args}


def _tool_name(call: dict[str, Any]) -> str:
    fn = call.get("function")
    if isinstance(fn, dict):
        return str(fn.get("name") or fn.get("function_name") or "unknown")
    return str(call.get("name") or call.get("function_name") or "unknown")


def _retell_call_payload(raw_export: dict[str, Any]) -> dict[str, Any]:
    """Unwrap call_analyzed webhook payloads and REST get-call payloads."""

    call = raw_export.get("call")
    if isinstance(call, dict):
        return call
    data = raw_export.get("data")
    if isinstance(data, dict):
        nested = data.get("call")
        if isinstance(nested, dict):
            return nested
        return data
    return raw_export


def _turns_from_flat_transcript(transcript: str) -> list[Turn]:
    turns: list[Turn] = []
    for line in transcript.splitlines():
        if ":" not in line:
            continue
        speaker, text = line.split(":", 1)
        normalized = speaker.strip().lower()
        if normalized in {"agent", "assistant", "bot"}:
            turns.append(Turn("agent", text.strip()))
        elif normalized in {"user", "caller", "customer"}:
            turns.append(Turn("user", text.strip()))
    return turns


def _retell_request(
    api_key: str,
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = None
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Retell response for {url} was not a JSON object")
    return payload


def _coerce_call_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("items", "calls", "data", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _call_id(call: dict[str, Any]) -> str | None:
    candidate = call.get("call_id") or call.get("chat_id") or call.get("id")
    return str(candidate) if candidate else None


def _session_metadata(call: dict[str, Any]) -> dict[str, Any]:
    metadata = {
        "vendor_call_id": str(call.get("call_id") or call.get("chat_id") or call.get("id") or "retell-call"),
        "vendor": "retell",
    }
    for source in (call.get("metadata"), call.get("retell_llm_dynamic_variables")):
        if not isinstance(source, dict):
            continue
        for key in ("scenario_id", "backbond_scenario_id", "peril", "attack_family"):
            if source.get(key):
                metadata[key] = source[key]
    return metadata


def _assert_free_envelope(*, max_calls: int, max_qa_minutes: int) -> None:
    if max_calls < 1:
        raise ValueError("max_calls must be >= 1")
    if max_calls > MAX_FREE_CONCURRENT_CALLS:
        raise ValueError(
            f"Retell free envelope allows at most {MAX_FREE_CONCURRENT_CALLS} concurrent calls; "
            f"requested max_calls={max_calls}"
        )
    if max_qa_minutes < 1 or max_qa_minutes > MAX_FREE_QA_MINUTES:
        raise ValueError(
            f"Retell free envelope allows 1..{MAX_FREE_QA_MINUTES} QA minutes; "
            f"requested max_qa_minutes={max_qa_minutes}"
        )


def _assert_live_volume(*, total_calls: int, batch_size: int, max_qa_minutes: int) -> None:
    if total_calls < 1:
        raise ValueError("total_calls must be >= 1")
    _assert_free_envelope(max_calls=batch_size, max_qa_minutes=max_qa_minutes)


def _retell_creds(credentials: dict[str, Any]) -> dict[str, Any]:
    merged = _load_retell_env()
    merged.update({k: v for k, v in credentials.items() if v is not None})
    for env_key in ("RETELL_API_KEY", "RETELL_AGENT_ID", "RETELL_BASE_URL"):
        if os.environ.get(env_key):
            merged.setdefault(env_key, os.environ[env_key])
    api_key = merged.get("api_key") or merged.get("RETELL_API_KEY") or ""
    agent_id = merged.get("agent_id") or merged.get("RETELL_AGENT_ID")
    base_url = merged.get("base_url") or merged.get("RETELL_BASE_URL") or RETELL_API_BASE
    merged["api_key"] = str(api_key)
    if agent_id:
        merged["agent_id"] = str(agent_id)
    merged["base_url"] = str(base_url).rstrip("/")
    return merged


def _load_retell_env() -> dict[str, str]:
    candidates = [
        Path(".secrets/retell.env"),
        Path(__file__).resolve().parents[5] / ".secrets" / "retell.env",
    ]
    loaded: dict[str, str] = {}
    for path in candidates:
        if not path.exists():
            continue
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            loaded[key.strip()] = value.strip().strip("'\"")
    return loaded


def _create_web_call_payload(agent_id: str, scenario: Any) -> dict[str, Any]:
    script = "\n".join(
        f"{turn.speaker}: {turn.text}".strip()
        for turn in scenario.conversation
        if getattr(turn, "text", "")
    )
    return {
        "agent_id": agent_id,
        "metadata": {
            "source": "backbond-e2e-harness",
            "scenario_id": scenario.scenario_id,
            "peril": scenario.peril,
            "attack_family": scenario.attack_family,
        },
        "retell_llm_dynamic_variables": {
            "backbond_scenario_id": scenario.scenario_id,
            "backbond_peril": scenario.peril,
            "backbond_script": script,
        },
    }


def _poll_retell_call(
    api_key: str,
    base_url: str,
    call_id: str,
    *,
    poll_seconds: float,
    poll_attempts: int,
) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for _ in range(max(1, poll_attempts)):
        latest = _retell_request(api_key, "GET", f"{base_url}/v2/get-call/{call_id}")
        call = _retell_call_payload(latest)
        status = str(call.get("call_status") or "")
        has_analysis = bool(call.get("call_analysis") or call.get("transcript_object") or call.get("transcript"))
        if status in {"ended", "error"} and has_analysis:
            return latest
        time.sleep(max(0.0, poll_seconds))
    return latest
