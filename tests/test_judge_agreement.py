from pathlib import Path

from openpyxl import Workbook

from tools.judge_agreement import measure


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
