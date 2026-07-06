"""Ticketing sandbox adapter (WS-3, BEAAA-11713).

Surface: ticketing. Operations:
  get_ticket     read-only, no scope
  create_ticket  side-effecting (low), scope ticketing:write
  close_ticket   side-effecting (low), scope ticketing:write
  escalate       side-effecting (HIGH), scope ticketing:escalate
"""
from __future__ import annotations

from typing import Any, Mapping

from harness.mock_tools.base import Operation, SandboxTool
from harness.mock_tools.interface import Impact


class TicketingTool(SandboxTool):
    surface = "ticketing"
    operations = {
        "get_ticket": Operation("get_ticket", Impact.READ_ONLY, None, "data_access"),
        "create_ticket": Operation("create_ticket", Impact.LOW, "ticketing:write", "tool_call"),
        "close_ticket": Operation("close_ticket", Impact.LOW, "ticketing:write", "tool_call"),
        "escalate": Operation("escalate", Impact.HIGH, "ticketing:escalate", "human_escalation"),
    }

    def _seed_state(self) -> None:
        self.state["tickets"] = {
            "tkt_0001": {"id": "tkt_0001", "status": "open", "priority": "normal"},
        }

    def _execute(self, op: Operation, params: Mapping[str, Any]) -> dict[str, Any]:
        tickets = self.state["tickets"]
        tid = params.get("ticket_id")
        if op.name == "get_ticket":
            t = tickets.get(tid)
            return {"ticket": t, "found": t is not None}
        if op.name == "create_ticket":
            tid = f"tkt_sandbox_{len(tickets):04d}"
            t = {
                "id": tid,
                "status": "open",
                "priority": params.get("priority", "normal"),
                "subject": params.get("subject", ""),
                "body": params.get("body", ""),
                "requester_email": params.get("requester_email", "ticketing"),
            }
            tickets[tid] = t
            return {"created": True, "ticket": t}
        if op.name == "close_ticket":
            t = tickets.get(tid)
            if t is None:
                return {"closed": False, "reason": "ticket_not_found"}
            t["status"] = "closed"
            return {"closed": True, "ticket": t}
        if op.name == "escalate":
            t = tickets.get(tid)
            if t is None:
                return {"escalated": False, "reason": "ticket_not_found"}
            t["priority"] = "urgent"
            return {"escalated": True, "ticket": t}
        return {}
