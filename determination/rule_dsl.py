"""BEAAA-11372 shared deterministic rule DSL.

This module holds the generic, side-effect-free primitives shared by every
BackBond deterministic rule engine: dotted-path lookup, presence checks,
comparison operators, and canonical JSON hashing. It is deliberately domain
free — it knows nothing about the BackBond Scanner score envelope, runtime
evidence packets, or covered perils.

Both the underwriting decision engine (``underwriting/automatic_decision.py``)
and the runtime Determination Engine (``determination/engine.py``) call into
this module so the predicate/canonicalisation semantics stay identical instead
of being copied per engine. Per scanner-implementation-supervision-v1@v1.1, this
is the "refactor that doesn't touch behavior" lane: the automatic-underwriting
decision ids remain hash-stable (guarded by
``tests/test_automatic_underwriting_decision.py``).
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Mapping


class RuleEvalError(ValueError):
    """Raised when a rule table is malformed or a predicate cannot be evaluated."""


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    """Return canonical UTF-8 JSON bytes for a mapping.

    Keys are sorted, separators are tight, and non-ASCII is preserved so the
    same logical payload always produces the same bytes (and therefore the same
    SHA-256) across processes and machines.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def get_path(data: Mapping[str, Any], path: str) -> Any:
    """Resolve a dotted ``a.b.c`` path in a nested mapping, or ``None``."""
    current: Any = data
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return None
    return current


def path_missing(data: Mapping[str, Any], path: str) -> bool:
    """A path is missing when it is absent, empty string, or empty list."""
    value = get_path(data, path)
    return value is None or value == "" or value == []


def to_number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise RuleEvalError(f"expected numeric value, got {value!r}") from exc


def seq_length(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, Mapping):
        return len(value)
    if isinstance(value, (str, bytes)):
        return len(value)
    if isinstance(value, Iterable):
        return sum(1 for _ in value)
    return 0


def compare(actual: Any, operator: str, expected: Any) -> bool:
    """Evaluate ``actual <operator> expected`` for the supported operator set.

    This is the single shared operator vocabulary for BackBond rule tables.
    New operators are added here so every engine gains them at once.
    """
    if operator == "equals":
        return actual == expected
    if operator == "not_equals":
        return actual != expected
    if operator == "in":
        return actual in expected
    if operator == "not_in":
        return actual not in expected
    if operator == "less_than":
        return to_number(actual) < to_number(expected)
    if operator == "greater_than_or_equal":
        return to_number(actual) >= to_number(expected)
    if operator == "length_greater_than":
        return seq_length(actual) > int(expected)
    if operator == "contains_any":
        if not isinstance(actual, Iterable) or isinstance(actual, (str, bytes, Mapping)):
            return False
        actual_values = {str(item) for item in actual}
        expected_values = {str(item) for item in expected}
        return bool(actual_values & expected_values)
    if operator == "contains_none":
        if actual is None:
            return True
        if not isinstance(actual, Iterable) or isinstance(actual, (str, bytes, Mapping)):
            return False
        actual_values = {str(item) for item in actual}
        expected_values = {str(item) for item in expected}
        return not (actual_values & expected_values)
    if operator == "between_inclusive":
        lo, hi = expected
        value = to_number(actual)
        return to_number(lo) <= value <= to_number(hi)
    if operator == "in_ranges":
        value = to_number(actual)
        return any(to_number(lo) <= value <= to_number(hi) for lo, hi in expected)
    raise RuleEvalError(f"unsupported operator: {operator}")


__all__ = [
    "RuleEvalError",
    "canonical_json_bytes",
    "compare",
    "get_path",
    "path_missing",
    "seq_length",
    "to_number",
]
