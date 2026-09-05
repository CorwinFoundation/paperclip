from pathlib import Path

from harness.applicability import applicability_record, load_policy
from rubric.scoring import Rubric
from scanners.score_runtime import ScoreRuntime


RUBRIC = Rubric.load(Path(__file__).parents[1] / "rubric" / "rubric.yaml")


def _rows(dim_ids, runner="inspect-v1"):
    return {
        dim_id: {
            "raw": 2.0,
            "runner": runner,
            "evidence_uri": f"https://evidence.local/{dim_id}",
        }
        for dim_id in dim_ids
    }


def _compute(rows, **overrides):
    args = dict(
        accumulated_rows=rows,
        scan_id="scan-beaaa26439",
        agent_id="agent-test",
        seed=0,
        scanner_version="test",
        corpus_version="test",
        run_timestamp="2026-09-02T08:00:00Z",
        invocation_context=None,
        prior_scan_refs=[],
    )
    args.update(overrides)
    return ScoreRuntime(RUBRIC).compute_envelope(**args)


def _applicability():
    policy = load_policy()
    flags = {name: True for name in policy.architecture_flag_names}
    record = applicability_record(policy, flags)
    return flags, record["applicability_decisions"], record["measured_dims"]


def test_synthetic_subset_is_non_bindable():
    envelope = _compute(_rows(RUBRIC.active_dim_ids[:4], runner="synthetic-v0"))
    decision = envelope["bind_eligibility"]
    assert decision["status"] == "ineligible"
    assert decision["reason_codes"] == ["synthetic_evidence", "incomplete_measurement"]
    assert decision["measured_applicable_dimensions"] == 4
    assert decision["total_applicable_dimensions"] == len(RUBRIC.active_dim_ids)


def test_fully_measured_authorized_path_is_eligible():
    flags, decisions, _measured_dims = _applicability()
    envelope = _compute(
        _rows(RUBRIC.active_dim_ids),
        applicability_decisions=decisions,
        invocation_context={"architecture_flags": flags},
    )
    assert envelope["bind_eligibility"] == {
        "status": "eligible",
        "reason_codes": [],
        "measured_applicable_dimensions": len(RUBRIC.active_dim_ids),
        "total_applicable_dimensions": len(RUBRIC.active_dim_ids),
    }


def test_smoke_and_prior_paths_can_only_refer():
    flags, decisions, _measured_dims = _applicability()
    envelope = _compute(
        _rows(RUBRIC.active_dim_ids, runner="smoke-v1"),
        applicability_decisions=decisions,
        invocation_context={"architecture_flags": flags},
        prior_scan_refs=[{"scan_id": "prior"}],
    )
    decision = envelope["bind_eligibility"]
    assert decision["status"] == "refer"
    assert decision["reason_codes"] == ["prior_filled", "smoke_only"]
