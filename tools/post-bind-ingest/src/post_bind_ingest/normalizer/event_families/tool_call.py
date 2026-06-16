"""`tool_call` event family.

One tool invocation within a session. Schema source:
`contracts/log-event-schema-v1.md @ e19cfb9` §Event family — `tool_call`.
"""

from __future__ import annotations

from . import FieldSpec

EVENT_TYPE = "tool_call"

FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("tool_name", "string", "yes", "clear", "customer_agent"),
    FieldSpec(
        "tool_source_kind",
        "enum(internal, http, db, fs, mcp, plugin, other)",
        "yes", "clear", "customer_agent",
    ),
    FieldSpec("tool_scope", "string", "yes", "clear", "customer_agent"),
    FieldSpec("args_sha", "sha256", "yes", "clear", "backbond_normalizer"),
    FieldSpec("args_preview", "string", "optional", "redact-on-receipt", "customer_agent"),
    FieldSpec("return_sha", "sha256", "yes", "clear", "backbond_normalizer"),
    FieldSpec("return_preview", "string", "optional", "redact-on-receipt", "customer_agent"),
    FieldSpec("ok", "bool", "yes", "clear", "customer_agent"),
    FieldSpec("latency_ms", "int", "yes", "clear", "customer_log_sink"),
    FieldSpec("tool_source_reference_event_id", "uuid", "optional", "clear", "backbond_normalizer"),
)
