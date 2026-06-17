from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from log_tracker.claim_events import (  # noqa: E402
    ALLOWED_CLAIM_KINDS,
    ALLOWED_CLAIM_PERILS,
    ALLOWED_SEVERITY_CLASSES,
    ClaimEventError,
    SQLiteClaimEventSink,
    emit_claim_candidate_event,
)


def _base(**overrides):
    body = {
        "claim_id": "claim-001",
        "policy_id": "pol-001",
        "agent_id": "agent-001",
        "customer_id": "cust-001",
        "peril": "P5",
        "severity_class": "S2",
        "claim_kind": "candidate",
        "event_t": "2026-06-17T10:05:00Z",
        "evidence_refs_uri": "s3://backbond-test/redacted-claim-bundle",
        "metadata": {
            "detector_id": "P5.prompt_injection.indirect_injection_marker",
            "detector_version": "0.1.0",
            "redaction_applied": ["metadata.prompt_excerpt"],
        },
        "redaction_class": {
            "claim_id": "hash",
            "policy_id": "hash",
            "agent_id": "hash",
            "customer_id": "hash",
            "peril": "clear",
            "severity_class": "clear",
            "metadata": "redact-on-receipt",
        },
    }
    body.update(overrides)
    return body


def _seed_brackets(sink: SQLiteClaimEventSink) -> None:
    sink.seed_scanner_run(
        scan_id="scan-bind",
        agent_id="agent-001",
        run_timestamp="2026-06-17T09:00:00Z",
    )
    sink.seed_scanner_run(
        scan_id="scan-loss",
        agent_id="agent-001",
        run_timestamp="2026-06-17T10:00:00Z",
    )
    sink.seed_scanner_run(
        scan_id="scan-after-loss",
        agent_id="agent-001",
        run_timestamp="2026-06-17T10:06:00Z",
    )
    sink.seed_policy_event(
        policy_id="pol-001",
        agent_id="agent-001",
        customer_id="cust-001",
        event_kind="bind",
        scan_id_priced="scan-bind",
        event_t="2026-06-17T09:30:00Z",
    )


def test_claim_event_enums_match_log_tracker_contract() -> None:
    assert ALLOWED_CLAIM_PERILS == {"P1", "P2", "P3", "P4", "P5", "P6"}
    assert ALLOWED_SEVERITY_CLASSES == {"S0", "S1", "S2", "S3", "S4"}
    assert ALLOWED_CLAIM_KINDS == {"candidate", "submitted", "paid", "denied"}


def test_claim_candidate_event_writes_with_bind_and_loss_brackets() -> None:
    sink = SQLiteClaimEventSink()
    _seed_brackets(sink)

    stored = emit_claim_candidate_event(sink=sink, **_base())

    assert stored["claim_kind"] == "candidate"
    assert stored["peril"] == "P5"
    assert stored["severity_class"] == "S2"
    assert stored["scan_id_at_bind"] == "scan-bind"
    assert stored["scan_id_at_loss"] == "scan-loss"
    assert stored["metadata"]["detector_id"] == "P5.prompt_injection.indirect_injection_marker"
    assert stored["redaction_class"]["metadata"] == "redact-on-receipt"
    assert sink.count_missing_brackets() == 0


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("peril", "P7", "peril_invalid"),
        ("severity_class", "S5", "severity_class_invalid"),
        ("claim_kind", "opened", "claim_kind_invalid"),
    ],
)
def test_claim_event_rejects_invalid_enums(field: str, value: str, match: str) -> None:
    sink = SQLiteClaimEventSink()
    _seed_brackets(sink)

    with pytest.raises(ClaimEventError, match=match):
        emit_claim_candidate_event(sink=sink, **_base(**{field: value}))


def test_claim_event_missing_bracket_fails_unless_candidate_pending() -> None:
    sink = SQLiteClaimEventSink()
    sink.seed_scanner_run(
        scan_id="scan-loss",
        agent_id="agent-001",
        run_timestamp="2026-06-17T10:00:00Z",
    )

    with pytest.raises(ClaimEventError, match="scan_bracket_required"):
        emit_claim_candidate_event(sink=sink, **_base())

    stored = emit_claim_candidate_event(
        sink=sink,
        **_base(claim_id="claim-pending", metadata={"bracket_status": "pending"}),
    )
    assert stored["scan_id_at_bind"] is None
    assert stored["scan_id_at_loss"] == "scan-loss"
