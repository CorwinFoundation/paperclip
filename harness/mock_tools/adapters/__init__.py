"""Reference sandbox adapters implementing the Standard Agent Interface.

BEAAA-11713 (WS-3). Import ``ADAPTERS`` to get the surface->class registry.
"""
from __future__ import annotations

from harness.mock_tools.adapters.crm import CrmTool
from harness.mock_tools.adapters.email import EmailTool
from harness.mock_tools.adapters.ledger import LedgerTool
from harness.mock_tools.adapters.payments import PaymentsTool
from harness.mock_tools.adapters.ticketing import TicketingTool

ADAPTERS = {
    PaymentsTool.surface: PaymentsTool,
    CrmTool.surface: CrmTool,
    EmailTool.surface: EmailTool,
    TicketingTool.surface: TicketingTool,
    LedgerTool.surface: LedgerTool,
}

__all__ = ["ADAPTERS", "PaymentsTool", "CrmTool", "EmailTool", "TicketingTool", "LedgerTool"]
