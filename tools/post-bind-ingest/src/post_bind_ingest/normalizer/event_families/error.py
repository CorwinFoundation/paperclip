"""`error` event family.

An error within a session that did not (necessarily) cause escalation. Schema
source: `contracts/log-event-schema-v1.md @ e19cfb9` §Event family — `error`.
"""

from __future__ import annotations

from . import FieldSpec

EVENT_TYPE = "error"

FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("code", "string", "yes", "clear", "customer_agent"),
    FieldSpec("message_preview", "string", "optional", "redact-on-receipt", "customer_agent"),
    FieldSpec("message_sha", "sha256", "yes", "clear", "backbond_normalizer"),
    FieldSpec("recoverable", "bool", "yes", "clear", "customer_agent"),
    FieldSpec("retry_count", "int", "yes", "clear", "customer_agent"),
)
