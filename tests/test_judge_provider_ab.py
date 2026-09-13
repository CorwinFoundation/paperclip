from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from scripts import judge_provider_ab


def test_preflight_rejects_non_anthropic_provider_before_spend(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-test-value")
    monkeypatch.setenv("ARE_FULL_SCAN_JUDGE_MODEL", "openrouter/anthropic/claude-haiku-4.5")

    with pytest.raises(SystemExit, match="expected anthropic"):
        judge_provider_ab.preflight()


def test_preflight_requires_anthropic_key_without_exposing_value(monkeypatch):
    monkeypatch.setenv("ARE_FULL_SCAN_JUDGE_MODEL", "anthropic/claude-haiku-4.5")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(SystemExit) as caught:
        judge_provider_ab.preflight()

    assert str(caught.value) == "ANTHROPIC_API_KEY is absent"


def test_missing_attribute_failure_names_attribute_without_exception_value():
    secret = "sentinel-must-not-print"
    error = AttributeError(f"object with {secret} has no attribute 'batches'", name="batches")

    message = judge_provider_ab.safe_failure_message(error)

    assert message == "judge provider A/B failed (missing attribute: batches)"
    assert secret not in message


def test_api_status_failure_exposes_only_status_and_structured_type(monkeypatch):
    secret = "sentinel-must-not-print"

    class APIStatusError(Exception):
        def __init__(self):
            super().__init__(f"provider message containing {secret}")
            self.status_code = 400
            self.body = {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": f"request and secret context: {secret}",
                },
            }
            self.request = SimpleNamespace(headers={"x-api-key": secret})

    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        SimpleNamespace(APIStatusError=APIStatusError),
    )

    message = judge_provider_ab.safe_failure_message(APIStatusError())

    assert message == (
        "judge provider A/B failed "
        "(HTTP 400, error.type=invalid_request_error)"
    )
    assert secret not in message


def test_batch_custom_ids_are_api_safe_and_map_back_to_case_ids():
    rows = [
        {"case_id": "case:with/punctuation", "prompt": "rubric\nResponses JSON:[]"},
        {"case_id": "x" * 100, "prompt": "rubric\nResponses JSON:[]"},
    ]

    requests, case_by_custom_id = judge_provider_ab.build_batch_requests(rows)

    assert [request["custom_id"] for request in requests] == [
        "row-000-vote-0", "row-000-vote-1", "row-000-vote-2",
        "row-001-vote-0", "row-001-vote-1", "row-001-vote-2",
    ]
    assert all(len(custom_id) <= 64 for custom_id in case_by_custom_id)
    assert set(case_by_custom_id.values()) == {
        "case:with/punctuation", "x" * 100,
    }
    assert requests[0]["params"]["messages"][0]["content"][0][
        "cache_control"
    ] == {"type": "ephemeral"}
