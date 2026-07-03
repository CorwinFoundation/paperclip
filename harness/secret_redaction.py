"""Secret redaction helpers for exception and transport boundaries."""
from __future__ import annotations

import re
from typing import Any


REDACTED_SECRET = "[REDACTED_SECRET]"

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=_-]{8,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|token|secret|password|authorization)"
        r"\s*[:=]\s*['\"]?[^'\"\s,;}]+"
    ),
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9._-]{8,}"),
    re.compile(r"\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
)


def redact_secret_text(value: str) -> str:
    """Return ``value`` with credential-shaped substrings removed."""
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(REDACTED_SECRET, redacted)
    return redacted


def redact_secret_obj(value: Any) -> Any:
    """Recursively redact secret-shaped strings in API response details."""
    if isinstance(value, str):
        return redact_secret_text(value)
    if isinstance(value, list):
        return [redact_secret_obj(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secret_obj(item) for item in value)
    if isinstance(value, dict):
        return {key: redact_secret_obj(item) for key, item in value.items()}
    return value
