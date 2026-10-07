from datetime import datetime, timezone

import pytest

from stage_b_pilot.instinct_standing import EVENT_KEYS, SCHEMA_VERSION, StandingReportStore


NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)


def _report(key):
    event = {field: "unknown" for field in EVENT_KEYS}
    event.update(evidence_origin="agent_reported", synthetic=True,
                 shared_context_status="unverified")
    return {"schema_version": SCHEMA_VERSION, "deduplication_key": key,
            "trigger": "request_completed", "event": event}


def test_authorized_stop_is_idempotent_clears_content_and_isolates_other_grants(tmp_path):
    store = StandingReportStore(tmp_path / "standing.sqlite3", now=lambda: NOW)
    stopped = store.issue("user-a", "agent-a@example.com", followup_enabled=True)
    other = store.issue("user-a", "agent-a@example.com", followup_enabled=True)
    store.ingest(stopped["token"], "agent-a@example.com", _report("stopped-task"))
    request = store.issue_followup("user-a", stopped["grant_id"], "A bounded question")
    store.reply_followup(stopped["token"], request["request_id"], {
        "request_id": request["request_id"], "answer": "private reply",
        "evidence_origin": "agent_reported", "synthetic": True})
    store.ingest(other["token"], "agent-a@example.com", _report("other-task"))

    assert store.relay_stop("user-a", stopped["grant_id"], "agent-a@example.com") == {
        "state": "revoked", "backend_enforcement": "confirmed",
        "message": "Stop ends reporting and keeps what you sent; ask for deletion separately."}
    assert store.relay_stop("user-a", stopped["grant_id"], "agent-a@example.com")["state"] == "already_revoked"
    states = {row["grant_id"]: row["state"] for row in store.grants_for("user-a")}
    assert states[stopped["grant_id"]] == "revoked"
    assert sorted(row["report"]["deduplication_key"] for row in store.reports_for("user-a")) == [
        "other-task", "stopped-task"]
    assert store.followups_for("user-a")[0]["answer"] == "private reply"
    with pytest.raises(PermissionError, match="revoked_or_invalid"):
        store.ingest(stopped["token"], "agent-a@example.com", _report("later-task"))
    assert states[other["grant_id"]] == "active"


@pytest.mark.parametrize("user_id,sender", [
    ("user-b", "agent-a@example.com"),
    ("user-a", "agent-b@example.com"),
])
def test_stop_rejects_mismatched_identity_or_agent_binding(tmp_path, user_id, sender):
    store = StandingReportStore(tmp_path / "standing.sqlite3", now=lambda: NOW)
    grant = store.issue("user-a", "agent-a@example.com")
    with pytest.raises(PermissionError):
        store.relay_stop(user_id, grant["grant_id"], sender)
    assert store.grants_for("user-a")[0]["state"] == "active"
