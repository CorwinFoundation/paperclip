"""BEAAA-29085: unbound rows must not enter full-scan acceptance."""
from __future__ import annotations

from collections import Counter
import hashlib
import json

import pytest

from tools.active_test_registry import build_registry


UNBOUND_FAMILIES = {
    "agentdojo",
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

    # Pin the complete per-family count map. A total-only assertion could miss
    # equal and opposite family changes. Refrozen by BEAAA-29742, which gated
    # nine more unbound families (192 rows) out of production execution, so the
    # map loses those nine families and 192 rows: 125 -> 116 families and
    # 2,314 -> 2,122 rows. The ninth, pii_attestation, was gated after the CTO
    # ruling on BEAAA-29746; it contributes one row and one family. Binding
    # CBRN and cyber-misuse restore two families and 80 rows; harmful-output
    # restores one more family and 60 rows. AI disclosure, consequential-action
    # confirmation, and counterfactual stability subsequently restore another
    # three families and 76 rows.
    assert len(counts) == 123
    assert sum(counts.values()) == 2422
    assert hashlib.sha256(serialized).hexdigest() == (
        "a74bb2a5aa91f94ef873014c42e327f3c2762f380f20df55ef49d43f4d782f56"
    )
