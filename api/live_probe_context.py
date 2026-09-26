"""Report-only underwriting context for live-probe evidence.

The context is deliberately outside scoring.  It gives an underwriter the
operating denominator around an observed probe result without treating a
missing declaration as a control.  Callers that do not supply
``underwriting_context`` retain their legacy evidence shape.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any


UNKNOWN = "unknown"

_GROUP_FIELDS: dict[str, tuple[str, ...]] = {
    "tooling": ("tools", "permission_classes", "action_classes"),
    "autonomy": ("autonomy_level", "approval_boundaries"),
    "materiality": ("action_volume", "records_at_risk", "value_at_risk", "blast_radius"),
    "human_review": ("review_events", "intervention_events"),
    "data_handling": ("pii_flow", "retention", "access_control_references"),
}

# Key names, rather than value heuristics, make the exclusion deterministic.
# References to access controls are allowed; authentication material is not.
_SENSITIVE_KEY_PARTS = (
    "credential",
    "password",
    "secret",
    "api_key",
    "apikey",
    "access_token",
    "auth_token",
    "system_prompt",
    "user_prompt",
    "prompt_text",
    "raw_prompt",
)


def _safe_copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _safe_copy(item)
            for key, item in value.items()
            if not any(part in str(key).lower() for part in _SENSITIVE_KEY_PARTS)
        }
    if isinstance(value, (list, tuple)):
        return [_safe_copy(item) for item in value]
    if value is None:
        return UNKNOWN
    return copy.deepcopy(value)


def serialize_underwriting_context(value: Any) -> dict[str, dict[str, Any]]:
    """Return the fixed five-group envelope with explicit unknowns.

    Unknown group names and fields are ignored.  This prevents arbitrary
    caller payloads, including credentials or proprietary prompts, from being
    copied into forensic evidence.
    """
    supplied = value if isinstance(value, Mapping) else {}
    result: dict[str, dict[str, Any]] = {}
    for group, fields in _GROUP_FIELDS.items():
        raw_group = supplied.get(group)
        group_values = raw_group if isinstance(raw_group, Mapping) else {}
        result[group] = {
            field: _safe_copy(group_values[field]) if field in group_values else UNKNOWN
            for field in fields
        }
    return result


def serialize_live_probe_evidence(record: Mapping[str, Any]) -> dict[str, Any]:
    """Copy one record, normalizing context only when the caller supplied it."""
    result = copy.deepcopy(dict(record))
    if "underwriting_context" in result:
        result["underwriting_context"] = serialize_underwriting_context(
            result["underwriting_context"]
        )
    return result


__all__ = [
    "UNKNOWN",
    "serialize_live_probe_evidence",
    "serialize_underwriting_context",
]
