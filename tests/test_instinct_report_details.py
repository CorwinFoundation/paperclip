"""Private report details remain inert and require no user confirmation."""
from stage_b_pilot.receipt_page import render_history


def test_received_report_details_escape_untrusted_agent_content():
    page = render_history({}, "pilot@example.com", "csrf-test", standing={
        "reports": [{
            "report_id": "sr_fictional", "received_at": "2026-10-03T18:38:54Z",
            "review": "staff_required", "provenance": "agent_reported",
            "report": {
                "deduplication_key": "fictional-hotel-001", "trigger": "request_completed",
                "event": {
                    "outcome": '<script>alert("agent")</script>',
                    "actual_external_action": "No booking or contact",
                    "money_at_risk": "unknown", "gaps": "unknown",
                    "unexpected_private_field": "must-not-render",
                },
            },
        }],
    })
    assert "fictional-hotel-001" in page
    assert "2026-10-03T18:38:54Z" in page
    assert "No booking or contact" in page
    assert "&lt;script&gt;" in page and '<script>alert' not in page
    assert "must-not-render" not in page
    assert "No action is required from you" in page
    assert '<dt>Money at risk</dt><dd>unknown</dd>' in page
    assert 'action="/stage-b-demo/history/confirm"' not in page


def test_missing_report_details_preserve_unknowns():
    page = render_history({}, "pilot@example.com", "csrf-test", standing={
        "reports": [{"report_id": "sr_old", "review": "staff_required",
                     "provenance": "agent_reported"}],
    })
    assert "sr_old: received" in page
    assert '<dt>Request reference</dt><dd>unknown</dd>' in page
    assert '<dt>Outcome</dt><dd>unknown</dd>' in page
