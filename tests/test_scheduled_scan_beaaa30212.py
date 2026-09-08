from scripts.run_scheduled_full_scan import EVIDENCE_LABEL, summarize


def test_manifest_counts_and_evidence_label(monkeypatch):
    monkeypatch.setattr("scripts.run_scheduled_full_scan.time.monotonic", lambda: 12.5)
    result = summarize("vapi", {"scan_id": "s1", "status": "complete", "full_suite_manifest": {"evidence_rows": [
        {"family": "f1", "routed": True, "target_called": True, "verdict": "PASS"},
        {"family": "f1", "routed": True, "target_called": False, "verdict": "INCOMPLETE", "incomplete_reason": "missing_judge"},
    ]}}, started=10, estimated_cost_usd=1.25)
    assert result["evidence_label"] == EVIDENCE_LABEL
    assert result["live_service_signed"] is False
    assert result["families"]["f1"] == {"considered": 2, "incomplete": 1, "pass": 1, "routed": 2, "target_called": 1}
    assert result["incomplete_by_reason"]["dispatch_failure"] == 1
    assert result["incomplete_by_reason"]["missing_judge"] == 0
