import importlib


def _registry(monkeypatch, dev_mode: str):
    monkeypatch.setenv("BACKBOND_FOUNDER_DEV_MODE", dev_mode)
    import tools.active_test_registry as registry
    registry = importlib.reload(registry)
    return registry.build_registry()


def test_exactly_sixty_spread_qualified_trace_rows_score_in_dev_mode(monkeypatch):
    rows = _registry(monkeypatch, "on")
    selected = [
        row for row in rows
        if row.get("trace_observation_adapter")
        and row.get("execution_evidence_status") == "beaaa41063_recorded_trace_target_measured"
    ]
    assert len(selected) == 60
    assert {row["test_id"] for row in selected} == {"counterfactual_stability"}
    assert {row["execution_evidence_verdict"] for row in selected} == {"pass", "fail"}
    assert all(row["candidate_rubric_point_id"] == "P2.3" for row in selected)
    assert all(row["scoring_active"] is True for row in selected)
    assert all(row.get("underwriting_active") is not True for row in selected)


def test_trace_promotion_is_report_only_with_flag_off_and_never_violates_d2(monkeypatch):
    rows = _registry(monkeypatch, "off")
    selected = [
        row for row in rows
        if row.get("execution_evidence_status") == "beaaa41063_recorded_trace_target_measured"
    ]
    assert len(selected) == 60
    assert all(row["scoring_active"] is False for row in selected)
    assert all(row["reporting_status"] == "reported_not_scored" for row in selected)
    assert not any(
        row.get("scoring_active") is True
        or not row.get("candidate_rubric_point_id")
        or row.get("execution_evidenced") is not True
        for row in selected
    )
