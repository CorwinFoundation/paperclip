import hashlib
import hmac
import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest

from stage_b_pilot.inbound import InboundStore
from stage_b_pilot.mailgun import ingest_route, make_handler


NOW = 1_800_000_000
KEY = "test-signing-key"


def fields(**changes):
    token = "a" * 50
    stamp = str(NOW)
    base = {
        "timestamp": stamp,
        "token": token,
        "signature": hmac.new(KEY.encode(), f"{stamp}{token}".encode(), hashlib.sha256).hexdigest(),
        "recipient": "check@inbound.backbond.ai",
        "sender": "user@example.com",
        "from": "User <user@example.com>",
        "subject": "Fwd: BackBond check",
        "body-plain": "Order 42 confirmed",
    }
    base.update(changes)
    return base


@pytest.fixture
def store(tmp_path):
    opened = InboundStore(tmp_path / "inbound.sqlite3")
    _, token = opened.begin_signup("user@example.com")
    opened.confirm_signup("user@example.com", token)
    yield opened
    opened.close()


def test_authenticated_route_writes_private_demo_receipt(store):
    receipt = ingest_route(store, fields(), KEY, now=NOW)
    assert receipt.kind == "forwarded_confirmation"
    assert receipt.mode == "DEMO"
    assert receipt.body_text == "Order 42 confirmed"


@pytest.mark.parametrize(
    "change,error",
    [
        ({"signature": "0" * 64}, "mailgun_signature_invalid"),
        ({"recipient": "anything@inbound.backbond.ai"}, "recipient_not_supported"),
    ],
)
def test_signature_and_recipient_fail_closed(store, change, error):
    with pytest.raises((PermissionError, ValueError), match=error):
        ingest_route(store, fields(**change), KEY, now=NOW)


def test_stale_signature_is_rejected(store):
    with pytest.raises(PermissionError, match="mailgun_signature_stale"):
        ingest_route(store, fields(), KEY, now=NOW + 901)


def test_http_receiver_health_and_authenticated_delivery(tmp_path, monkeypatch):
    db = tmp_path / "live.sqlite3"
    seeded = InboundStore(db)
    _, token = seeded.begin_signup("user@example.com")
    seeded.confirm_signup("user@example.com", token)
    seeded.close()
    monkeypatch.setattr("stage_b_pilot.mailgun.time.time", lambda: NOW)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(str(db), KEY))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = HTTPConnection("127.0.0.1", server.server_port)
        client.request("GET", "/healthz")
        assert json.loads(client.getresponse().read()) == {"mode": "DEMO", "status": "ok"}
        body = urlencode(fields())
        client.request(
            "POST", "/mailgun/mime", body,
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = client.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["accepted"] is True
        client.request(
            "GET", "/receipts?email=user%40example.com", headers={"Authorization": f"Bearer {token}"}
        )
        response = client.getresponse()
        assert response.status == 200
        receipts = json.loads(response.read())["receipts"]
        assert len(receipts) == 1
        assert receipts[0]["sender_address"] == "user@example.com"
        client.request("GET", "/receipts?email=user%40example.com")
        assert client.getresponse().status == 401
        client.request("POST", "/answers", "")
        assert client.getresponse().status == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
