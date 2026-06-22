from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .invariants import canonical_dumps
from .redaction import apply_redaction


SCHEMA_VERSION = "log-event-schema-v1.0"
MANIFEST_VERSION = "1"
DOGFOOD_TAP_VERSION = "0.1.0"
DEFAULT_MANIFEST_ID_PREFIX = "dogfood-shadow"

CAPTURE_REDACTION_POLICY = {
    "content": "redact-on-receipt",
    "text": "redact-on-receipt",
    "tool_input": "redact-on-receipt",
    "tool_result": "redact-on-receipt",
    "prompt": "redact-on-receipt",
    "summary": "redact-on-receipt",
    "base_instructions": "redact-on-receipt",
}


def discover_codex_rollouts(
    sessions_root: Path,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[Path, ...]:
    paths = sorted(Path(sessions_root).glob("20[0-9][0-9]/*/*/rollout-*.jsonl"))
    return tuple(_filter_paths_by_date(paths, start_date=start_date, end_date=end_date))


def discover_claude_transcripts(projects_root: Path) -> tuple[Path, ...]:
    return tuple(sorted(Path(projects_root).glob("*/*.jsonl")))


def build_dogfood_manifest(
    paths: Iterable[Path],
    *,
    customer_id: str = "backbond-dogfood",
    manifest_id: str | None = None,
    issued_at: str | None = None,
    max_events: int | None = None,
) -> dict[str, Any]:
    issued_at = issued_at or _utc_now_iso()
    events: list[dict[str, Any]] = []
    source_files: list[str] = []
    for path in paths:
        path = Path(path)
        source_files.append(_stable_source_path(path))
        mapper = _mapper_for_path(path)
        for event in mapper(path):
            events.append(event)
            if max_events is not None and len(events) >= max_events:
                break
        if max_events is not None and len(events) >= max_events:
            break
    if not events:
        raise ValueError("dogfood tap produced no schema-v1.0 events")

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "manifest_id": manifest_id or _manifest_id(source_files, issued_at),
        "customer_id": customer_id,
        "schema_version": SCHEMA_VERSION,
        "issued_at": issued_at,
        "events": events,
        "metadata": {
            "dogfood": True,
            "mode": "shadow",
            "tap_version": DOGFOOD_TAP_VERSION,
            "source_files": source_files,
            "retained_raw_transcripts": False,
            "redaction_policy": CAPTURE_REDACTION_POLICY,
        },
    }
    manifest["signature"] = _sha256_signature(manifest)
    return manifest


def iter_codex_events(path: Path) -> Iterator[dict[str, Any]]:
    for idx, record in _iter_jsonl(path):
        timestamp = _coerce_timestamp(record.get("timestamp"))
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        session_id = _session_id(record, path)
        payload_type = payload.get("type")
        for content_idx, item in enumerate(_content_items(payload)):
            item_type = item.get("type")
            event_id = _event_id("codex", path, idx, content_idx, item_type)
            if item_type in {"output_text", "message"} and isinstance(item.get("text"), str):
                yield _redacted_event(
                    event_id=event_id,
                    event_type="llm_completion",
                    logged_at=timestamp,
                    payload={
                        "agent_invocation_id": session_id,
                        "source": "codex",
                        "role": payload.get("role"),
                        "content_type": item_type,
                        "content": item.get("text"),
                        "content_sha256": _hash_text(item.get("text")),
                    },
                )
            elif item_type in {"input_text", "input"} and isinstance(item.get("text"), str):
                yield _redacted_event(
                    event_id=event_id,
                    event_type="tool_source_reference",
                    logged_at=timestamp,
                    payload={
                        "agent_invocation_id": session_id,
                        "source": "codex",
                        "source_reference_id": event_id,
                        "content_type": item_type,
                        "content": item.get("text"),
                        "content_sha256": _hash_text(item.get("text")),
                    },
                )
            elif item_type in {"function_call", "tool_call"}:
                yield _redacted_event(
                    event_id=event_id,
                    event_type="tool_call",
                    logged_at=timestamp,
                    payload={
                        "agent_invocation_id": session_id,
                        "source": "codex",
                        "tool": item.get("name") or payload_type or "unknown",
                        "tool_call_id": item.get("call_id") or item.get("id") or event_id,
                        "tool_input": item.get("arguments") or item.get("input") or {},
                    },
                )
        if not list(_content_items(payload)) and payload_type in {"function_call", "tool_call"}:
            yield _redacted_event(
                event_id=_event_id("codex", path, idx, 0, payload_type),
                event_type="tool_call",
                logged_at=timestamp,
                payload={
                    "agent_invocation_id": session_id,
                    "source": "codex",
                    "tool": payload.get("name") or "unknown",
                    "tool_call_id": payload.get("call_id") or payload.get("id") or f"{path.name}:{idx}",
                    "tool_input": payload.get("arguments") or payload.get("input") or {},
                },
            )


def iter_claude_events(path: Path) -> Iterator[dict[str, Any]]:
    for idx, record in _iter_jsonl(path):
        timestamp = _coerce_timestamp(record.get("timestamp"))
        msg = record.get("message")
        if not isinstance(msg, dict):
            continue
        session_id = str(record.get("sessionId") or path.stem)
        role = msg.get("role") or record.get("type")
        content = msg.get("content")
        if isinstance(content, str) and content:
            yield _redacted_event(
                event_id=_event_id("claude", path, idx, 0, "text"),
                event_type="tool_source_reference" if role == "user" else "llm_completion",
                logged_at=timestamp,
                payload={
                    "agent_invocation_id": session_id,
                    "source": "claude",
                    "role": role,
                    "content_type": "text",
                    "content": content,
                    "content_sha256": _hash_text(content),
                },
            )
            continue
        if not isinstance(content, list):
            continue
        for content_idx, item in enumerate(content):
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            event_id = _event_id("claude", path, idx, content_idx, item_type)
            if item_type == "tool_use":
                yield _redacted_event(
                    event_id=event_id,
                    event_type="tool_call",
                    logged_at=timestamp,
                    payload={
                        "agent_invocation_id": session_id,
                        "source": "claude",
                        "tool": item.get("name") or "unknown",
                        "tool_call_id": item.get("id") or event_id,
                        "tool_input": item.get("input") or {},
                    },
                )
            elif item_type == "tool_result":
                tool_use_id = item.get("tool_use_id") or event_id
                yield _redacted_event(
                    event_id=event_id,
                    event_type="tool_source_reference",
                    logged_at=timestamp,
                    payload={
                        "agent_invocation_id": session_id,
                        "source": "claude",
                        "source_reference_id": tool_use_id,
                        "tool_source_event_id": tool_use_id,
                        "content_type": "tool_result",
                        "tool_result": item.get("content"),
                        "content_sha256": _hash_text(item.get("content")),
                        "is_error": bool(item.get("is_error")),
                    },
                )
            elif item_type == "text" and isinstance(item.get("text"), str):
                yield _redacted_event(
                    event_id=event_id,
                    event_type="llm_completion",
                    logged_at=timestamp,
                    payload={
                        "agent_invocation_id": session_id,
                        "source": "claude",
                        "role": role,
                        "content_type": "text",
                        "content": item.get("text"),
                        "content_sha256": _hash_text(item.get("text")),
                    },
                )


def _mapper_for_path(path: Path):
    if path.name.startswith("rollout-"):
        return iter_codex_events
    return iter_claude_events


def _iter_jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    with Path(path).open(encoding="utf-8") as handle:
        for idx, line in enumerate(handle):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield idx, record


def _content_items(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    content = payload.get("content")
    if not isinstance(content, list):
        return ()
    return tuple(item for item in content if isinstance(item, dict))


def _redacted_event(
    *,
    event_id: str,
    event_type: str,
    logged_at: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    policy = {path: redaction for path, redaction in CAPTURE_REDACTION_POLICY.items() if path in payload}
    redacted_payload, redactions = apply_redaction(payload, policy)
    return {
        "event_id": event_id,
        "event_type": event_type,
        "logged_at": logged_at,
        "payload": redacted_payload,
        "redaction": {"field_redaction": policy},
        "redaction_applied": tuple(field.path for field in redactions),
    }


def _sha256_signature(manifest: dict[str, Any]) -> dict[str, str]:
    payload = {key: value for key, value in manifest.items() if key != "signature"}
    return {
        "algorithm": "sha256",
        "value": hashlib.sha256(canonical_dumps(payload)).hexdigest(),
        "key_id": "dogfood-shadow-local",
    }


def _manifest_id(source_files: list[str], issued_at: str) -> str:
    digest = hashlib.sha256(canonical_dumps({"source_files": source_files, "issued_at": issued_at})).hexdigest()
    return f"{DEFAULT_MANIFEST_ID_PREFIX}-{digest[:16]}"


def _event_id(source: str, path: Path, line_idx: int, content_idx: int, item_type: Any) -> str:
    seed = f"{source}:{_stable_source_path(path)}:{line_idx}:{content_idx}:{item_type}"
    return f"{source}-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:20]}"


def _stable_source_path(path: Path) -> str:
    parts = path.parts
    for marker in ("sessions", "projects"):
        if marker in parts:
            return "/".join(parts[parts.index(marker):])
    return path.name


def _session_id(record: dict[str, Any], path: Path) -> str:
    payload = record.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("id"), str):
        return payload["id"]
    return path.stem


def _coerce_timestamp(value: Any) -> str:
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).isoformat().replace("+00:00", "Z")
        except ValueError:
            return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return _utc_now_iso()


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _hash_text(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_dumps(value)).hexdigest()


def _filter_paths_by_date(paths: Iterable[Path], *, start_date: str | None, end_date: str | None) -> Iterator[Path]:
    for path in paths:
        date = _date_from_codex_path(path)
        if start_date is not None and date is not None and date < start_date:
            continue
        if end_date is not None and date is not None and date > end_date:
            continue
        yield path


def _date_from_codex_path(path: Path) -> str | None:
    parts = path.parts
    for idx, part in enumerate(parts):
        if part == "sessions" and len(parts) > idx + 3:
            return f"{parts[idx + 1]}-{parts[idx + 2]}-{parts[idx + 3]}"
    return None
