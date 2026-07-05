"""Reference Mock-Tool Harness (BEAAA-11713, CI/CD WS-3, CTO Directive v2.2 §4.3).

Reference sandbox adapters for common tool surfaces (payments / CRM / email /
ticketing) so a design partner maps their agent onto our Standard Agent
Interface (WS-2) once instead of building their own mocked tools. Every
attempted action is recorded for scoring; nothing touches production systems.

Layering: WS-2 (harness.agent_interface) owns the conversation/tool-call schema
and the SCANNER_MEDIATED driver loop. WS-3 provides ``ReferenceToolSandbox``
(a drop-in for ``ProbeScript.sandbox``) backed by real, scoped, in-memory tool
surfaces, plus the ledger + scorer bridge that turn a recorded misuse attempt
into a scored ROBUSTNESS.TOOL_MISUSE result.
"""
from __future__ import annotations

from harness.mock_tools.harness import MockToolHarness
from harness.mock_tools.ledger import LedgerRow, ProductionAccessError, ToolLedger
from harness.mock_tools.sandbox import PilotMapping, ReferenceToolSandbox

__all__ = [
    "MockToolHarness",
    "PilotMapping",
    "ReferenceToolSandbox",
    "ToolLedger",
    "LedgerRow",
    "ProductionAccessError",
]
