from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from log_tracker.write_pipeline import (  # noqa: E402
    CONTRACT_VERSION,
    WritePipelineError,
    append_envelope,
    apply_migrations,
    w1_validate_envelope,
    w2_redact_by_class,
    w3_apply_secret_floor,
)


def _scanner_envelope(**overrides):
    envelope = {
        "contract_version": CONTRACT_VERSION,
        "hook": "scanner_run.completed",
        "event_id": "evt-scan-1",
        "event_t": "2026-06-17T10:00:00Z",
        "source_system": "backbond-scanner",
        "source_version": "v0.4.1",
        "schema_version": "are-scanner-api-v1@v1.0",
        "redaction_class_map": {
            "scan_id": "clear",
            "agent_id": "hash",
            "customer_id": "hash",
            "purpose": "clear",
            "scanner_version": "clear",
            "corpus_version": "clear",
            "model_versions.primary": "clear",
            "evidence_refs[].rubric_point_id": "clear",
            "evidence_refs[].test_id": "clear",
            "evidence_refs[].artifact_uri": "clear",
            "evidence_refs[].verdict": "clear",
            "evidence_refs[].redaction_class.artifact_uri": "clear",
        },
        "payload": {
            "scan_id": "scan-1",
            "agent_id": "agent-raw",
            "customer_id": "cust-raw",
            "purpose": "pre_bind",
            "are_total": 78,
            "subscores": {
                "GOVERNANCE": 20,
                "ROBUSTNESS": 19,
                "BIAS_FAIRNESS": 20,
                "OPS": 19,
            },
            "band": "B",
            "confidence": 0.91,
            "scanner_version": "v0.4.1",
            "corpus_version": "v0.2.0",
            "model_versions": {"primary": "fixture-model", "judges": []},
            "seed": 42,
            "run_timestamp": "2026-06-17T10:00:00Z",
            "uncovered_dimensions": [],
            "evidence_refs": [
                {
                    "rubric_point_id": "ROB.PI.MULTI_TURN",
                    "test_id": "probe-1",
                    "artifact_uri": "portal-evidence://scan-1/probe-1",
                    "verdict": "pass",
                    "weight": 1.0,
                    "redaction_class": {"artifact_uri": "clear"},
                }
            ],
        },
    }
    envelope.update(overrides)
    return envelope


def _policy_envelope(**overrides):
    envelope = {
        "contract_version": CONTRACT_VERSION,
        "hook": "policy_event.appended",
        "event_id": "evt-policy-1",
        "event_t": "2026-06-17T11:00:00Z",
        "source_system": "policy-service",
        "source_version": "v0.1.0",
        "schema_version": "log-event-schema-v1@v1.0.1",
        "redaction_class_map": {
            "policy_id": "hash",
            "agent_id": "hash",
            "customer_id": "hash",
            "metadata.internal_note": "redact-on-receipt",
            "metadata.raw_secret": "clear",
        },
        "payload": {
            "policy_event_id": "policy-event-1",
            "policy_id": "POL-RAW",
            "agent_id": "agent-raw",
            "customer_id": "cust-raw",
            "event_kind": "bind",
            "scan_id_priced": "scan-1",
            "event_t": "2026-06-17T11:00:00Z",
            "premium_band": "B",
            "wording_version": "wording-v2",
            "metadata": {
                "internal_note": "store redacted",
                "raw_secret": "sk-abcdef1234567890",
            },
        },
    }
    envelope.update(overrides)
    return envelope


def _claim_envelope(**overrides):
    envelope = {
        "contract_version": CONTRACT_VERSION,
        "hook": "claim_event.appended",
        "event_id": "evt-claim-1",
        "event_t": "2026-06-17T12:00:00Z",
        "source_system": "post-bind-ingest",
        "source_version": "v0.1.0",
        "schema_version": "log-event-schema-v1@v1.0.1",
        "redaction_class_map": {
            "claim_id": "hash",
            "policy_id": "hash",
            "agent_id": "hash",
            "customer_id": "hash",
            "evidence_refs_uri": "redact-on-receipt",
            "metadata.claimant_email": "drop",
        },
        "payload": {
            "claim_event_id": "claim-event-1",
            "claim_id": "CLM-RAW",
            "policy_id": "POL-RAW",
            "agent_id": "agent-raw",
            "customer_id": "cust-raw",
            "peril": "P5",
            "severity_class": "S2",
            "claim_kind": "candidate",
            "event_t": "2026-06-17T12:00:00Z",
            "scan_id_at_bind": "scan-1",
            "scan_id_at_loss": "scan-1",
            "evidence_refs_uri": "portal-evidence://claim/CLM-RAW",
            "metadata": {"claimant_email": "claimant@example.com"},
        },
    }
    envelope.update(overrides)
    return envelope


def test_migration_creates_four_append_only_ledger_tables() -> None:
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn)

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {"scanner_runs", "policy_events", "claim_events", "evidence_refs"} <= tables

    append_envelope(conn, _scanner_envelope())
    with pytest.raises(sqlite3.DatabaseError, match="append_only_update_forbidden"):
        conn.execute("UPDATE scanner_runs SET band = 'A' WHERE event_id = 'evt-scan-1'")
    with pytest.raises(sqlite3.DatabaseError, match="append_only_delete_forbidden"):
        conn.execute("DELETE FROM scanner_runs WHERE event_id = 'evt-scan-1'")


def test_w1_rejects_malformed_scanner_policy_and_claim_envelopes() -> None:
    bad_scanner = _scanner_envelope(
        payload={**_scanner_envelope()["payload"], "are_total": 101}
    )
    with pytest.raises(WritePipelineError):
        w1_validate_envelope(bad_scanner)

    bad_policy = _policy_envelope(
        payload={**_policy_envelope()["payload"], "scan_id_priced": None}
    )
    with pytest.raises(WritePipelineError):
        w1_validate_envelope(bad_policy)

    bad_claim = _claim_envelope(
        payload={**_claim_envelope()["payload"], "peril": "P7"}
    )
    with pytest.raises(WritePipelineError):
        w1_validate_envelope(bad_claim)


def test_w2_w3_enforce_redaction_and_secret_family_floor() -> None:
    record = w2_redact_by_class(w1_validate_envelope(_policy_envelope()))
    floored = w3_apply_secret_floor(record)

    metadata = floored.sanitized_payload["metadata"]
    assert metadata["internal_note"] == "[REDACTED:log_tracker]"
    assert metadata["raw_secret"] == "[REDACTED:secret_family]"
    assert any(item.endswith(":secret-floor") for item in floored.redaction_applied)


def test_w4_appends_all_event_families_and_uses_supersedes_for_corrections() -> None:
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn)

    append_envelope(conn, _scanner_envelope())
    append_envelope(conn, _policy_envelope())
    append_envelope(conn, _claim_envelope())
    append_envelope(
        conn,
        _policy_envelope(
            event_id="evt-policy-2",
            supersedes_id="evt-policy-1",
            payload={
                **_policy_envelope()["payload"],
                "policy_event_id": "policy-event-2",
                "metadata": {"internal_note": "corrected", "raw_secret": "none"},
            },
        ),
    )

    assert conn.execute("SELECT COUNT(*) FROM scanner_runs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM evidence_refs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM claim_events").fetchone()[0] == 1

    policy_rows = conn.execute(
        "SELECT event_id, supersedes_id, payload_json FROM policy_events "
        "ORDER BY event_id"
    ).fetchall()
    assert len(policy_rows) == 2
    assert policy_rows[1][1] == "evt-policy-1"
    assert "POL-RAW" not in json.dumps(policy_rows)
    assert "cust-raw" not in json.dumps(policy_rows)
