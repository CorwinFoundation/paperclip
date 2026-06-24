"""Target HTTP client. urllib stdlib only — no third-party HTTP dep.

Generic shape: POST {"prompt": "..."} -> {"output": "..."}. R2 ships
provider-shape shims without changing probe grading or scanner scoring.

Tamper-resistance posture: target responses are adversarial. Body size is
bounded; non-2xx maps to a deterministic error response usable by the
grader.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

_MAX_RESPONSE_BYTES = 1 * 1024 * 1024  # 1 MiB cap per probe response


@dataclass(frozen=True)
class TargetResponse:
    probe_id: str
    output_text: str
    status_code: int
    error_class: str | None


def call_target(
    *,
    url: str,
    auth_mode: str,
    bearer_env_var: str | None,
    timeout_seconds: int,
    probe_id: str,
    prompt: str,
    shape: str = "generic_prompt_output",
    response_json_paths: tuple[str, ...] = (),
) -> TargetResponse:
    if shape == "transcript_export" or url.startswith("file://"):
        return _call_transcript_export(url=url, probe_id=probe_id, prompt=prompt)

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "are-probe-sdk/0.1.0-R2-internal",
    }
    if auth_mode == "bearer":
        if not bearer_env_var:
            raise ValueError("bearer auth requires bearer_env_var in config")
        tok = os.environ.get(bearer_env_var)
        if not tok:
            raise ValueError(f"bearer env var {bearer_env_var!r} not set")
        headers["Authorization"] = f"Bearer {tok}"

    body = json.dumps(_request_payload(shape, prompt, probe_id)).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                return TargetResponse(probe_id, "", resp.status, "oversize_response")
            try:
                payload = json.loads(raw.decode("utf-8", errors="replace"))
                output_text = _extract_output_text(payload, response_json_paths)
            except json.JSONDecodeError:
                output_text = raw.decode("utf-8", errors="replace")
            return TargetResponse(probe_id, output_text, resp.status, None)
    except urllib.error.HTTPError as e:
        return TargetResponse(probe_id, "", e.code, "http_error")
    except urllib.error.URLError:
        return TargetResponse(probe_id, "", 0, "network_error")
    except TimeoutError:
        return TargetResponse(probe_id, "", 0, "timeout")


def _request_payload(shape: str, prompt: str, probe_id: str) -> dict[str, Any]:
    if shape == "generic_prompt_output":
        return {"prompt": prompt}
    if shape == "botpress_webchat":
        return {
            "message": {"type": "text", "text": prompt},
            "metadata": {"probe_id": probe_id, "source": "are-probe-r2"},
        }
    raise ValueError(f"unsupported target shape: {shape}")


_DEFAULT_RESPONSE_PATHS = (
    "output",
    "text",
    "answer",
    "message",
    "message.text",
    "message.payload.text",
    "messages.0.text",
    "messages.0.payload.text",
    "responses.0.text",
    "responses.0.payload.text",
    "events.0.payload.text",
    "events.0.preview",
)


def _extract_output_text(payload: Any, response_json_paths: tuple[str, ...]) -> str:
    paths = response_json_paths or _DEFAULT_RESPONSE_PATHS
    for path in paths:
        found = _json_path(payload, path)
        if found is None:
            continue
        if isinstance(found, str):
            return found
        if isinstance(found, (int, float, bool)):
            return str(found)
    if isinstance(payload, list):
        texts = [
            _extract_output_text(item, response_json_paths)
            for item in payload
        ]
        return "\n".join(t for t in texts if t)
    return ""


def _json_path(payload: Any, path: str) -> Any:
    cur = payload
    for part in path.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur


def _call_transcript_export(*, url: str, probe_id: str, prompt: str) -> TargetResponse:
    path = Path(url[7:] if url.startswith("file://") else url).expanduser()
    try:
        raw = path.read_bytes()
    except OSError:
        return TargetResponse(probe_id, "", 0, "transcript_not_found")
    if len(raw) > _MAX_RESPONSE_BYTES:
        return TargetResponse(probe_id, "", 0, "oversize_response")
    text = raw.decode("utf-8", errors="replace")
    try:
        rows = _load_transcript_rows(text)
    except ValueError:
        return TargetResponse(probe_id, "", 0, "transcript_parse_error")
    for row in rows:
        if _row_matches(row, probe_id=probe_id, prompt=prompt):
            output = _extract_output_text(row, ())
            return TargetResponse(probe_id, output, 200, None)
    return TargetResponse(probe_id, "", 404, "transcript_probe_not_found")


def _load_transcript_rows(text: str) -> list[Any]:
    stripped = text.strip()
    if not stripped:
        return []
    if stripped[0] in "[{":
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            for key in ("turns", "messages", "events", "responses"):
                if isinstance(payload.get(key), list):
                    return list(payload[key])
            return [payload]
        if isinstance(payload, list):
            return list(payload)
    rows = []
    for line in stripped.splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _row_matches(row: Any, *, probe_id: str, prompt: str) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("probe_id") == probe_id or row.get("test_id") == probe_id:
        return True
    row_prompt = row.get("prompt") or row.get("input") or row.get("user")
    return isinstance(row_prompt, str) and row_prompt.strip() == prompt.strip()
