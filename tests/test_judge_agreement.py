from pathlib import Path

from openpyxl import Workbook

from tools.judge_agreement import measure
from tools.dual_judge_grader import agreement_metrics, render_metrics


def test_seeded_report_separates_repeatability_contested_and_adjudication(tmp_path: Path):
    path = tmp_path / "seeded.xlsx"
    wb = Workbook(); ws = wb.active; ws.title = "sample"
    ws.append(["case_id", "family", "judge_verdict", "judge_reason", "reviewer__ari__pass1",
               "reviewer__ari__pass2", "reviewer__sam__pass1", "adjudication_label",
               "adjudication_reason", "seeded_label"])
    ws.append(["a", "one", "pass", "ok", "pass", "pass", "pass", "", "", True])
    ws.append(["b", "one", "pass", "ok", "pass", "fail", "fail", "", "", True])
    ws.append(["c", "two", "fail", "bad", "pass", "pass", "fail", "pass", "evidence supports pass", True])
    ws.append(["d", "two", "fail", "bad", "fail", "fail", "fail", "", "", True])
    wb.save(path)

    report = measure(path)

    assert report["warning"].startswith("SEEDED LABELS")
    assert report["within_reviewer_repeatability"]["ari:pass1_vs_pass2"]["agreement_rate"] == .75
    assert report["inter_reviewer_agreement"]["ari_vs_sam"]["n"] == 3
    assert report["reference"]["contested_count"] == 1
    assert report["reference"]["contested_rows"][0]["case_id"] == "b"
    assert report["overall"]["n"] == 3
    assert report["overall"]["false_fail_rate"] == 1 / 3


def test_legacy_human_label_remains_supported(tmp_path: Path):
    path = tmp_path / "legacy.xlsx"
    wb = Workbook(); ws = wb.active; ws.title = "sample"
    ws.append(["case_id", "family", "judge_verdict", "human_label"])
    ws.append(["a", "one", "pass", "pass"])
    wb.save(path)
    assert measure(path)["overall"]["agreement_rate"] == 1.0


def test_exact_truth_is_first_class_and_takes_precedence_over_human_label():
    rows = [{
        "case_id": "exact-1", "family": "M", "underwriting_family": "M",
        "source": "sandbox", "judge_a_verdict": "fail", "judge_b_verdict": "fail",
        "exact_verdict": "fail", "human_label": "pass",
    }]
    metric = next(m for m in agreement_metrics(rows) if m["dimension"] == "family")
    assert metric["truth_source"] == "exact"
    assert metric["judge_a_accuracy"] == 1.0
    assert metric["judge_b_accuracy"] == 1.0
    assert "truth_source" in render_metrics([metric]).splitlines()[0]


def test_human_truth_remains_available_in_json_reports():
    rows = [{
        "case_id": "human-1", "family": "M", "underwriting_family": "M",
        "source": "workbook", "judge_a_verdict": "pass", "judge_b_verdict": "fail",
        "human_label": "pass",
    }]
    metric = next(m for m in agreement_metrics(rows) if m["dimension"] == "family")
    assert metric["truth_source"] == "human"
    assert metric["judge_a_accuracy"] == 1.0
    assert metric["judge_b_accuracy"] == 0.0
