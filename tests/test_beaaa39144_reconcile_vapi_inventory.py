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
            {"runtime_case_identity": "a", "target_calls": 1, "verdict": "PASS"},
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
    assert report["missing_acquisition"] == ["c"]
    assert report["acquired_not_judged"] == ["b"]
    assert report["evidence_not_in_inventory"] == ["outside"]
