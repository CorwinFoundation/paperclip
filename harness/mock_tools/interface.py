"""WS-3 ↔ WS-2 seam: value types for the reference Mock-Tool Harness.

BEAAA-11713 (CI/CD WS-3, CTO Directive v2.2 §4.3).

WS-3 does NOT define its own conversation/tool-call schema. It reuses the
Standard Agent Interface (WS-2, BEAAA-11712 / ADR-0042) types verbatim:

    ToolCall, ToolResult, ConversationTrace, ToolVisibilityMode, ACTION_CLASSES

and adds only what a *reference tool implementation* needs beyond WS-2's
allowlist ``ToolSandbox``: per-surface operations, side-effect impact, and
scope-based authorization. The `ReferenceToolSandbox` (see ``sandbox``) is a
drop-in for ``ProbeScript.sandbox`` — same ``handle(call) -> ToolResult``
contract — so a WS-2 ``SessionProbeDriver`` drives our sandboxes unchanged.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum

# Re-export the WS-2 SAI types so WS-3 code has one import surface and the
# dependency on WS-2 landing is explicit (this import fails until WS-2 lands).
from harness.agent_interface import (  # noqa: F401
    ACTION_CLASSES,
    ConversationTrace,
    ToolCall,
    ToolResult,
    ToolVisibilityMode,
)


class Decision(str, Enum):
    """What the reference sandbox did with an attempted tool call.

    Derived (not a new wire field): projected from the WS-2 ``ToolResult``
    (``authorized`` / ``is_error``) plus WS-3 impact, and stashed in
    ``ToolResult.raw['decision']`` for the scorer bridge.
    """

    EXECUTED_SANDBOX = "executed_sandbox"
    BLOCKED_POLICY = "blocked_policy"
    REFUSED = "refused"


class Impact(str, Enum):
    READ_ONLY = "read_only"
    LOW = "low"
    HIGH = "high"  # side-effecting: money movement, deletion, outbound send


# Impact -> evidence-profile-v1 action_class (must stay within ACTION_CLASSES).
# Surfaces override where a more specific class applies (see adapters).
ACTION_CLASS_DEFAULT = "tool_call"


@dataclass(frozen=True)
class Operation:
    """One callable operation on a reference tool surface."""

    name: str
    impact: Impact
    required_scope: str | None          # None => no scope needed
    action_class: str = ACTION_CLASS_DEFAULT

    def __post_init__(self) -> None:
        if self.action_class not in ACTION_CLASSES:
            raise ValueError(
                f"action_class {self.action_class!r} not in evidence-profile-v1 enum"
            )


@dataclass(frozen=True)
class AuthContext:
    """Scopes the mapped agent legitimately holds, per the pilot YAML."""

    principal: str
    scopes: frozenset[str] = field(default_factory=frozenset)

    def has(self, scope: str) -> bool:
        return scope in self.scopes


def dumps_canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
