"""BEAAA-30934: the hand-sample re-judge can grade rows again with a live judge."""

from __future__ import annotations

from types import SimpleNamespace

from scripts import beaaa30934_rejudge_hand_sample as rj


def _row(case_id: str, verdict: str = "fail") -> dict:
    return {"test_id": "fam", "case_id": case_id, "verdict": verdict, "responses": ["Canberra."]}


def test_live_rejudger_regrades_rows_no_gate_decides(monkeypatch):
    import tools.registered_case_executor as rce
    import tools.text_case_materializer as tcm

    calls = []
    monkeypatch.setattr(rj, "rejudge", lambda row, manifest: ("fail", "judge_verdict_unchanged_by_deterministic_gates", "recorded"))
    monkeypatch.setattr(tcm, "is_text_compatible_test", lambda test_id: True)
    monkeypatch.setattr(tcm, "materialize_text_case", lambda t, c, r: SimpleNamespace(prompts=("q",), expected={"e": 1}, judge_criteria="crit"))

    def _judge(judge, **kwargs):
        calls.append(kwargs)
        return {"verdict": "pass"}

    monkeypatch.setattr(rce, "_judge_materialized_text_case", _judge)
    fn = rj.live_rejudger(object(), [{"case_id": "c1"}])

    assert fn(_row("c1"), None) == ("pass", "live_judge", "live_judge")
    assert calls[0]["responses"] == ["Canberra."] and calls[0]["judge_criteria"] == "crit"
    # No registry row: the recorded verdict stands and no judge call is made.
    assert fn(_row("c2"), None)[1] == "live_judge_skipped:not_a_registry_text_case"
    assert len(calls) == 1


def test_live_rejudger_keeps_deterministic_decisions(monkeypatch):
    monkeypatch.setattr(rj, "rejudge", lambda row, manifest: ("not_applicable", "no tools", "applicability_gate"))
    fn = rj.live_rejudger(object(), [{"case_id": "c1"}])
    assert fn(_row("c1"), None) == ("not_applicable", "no tools", "applicability_gate")


def test_agreement_uses_the_supplied_verdict_function():
    labels = [{"case_id": "c1", "cto": "correct refusal", "cause": "scope", "agrees_with_judge": False}]
    result = rj.agreement([_row("c1")], labels, None, verdict_fn=lambda row, manifest: ("pass", "live_judge", "live_judge"))
    assert result["matches_cto"] == 1
