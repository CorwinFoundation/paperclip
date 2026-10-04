from datetime import datetime, timedelta, timezone

from stage_b_pilot.instinct_risk_checks import CASES, MANIFEST_VERSION, manifest
from stage_b_pilot.instinct_standing import EVENT_KEYS, SCHEMA_VERSION, StandingReportStore


NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


def report(key, *, approval_source="user", approval_status="approved", amount="USD 10"):
    event = {field: "unknown" for field in EVENT_KEYS}
    event.update(approval_source=approval_source, approval_status=approval_status,
                 money_at_risk=amount, evidence_origin="agent_reported", synthetic=True,
                 observed_channel="unknown", shared_context_status="unverified")
    return {"schema_version": SCHEMA_VERSION, "deduplication_key": key,
            "trigger": "request_completed", "event": event}


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


def test_ingest_schedule_failure_rolls_back_report_and_same_report_can_retry(tmp_path):
    store = StandingReportStore(tmp_path / "standing.sqlite3", now=lambda: NOW)
    grant = store.issue("user", "agent@example.com", followup_enabled=True)
    assert store.ingest(grant["token"], "agent@example.com", report("baseline"))["risk_checks_scheduled"] == 0
    changed = report("changed-1", approval_status="denied")
    real = store.connection

    class FailOnce:
        def __init__(self):
            self.failed = False
        def execute(self, sql, parameters=()):
            if sql.startswith("INSERT INTO instinct_risk_check_schedule") and not self.failed:
                self.failed = True
                raise RuntimeError("schedule write failed")
            return real.execute(sql, parameters)
        def __getattr__(self, name):
            return getattr(real, name)

    store.connection = FailOnce()
    with __import__("pytest").raises(RuntimeError, match="schedule write failed"):
        store.ingest(grant["token"], "agent@example.com", changed)
    assert [row["report"]["deduplication_key"] for row in store.reports_for("user")] == ["baseline"]
    retried = store.ingest(grant["token"], "agent@example.com", changed)
    assert retried["state"] == "received" and retried["risk_checks_scheduled"] == 2


def test_selective_triggers_require_change_from_previous_report(tmp_path):
    store = StandingReportStore(tmp_path / "standing.sqlite3", now=lambda: NOW)
    grant = store.issue("user", "agent@example.com", followup_enabled=True)
    first = store.ingest(grant["token"], "agent@example.com", report("request-1"))
    same = store.ingest(grant["token"], "agent@example.com", report("request-2"))
    authority = store.ingest(
        grant["token"], "agent@example.com", report("request-3", approval_status="denied"))
    amount = store.ingest(
        grant["token"], "agent@example.com",
        report("request-4", approval_status="denied", amount="USD 25"))
    assert [first["risk_checks_scheduled"], same["risk_checks_scheduled"],
            authority["risk_checks_scheduled"], amount["risk_checks_scheduled"]] == [0, 0, 2, 2]


def test_account_task_identity_deduplicates_across_channels_but_not_equal_text_tasks(tmp_path):
    store = StandingReportStore(tmp_path / "standing.sqlite3", now=lambda: NOW)
    grant = store.issue("account-1", "agent@example.com")
    first = report("task-0001")
    first["event"].update(observed_channel="whatsapp",
                          history_sources_included="whatsapp:task-0001",
                          history_sources_unavailable="imessage",
                          outcome="same fictional response text")
    repeated = {**first, "event": {**first["event"], "observed_channel": "imessage"}}
    distinct = report("task-0002")
    distinct["event"].update(observed_channel="imessage",
                             history_sources_included="imessage:task-0002",
                             history_sources_unavailable="whatsapp",
                             outcome="same fictional response text")
    assert store.ingest(grant["token"], "agent@example.com", first)["state"] == "received"
    assert store.ingest(grant["token"], "agent@example.com", repeated)["state"] == "duplicate"
    assert store.ingest(grant["token"], "agent@example.com", distinct)["state"] == "received"
    assert len(store.reports_for("account-1")) == 2


def test_sender_change_fails_closed_and_shared_context_cannot_be_claimed(tmp_path):
    store = StandingReportStore(tmp_path / "standing.sqlite3", now=lambda: NOW)
    grant = store.issue("account-1", "bound@example.com")
    with __import__("pytest").raises(PermissionError, match="sender_mismatch"):
        store.ingest(grant["token"], "changed@example.com", report("task-0001"))
    overstated = report("task-0001")
    overstated["event"]["shared_context_status"] = "verified"
    with __import__("pytest").raises(ValueError, match="shared_context_status_invalid"):
        store.ingest(grant["token"], "bound@example.com", overstated)
