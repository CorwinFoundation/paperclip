"""`escalation` event family.

Agent escalated to a human. Schema source:
`contracts/log-event-schema-v1.md @ e19cfb9` §Event family — `escalation`.
"""

from __future__ import annotations

from . import FieldSpec

EVENT_TYPE = "escalation"

FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "reason_code",
        "enum(unable_to_complete, refusal_policy, high_uncertainty, customer_escape_hatch, security_policy_block, other)",
        "yes", "clear", "customer_agent",
    ),
    FieldSpec("transcript_sha", "sha256", "yes", "clear", "backbond_normalizer"),
    FieldSpec("severity", "enum(low, medium, high, critical)", "yes", "clear", "customer_agent"),
    FieldSpec("notified_human", "bool", "yes", "clear", "customer_agent"),
    FieldSpec(
        "notification_channel",
        "enum(ui, page, ticket, email, slack, other, none)",
        "yes", "clear", "customer_agent",
    ),
)
