"""Detector input normalization for vendor-side runtime evidence.

Vendors emit ``tool_call`` events with the tool identity carried in a variety
of field shapes (``tool``, ``tool_name``, OpenAI-style ``function.name``,
nested ``tool.name``, etc.). The passive peril detectors classify perils by
matching the tool name against known-dangerous tool sets, so a shape variant
the detector does not understand silently drops a peril candidate — a
Monitor-readiness blocker. This module gives every detector a single,
deterministic tool-name extractor so shape variance never causes an
under-count.

This is runtime-evidence input normalization ONLY. It imports no Scanner
scoring, no rubric, and no LLM judge, per Company Canon §10 (vendor-side
runtime evidence, NOT portal_v1).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


# Scalar keys that may directly hold a tool name, in priority order. Earlier
# keys win so an explicit ``tool``/``tool_name`` is never shadowed by a generic
# ``name``.
_SCALAR_TOOL_KEYS: tuple[str, ...] = (
    "tool",
    "tool_name",
    "toolName",
    "function_name",
    "name",
    "action",
    "action_name",
)

# Object-valued keys whose ``name`` sub-field carries the tool name, e.g.
# OpenAI ``{"function": {"name": ...}}`` or nested ``{"tool": {"name": ...}}``.
_NESTED_TOOL_CONTAINERS: tuple[str, ...] = (
    "tool",
    "function",
    "tool_call",
    "action",
)


def _coerce_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def normalize_tool_name(payload: Mapping[str, Any] | Any) -> str:
    """Return the lower-cased tool name from any recognized tool-call shape.

    Returns ``""`` when no tool name can be located. Deterministic and
    side-effect free: identical input always yields identical output.
    """

    if not isinstance(payload, Mapping):
        return ""

    for key in _SCALAR_TOOL_KEYS:
        name = _coerce_name(payload.get(key))
        if name:
            return name

    for container_key in _NESTED_TOOL_CONTAINERS:
        container = payload.get(container_key)
        if isinstance(container, Mapping):
            name = _coerce_name(container.get("name"))
            if name:
                return name
            # OpenAI tool_call shape: {"tool_call": {"function": {"name": ...}}}
            inner = container.get("function")
            if isinstance(inner, Mapping):
                name = _coerce_name(inner.get("name"))
                if name:
                    return name

    return ""
