"""Canonical JSON serialization — same shape as the in-BackBond connector uses."""
from __future__ import annotations

import json
from typing import Any


def canonical_json(obj: Any) -> bytes:
    """sorted-key, no-whitespace, NFC-stable bytes for hashing or signing."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
