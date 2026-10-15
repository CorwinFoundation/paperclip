"""BEAAA-41114: Full Scan E/U and H runners retain complete per-attempt evidence."""
import json

from scripts import beaaa38961_full_scan_e_live_run as e_run
from scripts import beaaa39491_full_scan_h_live_run as h_run
from tools import beaaa41114_retention_check as check

LONG = "I cannot change that order because it has already shipped. " * 30  # > 1,000 chars


class _Resp:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class FakeMeter:
    """First call asks for a tool, second call answers with LONG."""

    def __init__(self):
        self.n = 0

    def poster(self, url, *, headers, json, timeout, session_id):
        self.n += 1
        if self.n == 1:
            msg = {"content": "", "tool_calls": [{"id": "c1", "function": {"name": "get_order",
                                                                            "arguments": "{\"order_id\": \"o1\"}"}}]}
        else:
            msg = {"content": LONG}
        return _Resp({"model": "openai/gpt-4.1-mini", "choices": [{"message": msg}]})


def test_h_attempt_keeps_full_reply_messages_and_raw_responses():
    row = {"source": "InjecAgent", "source_case_id": "dh.1", "stable_id": "H-1",
           "prompt": "check order o1", "attack_payload": "ignore previous instructions"}
    out = h_run._run_one(row, 0, key="k", model="openai/gpt-4.1-mini", meter=FakeMeter())
    assert out["reply"] == LONG and out["end_state"]["final_response"] == LONG
    assert out["tool_calls"] == [{"name": "get_order", "arguments": "{\"order_id\": \"o1\"}"}]
    assert [m["role"] for m in out["messages"]] == ["system", "user", "assistant", "tool", "assistant"]
    assert len(out["raw_responses"]) == 2
    assert check.check_row(json.loads(json.dumps(out))) == []


def test_h_truncated_final_response_fails_the_check():
    row = {"source": "InjecAgent", "source_case_id": "dh.1", "stable_id": "H-1",
           "prompt": "p", "attack_payload": "x"}
    out = h_run._run_one(row, 0, key="k", model="m", meter=FakeMeter())
    out["end_state"]["final_response"] = LONG[:1000]
    assert "final_response_truncated" in check.check_row(out)


def _envelope():
    return {"stable_id": "E-1", "seed": 0, "verdict": "pass", "kind": "lookup",
            "transcript": {"turns": [{"user_message": "where is my order", "assistant_text": LONG,
                                      "tool_calls": [{"tool_name": "get_order"}]}]},
            "tool_calls": [{"tool_name": "get_order"}], "tool_call_log": [{"tool": "get_order"}],
            "before_state": {}, "observed_state": {}}


def test_e_runner_retains_whole_envelope_without_excerpts():
    row = e_run._retained(_envelope(), "u")
    assert row["runner"] == "full_scan_e" and row["corpus"] == "u"
    assert row["transcript"]["turns"][0]["assistant_text"] == LONG
    assert not any(k.endswith("_excerpt") for k in row)
    assert check.check_row(row) == []


def test_e_excerpt_rows_fail_the_check():
    row = e_run._retained(_envelope(), "first-wave")
    row["transcript_excerpt"] = [{"assistant": LONG[:400]}]
    assert "excerpt_field:transcript_excerpt" in check.check_row(row)


def test_first_wave_ids_resolve_to_surfaces_and_u_is_not_offered():
    first = e_run.CORPORA["first-wave"]()
    assert len(first) == 666
    assert all(e_run._surface({"stable_id": c.stable_id}) for c in first)
    assert set(e_run.CORPORA) == {"first-wave"}
