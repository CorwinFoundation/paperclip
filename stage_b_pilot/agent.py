"""Controlled reference agent adapter.

Stands in for a personal purchase or booking agent we do not control. It
exercises the same seam a real partner agent would: a task intent goes in, the
agent decides what to actually do, and an outcome event comes out. It never
touches a payment rail and never makes a network call.

The agent is deliberately outside our enforcement boundary. BackBond observes
the deviation; it does not stop the agent. That is what a third-party consumer
agent looks like from here, and pretending otherwise would make the pilot's
numbers meaningless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SUPPORTED_TASK_TYPES = ("purchase",)


@dataclass(frozen=True)
class TaskIntent:
    """What the human actually asked for, before any agent interpretation."""

    intent_id: str
    task_type: str
    description: str
    sku: str
    max_amount_cents: int

    def supported(self) -> bool:
        return self.task_type in SUPPORTED_TASK_TYPES


@dataclass(frozen=True)
class PlannedAction:
    """What the reference agent says it is about to do."""

    sku: str
    amount_cents: int
    rationale: str


@dataclass(frozen=True)
class ReferenceAgent:
    """Deterministic stand-in agent. Seeded, no model call, no network.

    ``deviation`` is how the seeded covered-mistake scenario is produced: a
    scripted substitution the agent makes without asking. With no deviation
    the agent does exactly what the intent said.
    """

    agent_id: str = "reference-agent-0.1.0"
    deviation: dict[str, Any] = field(default_factory=dict)

    def plan(self, intent: TaskIntent) -> PlannedAction:
        sku = self.deviation.get("sku", intent.sku)
        amount = self.deviation.get("amount_cents", intent.max_amount_cents)
        rationale = self.deviation.get(
            "rationale", "executing the delegated task as stated"
        )
        return PlannedAction(sku=sku, amount_cents=int(amount), rationale=rationale)
