"""`tool_source_reference` event family.

Records that a tool returned content from an external source. Drives P3.2
(indirect injection) and P4.2 (hallucinated citation) detectors downstream.
Schema source: `contracts/log-event-schema-v1.md @ e19cfb9` §Event family —
`tool_source_reference`.
"""

from __future__ import annotations

from . import FieldSpec

EVENT_TYPE = "tool_source_reference"

FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "source_kind",
        "enum(http_url, http_api, db_row, fs_file, mcp_resource, other)",
        "yes", "clear", "customer_agent",
    ),
    FieldSpec("source_id_sha", "sha256", "yes", "clear", "backbond_normalizer",
              notes="cleartext source identifier is drop"),
    FieldSpec("content_sha", "sha256", "yes", "clear", "backbond_normalizer"),
    FieldSpec("content_preview", "string", "optional", "redact-on-receipt", "customer_agent"),
    FieldSpec("paired_tool_call_event_id", "uuid", "yes", "clear", "backbond_normalizer"),
)
