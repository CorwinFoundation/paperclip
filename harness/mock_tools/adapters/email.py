"""Email sandbox adapter (WS-3, BEAAA-11713).

Surface: email. Operations:
  send_email  side-effecting (HIGH, outbound), scope email:send

Even with the scope, sends default-deny to any recipient outside the pilot's
owned-recipient allowlist — matching the sandbox transport discipline used for
support/voice scans. Every "send" is dropped to the ledger; nothing leaves.
"""
from __future__ import annotations

from typing import Any, Mapping

from harness.mock_tools.base import Operation, SandboxTool
from harness.mock_tools.interface import Impact

# Owned test recipients. Anything else is dropped even when scoped.
DEFAULT_ALLOWLIST = ("qa@sandbox.backbond.test", "pilot@sandbox.backbond.test")


class EmailTool(SandboxTool):
    surface = "email"
    operations = {
        "send_email": Operation("send_email", Impact.HIGH, "email:send", "message_send"),
    }

    def __init__(self, allowlist: tuple[str, ...] = DEFAULT_ALLOWLIST) -> None:
        self.allowlist = tuple(allowlist)
        super().__init__()

    def _seed_state(self) -> None:
        self.state["outbox"] = []  # dropped sends, for evidence only

    def _execute(self, op: Operation, params: Mapping[str, Any]) -> dict[str, Any]:
        to = params.get("to", "")
        allowed = to in self.allowlist
        record = {
            "to": to,
            "subject": params.get("subject", ""),
            "delivered": False,  # sandbox never delivers
            "allowlisted": allowed,
        }
        self.state["outbox"].append(record)
        return {
            "queued_to_sandbox": True,
            "delivered": False,
            "allowlisted": allowed,
            "reason": "sandbox_drop_log" if allowed else "default_deny_recipient",
        }
