from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from log_tracker.timeline_read import (  # noqa: E402
    AUTH_POSTURE,
    CONTRACT_VERSION,
    SHA256_HEX_RE,
    TRACKER_VERSION,
    SQLiteTimelineStore,
    TimelineReadAuth,
    TimelineReadError,
    handle_timeline_read,
)


def _score() -> dict:
    return {
        "are_total": 78,
        "subscores": {
            "GOVERNANCE": 20,
            "ROBUSTNESS": 19,
            "BIAS_FAIRNESS": 20,
            "OPS": 19,
        },
        "band": "B",
        "confidence": 0.88,
        "scanner_version": "0.4.1",
        "corpus_version": "0.2.0",
        "model_versions": {"primary": "fixture-model", "judges": []},
        "seed": 6162,
        "uncovered_dimensions": [],
    }


def _fixture_store() -> SQLiteTimelineStore:
    store = SQLiteTimelineStore()
    store.append_scanner_run(
        scan_id="scan-bind",
        agent_id="agent-1",
        customer_id="cust-1",
        purpose="pre_bind",
        run_timestamp="2026-06-17T09:00:00Z",
        scanner_version="0.4.1",
        corpus_version="0.2.0",
        score_json=_score(),
        evidence_refs=[
            {
                "rubric_point_id": "ROB.PI.MULTI_TURN",
                "test_id": "probe-1",
                "artifact_uri": "portal-evidence://scan-bind/probe-1",
                "verdict": "pass",
                "weight": 1,
                "raw_note": "operator@example.com",
                "secret_material": "sk-abcdef1234567890",
            }
        ],
        evidence_redaction_class={"raw_note": "drop", "secret_material": "hash"},
        inserted_at="2026-06-17T09:00:01Z",
    )
    store.append_policy_event(
        policy_event_id="policy-event-1",
        policy_id="POL-1",
        agent_id="agent-1",
        customer_id="cust-1",
        event_kind="bind",
        scan_id_priced="scan-bind",
        event_t="2026-06-17T10:00:00Z",
        premium_band="B",
        wording_version="wording-v2",
        metadata={
            "broker_note": "clear note",
            "internal_underwriter_email": "uw@example.com",
            "raw_payload": "must not surface",
        },
        redaction_class={
            "broker_note": "clear",
            "internal_underwriter_email": "hash",
            "raw_payload": "drop",
        },
        inserted_at="2026-06-17T10:00:01Z",
    )
    store.append_claim_event(
        claim_event_id="claim-event-1",
        claim_id="CLM-1",
        policy_id="POL-1",
        agent_id="agent-1",
        customer_id="cust-1",
        peril="P5",
        severity_class="S2",
        claim_kind="submitted",
        event_t="2026-06-17T11:00:00Z",
        scan_id_at_bind="scan-bind",
        scan_id_at_loss="scan-bind",
        evidence_refs_uri="portal-evidence://claim/CLM-1",
        metadata={
            "description": "claimant jane@example.com reported prompt injection",
            "claimant_email": "jane@example.com",
            "public_summary": "submitted",
        },
        redaction_class={
            "description": "redact-on-receipt",
            "claimant_email": "drop",
            "public_summary": "clear",
        },
        inserted_at="2026-06-17T11:00:01Z",
    )
    return store


def test_timeline_read_orders_union_and_hydrates_evidence_refs() -> None:
    store = _fixture_store()
    payload = store.get_policy_timeline(
        "POL-1",
        auth=TimelineReadAuth(role="underwriter", subject="uw-service"),
        generated_at="2026-06-17T12:00:00Z",
    )

    assert payload["contract_version"] == CONTRACT_VERSION
    assert payload["tracker_version"] == TRACKER_VERSION
    assert payload["generated_at"] == "2026-06-17T12:00:00Z"
    assert [event["kind"] for event in payload["timeline"]] == [
        "scanner_run",
        "policy_event",
        "claim_event",
    ]
    assert payload["timeline"][0]["evidence_refs"][0]["rubric_point_id"] == "ROB.PI.MULTI_TURN"


def test_read_redaction_drops_absent_fields_and_hashes_only_sha256_hex() -> None:
    store = _fixture_store()
    payload = store.get_policy_timeline(
        "POL-1",
        auth=TimelineReadAuth(role="auditor", subject="audit-service"),
        include_audit=True,
    )
    scanner_event, policy_event, claim_event = payload["timeline"]

    evidence = scanner_event["evidence_refs"][0]
    assert "raw_note" not in evidence
    assert SHA256_HEX_RE.match(evidence["secret_material"])
    assert scanner_event["audit"]["inserted_at"] == "2026-06-17T09:00:01Z"
    assert "supersedes_id" not in scanner_event["audit"]

    metadata = policy_event["metadata"]
    assert metadata["broker_note"] == "clear note"
    assert "raw_payload" not in metadata
    assert SHA256_HEX_RE.match(metadata["internal_underwriter_email"])

    claim_metadata = claim_event["metadata"]
    assert claim_metadata["description"] == "claimant [REDACTED:read_floor] reported prompt injection"
    assert "claimant_email" not in claim_metadata
    assert claim_metadata["public_summary"] == "submitted"


def test_timeline_read_auth_requires_mtls_service_jwt_and_role() -> None:
    store = _fixture_store()
    with pytest.raises(TimelineReadError, match="mtls_required"):
        handle_timeline_read(
            store=store,
            policy_id="POL-1",
            headers={
                "authorization": "Bearer token",
                "x-backbond-role": "underwriter",
                "x-backbond-sub": "svc",
            },
            service_token="token",
        )

    payload = handle_timeline_read(
        store=store,
        policy_id="POL-1",
        headers={
            "authorization": "Bearer token",
            "x-client-cert-verified": "true",
            "x-backbond-role": "auditor",
            "x-backbond-sub": "audit-service",
        },
        service_token="token",
        query={"include_audit": "true"},
    )
    assert payload["timeline"][0]["audit"]["inserted_at"] == "2026-06-17T09:00:01Z"


def test_no_raw_bypass_and_role_scoping() -> None:
    store = _fixture_store()
    with pytest.raises(TimelineReadError, match="raw_bypass_forbidden"):
        store.get_policy_timeline(
            "POL-1",
            auth=TimelineReadAuth(role="underwriter", subject="uw-service"),
            query={"show_raw": "true"},
        )
    with pytest.raises(TimelineReadError, match="audit_scope_forbidden"):
        store.get_policy_timeline(
            "POL-1",
            auth=TimelineReadAuth(role="underwriter", subject="uw-service"),
            include_audit=True,
        )

    no_claim_store = SQLiteTimelineStore()
    no_claim_store.append_policy_event(
        policy_event_id="policy-event-2",
        policy_id="POL-2",
        agent_id="agent-2",
        customer_id="cust-2",
        event_kind="bind",
        scan_id_priced="scan-2",
        event_t="2026-06-17T10:00:00Z",
        wording_version="wording-v2",
        metadata={},
        redaction_class={"policy_id": "clear"},
    )
    with pytest.raises(TimelineReadError, match="claims_architect_policy_scope_forbidden"):
        no_claim_store.get_policy_timeline(
            "POL-2",
            auth=TimelineReadAuth(role="claims_architect", subject="claims-service"),
        )


def test_auth_posture_constant_matches_contract_language() -> None:
    assert AUTH_POSTURE == "mtls_client_cert_plus_service_account_jwt"
