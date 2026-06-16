from __future__ import annotations

import json
from decimal import Decimal
from typing import Any


def canonical_json(value: Any) -> bytes:
    """RFC 8785-compatible canonical JSON for the connector envelope subset."""

    return _render(value).encode("utf-8")


def _render(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if not (value == value and value not in (float("inf"), float("-inf"))):
            raise ValueError("non-finite numbers are not valid canonical JSON")
        return _render_decimal(Decimal(str(value)))
    if isinstance(value, Decimal):
        return _render_decimal(value)
    if isinstance(value, list):
        return "[" + ",".join(_render(item) for item in value) + "]"
    if isinstance(value, dict):
        parts = []
        for key in sorted(value):
            if not isinstance(key, str):
                raise TypeError("canonical JSON object keys must be strings")
            parts.append(f"{_render(key)}:{_render(value[key])}")
        return "{" + ",".join(parts) + "}"
    raise TypeError(f"unsupported canonical JSON value: {type(value)!r}")


def _render_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal(1)))
    return format(normalized, "f").rstrip("0").rstrip(".")
