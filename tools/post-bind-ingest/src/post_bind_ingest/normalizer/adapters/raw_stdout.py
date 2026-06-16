"""`raw_stdout` adapter — MVP minimum per CTO brief §4.

Reads JSON-Lines records produced by the customer's stdout log sink. Each
record is a JSON object carrying a `kind` discriminator and a free-form
`payload` whose shape the normalizer maps onto a Log Event Schema v1.0 event
family.

This adapter does NOT redact, does NOT compute SHAs, and does NOT enforce
invariants. It simply parses, validates the minimal envelope, and yields raw
records to `normalizer.normalize_stream`. The normalizer owns redaction,
hashing, and invariant enforcement.

The Datadog / Splunk / CloudWatch / OTEL adapters are Phase 2.0.1 sub-children.
"""

from __future__ import annotations

import json
from typing import Iterable, Iterator


RAW_STDOUT_KIND_TO_EVENT_TYPE: dict[str, str] = {
    "agent.invocation": "agent_invocation",
    "agent.llm_completion": "llm_completion",
    "agent.tool_call": "tool_call",
    "agent.tool_source_reference": "tool_source_reference",
    "agent.escalation": "escalation",
    "agent.error": "error",
    "agent.security": "security_event",
    "agent.pii": "pii_emission",
}


def parse_lines(lines: Iterable[str]) -> Iterator[dict]:
    """Parse each line as a raw record. Skip blank lines.

    Raises `ValueError` on a malformed line that cannot be decoded as JSON. The
    receiver-side caller is responsible for routing unparseable records to the
    dead-letter queue rather than letting them propagate.
    """
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"raw_stdout: bad JSON line: {exc}") from exc
        if not isinstance(record, dict):
            raise ValueError("raw_stdout: each line must encode a JSON object")
        if "kind" not in record or "payload" not in record:
            raise ValueError("raw_stdout: each record requires 'kind' and 'payload' keys")
        kind = record["kind"]
        if kind not in RAW_STDOUT_KIND_TO_EVENT_TYPE:
            raise ValueError(f"raw_stdout: unknown kind {kind!r}")
        yield record
