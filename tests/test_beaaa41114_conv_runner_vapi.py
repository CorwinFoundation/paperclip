"""BEAAA-41114 item 4(b): chat-only VAPI target on the conversation runner."""
import gzip
import json

import pytest

from scripts import beaaa38922_conv_live_run as conv
from tests.test_beaaa41114_conv_runner_retention import FakeMeter, _case

AID = "0f0e0d0c-0b0a-4908-8706-050403020100"
LONG = "VAPI reply text " * 40


class _Resp:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


@pytest.fixture
def vapi(monkeypatch):
    sent = []

    def fake_urlopen(req, timeout=None):
        sent.append(json.loads(gzip.decompress(req.data)))
        assert req.full_url == conv.VAPI_CHAT_URL
        return _Resp({"id": "chat-1", "assistantId": AID, "cost": 0.0123,
                      "output": [{"role": "assistant", "content": LONG}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(conv, "TARGET", conv.VapiTarget("vk", AID))
    return sent


def test_vapi_attempt_sends_turns_only_and_retains_full_raw_reply(vapi):
    row = conv.run_case(_case("judge", turns=("hi", "hello", "again")), FakeMeter(), "k", 42)
    assert vapi == [{"assistantId": AID, "stream": False, "input": [
        {"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "again"}]}]
    assert row["reply"] == LONG and row["model"] == f"vapi:{AID}"
    assert row["system_prompt_delivered"] is False
    assert row["vapi_raw_response"]["output"][0]["content"] == LONG
    assert row["vapi_cost_usd"] == 0.0123 and row["target_cost_usd"] == 0.0
    assert row["verdict"] == "pass" and len(row["judges"]) == 2


def test_canary_only_in_case_system_prompt_cannot_run_on_fixed_assistant(vapi):
    row = conv.run_case(_case("canary", system="secret ZX-99", canary="ZX-99"), FakeMeter(), "k", 42)
    assert (row["verdict"], row["pass"]) == ("could_not_run", None)
    assert row["detail"] == conv.SYSTEM_PROMPT_FIXED
    assert vapi == []


def test_pair_case_calls_vapi_for_both_variants(vapi):
    row = conv.run_case(_case("pair", turns=("a",), pair_turns=("b",)), FakeMeter(), "k", 43)
    assert len(vapi) == 2 and row["reply_b"] == LONG
    assert row["vapi_raw_response_b"]["id"] == "chat-1"


def test_assistant_identity_mismatch_fails_closed(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _Resp(
        {"assistantId": "11111111-1111-4111-8111-111111111111", "output": [{"role": "assistant", "content": "x"}]}))
    monkeypatch.setattr(conv, "TARGET", conv.VapiTarget("vk", AID))
    row = conv.run_case(_case("judge"), FakeMeter(), "k", 44)
    assert (row["verdict"], row["pass"]) == ("could_not_run", None)
    assert "assistantId mismatch" in row["error"]


def test_vapi_target_requires_uuid():
    with pytest.raises(SystemExit):
        conv.VapiTarget("vk", "not-a-uuid")


def test_vapi_request_sends_explicit_user_agent(monkeypatch):
    # Cloudflare in front of api.vapi.ai rejects the default Python-urllib agent
    # with 403 / error 1010, which turned every live attempt into could_not_run.
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen.update({k.lower(): v for k, v in req.header_items()})
        return _Resp({"id": "chat-1", "assistantId": AID, "output": [{"role": "assistant", "content": "ok"}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    conv.VapiTarget("vk", AID).chat_full([{"role": "user", "content": "hi"}])
    assert seen.get("user-agent", "").startswith("backbond-scanner/")


class _HTTP429(Exception):
    pass


def _err429(retry_after=None):
    import urllib.error
    from email.message import Message
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(conv.VAPI_CHAT_URL, 429, "Too Many Requests", headers, None)


def test_vapi_429_backs_off_then_retains_the_successful_chat(monkeypatch):
    calls, waits = [], []

    def fake_urlopen(req, timeout=None):
        calls.append(1)
        if len(calls) < 3:
            raise _err429("7" if len(calls) == 1 else None)
        return _Resp({"id": "chat-2", "assistantId": AID, "cost": 0.004,
                      "output": [{"role": "assistant", "content": LONG}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(conv, "_vapi_sleep", waits.append)
    monkeypatch.setattr(conv, "TARGET", conv.VapiTarget("vk", AID))
    row = conv.run_case(_case("judge", turns=("hi",)), FakeMeter(), "k", 42)
    assert len(calls) == 3 and waits == [7.0, 4.0]
    assert row["error"] is None and row["reply"] == LONG and row["vapi_chat_id"] == "chat-2"


def test_vapi_429_exhausted_stays_could_not_run_with_transport_error(monkeypatch):
    waits = []

    def fake_urlopen(req, timeout=None):
        raise _err429()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(conv, "_vapi_sleep", waits.append)
    monkeypatch.setattr(conv, "TARGET", conv.VapiTarget("vk", AID))
    row = conv.run_case(_case("judge", turns=("hi",)), FakeMeter(), "k", 42)
    assert len(waits) == conv.VAPI_429_TRIES - 1 and max(waits) <= 60
    assert row["verdict"] == "could_not_run" and conv._is_429_row(row)
    assert row["judges"] == [] and row["reply"] is None


def test_vapi_non_429_http_error_is_not_retried(monkeypatch):
    import urllib.error
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(1)
        raise urllib.error.HTTPError(conv.VAPI_CHAT_URL, 500, "boom", None, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(conv, "_vapi_sleep", lambda s: pytest.fail("slept on a 500"))
    monkeypatch.setattr(conv, "TARGET", conv.VapiTarget("vk", AID))
    row = conv.run_case(_case("judge", turns=("hi",)), FakeMeter(), "k", 42)
    assert len(calls) == 1 and row["verdict"] == "could_not_run" and not conv._is_429_row(row)


def test_retry_429_resends_only_attempts_held_by_429_rows(monkeypatch, tmp_path):
    case = conv.CASES[0]
    out = tmp_path / "run"
    out.mkdir()
    rows_path = out / "conv_live_openai_gpt-4.1-mini_rows.jsonl"
    base = {"family": case.family_id, "check": case.check, "pass": None, "source": "backbond_written",
            "model": "openai/gpt-4.1-mini", "reply": None, "detail": None}
    r429 = base | {"case_id": case.case_id, "seed": 42, "verdict": "could_not_run",
            "error": "<HTTPError 429: 'Too Many Requests'>", "judges": []}
    decided = base | {"case_id": case.case_id, "seed": 43, "verdict": "pass", "error": None, "judges": []}
    healed = base | {"case_id": case.case_id, "seed": 44, "verdict": "could_not_run",
              "error": "<HTTPError 429: 'Too Many Requests'>", "judges": []}
    rows_path.write_text("\n".join(json.dumps(r) for r in (r429, decided, healed, healed | {
        "verdict": "fail", "pass": False, "error": None})) + "\n")
    sent = []

    def fake_run_case(c, meter, key, seed):
        sent.append((c.case_id, seed))
        return base | {"case_id": c.case_id, "seed": seed, "verdict": "pass", "pass": True,
                       "error": None, "judges": []}

    monkeypatch.setattr(conv, "run_case", fake_run_case)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr("sys.argv", ["x", "--case-ids", case.case_id, "--seeds", "42", "43", "44",
                                     "--out", str(out), "--workers", "1", "--retry-429"])
    conv.main()
    assert sent == [(case.case_id, 42)]
    retained = [json.loads(line) for line in rows_path.read_text().splitlines()]
    assert retained[0] == r429 and len(retained) == 5  # 429 evidence kept; retry appended
