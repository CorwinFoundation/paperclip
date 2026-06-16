"""Canonical-JSON serialization helpers.

RFC 8785 JCS compatible per Log Event Schema v1.0 §Field-shape conventions.
Mirrors `api/jcs.py` semantically but is intentionally not imported (Auditor
import-edge check — see `tests/test_no_score_imports.py`).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    """RFC 8785 JCS-compatible canonical JSON serialization."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_of(value: Any) -> str:
    """Contract-level `sha256:<hex>` form of a canonical-JSON value.

    Used wherever Schema v1.0 declares a `sha256` field type.
    """
    canonical = canonical_json(value)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
