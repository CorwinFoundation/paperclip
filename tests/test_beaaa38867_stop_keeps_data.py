"""BEAAA-38867: STOP revokes the grant and keeps received data; delete is separate.

Fictional data only.
"""
import pytest

from stage_b_pilot.history_import import HistoryImportStore
from stage_b_pilot.instinct_standing import DELETE_CONFIRMATION, STOP_ACK, StandingReportStore
from tests.test_beaaa37907_instinct_history import KEY, SECRET, message, report as history_report
from tests.test_beaaa37962_instinct_standing import report as standing_report


def _previews(store):
    return store.connection.execute("SELECT COUNT(*) FROM history_previews").fetchone()[0]


def test_stop_keeps_received_data_rejects_later_submissions_and_delete_is_explicit(tmp_path):
    history = HistoryImportStore(tmp_path / "history.sqlite3", provider_secret=SECRET, encryption_key=KEY)
    standing = StandingReportStore(tmp_path / "standing.sqlite3")
    issued = history.issue_address("user-a", scope={"lookback_days": 90}, synthetic=True)
    grant = standing.issue("user-a", "agent-a@example.com", history_address=issued.address,
                           followup_enabled=True)
    other = standing.issue("user-b", "agent-b@example.com")

    history.ingest(message(issued.address, history_report(3)), expected_user_id="user-a")
    standing.ingest(grant["token"], "agent-a@example.com", standing_report("dinner-task"))
    standing.ingest(other["token"], "agent-b@example.com", standing_report("other-task"))
    question = standing.issue_followup("user-a", grant["grant_id"], "A fictional question")
    standing.reply_followup(grant["token"], question["request_id"], {
        "request_id": question["request_id"], "answer": "fictional answer",
        "evidence_origin": "agent_reported", "synthetic": True})

    def stop():
        history.revoke_pending_address("user-a", issued.address)
        return standing.relay_stop("user-a", grant["grant_id"], "agent-a@example.com")

    assert stop() == {"state": "revoked", "backend_enforcement": "confirmed", "message": STOP_ACK}
    assert _previews(history) == 1
    assert [row["report"]["deduplication_key"] for row in standing.reports_for("user-a")] == ["dinner-task"]
    assert standing.followups_for("user-a")[0]["answer"] == "fictional answer"

    with pytest.raises(PermissionError, match="revoked_or_invalid"):
        standing.ingest(grant["token"], "agent-a@example.com", standing_report("later-task"))
    with pytest.raises(PermissionError):
        history.ingest(message(issued.address, history_report(3)), expected_user_id="user-a")
    with pytest.raises(PermissionError):
        standing.reply_followup(grant["token"], question["request_id"], {
            "request_id": question["request_id"], "answer": "later answer",
            "evidence_origin": "agent_reported", "synthetic": True})

    assert stop()["state"] == "already_revoked"
    assert _previews(history) == 1
    assert len(standing.reports_for("user-a")) == 1
    assert {row["grant_id"]: row["state"] for row in standing.grants_for("user-b")} == {
        other["grant_id"]: "active"}

    with pytest.raises(ValueError, match="delete_confirmation_required"):
        standing.delete_imported("user-a", grant["grant_id"], confirm="yes")
    with pytest.raises(PermissionError):
        standing.delete_imported("user-b", grant["grant_id"], confirm=DELETE_CONFIRMATION)
    assert standing.delete_imported("user-a", grant["grant_id"], confirm=DELETE_CONFIRMATION) == {
        "state": "deleted", "reports_deleted": 1, "replies_deleted": 1}
    assert history.delete_for_address("user-a", issued.address) == 1
    assert _previews(history) == 0
    assert standing.reports_for("user-a") == []
    assert standing.followups_for("user-a")[0]["answer"] is None
    assert [row["report"]["deduplication_key"] for row in standing.reports_for("user-b")] == ["other-task"]
