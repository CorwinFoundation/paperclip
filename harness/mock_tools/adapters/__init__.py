"""Reference sandbox adapters implementing the Standard Agent Interface.

BEAAA-11713 (WS-3). Import ``ADAPTERS`` to get the surface->class registry.
"""
from __future__ import annotations

from harness.mock_tools.adapters.catalog import CatalogTool
from harness.mock_tools.adapters.crm import CrmTool
from harness.mock_tools.adapters.email import EmailTool
from harness.mock_tools.adapters.ledger import LedgerTool
from harness.mock_tools.adapters.payments import PaymentsTool
from harness.mock_tools.adapters.records import RecordsTool
from harness.mock_tools.adapters.ticketing import TicketingTool
from harness.mock_tools.adapters.warranty_authority import WarrantyAuthorityTool

ADAPTERS = {
    PaymentsTool.surface: PaymentsTool,
    CrmTool.surface: CrmTool,
    EmailTool.surface: EmailTool,
    RecordsTool.surface: RecordsTool,
    TicketingTool.surface: TicketingTool,
    LedgerTool.surface: LedgerTool,
    CatalogTool.surface: CatalogTool,
    WarrantyAuthorityTool.surface: WarrantyAuthorityTool,
}

__all__ = [
    "ADAPTERS",
    "PaymentsTool",
    "CrmTool",
    "EmailTool",
    "RecordsTool",
    "TicketingTool",
    "LedgerTool",
    "CatalogTool",
    "WarrantyAuthorityTool",
]
