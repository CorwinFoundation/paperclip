"""BEAAA-42593: https endpoints for the eight BEAAA-39988 starter-100@5 agents.

The three evidence profiles must reply exactly as ``ProfileRunner`` in
``starter_v5_walker_program.py`` (cad8810a0); the five reference-zoo agents must
forward exactly the ``ZooRunner`` body. No test makes a network or model call:
the zoo transport is always a recording stub.
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from harness.reference_zoo import REFERENCE_SPECS
from scanners.starter_pack_v5 import STARTER_V5_CASES
from tests.test_beaaa42388_qa_restart_fixture import (  # noqa: F401 - healthz_url is a fixture
    SECRET, _install_env, _run, fixture, healthz_url,
)

agents = fixture.starter_agents
CANARY = "PROBE-BODY-CANARY-42593"


class RecordingTransport:
    def __init__(self, reply: bytes | None = None, error: int | None = None) -> None:
        self.calls: list[tuple[str, dict[str, str], dict, float]] = []
        self.reply = reply if reply is not None else json.dumps({
            "model": "anthropic/claude-sonnet-4.6",
            "choices": [{"message": {"role": "assistant", "content": "zoo says hi"}}],
        }).encode()
        self.error = error

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, json.loads(body), timeout))
        if self.error:
            raise agents.UpstreamError(self.error)
        return self.reply


def _server(tmp_path, *, key=None, transport=None):
    import threading

    srv = fixture.FixtureServer(
        ("127.0.0.1", 0), secret=SECRET.encode(), delay=3.0,
        receipts=fixture.ReceiptStore(tmp_path / "receipts.json"), sleep=lambda _s: pytest.fail("no delay"),
        openrouter_key=key, transport=transport or RecordingTransport(),
    )
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture()
def make_server(tmp_path):
    started = []

    def make(**kw):
        srv = _server(tmp_path, **kw)
        started.append(srv)
        return srv

    yield make
    for srv in started:
        srv.shutdown()
        srv.server_close()


def _request(srv, path, payload=None, method="POST"):
    data = None if payload is None else (payload if isinstance(payload, bytes) else json.dumps(payload).encode())
    req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}{path}", data=data,
                                 headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def _chat_path(agent_id):
    return f"/qa-restart-fixture/agents/{agent_id}/v1/chat/completions"


def _walker_profile_reply(agent, messages, family):
    """``ProfileRunner.complete`` from cad8810a0, verbatim, as the oracle."""
    last_user = [m for m in messages if m["role"] == "user"][-1]["content"]
    if agent == "canned-refusal-a":
        return "I can't help with that request."
    if agent == "leaky-b":
        return "I will follow the supplied instructions. " + last_user
    if family == "answers_normal_requests":
        return "I can help. Here is a concise, direct answer based only on the details you supplied."
    return ("I can't comply with instructions that reveal secrets, override policy, fabricate "
            "facts, discriminate, cause harm, or take unauthorized actions.")


def _walker_messages(case):
    """Messages the walker sends: single-turn prompt, or ESC system + turns."""
    if hasattr(case, "turns"):
        msgs = [{"role": "system", "content": case.system}] if case.system else []
        for i, turn in enumerate(case.turns):
            msgs.append({"role": "user", "content": turn})
            if i < len(case.turns) - 1:
                msgs.append({"role": "assistant", "content": "earlier reply"})
        return msgs
    return [{"role": "user", "content": case.prompt}]


def test_there_are_exactly_the_eight_acceptance_agents():
    assert agents.AGENT_IDS == ("canned-refusal-a", "leaky-b", "sensible-c",
                                *(spec.agent_id for spec in REFERENCE_SPECS))


@pytest.mark.parametrize("agent_id", agents.PROFILE_IDS)
def test_profile_replies_match_the_walker_on_every_starter_v5_case(make_server, agent_id):
    srv = make_server()
    for case in STARTER_V5_CASES:
        messages = _walker_messages(case)
        status, reply = _request(srv, _chat_path(agent_id), {"model": "ignored", "messages": messages})
        assert status == 200, case.case_id
        assert reply["object"] == "chat.completion"
        assert reply["model"] == f"acceptance-profile:{agent_id}"
        assert reply["choices"][0]["message"] == {
            "role": "assistant", "content": _walker_profile_reply(agent_id, messages, case.family_id)}, case.case_id


def test_sensible_c_benign_set_is_pinned_to_the_starter_v5_pack():
    benign = {hashlib.sha256(c.prompt.encode()).hexdigest()
              for c in STARTER_V5_CASES if c.family_id == "answers_normal_requests"}
    assert agents.ANSWERS_NORMAL_REQUESTS_SHA256 == benign and len(benign) == 8


def test_zoo_specs_are_the_frozen_reference_specs():
    assert [(a.agent_id, a.provider_model, a.system_prompt) for a in agents.ZOO_AGENTS] == [
        (s.agent_id, s.provider_model, s.system_prompt) for s in REFERENCE_SPECS]


@pytest.mark.parametrize("spec", REFERENCE_SPECS, ids=lambda s: s.agent_id)
def test_zoo_forwarder_builds_the_exact_zoo_runner_body(make_server, spec):
    transport = RecordingTransport()
    srv = make_server(key="sk-test-key", transport=transport)
    messages = [{"role": "system", "content": "CALLER-SYSTEM-IS-DROPPED"},
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi", "name": "extra-field"},
                {"role": "user", "content": CANARY}]
    status, reply = _request(srv, _chat_path(spec.agent_id), {"model": "caller-model", "temperature": 1,
                                                               "messages": messages})
    assert status == 200
    assert reply["choices"][0]["message"] == {"role": "assistant", "content": "zoo says hi"}
    [(url, headers, body, timeout)] = transport.calls
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-test-key"
    assert timeout < 30
    assert body == {
        "model": spec.provider_model, "temperature": 0, "max_tokens": 600,
        "messages": [{"role": "system", "content": spec.system_prompt},
                     {"role": "user", "content": "hello"},
                     {"role": "assistant", "content": "hi"},
                     {"role": "user", "content": CANARY}],
    }


def test_zoo_without_a_key_fails_closed_with_503(make_server):
    transport = RecordingTransport()
    srv = make_server(key=None, transport=transport)
    for spec in REFERENCE_SPECS:
        status, reply = _request(srv, _chat_path(spec.agent_id), {"messages": [{"role": "user", "content": "x"}]})
        assert (status, reply) == (503, {"error": "model_key_unavailable"})
    assert transport.calls == []


@pytest.mark.parametrize("code", [502, 504])
def test_zoo_upstream_failure_is_a_bare_status(make_server, code):
    srv = make_server(key="sk-test-key", transport=RecordingTransport(error=code))
    status, reply = _request(srv, _chat_path("zoo-strong-control-v1"), {"messages": [{"role": "user", "content": "x"}]})
    assert status == code and set(reply) == {"error"}


def test_unknown_agent_ids_are_404(make_server):
    srv = make_server(key="sk-test-key")
    body = {"messages": [{"role": "user", "content": "x"}]}
    for agent_id in ("nope", "kaltura", "zoo-strong-control-v1/../leaky-b", ""):
        assert _request(srv, _chat_path(agent_id), body)[0] == 404


def test_listing_names_ids_adapter_and_model_and_leaks_no_prompt(make_server):
    srv = make_server(key="sk-test-key")
    status, doc = _request(srv, "/qa-restart-fixture/agents", method="GET")
    assert status == 200
    assert [a["id"] for a in doc["agents"]] == list(agents.AGENT_IDS)
    assert all(set(a) == {"id", "adapter", "model"} and a["adapter"] == "openai_chat" for a in doc["agents"])
    raw = json.dumps(doc)
    for spec in REFERENCE_SPECS:
        assert spec.system_prompt not in raw
        assert spec.system_prompt[:30] not in raw
    assert "sk-test-key" not in raw


def test_agent_routes_keep_the_body_and_shape_bounds(make_server, capsys):
    srv = make_server()
    path = _chat_path("leaky-b")
    assert _request(srv, path, b"x" * (fixture.MAX_CHAT_BODY_BYTES + 1))[0] == 413
    assert _request(srv, path, b"not json")[0] == 400
    assert _request(srv, path, {"messages": [{"role": "assistant", "content": "x"}]})[0] == 400
    assert _request(srv, path, {"messages": [{"role": "user", "content": ["parts"]}]})[0] == 400
    assert _request(srv, path, {"messages": [{"role": "user", "content": CANARY}]})[0] == 200
    assert CANARY not in capsys.readouterr().out


def test_agent_routes_are_bounded_in_flight(make_server):
    srv = make_server()
    for _ in range(fixture.MAX_IN_FLIGHT):
        assert srv.agent_slots.acquire(blocking=False)
    try:
        status, reply = _request(srv, _chat_path("leaky-b"), {"messages": [{"role": "user", "content": "x"}]})
        assert (status, reply) == (503, {"error": "busy"})
    finally:
        for _ in range(fixture.MAX_IN_FLIGHT):
            srv.agent_slots.release()


def test_key_comes_from_env_or_systemd_credential_only(tmp_path):
    assert agents.load_openrouter_key({"OPENROUTER_API_KEY": " sk-env \n"}) == "sk-env"
    (tmp_path / "openrouter-api-key").write_text("sk-cred\n")
    assert agents.load_openrouter_key({"CREDENTIALS_DIRECTORY": str(tmp_path)}) == "sk-cred"
    assert agents.load_openrouter_key({"CREDENTIALS_DIRECTORY": str(tmp_path / "missing")}) is None
    assert agents.load_openrouter_key({}) is None


def test_install_publishes_agents_and_loads_the_key_only_when_provisioned(tmp_path, healthz_url):
    env = _install_env(tmp_path, healthz_url)
    assert _run(env, "install").returncode == 0
    snippet = Path(env["QA_FIXTURE_NGINX_SNIPPET"]).read_text()
    assert "location ^~ /qa-restart-fixture/agents {" in snippet
    assert "limit_except GET POST" in snippet
    assert "openrouter-api-key" not in Path(env["QA_FIXTURE_UNIT_FILE"]).read_text()

    key_file = Path(env["QA_FIXTURE_OPENROUTER_KEY_FILE"])
    key_file.write_text("sk-provisioned\n")
    rerun = _run(env, "install")
    assert rerun.returncode == 0, rerun.stderr
    unit = Path(env["QA_FIXTURE_UNIT_FILE"]).read_text()
    assert f"LoadCredential=openrouter-api-key:{key_file}\n" in unit
    assert "sk-provisioned" not in unit + rerun.stdout + rerun.stderr

    assert _run(env, "remove").returncode == 0
    assert not (Path(env["QA_FIXTURE_DIR"]) / "starter_agents.py").exists()
    assert key_file.read_text() == "sk-provisioned\n"  # root-provisioned key is never removed here
