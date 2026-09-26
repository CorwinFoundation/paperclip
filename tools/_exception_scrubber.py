"""Credential-scrubbing helpers for exception diagnostics.

Kept in a standalone module so tests can import these pure functions without
pulling in the full harness dependency tree that framework_workers.py requires.
"""
from __future__ import annotations

import re
from typing import Any, Mapping


_HTTP_HEADER_ALLOWLIST = frozenset({"server", "cf-ray", "via", "x-cache", "content-type"})
_HTTP_BODY_CAP = 200


def scrub(text: str, subject: Mapping[str, Any]) -> str:
    """Redact credentials from an arbitrary string."""
    for protected in (subject.get("secret"), subject.get("agent_id")):
        value = str(protected or "")
        if value:
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)\b(bearer\s+)[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(
        r"(?i)\b(api[_-]?key|secret|token|password)\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
        r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b",
        "[REDACTED]",
        text,
    )
    return text


def http_error_diagnostics(exc: Any, subject: Mapping[str, Any]) -> str:
    """Format bounded, scrubbed diagnostics from a CustomerEndpointHTTPError.

    Appends status line, allow-listed response headers, and a truncated body
    prefix so WAF interceptions are distinguishable from target policy denials
    in the manifest without a live re-request (BEAAA-31617).
    """
    parts = [f"status={exc.status_code} {exc.reason_phrase}"]
    headers: Mapping[str, str] = getattr(exc, "headers", {}) or {}
    for name, value in headers.items():
        if name.lower() in _HTTP_HEADER_ALLOWLIST:
            scrubbed = scrub(str(value), subject)
            parts.append(f"{name.lower()}={scrubbed}")
    body: str = getattr(exc, "body", "") or ""
    if body:
        body_prefix = scrub(body[:_HTTP_BODY_CAP], subject)
        parts.append(f"body={body_prefix!r}")
    return " ".join(parts)


def garak_exception_reason(
    prefix: str, exc: BaseException, subject: Mapping[str, Any]
) -> str:
    """Keep Garak failures diagnosable without leaking target credentials."""
    detail = " ".join(str(exc).split())
    detail = scrub(detail, subject)
    detail = detail[:200]
    base = f"{prefix}:{exc.__class__.__name__}" if prefix else exc.__class__.__name__
    result = f"{base}:{detail}" if detail else base
    # Append bounded HTTP diagnostics for CustomerEndpointHTTPError so WAF
    # interceptions are distinguishable from target-policy denials in the
    # manifest (BEAAA-31617).
    if hasattr(exc, "status_code") and hasattr(exc, "body") and hasattr(exc, "headers"):
        diag = http_error_diagnostics(exc, subject)
        result = f"{result} [{diag}]"
    return result
