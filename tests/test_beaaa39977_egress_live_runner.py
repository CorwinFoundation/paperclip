from scripts.beaaa39977_full_scan_k_egress_live_run import _records, summarize


def test_tool_calls_become_terminal_sandbox_records():
    message = {"tool_calls": [{"function": {"name": "http_request", "arguments": '{"url":"https://example.test"}'}}]}
    assert _records(message) == [{"tool": "http_request", "arguments": {"url": "https://example.test"}}]


def test_incomplete_k_is_could_not_run_never_pass():
    rows = [{"stable_id": "a", "category": "x"}, {"stable_id": "b", "category": "x"}]
    results = [{"stable_id": "a", "verdict": "pass"}, {"stable_id": "a", "verdict": "pass"},
               {"stable_id": "b", "verdict": "pass"}]
    assert summarize(rows, results, [], 2) == [{
        "family": "x", "unique_tests": 2, "graded": 1, "pass_runs": 2,
        "pass^2": 1, "flaky": 0, "could_not_run": 1,
    }]
