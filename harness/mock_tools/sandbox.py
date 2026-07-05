"""ReferenceToolSandbox — drop-in tool runtime for WS-2's SCANNER_MEDIATED loop.

BEAAA-11713 (WS-3). WS-2 ships a minimal ``ToolSandbox`` (allowlist of tool
names + canned string results). WS-3 ships this richer sandbox: the four
reference surfaces (payments / CRM / email / ticketing) with real in-memory
state, per-operation scopes, and side-effect impact — the "reference harness"
the directive promises so vendors don't build their own mocked tools.

It exposes the exact same ``handle(call: ToolCall) -> ToolResult`` contract as
WS-2's ``ToolSandbox``, so a WS-2 ``SessionProbeDriver`` drives it unchanged.
Every attempted call is recorded to a ``ToolLedger``; nothing touches prod.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from harness.mock_tools.adapters import ADAPTERS
from harness.mock_tools.base import SandboxTool
from harness.mock_tools.interface import AuthContext, Decision, Impact, ToolCall, ToolResult
from harness.mock_tools.ledger import ToolLedger


@dataclass(frozen=True)
class ToolBinding:
    """One pilot-YAML row: partner tool name -> (reference surface, operation)."""

    partner_tool: str
    surface: str
    operation: str


class PilotMapping:
    """Parsed 'one block of YAML' for a design partner."""

    def __init__(self, raw: Mapping[str, Any]) -> None:
        self.partner: str = raw["partner"]
        self.principal: str = raw.get("principal", self.partner)
        self.scopes: frozenset[str] = frozenset(raw.get("authorized_scopes", []))
        self.bindings: dict[str, ToolBinding] = {}
        for row in raw.get("tool_bindings", []):
            b = ToolBinding(row["partner_tool"], row["surface"], row["operation"])
            self.bindings[b.partner_tool] = b

    @classmethod
    def from_yaml(cls, path: str | Path) -> "PilotMapping":
        return cls(yaml.safe_load(Path(path).read_text()))

    def declared_tools(self) -> tuple[str, ...]:
        return tuple(sorted(self.bindings))


class ReferenceToolSandbox:
    """Scanner-side tool runtime backed by the reference sandbox adapters."""

    def __init__(self, mapping: PilotMapping, *,
                 ledger: ToolLedger | None = None,
                 session_id: str = "sandbox") -> None:
        self.mapping = mapping
        self.ledger = ledger if ledger is not None else ToolLedger()
        self.session_id = session_id
        self.authz = AuthContext(principal=mapping.principal, scopes=mapping.scopes)
        self._tools: dict[str, SandboxTool] = {}
        for b in mapping.bindings.values():
            if b.surface not in self._tools:
                if b.surface not in ADAPTERS:
                    raise ValueError(f"no reference adapter for surface {b.surface!r}")
                self._tools[b.surface] = ADAPTERS[b.surface]()

    def surface_state(self, surface: str) -> dict[str, Any]:
        return self._tools[surface].state

    def handle(self, call: ToolCall) -> ToolResult:
        """WS-2 ToolSandbox-compatible entry point. Records + returns a result."""
        binding = self.mapping.bindings.get(call.tool_name)
        if binding is None:
            result = ToolResult(
                call_id=call.call_id, tool_name=call.tool_name,
                content=f"DENIED: tool {call.tool_name!r} not in pilot mapping",
                is_error=True, authorized=False,
                raw={"decision": Decision.BLOCKED_POLICY.value,
                     "impact": Impact.HIGH.value, "surface": None,
                     "operation": call.tool_name, "side_effect_target": "sandbox"},
            )
            self.ledger.record(self.session_id, call, result)
            return result

        tool = self._tools[binding.surface]
        result = tool.dispatch(call, binding.operation, self.authz)
        self.ledger.record(self.session_id, call, result)
        return result
