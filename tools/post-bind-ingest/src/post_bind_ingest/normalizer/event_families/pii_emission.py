"""`pii_emission` event family.

A PII-class value was observed in agent output before redaction. Schema source:
`contracts/log-event-schema-v1.md @ e19cfb9` §Event family — `pii_emission`.

If `redaction_method_used == "none"` AND the source field was declared `clear`,
the normalizer MUST also fire a paired `security_event(redaction_floor_triggered)`
per Schema §pii_emission notes.
"""

from __future__ import annotations

from . import FieldSpec

EVENT_TYPE = "pii_emission"

FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "pii_class",
        "enum(email, phone, ssn, credit_card, bank_account, address, name, dob, government_id, other_pii)",
        "yes", "clear", "backbond_normalizer",
    ),
    FieldSpec("count", "int", "yes", "clear", "backbond_normalizer"),
    FieldSpec(
        "location",
        "enum(prompt, completion, tool_args, tool_return, tool_source_content, error_message)",
        "yes", "clear", "backbond_normalizer",
    ),
    FieldSpec("evidence_event_id", "uuid", "yes", "clear", "backbond_normalizer"),
    FieldSpec(
        "redaction_method_used",
        "enum(redact-on-receipt, hash, drop, none)",
        "yes", "clear", "backbond_normalizer",
    ),
)
