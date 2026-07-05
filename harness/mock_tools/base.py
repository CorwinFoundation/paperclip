"""SandboxTool base — reference tool-surface implementation (WS-3, BEAAA-11713).

A SandboxTool models one tool surface (payments / CRM / email / ticketing) with
real in-memory state and scope-based authorization. It NEVER performs real I/O.
Given a WS-2 ``ToolCall`` it returns a WS-2 ``ToolResult`` whose ``raw`` carries
the WS-3 scoring context (decision, impact, surface, operation, side_effect_target)
so the scorer bridge can derive a rubric label. A side-effecting call the caller
is not scoped for is recorded ``authorized=False`` and NOT executed — this is the
"tool-harness policy" the ROBUSTNESS.TOOL_MISUSE rubric refers to.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping

from harness.mock_tools.interface import (
    AuthContext,
    Decision,
    Impact,
    Operation,
    ToolCall,
    ToolResult,
)


class SandboxTool(ABC):
    """Base class for reference sandbox adapters."""

    #: surface name as mapped in the pilot YAML, e.g. "payments"
    surface: str = "abstract"
    #: operation name -> Operation metadata
    operations: dict[str, Operation] = {}

    def __init__(self) -> None:
        self.state: dict[str, Any] = {}
        self._seed_state()

    def _seed_state(self) -> None:  # pragma: no cover - optional hook
        """Populate deterministic fixture state (no randomness / clock)."""

    def authorize(self, op: Operation, authz: AuthContext) -> bool:
        return op.required_scope is None or authz.has(op.required_scope)

    def dispatch(self, call: ToolCall, operation: str, authz: AuthContext) -> ToolResult:
        """Run a call against the sandbox, enforcing scope policy.

        ``operation`` is the resolved reference operation (the pilot mapping
        translated the partner tool name into it). Returns a WS-2 ToolResult.
        """
        op = self.operations.get(operation)
        if op is None:
            return self._result(call, ok=False, decision=Decision.BLOCKED_POLICY,
                                impact=Impact.HIGH, authorized=False,
                                content=f"unknown operation {operation!r} on {self.surface!r}",
                                op=None)
        authorized = self.authorize(op, authz)
        if not authorized:
            # Attempted but blocked by tool-harness policy. No side effect runs.
            return self._result(
                call, ok=False, decision=Decision.BLOCKED_POLICY, impact=op.impact,
                authorized=False, op=op,
                content=(f"DENIED: principal {authz.principal!r} lacks scope "
                         f"{op.required_scope!r} for {self.surface}.{op.name}"),
            )
        payload = self._execute(op, dict(call.arguments))
        return self._result(call, ok=True, decision=Decision.EXECUTED_SANDBOX,
                            impact=op.impact, authorized=True, op=op, content=payload)

    def _result(self, call: ToolCall, *, ok: bool, decision: Decision, impact: Impact,
                authorized: bool, content: Any, op: Operation | None) -> ToolResult:
        return ToolResult(
            call_id=call.call_id,
            tool_name=call.tool_name,
            content=content,
            is_error=not ok,
            authorized=authorized,
            raw={
                "decision": decision.value,
                "impact": impact.value,
                "surface": self.surface,
                "operation": op.name if op else call.tool_name,
                # Invariant asserted by the ledger: reference tools never leave
                # the sandbox. A real adapter would set this to its live target.
                "side_effect_target": "sandbox",
            },
        )

    @abstractmethod
    def _execute(self, op: Operation, params: Mapping[str, Any]) -> dict[str, Any]:
        """Perform the operation against in-memory state ONLY. No real I/O."""
        raise NotImplementedError
