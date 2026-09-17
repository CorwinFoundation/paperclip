"""BEAAA-33507 TASK 2: the dev tier clears D2 on its own fields.

Part A of BEAAA-33506 found that every Founder-dev-scored row read
``candidate_rubric_mapping_status: unmapped_reported_not_scored`` and
``execution_evidenced: False`` in the registry, and stayed scoring-active only
because ``row_blocks_scoring_activation`` exempts ``dev_scoring_active`` before
evaluating D2. The rank-2 ledger held the real mapping and the real evidence.
These tests pin that the writer now carries both onto the rows, so the carve-out
is no longer load-bearing and can be deleted on its own card.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.active_test_registry import (
    MAPPED_DEV_SCORED_ZERO_WEIGHT_STATUS,
    UNMAPPED_CANDIDATE_RUBRIC_STATUS,
    _beaaa31092_dev_evidence_metadata,
    build_registry,
)

LEDGER = Path(__file__).resolve().parents[1] / "artifacts/beaaa-31092/rank2-ledger.json"
RUN_MANIFEST_SHA256 = (
    "ab9fdb0304b614e0689536b5bb8bd4a933591c3a0543472f523a2097992607cb"
)


def _d2_blocks_ignoring_dev_carve_out(row) -> bool:
    """D2 with the ``dev_scoring_active`` early-return bypassed."""
    return (
        row.get("candidate_rubric_mapping_status") == UNMAPPED_CANDIDATE_RUBRIC_STATUS
        or row.get("execution_evidenced") is not True
    )


@pytest.fixture(scope="module")
def dev_rows(monkeypatch_module=None):
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    scored_ids = {
        str(row["stable_case_id"])
        for row in ledger["rows"]
        if row.get("disposition") == "scored"
    }
    import os

    previous = os.environ.get("BACKBOND_FOUNDER_DEV_MODE")
    os.environ["BACKBOND_FOUNDER_DEV_MODE"] = "1"
    try:
        rows = [
            row for row in build_registry() if str(row.get("case_id")) in scored_ids
        ]
    finally:
        if previous is None:
            os.environ.pop("BACKBOND_FOUNDER_DEV_MODE", None)
        else:
            os.environ["BACKBOND_FOUNDER_DEV_MODE"] = previous
    assert len(rows) == len(scored_ids) == 67
    return rows


def test_every_dev_scored_row_carries_the_ledger_mapping(dev_rows):
    for row in dev_rows:
        assert (
            row["candidate_rubric_mapping_status"]
            == MAPPED_DEV_SCORED_ZERO_WEIGHT_STATUS
        ), row["case_id"]
        assert row["candidate_rubric_point_id"] == "P3.4", row["case_id"]
        assert row["candidate_rubric_mapping_rationale"], row["case_id"]


def test_every_dev_scored_row_carries_target_measured_evidence(dev_rows):
    for row in dev_rows:
        assert row["execution_evidenced"] is True, row["case_id"]
        assert (
            row["execution_evidence_status"]
            == "beaaa31092_rank2_ledger_target_measured"
        ), row["case_id"]
        assert row["execution_evidence_ref"].startswith("evidence://"), row["case_id"]
        assert row["execution_evidence_verdict"] in {"pass", "fail", "partial"}
        assert row["execution_evidence_model_version"].startswith("vapi:09aea361")
        assert row["execution_evidence_run_manifest_sha256"] == RUN_MANIFEST_SHA256


def test_all_67_survive_D2_with_the_dev_carve_out_bypassed(dev_rows):
    """The card's acceptance check: 67/67 still scoring-active without the exemption."""
    assert all(row.get("scoring_active") is True for row in dev_rows)
    demoted = [
        row["case_id"] for row in dev_rows if _d2_blocks_ignoring_dev_carve_out(row)
    ]
    assert demoted == [], f"{len(demoted)} dev rows would demote without the carve-out"


def test_evidence_metadata_is_fail_closed_on_an_incomplete_ledger_row():
    """A ledger row missing mapping or evidence contributes nothing, so the row
    keeps the unmapped registration literal and would demote under D2."""
    complete = {
        "rubric_point_id": "P3.4",
        "recorded_target_response": "some recorded response",
        "verdict": "fail",
        "evidence_ref": "evidence://job/family/case.json",
    }
    assert _beaaa31092_dev_evidence_metadata(complete)
    for dropped in ("rubric_point_id", "recorded_target_response", "verdict"):
        assert _beaaa31092_dev_evidence_metadata({**complete, dropped: None}) == {}
    assert _beaaa31092_dev_evidence_metadata({**complete, "verdict": "inconclusive"}) == {}
