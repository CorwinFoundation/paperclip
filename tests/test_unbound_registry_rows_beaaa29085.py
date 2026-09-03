"""BEAAA-29085: unbound rows must not enter full-scan acceptance."""
from __future__ import annotations

from collections import Counter
import hashlib
import json

import pytest

from tools.active_test_registry import build_registry


UNBOUND_FAMILIES = {
    "cbrn_refusal",
    "consequential_action_confirmation",
    "ai_disclosure",
    "agentdojo",
    "counterfactual_stability",
}


@pytest.mark.parametrize("family", sorted(UNBOUND_FAMILIES))
def test_unbound_family_rows_are_gated_out_of_production_execution(family):
    rows = [
        row
        for row in build_registry()
        if row["status"] == "active" and row["test_id"] == family
    ]

    assert rows, f"expected active registry rows for {family}"
    assert not any(row["production_runner_executable"] for row in rows)


def test_unbound_override_preserves_pinned_out_of_scope_family_counts():
    counts = Counter(
        row["test_id"]
        for row in build_registry()
        if row["production_runner_executable"]
        and row["test_id"] not in UNBOUND_FAMILIES
    )
    serialized = json.dumps(
        dict(sorted(counts.items())), sort_keys=True, separators=(",", ":")
    ).encode()

    # Pin the complete per-family count map from master 3c6e4677. A total-only
    # assertion could miss equal and opposite family changes.
    assert len(counts) == 125
    assert sum(counts.values()) == 2314
    assert hashlib.sha256(serialized).hexdigest() == (
        "4c332ace720107965d6d395cf3e27446b1ecab5f37ae12f3cef04959fd9652d2"
    )
