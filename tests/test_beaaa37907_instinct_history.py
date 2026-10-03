import copy
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest

from stage_b_pilot.history_import import (
    EXPECTED_ROWS,
    HISTORY_DOMAIN,
    HistoryImportStore,
    REPORT_VERSION,
    ROW_KEYS,
    _canonical_report_bytes,
)


SECRET = b"provider-secret-for-focused-tests"
KEY = b"history-encryption-key-32-bytes!!"


def report():
    rows = []
    for index in range(EXPECTED_ROWS):
        row = {key: "unknown" for key in ROW_KEYS}
        row.update(task_id=f"task-{index:02d}", evidence_origin="agent_reported", synthetic=True)
        rows.append(row)
    return {"schema_version": REPORT_VERSION, "row_count": EXPECTED_ROWS,
            "rows": rows, "end_marker": "END INSTINCT HISTORY"}


def message(address, payload=None, *, secret=SECRET, attachment=False, html=False):
    msg = EmailMessage()
    msg["From"] = "reports@instinct.example"
    msg["To"] = address
    msg["Subject"] = "Your Instinct history"
    body = json.dumps(report() if payload is None else payload)
    msg.set_content(body)
    if html:
        msg.add_alternative("<p>ignored</p>", subtype="html")
    if attachment:
        msg.add_attachment(b"no", maintype="application", subtype="octet-stream", filename="history.bin")
    msg["X-Instinct-Signature"] = hmac.new(
        secret, _canonical_report_bytes(payload if payload is not None else report()), hashlib.sha256
    ).hexdigest()
    return msg.as_bytes(policy=msg.policy.clone(linesep="\r\n"))


@pytest.fixture
def store(tmp_path):
    return HistoryImportStore(tmp_path / "history.sqlite3", provider_secret=SECRET, encryption_key=KEY)


def test_51_row_report_imports_once_for_bound_user_and_confirms(store):
    issued = store.issue_address("user-a", scope={"lookback_days": 90, "categories": ["purchases"]})
    assert issued.address.endswith("@" + HISTORY_DOMAIN)
    assert "check@inbound.backbond.ai" not in issued.address
    preview_id = store.ingest(message(issued.address, html=True), expected_user_id="user-a")
    assert len(store.preview("user-a", preview_id)["report"]["rows"]) == 51
    with pytest.raises(PermissionError, match="replayed"):
        store.ingest(message(issued.address), expected_user_id="user-a")
    store.act("user-a", preview_id, "confirm")
    state = store.onboarding_state("user-a")
    assert state["history_informed_underwriting_review"] == "ready"
    assert state["insurance_or_coverage_activation"] is False


@pytest.mark.parametrize("mutation,error", [
    (lambda p: p.update(row_count=50), "row_count_mismatch"),
    (lambda p: p.pop("end_marker"), "end_marker_missing"),
    (lambda p: p["rows"][0].pop("outcome"), "row_keys_missing"),
    (lambda p: p["rows"].__setitem__(1, copy.deepcopy(p["rows"][0])), "duplicate_row"),
])
def test_full_key_schema_failures_hold_review(store, mutation, error):
    issued = store.issue_address("user-a", scope={"lookback_days": 30})
    payload = report()
    mutation(payload)
    with pytest.raises(ValueError, match=error):
        store.ingest(message(issued.address, payload))
    assert store.onboarding_state("user-a")["history_informed_underwriting_review"] == "held"


def test_signature_expiry_attachment_size_binding_and_cross_user_fail(store):
    issued = store.issue_address("user-a", scope={"lookback_days": 30})
    with pytest.raises(PermissionError, match="account_mismatch"):
        store.ingest(message(issued.address), expected_user_id="user-b")
    with pytest.raises(PermissionError, match="signature_invalid"):
        store.ingest(message(issued.address, secret=b"wrong-provider-secret-value"))
    issued2 = store.issue_address("user-a", scope={"lookback_days": 30})
    with pytest.raises(ValueError, match="attachments_rejected"):
        store.ingest(message(issued2.address, attachment=True))
    with pytest.raises(ValueError, match="payload_too_large"):
        store.ingest(b"x" * 256_001)
    expired = store.issue_address("user-a", scope={"lookback_days": 30}, ttl=timedelta(seconds=-1))
    with pytest.raises(PermissionError, match="expired"):
        store.ingest(message(expired.address))


def test_decline_preview_correction_discard_delete_and_private_access(store):
    declined = store.decline("user-a")
    assert declined["history_state"] == "history_not_supplied"
    assert declined["underwriting_evidence"] == "incomplete"
    assert declined["account_creation"] == declined["observation_demo"] == "available"
    assert "does not activate insurance or coverage" in declined["notice"]

    first = store.issue_address("user-a", scope={"lookback_days": 30})
    preview_id = store.ingest(message(first.address))
    with pytest.raises(PermissionError, match="preview_not_found"):
        store.preview("user-b", preview_id)
    corrected = report()
    corrected["rows"][0]["outcome"] = "user corrected"
    store.act("user-a", preview_id, "correct", corrected_report=corrected)
    assert store.preview("user-a", preview_id)["provenance"] == "user_confirmed"
    store.act("user-a", preview_id, "discard")
    with pytest.raises(PermissionError, match="preview_not_found"):
        store.preview("user-a", preview_id)

    second = store.issue_address("user-a", scope={"lookback_days": 30})
    second_id = store.ingest(message(second.address))
    store.act("user-a", second_id, "delete")
    assert store.onboarding_state("user-a")["history_informed_underwriting_review"] == "held"
