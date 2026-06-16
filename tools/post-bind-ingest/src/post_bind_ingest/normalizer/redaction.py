"""Redaction floor for the post-bind normalizer.

Copy-and-adapt of the `SECRET_PATTERNS` substrate from
`tools/are-connector/src/are_connector/redaction.py` (BEAAA-5740). Per ADR-0034
§9 the post-bind ingest package may not import from the connector at runtime
(Auditor import-edge check enforced by `tests/test_no_score_imports.py`).
Drift between this copy and the connector source is caught CI-loud by
`tests/test_redaction_drift_vs_connector.py` — byte-equality against the
source patterns. Floor non-relaxation is a contract bump per Log Event Schema
v1.0 §Redaction policy classes.

Application order on receipt (Schema §Redaction policy classes — Floor rule):
1. Floor pass: every string-typed field runs through `SECRET_PATTERNS` and any
   match is replaced with `[REDACTED:secret_family]` — irrespective of the
   field's declared class.
2. Declared class pass: the field's declared class (`clear` / `hash` /
   `redact-on-receipt` / `drop`) is applied to the floor-scrubbed value.
3. The names of altered fields are appended (in deterministic order) to the
   event's `redaction_applied` array.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

# IMPORTANT (BEAAA-5816 §Status header A2 — symbol fix): the canonical floor
# pattern list is `SECRET_PATTERNS` in `tools/are-connector/src/are_connector/
# redaction.py`. The list below MUST byte-equal that source. The byte-equality
# guard test (`tests/test_redaction_drift_vs_connector.py`) reads both lists
# at import time and asserts identity on the compiled pattern strings.
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)\bDefaultEndpointsProtocol=https;AccountName=[^;\s]+;AccountKey=[^;\s]+(?:;EndpointSuffix=[^;\s]+)?"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}={0,2}"),
    re.compile(r"(?i)sk-[a-z0-9_-]{20,}"),
    re.compile(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"]?[^'\"\s]{12,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),
)

SECRET_FAMILY_REPLACEMENT = "[REDACTED:secret_family]"

# Redaction policy classes (Schema v1.0 §Redaction policy classes). The literal
# strings are the contract-level identifiers carried in the per-event-family
# field declarations under `src/post_bind_ingest/normalizer/event_families/`.
CLASS_CLEAR = "clear"
CLASS_HASH = "hash"
CLASS_REDACT_ON_RECEIPT = "redact-on-receipt"
CLASS_DROP = "drop"

REDACTION_CLASSES = (
    CLASS_CLEAR,
    CLASS_HASH,
    CLASS_REDACT_ON_RECEIPT,
    CLASS_DROP,
)

# PII / generic redaction tokens applied in `redact-on-receipt` mode. The
# redaction-on-receipt pass uses a single sentinel form `[REDACTED:<class>]`
# for cleartext-content placeholders to keep the post-redaction byte stream
# stable across pattern-list expansions.
_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("phone", re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("credit_card", re.compile(r"\b(?:\d[ -]*?){13,19}\b")),
    ("government_id", re.compile(r"\b[A-Z]{2}\d{6,12}\b")),
    ("address", re.compile(r"\b\d{1,6}\s+[A-Za-z0-9 .'-]+\s+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr)\b", re.I)),
)


def floor_scrub_secrets(text: str) -> tuple[str, bool]:
    """Run the secret-family floor pass on a single string.

    Returns (scrubbed_text, altered). `altered` is `True` iff the floor
    replaced at least one match. This is the byte-equal counterpart of
    `_scrub_secrets` in the are-connector redaction module.
    """
    scrubbed = text
    altered = False
    for pattern in SECRET_PATTERNS:
        new = pattern.sub(SECRET_FAMILY_REPLACEMENT, scrubbed)
        if new != scrubbed:
            altered = True
            scrubbed = new
    return scrubbed, altered


def apply_redact_on_receipt(text: str) -> tuple[str, bool]:
    """Apply the `redact-on-receipt` declared-class pass to a floor-scrubbed string.

    Returns (redacted_text, altered).
    """
    scrubbed = text
    altered = False
    for pii_class, pattern in _PII_PATTERNS:
        marker = f"[REDACTED:{pii_class}]"
        new = pattern.sub(marker, scrubbed)
        if new != scrubbed:
            altered = True
            scrubbed = new
    return scrubbed, altered


def hash_value(value: Any) -> str:
    """Compute the contract-level `sha256:<hex>` form of a canonical-JSON value.

    Used for the `hash` declared class. The hashing happens *after* the
    secret-family floor pass.
    """
    canonical = _canonical_json(value)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _canonical_json(value: Any) -> str:
    """RFC 8785 JCS-compatible canonical JSON serialization.

    Lifted from the contract-output convention (`are-scanner-api-v1@v1.0`
    §Canonical-JSON serialization, mirrored in Log Event Schema v1.0
    §Field-shape conventions). Sorted keys, no extra whitespace, NFC strings
    (UTF-8), float canonicalization handled by `json.dumps` for `allow_nan=False`.
    """
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def apply_field_redaction(
    value: Any,
    declared_class: str,
    field_path: str,
) -> tuple[Any, list[str]]:
    """Apply floor + declared-class redaction to a single field value.

    Returns (out_value, applied_paths). `applied_paths` contains `field_path`
    iff the value was altered by either pass (floor match, declared-class
    transform, or both). `drop`-class returns the sentinel `_DROPPED` and a
    one-entry `applied_paths`; the caller is responsible for omitting the
    field from the emitted event.
    """
    if declared_class not in REDACTION_CLASSES:
        raise ValueError(
            f"unknown redaction class {declared_class!r} for field {field_path!r}"
        )

    if declared_class == CLASS_DROP:
        return _DROPPED, [field_path]

    if isinstance(value, str):
        floored, floor_altered = floor_scrub_secrets(value)
    else:
        floored = value
        floor_altered = False

    declared_altered = False
    if declared_class == CLASS_CLEAR:
        out = floored
    elif declared_class == CLASS_HASH:
        out = hash_value(value)  # hash over the original pre-floor value
        declared_altered = True
    elif declared_class == CLASS_REDACT_ON_RECEIPT:
        if isinstance(floored, str):
            out, declared_altered = apply_redact_on_receipt(floored)
        else:
            out = floored
    else:  # pragma: no cover — guarded above
        raise AssertionError("unreachable")

    if floor_altered or declared_altered:
        return out, [field_path]
    return out, []


def merge_redaction_applied(target: list[str], additions: Iterable[str]) -> list[str]:
    """Append redaction paths in deterministic order, no duplicates."""
    seen = set(target)
    for path in additions:
        if path not in seen:
            target.append(path)
            seen.add(path)
    return target


class _DroppedSentinel:
    """Singleton marker for `drop`-class fields. Compare with `is _DROPPED`."""

    _instance: "_DroppedSentinel | None" = None

    def __new__(cls) -> "_DroppedSentinel":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<DROPPED>"


_DROPPED = _DroppedSentinel()
