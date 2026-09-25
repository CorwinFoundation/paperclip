"""BEAAA-35793: the underwriter_evidence index must label its attestation tier.

PR #729 added ``underwriter_evidence`` to the operator manifest with a
``capability_materiality`` block that summed pass/fail/other per declared
capability with no reference to ``attestation_tier``. A row scored under
Founder Development Mode and a production-attested row contributed identically
to those counts, so a reader could not tell them apart.

BEAAA-24008 ruling D.4 keeps the isolation property in force under Development
Mode: report-only rows must not carry weight silently. The fix labels rather
than refuses (scope item 3) — this is an internal operator manifest and must
keep building under Development Mode.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.scheduled_scan import (  # noqa: E402
    ATTESTATION_TIER_UNKNOWN,
    _attestation_tier_by_test_id,
    _underwriter_evidence_index,
)
from tools.founder_dev_mode import (  # noqa: E402
    ATTESTATION_FOUNDER_DEV,
    ATTESTATION_PRODUCTION_SIGNED,
)

CAPABILITY = "tool_use"

JOB = SimpleNamespace(capability_manifest={CAPABILITY: True})

REGISTRY_ROWS = [
    {"test_id": "t-dev", "attestation_tier": ATTESTATION_FOUNDER_DEV},
    {"test_id": "t-prod", "attestation_tier": ATTESTATION_PRODUCTION_SIGNED},
]

MIXED_ROWS = [
    {
        "test_id": "t-dev",
        "rubric_point_id": f"P2.1_{CAPABILITY}",
        "verdict": "PASS",
        "family": "tool_misuse",
    },
    {
        "test_id": "t-prod",
        "rubric_point_id": f"P2.2_{CAPABILITY}",
        "verdict": "FAIL",
        "family": "tool_misuse",
    },
]


def _index(rows=MIXED_ROWS, registry_rows=REGISTRY_ROWS):
    return _underwriter_evidence_index(
        evidence_rows=list(rows),
        job=JOB,
        attestation_tiers=_attestation_tier_by_test_id(registry_rows),
    )


def test_mixed_tier_run_separates_capability_counts_and_reports_both_tiers():
    """Scope item 1 + 2 + 4: the mixed-tier acceptance case."""
    index = _index()

    # Scope item 2: top-level summary names every tier present.
    attestation = index["attestation"]
    assert attestation["tiers_present"] == sorted(
        [ATTESTATION_FOUNDER_DEV, ATTESTATION_PRODUCTION_SIGNED]
    )
    assert attestation["rows_by_attestation_tier"] == {
        ATTESTATION_FOUNDER_DEV: 1,
        ATTESTATION_PRODUCTION_SIGNED: 1,
    }
    assert attestation["evidence_rows"] == 2
    assert attestation["dev_scored_rows"] == 1
    assert attestation["production_signed_rows"] == 1
    assert attestation["all_rows_production_signed"] is False
    # ...and states plainly that a row is dev-scored.
    assert ATTESTATION_FOUNDER_DEV in attestation["note"]
    assert "not production-attested" in attestation["note"].lower()

    # Scope item 1: the counts are separable per capability.
    materiality = index["aon_category_b_autonomy_and_materiality"][
        "capability_materiality"
    ][CAPABILITY]
    assert materiality["by_attestation_tier"] == {
        ATTESTATION_FOUNDER_DEV: {"pass": 1, "fail": 0, "other": 0},
        ATTESTATION_PRODUCTION_SIGNED: {"pass": 0, "fail": 1, "other": 0},
    }
    assert materiality["dev_scored_rows"] == 1
    # The tier-blind totals are unchanged, and now carry a read-me-first note.
    assert (materiality["pass"], materiality["fail"], materiality["other"]) == (1, 1, 0)
    assert "by_attestation_tier" in (
        index["aon_category_b_autonomy_and_materiality"]["attestation_note"]
    )


def test_all_production_rows_report_a_clean_tier_census():
    index = _index(
        rows=[MIXED_ROWS[1]], registry_rows=[REGISTRY_ROWS[1]]
    )
    attestation = index["attestation"]
    assert attestation["tiers_present"] == [ATTESTATION_PRODUCTION_SIGNED]
    assert attestation["dev_scored_rows"] == 0
    assert attestation["all_rows_production_signed"] is True


def test_row_missing_from_the_registry_is_unknown_not_production_signed():
    """A row with no declared tier must never be credited as production-attested."""
    index = _index(rows=[MIXED_ROWS[0]], registry_rows=[])
    attestation = index["attestation"]
    assert attestation["tiers_present"] == [ATTESTATION_TIER_UNKNOWN]
    assert attestation["production_signed_rows"] == 0
    assert attestation["dev_scored_rows"] == 1
    assert attestation["all_rows_production_signed"] is False

    materiality = index["aon_category_b_autonomy_and_materiality"][
        "capability_materiality"
    ][CAPABILITY]
    assert materiality["by_attestation_tier"] == {
        ATTESTATION_TIER_UNKNOWN: {"pass": 1, "fail": 0, "other": 0}
    }


def test_registry_row_with_no_declared_tier_defaults_to_production_signed():
    """Matches tools.founder_dev_mode.attestation_tier: absent means production."""
    assert _attestation_tier_by_test_id([{"test_id": "t-x"}]) == {
        "t-x": ATTESTATION_PRODUCTION_SIGNED
    }


def test_index_still_builds_with_no_evidence_rows():
    """Scope item 3: label, do not refuse. No emission guard on this path."""
    index = _index(rows=[], registry_rows=[])
    assert index["attestation"]["tiers_present"] == []
    assert index["attestation"]["all_rows_production_signed"] is False
    assert "no attestation tier" in index["attestation"]["note"].lower()
