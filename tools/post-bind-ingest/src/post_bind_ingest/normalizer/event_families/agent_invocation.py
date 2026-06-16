"""`agent_invocation` event family.

Marks the start of an agent session under coverage. Replaces / formalizes the
ADR-0034 §3.1 `agent_session_start` sketch. Schema source:
`contracts/log-event-schema-v1.md @ e19cfb9` §Event family — `agent_invocation`.
"""

from __future__ import annotations

from . import FieldSpec

EVENT_TYPE = "agent_invocation"

FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("agent_version", "string", "yes", "clear", "customer_agent"),
    FieldSpec("agent_invocation_args_preview", "string", "optional", "redact-on-receipt", "customer_agent"),
    FieldSpec("agent_invocation_args_sha", "sha256", "yes", "clear", "backbond_normalizer",
              notes="SHA over the raw (pre-redaction) args canonical-JSON"),
    FieldSpec("model", "string", "optional", "clear", "customer_agent"),
    FieldSpec("scanner_version_at_bind", "string", "yes", "clear", "backbond_normalizer"),
    FieldSpec("entrypoint", "string", "optional", "clear", "customer_agent"),
)
