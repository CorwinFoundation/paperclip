import json

import tools.materialize_full_scan_h_injecagent as materializer


def _row(label: str) -> dict[str, object]:
    return {
        "User Instruction": f"retrieve {label}",
        "Tool Response": f"tool output containing attack {label}",
    }


def test_reference_cases_have_stable_source_ids():
    cases = materializer.reference_cases({"direct_harm": [_row("a")], "data_stealing": [_row("b")]})
    assert [case.source_case_id for case in cases] == ["dh.0000", "ds.0000"]
    assert all(case.source_version == materializer.SOURCE_COMMIT for case in cases)


def test_materialize_enforces_complete_pinned_count_and_report_only_contract(tmp_path, monkeypatch):
    direct = [_row(f"direct-{index}") for index in range(510)]
    stealing = [_row(f"steal-{index}") for index in range(544)]
    payloads = iter((direct, stealing))
    monkeypatch.setattr(materializer, "fetch", lambda *_: next(payloads))
    output = tmp_path / "injecagent.jsonl"
    summary = materializer.materialize(output)
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert summary["source_cases"] == summary["materialized_cases"] == 1_054
    assert summary["deduplicated_cases"] == 0
    assert len(rows) == 1_054
    assert all(row["forms"] == [{"form_id": "fixed", "prompt": row["prompt"]}] for row in rows)
    assert all(not row["starter_eligible"] and not row["score_input"] for row in rows)
    assert all(not row["underwriting_active"] and row["priced_score_weight"] == 0.0 for row in rows)
