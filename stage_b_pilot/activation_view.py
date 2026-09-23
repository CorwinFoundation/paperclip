"""The pilot's only route to protection status.

Re-exports the BEAAA-35012 gate so no module in this package can grow its own
notion of coverage. Nothing here takes a scan result, receipt, policy id or
subscription as input.
"""

from __future__ import annotations

from datetime import datetime

from stage_a_demo.activation import (  # noqa: F401  (re-export is the point)
    NO_COVERAGE_STATEMENT,
    ActivationStatus,
    RecordRegistry,
    check_activation,
    default_program_version,
    default_registry,
)


def pilot_status(
    *,
    program_version: str | None = None,
    registry: RecordRegistry | None = None,
    now: datetime | None = None,
) -> ActivationStatus:
    return check_activation(
        program_version or default_program_version(),
        registry if registry is not None else default_registry(),
        now=now,
    )
