"""Credential-scrubbing helpers for exception diagnostics.

Kept in a standalone module so tests can import these pure functions without
pulling in the full harness dependency tree that framework_workers.py requires.
"""
from __future__ import annotations

import re
from typing import Any, Mapping


_HTTP_HEADER_ALLOWLIST = frozenset({"server", "cf-ray", "via", "x-cache", "content-type"})
_HTTP_BODY_CAP = 200
#: Opening delimiter of the appended HTTP diagnostics block. Shared with the
#: manifest bucketer so both ends agree on where the reason stops being
#: harness-authored text and starts being endpoint-supplied text.
_HTTP_DIAGNOSTICS_OPEN = " ["
HTTP_DIAGNOSTICS_MARKER = f"{_HTTP_DIAGNOSTICS_OPEN}status="


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
        # ``diag`` always opens with ``status=``, so this is exactly
        # HTTP_DIAGNOSTICS_MARKER by construction.
        result = f"{result}{_HTTP_DIAGNOSTICS_OPEN}{diag}]"
    return result


def strip_http_diagnostics(reason: str) -> str:
    """Return ``reason`` without the appended bounded HTTP diagnostics block.

    ``garak_exception_reason`` appends ``[status=... body='...']`` so a WAF
    interception is readable from the manifest (BEAAA-31617). That block is
    endpoint- and CDN-supplied text, so any consumer that classifies a reason
    with unanchored substring rules has to drop it first or the response body
    gets a vote on the bucket.

    The *first* marker occurrence is the real one: everything the endpoint
    controls lives inside the block, so a body that itself contains
    ``" [status="`` is already to the right of the genuine delimiter and is
    removed with it.
    """
    if not reason.endswith("]"):
        return reason
    index = reason.find(HTTP_DIAGNOSTICS_MARKER)
    if index == -1:
        return reason
    return reason[:index]
