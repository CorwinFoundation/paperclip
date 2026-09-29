"""Strict correlation identifiers shared by Stage B ingestion and projection."""
from __future__ import annotations

import re

_LABEL = re.compile(
    r"^(?P<label>(?:backbond\s+)?(?:check|correlation)\s+id|test\s+reference)\s*:\s*(?P<value>.*)$",
    re.IGNORECASE,
)
_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,79}$")


def correlation_key(body: str) -> str | None:
    """Return one validated key, or fail closed on invalid/conflicting lines."""
    values: list[str] = []
    for line in body.splitlines():
        match = _LABEL.match(line.strip())
        if not match:
            continue
        value = match.group("value").strip()
        if not _VALUE.fullmatch(value):
            raise ValueError("correlation_identifier_invalid")
        values.append(value)
    if not values:
        return None
    if len(set(values)) != 1:
        raise ValueError("correlation_identifier_conflict")
    return values[0]
