from __future__ import annotations

from pathlib import Path

import openpyxl

from tools.dual_judge_grader import (
    JUDGE_B_MODEL,
    SpendMeter,
    agreement_metrics,
    grade_rows,
    write_unresolved_queue,
    UNDERWRITING_FAMILIES,
)


def rows():
    return [
        {"case_id": "resolved", "family": "prompt_injection", "source": "conv-en", "prompt": "p", "reply": "r"},
        {"case_id": "unresolved", "family": "prompt_injection", "source": "conv-en", "prompt": "p", "reply": "r"},
        {"case_id": "na", "family": "E", "source": "oss", "prompt": "p", "reply": "r"},
    ]


def test_both_judges_see_identical_rubric_and_reference_and_mismatch_is_unresolved():
    calls = []

    def judge_a(**kwargs):
        calls.append(("a", kwargs))
        return {"verdict": "pass", "reason": "a"}

    def judge_b(**kwargs):
        calls.append(("b", kwargs))
        return {"verdict": "fail", "reason": "b"}

    result = grade_rows(rows()[:1], judge_a, judge_b, rubric="same", reference_answer={"x": 1})[0]
    assert result["resolved_verdict"] == "unresolved"
    assert result["judge_b_model"] == JUDGE_B_MODEL
    assert calls[0][1]["rubric"] == calls[1][1]["rubric"] == "same"
    assert calls[0][1]["reference_answer"] == calls[1][1]["reference_answer"] == {"x": 1}


def test_na_and_could_not_run_never_become_pass_or_fail():
    def judge(**kwargs):
        return {"verdict": "could_not_run", "reason": "missing trace"}

    result = grade_rows(rows()[2:], judge, judge, rubric="r")[0]
    assert result["resolved_verdict"] == "could-not-run"


def test_queue_puts_unresolved_first_and_leaves_human_fields_blank(tmp_path: Path):
    def judge(row, rubric, reference_answer):
        return {"verdict": "fail" if row["case_id"] == "unresolved" else "pass", "reason": "ok"}

    graded = grade_rows(rows()[:2], judge, judge, rubric="r")
    path = write_unresolved_queue(graded, tmp_path / "unresolved_queue.xlsx")
    sheet = openpyxl.load_workbook(path).active
    assert sheet.cell(2, 1).value == "resolved"
    # Re-run with a mismatch to prove ordering.
    graded = grade_rows(
        rows()[:2],
        lambda *a, **k: {"verdict": "pass", "reason": "a"},
        lambda row, *a, **k: {
            "verdict": "fail" if row["case_id"] == "unresolved" else "pass",
            "reason": "b",
        },
        rubric="r",
    )
    write_unresolved_queue(graded, path)
    sheet = openpyxl.load_workbook(path).active
    assert sheet.cell(2, 1).value == "unresolved"
    assert sheet.cell(2, 10).value is None
    assert sheet.cell(2, 11).value is None


def test_metrics_emit_accuracy_only_for_real_human_labels():
    evidence = [
        {"case_id": "1", "family": "prompt_injection", "source": "conv-en", "judge_a_verdict": "pass", "judge_b_verdict": "pass"},
        {"case_id": "2", "family": "prompt_injection", "source": "conv-en", "judge_a_verdict": "pass", "judge_b_verdict": "fail", "human_label": "pass"},
    ]
    metrics = agreement_metrics(evidence)
    family = next(m for m in metrics if m["dimension"] == "family")
    assert family["agreement"] == 0.5
    assert family["unresolved_rate"] == 0.5
    assert family["judge_a_accuracy"] == 1.0
    assert family["judge_b_accuracy"] == 0.0
    assert family["agreed_verdict_accuracy"] == 0.0
    no_labels = agreement_metrics([dict(evidence[0])])[0]
    assert "judge_a_accuracy" not in no_labels


def test_spend_meter_stops_before_cap():
    meter = SpendMeter(cap_usd=1.0)
    meter.reserve(0.75)
    try:
        meter.reserve(0.26)
    except RuntimeError as exc:
        assert "cap exceeded" in str(exc)
    else:
        raise AssertionError("spend cap did not stop the next call")


def test_spend_is_normalised_per_1000_judged_rows():
    meter = SpendMeter()
    grade_rows(rows()[:2], lambda **k: "pass", lambda **k: "pass", rubric="r", spend=meter,
               estimated_cost_per_judge=0.01)
    assert meter.spent_usd == 0.04
    assert meter.judged_rows == 2
    assert meter.usd_per_1000_rows == 20.0


def test_expanded_full_scan_families_default_to_dual_judging():
    expected = {"E", "H", "K", "L", "M", "N", "O", "P", "Q", "T", "U"}
    assert expected <= UNDERWRITING_FAMILIES
    calls = []
    source = [{"case_id": family, "family": family, "source": "full-scan"} for family in expected]
    graded = grade_rows(
        source,
        lambda **kwargs: calls.append(("a", kwargs["row"]["family"])) or "pass",
        lambda **kwargs: calls.append(("b", kwargs["row"]["family"])) or "pass",
        rubric="r",
    )
    assert len(calls) == len(expected) * 2
    assert all(row["single_judge"] is False for row in graded)


def test_single_judge_cost_debug_is_explicitly_marked_and_calls_only_a():
    calls = []
    graded = grade_rows(
        [{"case_id": "o", "family": "O", "source": "full-scan"}],
        lambda **kwargs: calls.append("a") or {"verdict": "pass", "reason": "debug"},
        None,
        rubric="r",
        single_judge=True,
    )
    assert calls == ["a"]
    assert graded[0]["single_judge"] is True
    assert "judge_b_verdict" not in graded[0]


def test_starter_rows_never_enter_dual_or_single_judge_routes():
    calls = []
    starter_rows = [
        {"case_id": "pack", "family": "E", "pack_id": "starter-100@5", "verdict": "pass"},
        {"case_id": "product", "family": "O", "product": "starter", "verdict": "fail"},
    ]
    graded = grade_rows(
        starter_rows,
        lambda **kwargs: calls.append("a") or "pass",
        lambda **kwargs: calls.append("b") or "pass",
        rubric="r",
    )
    assert calls == []
    assert [row["resolved_verdict"] for row in graded] == ["pass", "fail"]
    assert all("judge_a_model" not in row for row in graded)


def test_per_row_rubric_is_identical_for_both_judges_and_actual_cost_settles():
    from tools.dual_judge_grader import SpendMeter as Meter

    seen = []

    def judge(name):
        def call(*, row, rubric, reference_answer):
            seen.append((name, rubric, reference_answer))
            return {"verdict": "pass", "reason": name, "cost_usd": 0.001}
        return call

    meter = Meter(cap_usd=1.0)
    rows_in = [{"case_id": "c1", "family": "over_refusal", "underwriting_family": "conv-en"}]
    graded = grade_rows(rows_in, judge("a"), judge("b"), rubric=lambda r: f"rule:{r['family']}",
                        spend=meter, estimated_cost_per_judge=0.5)
    assert seen[0][1:] == seen[1][1:] == ("rule:over_refusal", None)
    assert graded[0]["rubric_text"] == "rule:over_refusal"
    # Reservations (0.5 each) are replaced by reported cost, so the cap is not consumed.
    assert abs(meter.spent_usd - 0.002) < 1e-9


def test_provider_error_is_could_not_run_and_never_pass(monkeypatch):
    import urllib.request

    from tools import dual_judge_grader as grader

    def boom(*args, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    judge = grader.openrouter_judge(grader.JUDGE_B_MODEL, api_key="test", retries=0)
    ok = lambda **k: {"verdict": "pass", "reason": "ok"}  # noqa: E731
    row = {"case_id": "c1", "family": "authentication", "underwriting_family": "conv-en"}
    result = grade_rows([row], ok, judge, rubric="r")[0]
    assert result["judge_b_verdict"] == "could-not-run"
    assert result["judge_b_reason"].startswith("judge_error")
    assert result["resolved_verdict"] == "unresolved"


def test_prompt_withholds_prior_verdicts_and_human_fields():
    from tools.dual_judge_grader import _prompt

    row = {"case_id": "c1", "family": "f", "full_reply": "hello", "judge_verdict": "pass",
           "second_judge_verdict": "fail", "judge_reason": "SECRET-REASON", "human_label": "pass",
           "deterministic_detail": "judge:PASS"}
    text = _prompt(row, "rubric", None)
    assert "hello" in text
    for leaked in ("SECRET-REASON", "judge:PASS", "second_judge_verdict", "human_label"):
        assert leaked not in text


def test_gold_workbook_scopes_judge_rows_to_underwriting_families_and_rubric_sets(tmp_path: Path):
    from tools.dual_judge_grader import family_rubrics, load_gold_workbook

    workbook = openpyxl.Workbook()
    sample = workbook.active
    sample.title = "sample"
    sample.append(["case_id", "family", "source", "turns", "full_reply", "judge_verdict", "decision_method"])
    sample.append(["c1", "over_refusal", "tools/full-scan-conv-runs/r/x.jsonl", "[]", "r", "pass", "judge"])
    sample.append(["c2", "data_leak", "tools/full-scan-conv-runs/r/x.jsonl", "[]", "r", "pass", "canary"])
    sample.append(["c3", "action_sandbox:orders", "artifacts/B/full_scan_e_x.jsonl", "[]", "r", "pass", "deterministic_action_sandbox"])
    guide = workbook.create_sheet("label_guide")
    guide.append(["family", "pass_rule"])
    guide.append(["over_refusal", "Explain the safeguards."])
    path = tmp_path / "gold.xlsx"
    workbook.save(path)
    rows_in, rules = load_gold_workbook(path)
    assert [(r["underwriting_family"], r["judge_decided"]) for r in rows_in] == [
        ("conv-en", True), ("conv-en", False), ("E", False)]
    before = family_rubrics(rules, "before")(rows_in[0])
    after = family_rubrics(rules, "after")(rows_in[0])
    assert "Explain the safeguards." in before and before in after
    assert "Worked examples" in after and "Worked examples" not in before
    calls = []
    graded = grade_rows(rows_in, lambda **k: calls.append(1) or "pass", lambda **k: "pass", rubric="r")
    assert len(calls) == 1 and graded[1]["resolved_verdict"] == "pass"


def test_hardened_rubrics_have_decision_rules_and_exactly_three_examples():
    import re

    for name in ("hallucinated_action", "over_refusal"):
        text = (Path("rubric") / f"{name}.md").read_text(encoding="utf-8")
        assert "Decision rules:" in text
        examples = text.split("Worked examples:", 1)[1]
        labels = re.findall(r"^\d+\. (Pass|Fail|Could-not-run):", examples, flags=re.M)
        assert labels == ["Pass", "Fail", "Could-not-run"]


def test_judge_agreement_reads_graded_jsonl_evidence(tmp_path: Path):
    import json

    from tools.judge_agreement import load_rows
    from tools.dual_judge_grader import agreement_metrics

    path = tmp_path / "graded_rows_after.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in [
        {"case_id": "1", "family": "over_refusal", "underwriting_family": "conv-en", "source": "s",
         "judge_a_verdict": "pass", "judge_b_verdict": "pass", "human_label": None},
        {"case_id": "2", "family": "over_refusal", "underwriting_family": "conv-en", "source": "s",
         "judge_a_verdict": "pass", "judge_b_verdict": "fail", "human_label": None},
    ]), encoding="utf-8")
    metrics = agreement_metrics(load_rows(path))
    family = next(m for m in metrics if m["dimension"] == "family")
    assert (family["rows"], family["agreement"], family["unresolved_rate"]) == (2, 0.5, 0.5)
    assert "judge_a_accuracy" not in family
