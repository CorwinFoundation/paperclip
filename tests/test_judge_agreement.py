from pathlib import Path

from openpyxl import Workbook

from tools.judge_agreement import measure


def test_seeded_agreement_report_warns_and_measures(tmp_path: Path):
    path = tmp_path / "seeded.xlsx"
    wb = Workbook(); ws = wb.active; ws.title = "sample"
    ws.append(["case_id", "family", "judge_verdict", "judge_reason", "human_label", "human_note", "seeded_label"])
    ws.append(["a", "one", "pass", "ok", "pass", "seed", True])
    ws.append(["b", "one", "pass", "ok", "fail", "seed", True])
    ws.append(["c", "two", "fail", "bad", "pass", "seed", True])
    ws.append(["d", "two", "fail", "bad", "fail", "seed", True])
    wb.save(path)
    report = measure(path)
    assert report["warning"].startswith("SEEDED LABELS")
    assert report["overall"]["agreement_rate"] == .5
    assert report["overall"]["false_pass_rate"] == .25
    assert report["overall"]["false_fail_rate"] == .25
    assert len(report["disagreements"]) == 2
