import json
import threading

from scripts import beaaa38961_full_scan_e_live_run as runner


def test_incremental_rows_are_flushed_as_jsonl(tmp_path):
    path = tmp_path / "partial.jsonl"
    runner._append_jsonl(path, {"stable_id": "case-1", "verdict": "pass"}, threading.Lock())
    assert json.loads(path.read_text()) == {"stable_id": "case-1", "verdict": "pass"}


def test_non_scored_outcomes_are_distinct():
    assert runner._exception_outcome(NotImplementedError("unsupported surface")) == "unsupported"
    assert runner._exception_outcome(TimeoutError("provider timed out")) == "inconclusive"
    assert runner._exception_outcome(ValueError("malformed tool call")) == "inconclusive"
    assert runner._exception_outcome(RuntimeError("provider 503")) == "error"
