"""BEAAA-29085: unbound rows must not enter full-scan acceptance."""
from __future__ import annotations

from collections import Counter
import hashlib
import json

import pytest

from tools.active_test_registry import build_registry
from tools.assessment_test_catalog import RECORD_FAMILIES, LOG_CHALLENGE_FAMILY, SYNTHETIC_FAMILIES


UNBOUND_FAMILIES: set[str] = set()


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
    # Pin the new conditional families separately, then preserve the exact
    # pre-existing family map and its digest below.
    assert {family: counts.pop(family) for family in (
        "operational_oversight", "operational_logging", "operational_logging_quality",
    )} == {"operational_oversight": 8, "operational_logging": 4, "operational_logging_quality": 5}
    assert counts.pop(LOG_CHALLENGE_FAMILY) == 3
    assert {family: counts.pop(family) for family in RECORD_FAMILIES} == {family: 1 for family in RECORD_FAMILIES}
    assert {family: counts.pop(family) for family in SYNTHETIC_FAMILIES} == {family: 1 for family in SYNTHETIC_FAMILIES}
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
    # three families and 76 rows. BEAAA-33810 adds the eight-case agent-escape
    # incident tranche to out_of_scope_containment: one family unchanged, that
    # family 60 -> 68 rows, so 2,458 -> 2,466 and the map hash moves with it.
    # E018 adds twelve ai_disclosure cases, 24 -> 36, taking rows to 2,478.
    # Subtracting only those twelve reproduces the prior full-map hash.
    assert len(counts) == 130
    assert sum(counts.values()) == 2478
    assert hashlib.sha256(serialized).hexdigest() == (
        "b124e2f0cf83e7b9861ed40af2722b6808bc9f6d32be4074284d633a554ce183"
    )
