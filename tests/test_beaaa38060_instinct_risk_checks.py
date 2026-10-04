from datetime import datetime, timedelta, timezone

from stage_b_pilot.instinct_risk_checks import CASES, MANIFEST_VERSION, manifest
from stage_b_pilot.instinct_standing import StandingReportStore


NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


def test_manifest_is_bounded_and_keeps_claim_classes_separate():
    data = manifest()
    assert data["version"] == MANIFEST_VERSION
    assert data["score"] == "N/A"
    assert len(CASES) == 6 and {c.case_id for c in CASES} == {
        "IH-H1", "IH-H2", "IH-A1", "IH-A2", "IH-C1", "IH-C2"}
    assert all(c.repeat_count == 1 for c in CASES)
    assert all(c.classification == "response_observation" for c in CASES)
    assert all(c.execution_enforcement == "unobservable" for c in CASES)
    assert all(c.underwriting_active is False for c in CASES)
    assert all(c.priced_score_weight == 0.0 for c in CASES)
    assert data["outcomes"] == ["pass", "fail", "indeterminate", "pending", "timeout"]
    assert all(c.source_revision == "0.1" for c in CASES)


def test_once_per_valid_intake_and_selective_triggers_preserve_attempts(tmp_path):
    clock = [NOW]
    store = StandingReportStore(tmp_path / "standing.sqlite3", now=lambda: clock[0])
    grant = store.issue("user", "agent@example.com", followup_enabled=True)
    first = store.schedule_risk_checks("user", grant["grant_id"], "valid_intake",
                                       trigger_key="intake-1")
    assert [row["case_id"] for row in first] == ["IH-H1", "IH-H2", "IH-A2", "IH-C2"]
    clock[0] += timedelta(seconds=1)
    assert store.schedule_risk_checks("user", grant["grant_id"], "valid_intake",
                                      trigger_key="intake-1") == []
    assert [row["case_id"] for row in store.schedule_risk_checks(
        "user", grant["grant_id"], "authority_changed", trigger_key="event-1")] == ["IH-A1"]
    assert [row["case_id"] for row in store.schedule_risk_checks(
        "user", grant["grant_id"], "amount_changed", trigger_key="event-2")] == ["IH-C1"]
    assert len(store.followups_for("user")) == 6

    clock[0] += timedelta(days=2)
    try:
        store.schedule_risk_checks("user", grant["grant_id"], "valid_intake",
                                   trigger_key="intake-2")
    except PermissionError as exc:
        assert str(exc) == "standing_grant_expired"
    else:
        raise AssertionError("expired grant scheduled checks")
