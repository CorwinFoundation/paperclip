"""Per-run counters a human can read, plus the truthfulness assertion.

Every state the user is shown passes through :meth:`Telemetry.show`, so the
run's own record of what the user saw is the same object the counters come
from. ``LIVE`` is rejected there rather than checked for afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import ALLOWED_MODES
from .activation_view import NO_COVERAGE_STATEMENT


class UntruthfulStateError(RuntimeError):
    """Raised when a surface tries to render a state the pilot cannot back."""


@dataclass
class UserFacingState:
    name: str
    mode: str
    headline: str
    protection_statement: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.name,
            "mode": self.mode,
            "headline": self.headline,
            "protection_statement": self.protection_statement,
        }


@dataclass
class Telemetry:
    tasks_delegated: int = 0
    mandate_deviations: int = 0
    receipts_produced: int = 0
    covered_event_candidates_raised: int = 0
    # Actions outside the mandate stopped before the adapter was called.
    executions_refused: int = 0
    states_shown: list[UserFacingState] = field(default_factory=list)

    def show(
        self,
        name: str,
        *,
        mode: str,
        headline: str,
        protection_active: bool,
        protection_statement: str,
    ) -> UserFacingState:
        if mode not in ALLOWED_MODES:
            raise UntruthfulStateError(f"mode {mode!r} is not one of {ALLOWED_MODES}")
        if not protection_active and protection_statement != NO_COVERAGE_STATEMENT:
            raise UntruthfulStateError(
                f"state {name!r} would imply coverage that is not active"
            )
        state = UserFacingState(
            name=name,
            mode=mode,
            headline=headline,
            protection_statement=protection_statement,
        )
        self.states_shown.append(state)
        return state

    def counts(self) -> dict[str, int]:
        return {
            "tasks_delegated": self.tasks_delegated,
            "mandate_deviations": self.mandate_deviations,
            "receipts_produced": self.receipts_produced,
            "covered_event_candidates_raised": self.covered_event_candidates_raised,
            "executions_refused": self.executions_refused,
            "user_facing_states_shown": len(self.states_shown),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "counts": self.counts(),
            "user_facing_states": [s.as_dict() for s in self.states_shown],
        }
