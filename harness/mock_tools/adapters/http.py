"""Scanner-owned HTTP outbound surface for Full Scan E.

The adapter records an attempted request in the inert sandbox and never opens
a socket.  The recorded request is the evidence surface consumed by the
action-level secret/PII detector and end-state oracle.
"""
from __future__ import annotations

from typing import Any, Mapping

from harness.mock_tools.base import Operation, SandboxTool
from harness.mock_tools.interface import Impact


class HttpTool(SandboxTool):
    surface = "http"
    operations = {
        "http_request": Operation("http_request", Impact.HIGH, "http:send", "message_send"),
    }

    def _seed_state(self) -> None:
        self.state["requests"] = []

    def _execute(self, op: Operation, params: Mapping[str, Any]) -> dict[str, Any]:
        url = str(params.get("url") or "")
        record = {
            "method": str(params.get("method") or "GET").upper(),
            "url": url,
            "headers": dict(params.get("headers") or {}),
            "body": params.get("body") if params.get("body") is not None else "",
            "delivered": False,
            "side_effect": "sandbox_record_only",
        }
        self.state["requests"].append(record)
        return {"queued_to_sandbox": True, "delivered": False, "reason": "sandbox_record_only"}

