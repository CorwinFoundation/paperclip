from harness.agent_interface import ToolCall
from harness.mock_tools.harness import MockToolHarness
from harness.mock_tools.sandbox import PilotMapping


def test_email_http_and_file_write_are_scanner_owned_recorded_surfaces():
    mapping = PilotMapping.from_yaml("harness/mock_tools/pilots/full_scan_e_outbound_v1.yaml")
    sandbox = MockToolHarness(mapping).sandbox_for("outbound-1")
    calls = [
        ToolCall("email-1", "send_email", {"to": "qa@sandbox.backbond.test", "body": "no secret"}),
        ToolCall("http-1", "http_request", {"url": "https://api.sandbox.backbond.test", "body": "no secret"}),
        ToolCall("file-1", "write_file", {"path": "report.txt", "content": "no secret"}),
    ]
    results = [sandbox.handle(call) for call in calls]
    assert all(result.raw["side_effect_target"] == "sandbox" for result in results)
    assert sandbox.surface_state("email")["outbox"][0]["delivered"] is False
    assert sandbox.surface_state("http")["requests"][0]["delivered"] is False
    assert sandbox.surface_state("file_write")["files"]["report.txt"]["committed"] is True


def test_outbound_scope_denial_is_recorded_without_a_state_change():
    mapping = PilotMapping({
        "partner": "full-scan-e-outbound",
        "authorized_scopes": [],
        "tool_bindings": [{"partner_tool": "http_request", "surface": "http", "operation": "http_request"}],
    })
    sandbox = MockToolHarness(mapping).sandbox_for("outbound-2")
    result = sandbox.handle(ToolCall("http-2", "http_request", {"url": "https://example.test", "body": "token"}))
    assert result.is_error is True
    assert result.raw["decision"] == "blocked_policy"
    assert sandbox.surface_state("http")["requests"] == []
