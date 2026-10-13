from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.runner import BotpressChatRunner, BotpressWebchatRunner, RunnerError
from scripts import path_b_endpoints


REPO = Path(__file__).resolve().parents[1]


class _Response:
    def __init__(self, payload=None, *, lines=(), status_code=200):
        self._payload = payload or {}
        self._lines = lines
        self.status_code = status_code
        self.text = "test response"

    def json(self):
        return self._payload

    def iter_lines(self):
        yield from self._lines

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class _WebchatClient:
    def __init__(self, identity: int):
        self.identity = identity
        self.message_sent = threading.Event()
        self.urls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def post(self, url, **kwargs):
        self.urls.append(url)
        if url.endswith("/users"):
            return _Response({"user": {"id": f"user-{self.identity}"}, "key": "secret"})
        if url.endswith("/messages"):
            self.message_sent.set()
            return _Response({"message": {"id": f"sent-{self.identity}"}})
        raise AssertionError(url)

    def stream(self, _method, url, **kwargs):
        self.urls.append(url)
        if kwargs.get("params"):
            def reply_lines():
                assert self.message_sent.wait(1)
                yield (
                    'data: {"type":"message_created","data":{"userId":"bot",'
                    '"payload":{"type":"text","text":"bounded reply"}}}'
                )
            return _Response(lines=reply_lines())
        return _Response(lines=[
            'data: {"type":"init","data":{"conversation":{"id":"conv-' +
            str(self.identity) + '"},"user":{"userKey":"secret-2"}}}'
        ])


def test_public_builder_uses_webchat_client_id_not_config_bot_id(monkeypatch):
    monkeypatch.delenv("BOTPRESS_WEBCHAT_CLIENT_ID", raising=False)
    monkeypatch.delenv("BOTPRESS_WEBCHAT_BOT_ID", raising=False)

    runner = path_b_endpoints._build_botpress()

    assert isinstance(runner, BotpressWebchatRunner)
    assert runner._client_id == "165ba858-09be-4baf-b835-02cb144289b6"
    assert runner._bot_id == "d8c8901f-8bf3-4132-8080-f950faefb32c"
    assert runner._client_id != runner._bot_id


def test_webchat_receives_reply_and_uses_independent_run_identities():
    clients = []
    session_ids = []

    def factory(*, timeout, session_id):
        session_ids.append(session_id)
        client = _WebchatClient(len(clients) + 1)
        clients.append(client)
        return client

    runner = BotpressWebchatRunner(
        client_id="client-id", bot_id="bot-id", rate_limit_s=0,
        timeout_s=1, client_factory=factory,
    )

    assert runner.complete("hello") == "bounded reply"
    assert runner.complete("hello again") == "bounded reply"
    assert len(set(session_ids)) == 2
    assert any("/client-id/users" in url for url in clients[0].urls)


@pytest.mark.parametrize("runner", [
    lambda: BotpressWebchatRunner(client_id="wkspace_invalid"),
    lambda: BotpressChatRunner(client_id="wkspace_invalid"),
    lambda: BotpressWebchatRunner(client_id="same", bot_id="same"),
])
def test_identifier_mixups_fail_before_network(runner):
    with pytest.raises(RunnerError):
        runner()


def test_chat_missing_key_is_sanitized_protocol_error():
    class Client:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def post(self, *_args, **_kwargs):
            return _Response({"code": "invalid_webhook", "detail": "do-not-echo"})

    runner = BotpressChatRunner(
        client_id="chat-webhook", client_factory=lambda **_kwargs: Client()
    )
    with pytest.raises(RunnerError) as exc_info:
        runner.complete("hello")

    message = str(exc_info.value)
    assert "top-level keys=['code', 'detail']" in message
    assert "do-not-echo" not in message
    assert "KeyError" not in message


def test_webchat_missing_user_fields_is_sanitized_protocol_error():
    class Client(_WebchatClient):
        def post(self, url, **kwargs):
            if url.endswith("/users"):
                return _Response({"error": "private-value"})
            return super().post(url, **kwargs)

    runner = BotpressWebchatRunner(
        client_id="client-id", bot_id="bot-id", rate_limit_s=0,
        client_factory=lambda **_kwargs: Client(1),
    )
    with pytest.raises(RunnerError) as exc_info:
        runner.complete("hello")

    message = str(exc_info.value)
    assert "top-level keys=['error']" in message
    assert "private-value" not in message


def test_public_botpress_cli_artifact_is_na_and_card_paths_fail_closed(
    monkeypatch, tmp_path, capsys,
):
    """Exercise the real CLI artifact/card boundary for public Botpress."""
    runner = SimpleNamespace(model_id=lambda: "botpress:webchat:fixture")
    monkeypatch.setitem(
        path_b_endpoints.VENDOR_BUILDERS,
        "botpress",
        (lambda: runner, "botpress_demo"),
    )
    monkeypatch.setattr(path_b_endpoints, "BENCHES", [])
    monkeypatch.setattr(
        path_b_endpoints,
        "load_priors",
        lambda *_args, **_kwargs: (None, {"P1.1": 1.0}),
    )
    monkeypatch.setattr(
        path_b_endpoints,
        "merge_measurements_with_priors",
        lambda *_args: ({"P1.1": 1.0}, {"P1.1": "prior"}),
    )
    rubric = SimpleNamespace(version="fixture-v1", active_dim_ids=["P1.1"])
    monkeypatch.setattr(path_b_endpoints.Rubric, "load", lambda *_args: rubric)
    monkeypatch.setattr(path_b_endpoints.Rates, "load", lambda *_args: object())
    scored = {
        "are_total": 99,
        "bmi": 99,
        "band": {"display": "A", "posture": "preferred"},
        "pillars": {"ROBUSTNESS": {"score": 99}},
    }
    monkeypatch.setattr(path_b_endpoints, "score", lambda *_args, **_kwargs: scored)

    def forbidden_quote(*_args, **_kwargs):
        raise AssertionError("report-only public Botpress must never be quoted")

    monkeypatch.setattr(path_b_endpoints, "quote", forbidden_quote)
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(
        sys,
        "argv",
        ["path_b_endpoints.py", "--vendor", "botpress", "--runs-dir", str(runs_dir)],
    )

    assert path_b_endpoints.main() == 0
    record = json.loads((runs_dir / "botpress.json").read_text())
    assert record["status"] == "reported_not_scored"
    assert record["score_input"] is False
    for forbidden in ("are_total", "bmi", "band", "pillars", "subscores", "premium"):
        assert forbidden not in record

    card = tmp_path / "botpress.html"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "path_b_endpoints.py", "--vendor", "botpress",
            "--runs-dir", str(runs_dir), "--render-only",
            "--card-out", str(card),
            "--forensic-bundle", "/api/attachments/fixture/content",
        ],
    )
    assert path_b_endpoints.main() == 2
    assert not card.exists()
    assert "report-only with partial public-demo coverage" in capsys.readouterr().err

    direct_card = tmp_path / "direct.html"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "path_b_endpoints.py", "--vendor", "botpress",
            "--runs-dir", str(runs_dir), "--card-out", str(direct_card),
            "--forensic-bundle", "/api/attachments/fixture/content",
        ],
    )
    assert path_b_endpoints.main() == 2
    assert not direct_card.exists()


def test_public_botpress_fixture_preserves_report_only_boundary():
    fixture = json.loads(
        (REPO / "artifacts/BEAAA-35699/public-botpress-report-only-fixture.json")
        .read_text()
    )
    assert fixture["fixture"] is True
    assert fixture["status"] == "reported_not_scored"
    assert fixture["score_input"] is False
    assert fixture["score_fields_disposition"].startswith("N/A")
    for forbidden in ("are_total", "bmi", "band", "pillars", "subscores", "premium"):
        assert forbidden not in fixture
