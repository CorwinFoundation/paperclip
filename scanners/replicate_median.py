"""Shared K-replicate median — single source of truth (BEAAA-544 T5 spec).

This is a **dependency-free leaf module**: it imports nothing beyond the
standard library so it can be imported from BOTH the Prober-side
orchestrator (:mod:`scanners.live_probe_orchestrator`) and the Scorer-side
worker (:mod:`scanners.live_probe_workers`) without dragging any heavy or
credential-bearing module across either import surface.

Why a leaf module instead of importing one from the other
--------------------------------------------------------
``scanners.live_probe_orchestrator`` transitively imports
``harness.credentials`` (it resolves the vault on the Prober side). The
Scorer-side ``scanners.live_probe_workers`` is required by Hard Invariant #4
(import isolation, verified by
``tests/test_live_probe_workers_beaaa542.py::test_module_does_not_import_credentials_at_module_load``)
to keep ``harness.credentials`` off its module-load surface. So the two
sides cannot import ``_median_of`` from each other. Extracting the single
definition here lets both import it — the orchestrator's
``ScanResult.dim_results[dim].raw`` and the Scorer's persisted envelope are
therefore aggregated by the IDENTICAL function, byte-identical BY
CONSTRUCTION (BEAAA-14422 / code-review F3).
"""
from __future__ import annotations

from typing import Sequence


def _median_of(values: Sequence[float]) -> float:
    """Median per T5 spec.

    * Odd K (default K=5): middle of the sorted list.
    * Even K (K=10): lower-of-two-middles convention — keeps the aggregated
      raw inside the observed range rather than an interpolated value that
      no replicate actually produced (deterministic, audit-friendly).
    """
    if not values:
        raise ValueError("_median_of: empty sequence")
    sorted_values = sorted(values)
    n = len(sorted_values)
    if n % 2 == 1:
        return float(sorted_values[n // 2])
    return float(sorted_values[n // 2 - 1])


__all__ = ["_median_of"]
