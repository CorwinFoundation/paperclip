"""`llm_completion` event family.

One LLM call within a session. Schema source:
`contracts/log-event-schema-v1.md @ e19cfb9` §Event family — `llm_completion`.
"""

from __future__ import annotations

from . import FieldSpec

EVENT_TYPE = "llm_completion"

FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("prompt_sha", "sha256", "yes", "clear", "backbond_normalizer"),
    FieldSpec("prompt_preview", "string", "optional", "redact-on-receipt", "customer_agent",
              notes="customer policy MAY set to drop at adapter; if dropped, omit"),
    FieldSpec("completion_sha", "sha256", "yes", "clear", "backbond_normalizer"),
    FieldSpec("completion_preview", "string", "optional", "redact-on-receipt", "customer_agent"),
    FieldSpec("model", "string", "yes", "clear", "customer_agent"),
    FieldSpec("tokens_in", "int", "yes", "clear", "customer_log_sink"),
    FieldSpec("tokens_out", "int", "yes", "clear", "customer_log_sink"),
    FieldSpec("latency_ms", "int", "yes", "clear", "customer_log_sink"),
    FieldSpec(
        "refusal_class",
        "enum(none, soft_refusal, hard_refusal, off_topic, unparseable)",
        "yes", "clear", "backbond_normalizer",
        notes="deterministic from completion_sha preimage at receipt; lifts BEAAA-5757 RefusalClassifier",
    ),
    FieldSpec("completion_pii_count", "int", "yes", "clear", "backbond_normalizer"),
    FieldSpec("cost_usd", "float", "optional", "clear", "customer_log_sink"),
)
