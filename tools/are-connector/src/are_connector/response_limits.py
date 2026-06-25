from __future__ import annotations

import os
import json
from typing import Any


MAX_RESPONSE_BYTES_ENV = "ARE_CONNECTOR_MAX_RESPONSE_BYTES"
DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024


class OversizedResponseError(ValueError):
    def __init__(self, observed_byte_count: int, max_response_bytes: int):
        self.observed_byte_count = observed_byte_count
        self.max_response_bytes = max_response_bytes
        super().__init__(
            "oversized_response: "
            f"observed_byte_count={observed_byte_count} "
            f"max_response_bytes={max_response_bytes}"
        )


def max_response_bytes() -> int:
    raw = os.environ.get(MAX_RESPONSE_BYTES_ENV)
    if raw is None or raw == "":
        return DEFAULT_MAX_RESPONSE_BYTES
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{MAX_RESPONSE_BYTES_ENV} must be an integer") from exc
    if value < 1:
        raise ValueError(f"{MAX_RESPONSE_BYTES_ENV} must be >= 1")
    return value


def ensure_under_response_cap_bytes(data: bytes, limit: int | None = None) -> bytes:
    cap = limit if limit is not None else max_response_bytes()
    observed = len(data)
    if observed > cap:
        raise OversizedResponseError(observed, cap)
    return data


def ensure_under_response_cap_text(text: str, limit: int | None = None) -> str:
    ensure_under_response_cap_bytes(text.encode("utf-8"), limit)
    return text


def ensure_json_serialized_under_response_cap(value: Any, limit: int | None = None) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    return ensure_under_response_cap_text(text, limit)
