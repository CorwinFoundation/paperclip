import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "beaaa39144_reconcile_vapi_inventory.py"


def test_reports_acquisition_and_judgement_separately(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.jsonl"
    inventory.write_text(
        '\n'.join(json.dumps({"stable_case_id": value}) for value in ("a", "b", "c", "c")),
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "rows.json").write_text(
        json.dumps({"records": [
            {"runtime_case_identity": "a", "budget": {"case_delta": {"target_calls": 1, "judge_calls": 1}}, "responses": ["actual reply"], "verdict": "PASS"},
            {"probe_id": "b", "evidence": {"messages": [{"role": "assistant", "content": "saved"}]}},
            {"stable_case_id": "c", "verdict": "unresolved"},
            {"stable_case_id": "outside", "target_response": "x", "judgement": "FAIL"},
        ]}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--inventory", str(inventory), "--evidence", str(evidence)],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert report["inventory"] == {
        "duplicate_rows": 1,
        "rows_with_case_id": 4,
        "unique_case_ids": 3,
    }
    assert report["evidence"]["acquired_case_ids"] == 2
    assert report["evidence"]["judged_case_ids"] == 1
    assert report["evidence"]["not_applicable_case_ids"] == 0
    assert report["evidence"]["completed_case_ids"] == 1
    assert report["evidence"]["target_call_case_ids"] == 1
    assert report["evidence"]["judge_call_case_ids"] == 1
    assert report["evidence"]["terminal_verdicts"] == {"fail": 0, "not_applicable": 0, "pass": 1}
    assert report["missing_acquisition"] == ["c"]
    assert report["acquired_not_judged"] == ["b"]
    assert report["evidence_not_in_inventory"] == ["outside"]
    assert report["remaining_executable"] == ["b", "c"]
    assert report["responses_without_target_counter"] == ["b"]
    assert report["target_counter_without_response"] == []


def test_accepts_single_jsonl_evidence_export(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.jsonl"
    inventory.write_text(json.dumps({"stable_case_id": "case-a"}) + "\n", encoding="utf-8")
    evidence = tmp_path / "private-conversations.jsonl"
    evidence.write_text(
        json.dumps({"messages": [
            {"role": "user", "content": "Behavioral evaluation case family/case-a, seed 0."},
            {"role": "assistant", "content": "saved"},
        ], "verdict": "PASS"})
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--inventory", str(inventory), "--evidence", str(evidence)],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert report["evidence"]["files_read"] == 1
    assert report["evidence"]["acquired_case_ids"] == 1
    assert report["evidence"]["judged_case_ids"] == 1
    assert report["evidence"]["completed_case_ids"] == 1


def test_reads_full_scan_manifest_inventory(tmp_path: Path) -> None:
    inventory = tmp_path / "manifest.json"
    inventory.write_text(
        json.dumps({"evidence_rows": [{"probe_id": "a"}, {"probe_id": "b"}]}),
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({"probe_id": "a", "response": "saved"}), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--inventory", str(inventory), "--evidence", str(evidence)],
        check=True, capture_output=True, text=True,
    )
    assert json.loads(result.stdout)["inventory"]["unique_case_ids"] == 2


def test_attempt_metadata_and_empty_responses_are_not_acquisitions(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.jsonl"
    inventory.write_text(
        "\n".join(json.dumps({"stable_case_id": value}) for value in ("empty", "na", "real")),
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "rows.json").write_text(json.dumps({"records": [
        {"stable_case_id": "empty", "budget": {"case_delta": {"target_calls": 1, "judge_calls": 1}}, "responses": ["  "], "verdict": "FAIL"},
        {"stable_case_id": "na", "responses": [], "verdict": {"label": "not_applicable"}},
        {"stable_case_id": "real", "responses": ["reply"], "verdict": {"result": "pass"}},
    ]}), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--inventory", str(inventory), "--evidence", str(evidence)],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert report["evidence"]["acquired_case_ids"] == 1
    assert report["evidence"]["judged_case_ids"] == 3
    assert report["evidence"]["completed_case_ids"] == 1
    assert report["judged_without_acquisition"] == ["empty"]
    assert report["target_counter_without_response"] == ["empty"]
    assert report["remaining_executable"] == ["empty"]
