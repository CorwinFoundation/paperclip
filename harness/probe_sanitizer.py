"""Probe/response sanitizer used by AgentDojo-backed Scanner probes.

The scanned agent receives the original body. Scanner-side render contexts get
a JSON-escaped, length-capped sibling so adversarial probe text cannot become
control text in logs, judge prompts, or evidence renderers.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


MAX_SCANNER_INTERNAL_CHARS = 4096


@dataclass(frozen=True)
class SanitizedProbePayload:
    agent_body: str
    scanner_internal_body: str
    body_sha256: str


@dataclass(frozen=True)
class SanitizedResponsePayload:
    scoring_body: str
    scanner_internal_body: str
    body_sha256: str


def _sha256(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _scanner_safe(kind: str, body: str) -> str:
    capped = body[:MAX_SCANNER_INTERNAL_CHARS]
    payload = {
        "kind": kind,
        "body_sha256": _sha256(body),
        "truncated": len(body) > MAX_SCANNER_INTERNAL_CHARS,
        "body_json_escaped": capped,
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=True)


def sanitize_probe_payload(body: str) -> SanitizedProbePayload:
    if not isinstance(body, str):
        raise TypeError("probe body must be str")
    return SanitizedProbePayload(
        agent_body=body,
        scanner_internal_body=_scanner_safe("probe", body),
        body_sha256=_sha256(body),
    )


def sanitize_response_payload(body: str) -> SanitizedResponsePayload:
    if not isinstance(body, str):
        raise TypeError("response body must be str")
    return SanitizedResponsePayload(
        scoring_body=body,
        scanner_internal_body=_scanner_safe("response", body),
        body_sha256=_sha256(body),
    )
