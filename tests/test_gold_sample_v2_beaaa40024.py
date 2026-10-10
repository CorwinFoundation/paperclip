"""BEAAA-40024 item 4: gold_sample_v2.xlsx reorder + 30 exact-check rows, with no labels written."""
from __future__ import annotations

import openpyxl
import pytest

from scripts import beaaa40024_gold_sample_v2 as g


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("gold") / "v2.xlsx"
    info = g.build(out=out)
    return info, openpyxl.load_workbook(out)


def _rows(wb, sheet):
    rows = list(wb[sheet].iter_rows(values_only=True))
    return [dict(zip(rows[0], r)) for r in rows[1:]]


def test_priority_families_first_then_exact_rows(built):
    info, wb = built
    rows = _rows(wb, "sample")
    n_pri = info["priority_rows"]
    assert n_pri > 0 and info["exact_rows"] == 30
    assert all(r["family"] in g.PRIORITY_FAMILIES for r in rows[:n_pri])
    assert all(r["family"] == g.EXACT_FAMILY for r in rows[n_pri:n_pri + 30])
    assert not any(r["family"] in g.PRIORITY_FAMILIES for r in rows[n_pri + 30:])
    assert [r["labelling_order"] for r in rows] == list(range(1, len(rows) + 1))


def test_every_v1_row_kept_unchanged_and_in_relative_order(built):
    _, wb = built
    v1 = _rows(openpyxl.load_workbook(g.V1), "sample")
    v2 = [r for r in _rows(wb, "sample") if r["family"] != g.EXACT_FAMILY]
    assert len(v2) == len(v1) == 300
    # case_id repeats across seeds for some action-sandbox rows, so compare whole rows.
    key = lambda r: tuple((k, r[k]) for k in v1[0])  # noqa: E731
    assert sorted(map(key, v2), key=repr) == sorted(map(key, v1), key=repr)
    rest = [key(r) for r in v1 if r["family"] not in g.PRIORITY_FAMILIES]
    assert [key(r) for r in v2 if r["family"] not in g.PRIORITY_FAMILIES] == rest
    pri = [key(r) for r in v1 if r["family"] in g.PRIORITY_FAMILIES]
    assert [key(r) for r in v2 if r["family"] in g.PRIORITY_FAMILIES] == pri


def test_no_labels_written_and_exact_verdicts_off_the_labelling_sheet(built):
    _, wb = built
    for r in _rows(wb, "sample"):
        assert r["human_label"] in (None, "") and r["human_note"] in (None, "") and r["seeded_label"] in (None, "")
        if r["family"] == g.EXACT_FAMILY:
            assert r["judge_verdict"] is None and r["deterministic_detail"] is None and r["full_reply"]
    exact = _rows(wb, "exact_check")
    assert len(exact) == 30 and len({e["case_id"] for e in exact}) == 30
    assert {e["exact_verdict"] for e in exact} == {"pass", "fail"}
    assert {e["decided_by"] for e in exact} <= {"regex", "model"}
    assert len({e["topic"] for e in exact}) == 7


def test_label_guide_gains_grounded_claims_rule(built):
    _, wb = built
    assert any(r["family"] == g.EXACT_FAMILY for r in _rows(wb, "label_guide"))


def test_deterministic(tmp_path):
    a, b = tmp_path / "a.xlsx", tmp_path / "b.xlsx"
    g.build(out=a), g.build(out=b)
    wa, wb = openpyxl.load_workbook(a), openpyxl.load_workbook(b)
    for name in wa.sheetnames:
        assert list(wa[name].iter_rows(values_only=True)) == list(wb[name].iter_rows(values_only=True))


def test_committed_v2_matches_builder(tmp_path):
    out = tmp_path / "v2.xlsx"
    g.build(out=out)
    fresh, committed = openpyxl.load_workbook(out), openpyxl.load_workbook(g.V2)
    assert fresh.sheetnames == committed.sheetnames
    for name in fresh.sheetnames:
        assert list(fresh[name].iter_rows(values_only=True)) == list(committed[name].iter_rows(values_only=True))
