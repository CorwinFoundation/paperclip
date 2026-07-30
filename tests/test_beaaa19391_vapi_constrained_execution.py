import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts/build_beaaa19391_vapi_constrained_execution.py"
SPEC = importlib.util.spec_from_file_location("vapi_constrained_execution", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_all_latest_constrained_rows_execute_and_reconcile():
    summary, records = MODULE.build(MODULE.DEFAULT_SOURCE)
    assert summary["counts"] == {
        "source_registry_rows": 2000,
        "constrained_rows": 502,
        "executed": 502,
        "impossible": 0,
        "structured_rows": 477,
        "role_bearing_rows": 25,
        "terminal_records": 502,
        "network_requests": 0,
        "duplicate_target_requests": 0,
    }
    assert len({record["stable_case_id"] for record in records}) == 502
    assert all(record["terminal_status"] == "executed" for record in records)


def test_structured_content_and_role_order_are_exact():
    _, records = MODULE.build(MODULE.DEFAULT_SOURCE)
    source = {
        row["stable_case_id"]: row
        for row in (
            __import__("json").loads(line)
            for line in MODULE.DEFAULT_SOURCE.read_text().splitlines()
            if line
        )
    }
    for record in records:
        authored = source[record["stable_case_id"]]["prompt_turn_sequence"]
        events = record["trace"]["events"]
        assert [event["sequence"] for event in events] == list(range(1, len(authored) + 1))
        assert [event["content"] for event in events] == [
            turn["content"] if set(turn) == {"content", "role"} else turn
            for turn in authored
        ]
        expected_roles = [
            turn["role"] if set(turn) == {"content", "role"} else "probe_fixture"
            for turn in authored
        ]
        assert [event["role"] for event in events] == expected_roles


def test_five_runs_are_byte_identical(tmp_path):
    hashes = []
    for run in range(5):
        ledger, report = MODULE.write(MODULE.DEFAULT_SOURCE, tmp_path / str(run))
        hashes.append((MODULE.file_sha(ledger), MODULE.file_sha(report)))
    assert len(set(hashes)) == 1
