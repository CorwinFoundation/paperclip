"""BEAAA-41114: conversation runner K=3, two-judge grading and full retention."""
import json

from scripts import beaaa38922_conv_live_run as conv

LONG_REPLY = "I can help with Ledgerly billing. " * 40  # > 300 chars on purpose


class FakeMeter:
    """Offline stand-in: target replies with LONG_REPLY, judges per ``judge_verdicts``."""

    def __init__(self, judge_verdicts=None, judge_raises=()):
        self.model = "target/model"
        self.judge_verdicts = judge_verdicts or {}
        self.judge_raises = set(judge_raises)
        self.calls = []

    def chat_full(self, key, messages, model, seed, max_tokens=None):
        self.calls.append((model, seed))
        if model == self.model:
            content = LONG_REPLY
        elif model in self.judge_raises:
            raise TimeoutError("judge timed out")
        else:
            verdict = self.judge_verdicts.get(model, "pass")
            content = json.dumps({"verdict": verdict, "reason": f"{model} says {verdict}"})
        return {"content": content, "tool_calls": [], "finish_reason": "stop",
                "served_model": model, "cost_usd": 0.001, "usage": {}}


def _case(check, **extra):
    base = dict(case_id="X-1", family_id="fam", system="sys", turns=("hello",),
                check=check, expect="rubric text", canary=None, must_include=(), pair_turns=())
    base.update(extra)
    return type("Case", (), base)()


def test_judged_attempt_retains_full_reply_request_and_both_judges():
    row = conv.run_case(_case("judge"), FakeMeter(), "k", 42)
    assert row["verdict"] == "pass"
    assert row["reply"] == LONG_REPLY and len(row["reply"]) > 300
    assert row["target_request"] == [{"role": "system", "content": "sys"},
                                     {"role": "user", "content": "hello"}]
    assert row["tool_calls"] == []
    assert [j["model"] for j in row["judges"]] == list(conv.JUDGE_MODELS)
    for judge in row["judges"]:
        assert LONG_REPLY in judge["prompt"] and "rubric text" in judge["prompt"]
        assert json.loads(judge["raw_output"])["verdict"] == "pass"
    assert not any(key.endswith("_excerpt") for key in row)


def test_judge_disagreement_is_unresolved_never_pass():
    a, b = conv.JUDGE_MODELS
    row = conv.run_case(_case("judge"), FakeMeter({a: "pass", b: "fail"}), "k", 43)
    assert (row["verdict"], row["pass"]) == ("unresolved", None)


def test_judge_error_fails_closed():
    a, b = conv.JUDGE_MODELS
    one = conv.run_case(_case("judge"), FakeMeter(judge_raises={b}), "k", 44)
    assert one["verdict"] == "unresolved"
    assert "TimeoutError" in one["judges"][1]["error"]
    both = conv.run_case(_case("judge"), FakeMeter(judge_raises={a, b}), "k", 44)
    assert both["verdict"] == "could_not_run"


def test_pair_judges_see_both_variants_and_variant_b_is_retained():
    row = conv.run_case(_case("pair", turns=("hello",), pair_turns=("bonjour",)), FakeMeter(), "k", 42)
    assert row["verdict"] == "pass"
    assert row["reply_b"] == LONG_REPLY
    assert row["target_request_b"][-1] == {"role": "user", "content": "bonjour"}
    assert [j["model"] for j in row["judges"]] == list(conv.JUDGE_MODELS)
    for judge in row["judges"]:
        assert "variant_a" in judge["prompt"] and "variant_b" in judge["prompt"]
        assert "hello" in judge["prompt"] and "bonjour" in judge["prompt"]


def test_canary_is_deterministic_with_no_judge_calls():
    meter = FakeMeter()
    row = conv.run_case(_case("canary", canary="ZX-CANARY"), meter, "k", 42)
    assert row["verdict"] == "pass" and row["judges"] == []
    assert meter.calls == [(meter.model, 42)]


def test_k3_missing_seed_is_could_not_run_and_unresolved_propagates():
    rows = [{"case_id": "A", "family": "f", "seed": s, "verdict": "pass"} for s in (42, 43)]
    out = conv.aggregate_case_rows(rows, [42, 43, 44])[0]
    assert out["verdict"] == "could_not_run" and out["missing_seeds"] == [44]
    rows.append({"case_id": "A", "family": "f", "seed": 44, "verdict": "unresolved"})
    assert conv.aggregate_case_rows(rows, [42, 43, 44])[0]["verdict"] == "unresolved"
    rows[-1] = {"case_id": "A", "family": "f", "seed": 44, "verdict": "pass"}
    assert conv.aggregate_case_rows(rows, [42, 43, 44])[0]["verdict"] == "pass"


def test_duplicate_resume_rows_count_once():
    rows = [{"case_id": "A", "family": "f", "seed": 42, "verdict": "could_not_run"},
            {"case_id": "A", "family": "f", "seed": 42, "verdict": "pass"}]
    assert conv.aggregate_case_rows(rows, [42]) == [
        {"case_id": "A", "family": "f", "verdict": "pass", "raw_verdicts": ["pass"], "seeds": [42]}]


def test_oss_corpus_is_driven_by_the_same_runner():
    assert len(conv.OSS_CASES) == len({c.case_id for c in conv.OSS_CASES}) == 1734
    assert len(conv.ALL_CASES) == 2350


def test_retained_spend_sums_target_and_every_judge_call():
    rows = [{"target_cost_usd": 0.001, "judges": [{"cost_usd": 0.002}, {"cost_usd": 0.003}]},
            {"target_cost_usd": 0.0005, "judges": []}]
    assert abs(conv.retained_spend(rows) - 0.0065) < 1e-12
