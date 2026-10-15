import json
import urllib.error

from scripts import beaaa41114_rejudge_402 as rj
from scripts.beaaa41114_report_aggregate import resolve_attempts

E402 = "<HTTPError 402: 'Payment Required'>"


def _judge(model, verdict="could-not-run", error=E402):
    return {"model": model, "prompt": f"grade for {model}", "raw_output": None, "served_model": None,
            "cost_usd": 0.0, "error": error, "verdict": verdict}


def _row(case, seed, judges, detail="hint:x|decided_by:judge|two_judge:could_not_run:a|b"):
    return {"case_id": case, "seed": seed, "reply": "full reply", "judges": judges,
            "verdict": "could_not_run", "pass": None, "detail": detail}


class FakeMeter:
    def __init__(self, answers, cap=5.0):
        self.answers, self.usd, self.cap, self.prompts = answers, 0.0, cap, []

    def over_cap(self, margin=0.10):
        return self.usd >= self.cap - margin

    def chat_full(self, key, messages, model, seed, max_tokens=None):
        self.prompts.append((model, messages[0]["content"]))
        ans = self.answers[model]
        if isinstance(ans, Exception):
            raise ans
        self.usd += 0.001
        return {"content": json.dumps({"verdict": ans, "reason": "r"}), "served_model": model,
                "cost_usd": 0.001}


def _write(tmp_path, rows):
    p = tmp_path / "rows.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


def _read(p):
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def test_rejudge_appends_decided_row_from_retained_prompts_and_wins_in_aggregate(tmp_path):
    ok = _judge("a", verdict="pass", error=None) | {"raw_output": '{"verdict":"pass"}'}
    rows = [_row("c1", 42, [_judge("a"), _judge("b")]),
            _row("c2", 42, [ok, _judge("b")]),
            {"case_id": "c3", "seed": 42, "verdict": "pass", "judges": []}]
    p = _write(tmp_path, rows)
    meter = FakeMeter({"a": "pass", "b": "fail"})
    summary = rj.run(p, meter, "k", workers=1)
    out = _read(p)
    assert out[:3] == rows  # originals untouched
    new = {r["case_id"]: r for r in out[3:]}
    assert summary["written"] == 2 and not summary["stopped_on_402"]
    assert new["c1"]["verdict"] == "unresolved" and new["c1"]["pass"] is None  # disagreement stays unresolved
    assert new["c1"]["detail"] == "hint:x|decided_by:judge|two_judge:unresolved:pass|fail"
    assert new["c2"]["judges"][0] is not None and new["c2"]["judges"][0]["raw_output"] == '{"verdict":"pass"}'
    assert sorted(m for m, _ in meter.prompts) == ["a", "b", "b"]  # c2's answered judge is not re-sent
    assert all(r["rejudged"]["reason"] == "judge_http_402" for r in out[3:])
    resolved = resolve_attempts(out)
    assert resolved[("c1", 42)] == "unresolved" and resolved[("c2", 42)] == "unresolved"
    # idempotent: a second run finds nothing pending
    assert rj.run(p, FakeMeter({"a": "pass", "b": "pass"}), "k", workers=1)["written"] == 0


def test_new_402_stops_without_writing(tmp_path):
    p = _write(tmp_path, [_row("c1", 42, [_judge("a"), _judge("b")])])
    err = urllib.error.HTTPError("u", 402, "Payment Required", {}, None)
    summary = rj.run(p, FakeMeter({"a": err, "b": "pass"}), "k", workers=1)
    assert summary["stopped_on_402"] and summary["written"] == 0
    assert len(_read(p)) == 1


def test_pair_detail_marker_is_preserved(tmp_path):
    p = _write(tmp_path, [_row("p1", 43, [_judge("a"), _judge("b")],
                               detail="two_judge_pair:could_not_run:could-not-run|could-not-run")])
    rj.run(p, FakeMeter({"a": "pass", "b": "pass"}), "k", workers=1)
    new = _read(p)[-1]
    assert new["detail"] == "two_judge_pair:pass:pass|pass" and new["verdict"] == "pass" and new["pass"] is True
