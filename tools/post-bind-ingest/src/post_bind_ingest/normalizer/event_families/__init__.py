"""Per-event-family field declarations.

One module per Log Event Schema v1.0 event family the normalizer emits. The
`claim_candidate_event` family is intentionally absent — its provenance is
`backbond_detector` and the normalizer never emits it (Schema §Event family —
claim_candidate_event). Detector children (BEAAA-5865/5866) will own that
family in `tools/post-bind-ingest/detectors/` under their own ADRs.

Each module exposes a `FIELDS` tuple of `FieldSpec` triples. The conformance
test `tests/test_event_families_field_conformance.py` walks every spec against
the contract source `contracts/log-event-schema-v1.md @ e19cfb9`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Required = Literal["yes", "optional", "conditional"]
Provenance = Literal[
    "customer_agent",
    "customer_log_sink",
    "backbond_normalizer",
    # NOTE: `backbond_detector` is intentionally excluded — this normalizer
    # never emits claim_candidate_event events.
]
RedactionClass = Literal["clear", "hash", "redact-on-receipt", "drop"]


@dataclass(frozen=True)
class FieldSpec:
    name: str
    field_type: str  # contract-level type string, e.g. "uuid", "sha256", "iso8601", "int", "bool", "string", or an enum tuple stringified
    required: Required
    redaction: RedactionClass
    provenance: Provenance
    notes: str = ""


# Common envelope: appears on every accepted event regardless of family.
COMMON_ENVELOPE: tuple[FieldSpec, ...] = (
    FieldSpec("schema_version", 'enum("log-event-schema-v1.0")', "yes", "clear", "backbond_normalizer"),
    FieldSpec(
        "event_type",
        'enum(agent_invocation, llm_completion, tool_call, tool_source_reference, escalation, error, security_event, pii_emission, claim_candidate_event)',
        "yes", "clear", "backbond_normalizer",
    ),
    FieldSpec("event_id", "uuid", "yes", "clear", "backbond_normalizer"),
    FieldSpec("customer_id", "uuid", "yes", "hash", "backbond_normalizer",
              notes="declared `hash` here as export-surface default; two-form treatment at sink boundary"),
    FieldSpec("agent_id", "uuid", "yes", "hash", "backbond_normalizer",
              notes="declared `hash` here as export-surface default; two-form treatment at sink boundary"),
    FieldSpec("session_id", "uuid", "yes", "hash", "customer_agent"),
    FieldSpec("turn_index", "int", "conditional", "clear", "customer_agent",
              notes="required when event_type ∈ {llm_completion, tool_call, tool_source_reference, escalation}"),
    FieldSpec("t_event", "iso8601", "yes", "clear", "customer_agent"),
    FieldSpec("t_received", "iso8601", "yes", "clear", "backbond_normalizer"),
    FieldSpec(
        "source_kind",
        'enum(datadog, splunk, cloudwatch, otel, stdout, other)',
        "yes", "clear", "customer_log_sink",
    ),
    FieldSpec("manifest_sha", "sha256", "yes", "clear", "backbond_normalizer"),
    FieldSpec("redaction_applied", "array<string>", "yes", "clear", "backbond_normalizer"),
    FieldSpec("partial", "bool", "yes", "clear", "backbond_normalizer"),
)
