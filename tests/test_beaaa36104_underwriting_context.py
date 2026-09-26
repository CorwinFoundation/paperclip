"""Focused acceptance coverage for BEAAA-36104."""
from __future__ import annotations

import json
from dataclasses import replace

from api.live_probe_context import UNKNOWN, serialize_live_probe_evidence
from api.scan_response_envelopes import scan_result_to_dict
from scanners.live_probe_orchestrator import ScanResult


def test_live_probe_underwriting_context_round_trip_and_secret_exclusion() -> None:
    record = {
        "probe_id": "live-1",
        "evidence_sha256": "sha256:" + "a" * 64,
        "underwriting_context": {
            "tooling": {
                "tools": [{"name": "crm.update", "credential": "never-copy"}],
                "permission_classes": ["customer_record.write"],
                "action_classes": ["reversible_mutation"],
            },
            "autonomy": {
                "autonomy_level": "bounded",
                "approval_boundaries": ["human approval above USD 1,000"],
            },
            "materiality": {
                "action_volume": {"count": 240, "period": "day"},
                "records_at_risk": 5000,
                "value_at_risk": {"amount": 25000, "currency": "USD"},
                "blast_radius": "single tenant",
            },
            "human_review": {
                "review_events": [{"event_id": "review-1", "outcome": "approved"}],
                "intervention_events": [{"event_id": "stop-1", "outcome": "halted"}],
            },
            "data_handling": {
                "pii_flow": "customer email enters CRM only",
                "retention": "30 days",
                "access_control_references": ["policy://crm-writers"],
                "access_token": "never-copy",
            },
            "system_prompt": "never-copy",
        },
    }

    encoded = json.dumps(serialize_live_probe_evidence(record), sort_keys=True)
    restored = json.loads(encoded)
    context = restored["underwriting_context"]

    assert context["tooling"]["tools"] == [{"name": "crm.update"}]
    assert context["autonomy"]["autonomy_level"] == "bounded"
    assert context["materiality"]["records_at_risk"] == 5000
    assert context["human_review"]["intervention_events"][0]["outcome"] == "halted"
    assert context["data_handling"]["retention"] == "30 days"
    assert "never-copy" not in encoded
    assert "system_prompt" not in encoded
    assert "access_token" not in encoded


def test_omissions_are_unknown_and_legacy_record_is_unchanged() -> None:
    contextual = serialize_live_probe_evidence({"underwriting_context": {"tooling": {}}})
    context = contextual["underwriting_context"]

    assert context["tooling"]["tools"] == UNKNOWN
    assert context["autonomy"]["approval_boundaries"] == UNKNOWN
    assert context["materiality"]["value_at_risk"] == UNKNOWN
    assert context["human_review"]["review_events"] == UNKNOWN
    assert context["data_handling"]["pii_flow"] == UNKNOWN

    legacy = {"probe_id": "live-legacy", "response": "observed"}
    assert serialize_live_probe_evidence(legacy) == legacy
    assert "underwriting_context" not in serialize_live_probe_evidence(legacy)


def test_report_only_context_does_not_change_score_output() -> None:
    baseline = ScanResult(
        scan_id="scan-1",
        status="complete",
        scanner_version="0.5.0",
        corpus_version="canonical-61-family-registry@2026-07-13",
        rows_emitted=1,
    )
    contextual = replace(
        baseline,
        evidence_records=(
            {"underwriting_context": {"materiality": {"records_at_risk": 50}}},
        ),
    )

    assert scan_result_to_dict(contextual) == scan_result_to_dict(baseline)
