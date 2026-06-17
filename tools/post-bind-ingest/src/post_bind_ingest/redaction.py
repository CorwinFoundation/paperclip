from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from typing import Any

from .invariants import ALLOWED_REDACTION_CLASSES, canonical_dumps, normalize_redaction_class


class RedactionError(ValueError):
    """Raised when field-level redaction inputs are invalid."""


@dataclass(frozen=True)
class RedactedField:
    path: str
    redaction_class: str


def normalize_redaction_policy(policy: Any) -> dict[str, str]:
    if policy is None:
        return {}
    if not isinstance(policy, dict):
        raise RedactionError("redaction policy must be an object")
    if not policy:
        return {}
    normalized: dict[str, str] = {}
    for raw_path, raw_class in policy.items():
        if not isinstance(raw_path, str):
            raise RedactionError("redaction policy path must be a string")
        if not raw_path.strip():
            raise RedactionError("redaction policy path must be non-empty")
        try:
            normalized[raw_path.strip()] = normalize_redaction_class(raw_class)
        except ValueError as exc:
            raise RedactionError(str(exc)) from exc
    return normalized


def normalize_payload_redaction_plan(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RedactionError("redaction policy payload must be an object")
    if not value:
        return {}
    candidate_keys = ("field_redaction", "field_redactions", "fields", "fields_redaction", "field_policies")
    for key in candidate_keys:
        if key in value:
            return normalize_redaction_policy(value[key])
    return normalize_redaction_policy(value)


def apply_redaction(
    payload: dict[str, Any],
    policy: dict[str, str] | None = None,
) -> tuple[dict[str, Any], tuple[RedactedField, ...]]:
    event_payload = copy.deepcopy(payload)
    if not policy:
        return event_payload, ()

    redactions = []
    for path, redaction_class in policy.items():
        if redaction_class not in ALLOWED_REDACTION_CLASSES:
            raise RedactionError(f"unsupported redaction class: {redaction_class!r}")
        if _apply_path_redaction(event_payload, path.split("."), redaction_class):
            redactions.append(RedactedField(path=path, redaction_class=redaction_class))

    return event_payload, tuple(redactions)


def _apply_path_redaction(value: Any, path_parts: list[str], redaction_class: str) -> bool:
    if not path_parts:
        return False
    if not isinstance(value, dict):
        return False

    head = path_parts[0]
    if head == "*":
        any_applied = False
        for key in list(value):
            if len(path_parts) == 1:
                any_applied = _apply_field_redaction(value, key, redaction_class) or any_applied
                continue
            any_applied = _apply_path_redaction(value[key], path_parts[1:], redaction_class) or any_applied
        return any_applied

    if head not in value:
        return False
    if len(path_parts) == 1:
        return _apply_field_redaction(value, head, redaction_class)
    return _apply_path_redaction(value[head], path_parts[1:], redaction_class)


def _apply_field_redaction(container: dict[str, Any], field: str, redaction_class: str) -> bool:
    if field not in container:
        return False
    if redaction_class == "clear":
        container[field] = None
        return True
    if redaction_class == "redact-on-receipt":
        container[field] = "[REDACTED]"
        return True
    if redaction_class == "drop":
        container.pop(field, None)
        return True
    if redaction_class == "hash":
        container[field] = "sha256:" + hashlib.sha256(canonical_dumps(container[field])).hexdigest()
        return True
    return False
