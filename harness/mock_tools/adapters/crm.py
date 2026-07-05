"""CRM sandbox adapter (WS-3, BEAAA-11713).

Surface: crm. Operations:
  get_contact     read-only, no scope
  update_contact  side-effecting (low), scope crm:write
  delete_contact  side-effecting (HIGH), scope crm:delete
"""
from __future__ import annotations

from typing import Any, Mapping

from harness.mock_tools.base import Operation, SandboxTool
from harness.mock_tools.interface import Impact


class CrmTool(SandboxTool):
    surface = "crm"
    operations = {
        "get_contact": Operation("get_contact", Impact.READ_ONLY, None, "data_access"),
        "update_contact": Operation("update_contact", Impact.LOW, "crm:write", "data_access"),
        "delete_contact": Operation("delete_contact", Impact.HIGH, "crm:delete", "admin_change"),
    }

    def _seed_state(self) -> None:
        self.state["contacts"] = {
            "cnt_0001": {"id": "cnt_0001", "name": "Pilot Customer", "tier": "smb"},
        }

    def _execute(self, op: Operation, params: Mapping[str, Any]) -> dict[str, Any]:
        contacts = self.state["contacts"]
        cid = params.get("contact_id")
        if op.name == "get_contact":
            c = contacts.get(cid)
            return {"contact": c, "found": c is not None}
        if op.name == "update_contact":
            c = contacts.get(cid)
            if c is None:
                return {"updated": False, "reason": "contact_not_found"}
            fields = params.get("fields", {})
            c.update({k: fields[k] for k in sorted(fields)})
            return {"updated": True, "contact": c}
        if op.name == "delete_contact":
            existed = contacts.pop(cid, None) is not None
            return {"deleted": existed}
        return {}
