"""Local end-to-end acceptance for the two private Instinct steps."""

import hashlib
import hmac
import json
import re
import threading
import time
from email.message import EmailMessage
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from stage_b_pilot.history_import import ROW_KEYS, REPORT_VERSION
from stage_b_pilot.inbound import InboundStore
from stage_b_pilot.mailgun import make_handler as inbound_handler
from stage_b_pilot.receipt_page import CredentialStore, make_handler as page_handler


SIGNING_KEY = "mailgun-signing-key-for-history-test"
ENCRYPTION_KEY = b"history-encryption-key-32-bytes!!"


def _json_request(base, path, token, fields=None):
    body = json.dumps(fields).encode() if fields is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    with urlopen(Request(base + path, data=body, headers=headers), timeout=5) as response:
        return json.loads(response.read())


def _report(count, *, synthetic=True):
    rows = []
    for index in range(count):
        row = {key: "unknown" for key in ROW_KEYS}
        row.update(task_id="unknown", category=f"test-{index}",
                   evidence_origin="agent_reported", synthetic=synthetic)
        rows.append(row)
    return {"schema_version": REPORT_VERSION, "row_count": count,
            "rows": rows, "end_marker": "END INSTINCT HISTORY"}


def _deliver(base, address, report, *, valid_signature=True, sender="instinct@example.com"):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = address
    message["Subject"] = "BackBond Instinct history"
    message.set_content(json.dumps(report))
    message.add_alternative("<p>ignored HTML copy</p>", subtype="html")
    timestamp = str(int(time.time()))
    token = "webhook-token"
    signature = hmac.new(SIGNING_KEY.encode(), (timestamp + token).encode(), hashlib.sha256).hexdigest()
    if not valid_signature:
        signature = "0" * 64
    fields = {"timestamp": timestamp, "token": token, "signature": signature,
              "recipient": address, "body-mime": message.as_string()}
    return urlopen(Request(base + "/history/mailgun/mime",
                           data=urlencode(fields).encode(),
                           headers={"Content-Type": "application/x-www-form-urlencoded"}), timeout=5)


def _deliver_check(base):
    message = EmailMessage()
    message["From"] = "user@example.com"
    message["To"] = "check@inbound.backbond.ai"
    message["Subject"] = "BackBond check"
    message.set_content(
        "Check ID: BB-SUPPORT-1\nProposed action: Research a fictional booking\n"
        "Terms: Synthetic only\nDeposit: $0\nMoney at risk: $0"
    )
    timestamp = str(int(time.time()))
    token = "check-token"
    fields = {
        "timestamp": timestamp, "token": token,
        "signature": hmac.new(SIGNING_KEY.encode(), (timestamp + token).encode(), hashlib.sha256).hexdigest(),
        "recipient": "check@inbound.backbond.ai", "body-mime": message.as_string(),
    }
    return urlopen(Request(base + "/mailgun/mime", data=urlencode(fields).encode(),
                           headers={"Content-Type": "application/x-www-form-urlencoded"}), timeout=5)


@pytest.fixture
def services(tmp_path):
    inbound_db = tmp_path / "inbound.sqlite3"
    history_db = tmp_path / "history.sqlite3"
    credential_file = tmp_path / "credentials.json"
    credential_file.touch(mode=0o600)
    credential_file.write_text("{}")
    enrolled = InboundStore(inbound_db)
    _, token = enrolled.begin_signup("user@example.com")
    enrolled.confirm_signup("user@example.com", token)
    enrolled.configure_agent_recipient("user@example.com", token, "instinct@example.com")
    enrolled.close()
    CredentialStore(str(credential_file)).provision(
        "user@example.com", "correct horse battery", token
    )
    inbound = ThreadingHTTPServer(("127.0.0.1", 0), inbound_handler(
        str(inbound_db), SIGNING_KEY, history_db_path=str(history_db),
        history_key=ENCRYPTION_KEY, history_synthetic_enabled=True,
    ))
    page = ThreadingHTTPServer(("127.0.0.1", 0), page_handler(
        "127.0.0.1", inbound.server_port, "session-secret-at-least-32-characters",
        credential_file=str(credential_file),
    ))
    threads = [threading.Thread(target=server.serve_forever, daemon=True)
               for server in (inbound, page)]
    for thread in threads:
        thread.start()
    try:
        yield (f"http://127.0.0.1:{inbound.server_port}",
               f"http://127.0.0.1:{page.server_port}", token)
    finally:
        for server in (page, inbound):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)


def test_new_user_then_support_history_flow(services):
    inbound, page, token = services
    with pytest.raises(HTTPError) as missing_identity:
        _json_request(inbound, "/history/state", token)
    assert missing_identity.value.code == 401
    # Authentication lands on the history step before the ongoing receipts.
    login = urlencode({"email": "user@example.com",
                       "passphrase": "correct horse battery"}).encode()
    with urlopen(Request(page + "/stage-b-demo/receipts", data=login)) as response:
        html = response.read().decode()
        cookie = response.headers["Set-Cookie"].split(";", 1)[0]
    assert "Instinct history" in html
    assert "Ongoing task support" in html
    csrf = re.search(r'name="csrf" value="([a-f0-9]+)"', html).group(1)
    request = urlencode({"csrf": csrf, "consent": "yes", "purpose": "onboarding",
                         "lookback_days": "30", "synthetic": "yes"}).encode()
    with urlopen(Request(page + "/stage-b-demo/history/request", data=request,
                         headers={"Cookie": cookie})) as response:
        html = response.read().decode()
    assert "Give Instinct this instruction" in html
    assert "Send one email from instinct@example.com" in html
    address = re.search(r"history\+[A-Za-z0-9_-]+@inbound\.backbond\.ai", html).group(0)
    with pytest.raises(HTTPError) as wrong_sender:
        _deliver(inbound, address, _report(2), sender="user@example.com")
    assert wrong_sender.value.code == 401
    with _deliver(inbound, address, _report(2)) as response:
        preview_id = json.loads(response.read())["preview_id"]
    state = _json_request(inbound, "/history/state?email=user%40example.com", token)
    assert state["onboarding"]["history_informed_underwriting_review"] == "held"
    assert len(state["imports"][0]["report"]["rows"]) == 2
    with pytest.raises(HTTPError) as replay:
        _deliver(inbound, address, _report(2))
    assert replay.value.code == 401
    _json_request(inbound, "/history/action", token, {
        "email": "user@example.com", "preview_id": preview_id, "action": "correct_field",
        "row_index": 0, "field": "outcome", "value": "User reports no booking",
    })
    _json_request(inbound, "/history/action", token, {
        "email": "user@example.com", "preview_id": preview_id, "action": "confirm",
    })
    state = _json_request(inbound, "/history/state?email=user%40example.com", token)
    assert state["onboarding"]["history_informed_underwriting_review"] == "ready"
    assert state["imports"][0]["provenance"] == "agent_reported"
    assert state["imports"][0]["report"]["rows"][0]["user_corrections"]["outcome"] == "User reports no booking"
    # A later support update is separate from the confirmed onboarding history.
    support = _json_request(inbound, "/history/request", token, {
        "email": "user@example.com", "consent": True, "lookback_days": 7,
        "purpose": "support", "synthetic": True,
    })
    with _deliver(inbound, support["address"], _report(1)) as response:
        assert json.loads(response.read())["accepted"] is True
    state = _json_request(inbound, "/history/state?email=user%40example.com", token)
    assert state["onboarding"]["history_informed_underwriting_review"] == "ready"
    assert state["support"]["history_informed_underwriting_review"] == "held"
    assert {item["purpose"] for item in state["imports"]} == {"onboarding", "support"}
    # The continuing support receiver still handles individual task checks.
    with _deliver_check(inbound) as response:
        assert json.loads(response.read())["accepted"] is True
    receipts = _json_request(inbound, "/receipts?email=user%40example.com", token)
    assert receipts["receipts"][0]["kind"] == "check"
    with urlopen(Request(page + "/stage-b-demo/receipts",
                         headers={"Cookie": cookie})) as response:
        assert "BB-SUPPORT-1" in response.read().decode()


def test_history_route_rejects_bad_signature_and_cross_user_read(services):
    inbound, _, token = services
    issued = _json_request(inbound, "/history/request", token, {
        "email": "user@example.com", "consent": True, "lookback_days": 7,
        "purpose": "onboarding", "synthetic": True,
    })
    with pytest.raises(HTTPError) as bad_signature:
        _deliver(inbound, issued["address"], _report(1), valid_signature=False)
    assert bad_signature.value.code == 401
    with pytest.raises(HTTPError) as wrong_user:
        _json_request(inbound, "/history/state?email=other%40example.com", token)
    assert wrong_user.value.code == 401
    with pytest.raises(HTTPError) as empty:
        _deliver(inbound, issued["address"], _report(0))
    assert empty.value.code == 406
