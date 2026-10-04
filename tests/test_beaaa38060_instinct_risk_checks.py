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
    assert all(c.source_revision == "0.2" for c in CASES)
    assert all(len(f"[{c.case_id}] {c.prompt}") <= 500 for c in CASES)


def test_once_per_valid_intake_and_selective_triggers_preserve_attempts(tmp_path):
    clock = [NOW]
    store = StandingReportStore(tmp_path / "standing.sqlite3", now=lambda: clock[0])
    grant = store.issue("user", "agent@example.com", followup_enabled=True)
    first = store.schedule_risk_checks("user", grant["grant_id"], "valid_intake",
                                       trigger_key="intake-1")
    assert [row["case_id"] for row in first] == [
        "IH-H1", "IH-H2", "IH-A1", "IH-A2", "IH-C1", "IH-C2"]
    clock[0] += timedelta(seconds=1)
    assert store.schedule_risk_checks("user", grant["grant_id"], "valid_intake",
                                      trigger_key="intake-1") == []
    assert [row["case_id"] for row in store.schedule_risk_checks(
        "user", grant["grant_id"], "authority_changed", trigger_key="event-1")] == ["IH-A1", "IH-A2"]
    assert [row["case_id"] for row in store.schedule_risk_checks(
        "user", grant["grant_id"], "amount_changed", trigger_key="event-2")] == ["IH-C1", "IH-C2"]
    assert len(store.followups_for("user")) == 10

    clock[0] += timedelta(days=2)
    with __import__("pytest").raises(PermissionError, match="standing_grant_expired"):
        store.schedule_risk_checks("user", grant["grant_id"], "valid_intake",
                                   trigger_key="intake-2")


def test_schedule_rolls_back_question_when_schedule_insert_fails(tmp_path, monkeypatch):
    store = StandingReportStore(tmp_path / "standing.sqlite3", now=lambda: NOW)
    grant = store.issue("user", "agent@example.com", followup_enabled=True)
    original = store.connection.execute

    class FailingConnection:
        def __init__(self, real):
            self._real = real
        def execute(self, sql, parameters=()):
            if sql.startswith("INSERT INTO instinct_risk_check_schedule"):
                raise RuntimeError("schedule write failed")
            return self._real.execute(sql, parameters)
        def __getattr__(self, name):
            return getattr(self._real, name)

    real = store.connection
    wrapper = FailingConnection(real)
    store.connection = wrapper
    with __import__("pytest").raises(RuntimeError, match="schedule write failed"):
        store.schedule_risk_checks("user", grant["grant_id"], "valid_intake",
                                   trigger_key="intake-atomic")
    store.connection = real
    assert store.followups_for("user") == []
