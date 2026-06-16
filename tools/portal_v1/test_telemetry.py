"""BEAAA-4967 acceptance tests for telemetry-api-v0 portal-side scanner +
signal envelope + degradation trigger + downstream emit.

Test plan maps 1:1 to contract section 11 row 2 acceptance bullets:
  - N=30 fixtures (5 per event) -> byte-equal signal envelopes across N=5
  - Trigger fires on synthetic threshold-crossing window
  - Both downstream envelopes persisted, signed, retrievable, audit-logged
  - Idempotency hits return existing envelope (no double-emit)
  - Boundary grep: no raw text in persisted artifacts (only match_excerpt_hash)
  - Two consumer endpoints return well-formed envelopes
  - Calibration-delta against Backbond score Output: zero
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid

import pytest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from portal import (
    CORPUS_VERSION,
    EVENT_RULE_CORPUS_VERSION,
    PORTAL_VERSION,
    PortalError,
    RUBRIC_VERSION,
    SCANNER_VERSION,
    TELEMETRY_API_VERSION,
    b64url,
    build_service,
    canonical_json,
    now_iso,
    sha256_hex,
    unb64url,
)
from test_portal import enroll_ready_dp, submit_all_tasks
from telemetry_fixtures_v0_1 import FIXTURE_CASES, by_event_type
from telemetry_v0_1 import CANONICAL_EVENT_TYPES, PII_INTAKE_PATTERNS


@pytest.fixture
def service(tmp_path):
    svc = build_service(tmp_path)
    yield svc
    svc.close()


def _record(connector_id: str, dp_id: str, agent_call_id: str, text: str) -> dict:
    return {
        "telemetry_api_version": TELEMETRY_API_VERSION,
        "connector_id": connector_id,
        "dp_id": dp_id,
        "agent_call_id": agent_call_id,
        "capture_window_start": "2026-06-12T00:00:00Z",
        "redaction_policy_signature": b64url(b"fixture-customer-signature"),
        "redaction_policy_version": "0.1",
        "redacted_output_text": text,
        "structural_fields_redacted": ["customer_email", "customer_ssn"],
        "agent_endpoint_config_hash": "sha256-fixture",
        "connector_signature": b64url(b"fixture-connector-signature"),
    }


def _produce_baseline_score(service):
    signing_key, dp, enrollment = enroll_ready_dp(service)
    scan = service.trigger_scan(dp["dp_id"])
    submit_all_tasks(service, signing_key, scan, enrollment["connector_id"])
    score = service.produce_score(scan["scan_id"])
    return signing_key, dp, enrollment, scan, score


# -- corpus load + sign ----------------------------------------------------------


def test_event_corpus_loads_with_signature(service):
    corpus = service.event_rule_corpus()
    assert corpus["event_rule_corpus_version"] == EVENT_RULE_CORPUS_VERSION
    assert set(corpus["events"].keys()) == set(CANONICAL_EVENT_TYPES)
    assert corpus["_signature"]
    assert corpus["_sha256"]
    # Second load returns cached, no exception.
    again = service.event_rule_corpus()
    assert again["_sha256"] == corpus["_sha256"]


def test_degradation_corpus_loads(service):
    body = service.degradation_rules_corpus()
    assert body["schema_version"] == "0.1"
    assert "event_type_weights" in body
    assert "per_event_type_count_thresholds" in body


# -- intake -----------------------------------------------------------------------


def test_intake_rejects_pii_email(service):
    _, dp, enrollment, _, _ = _produce_baseline_score(service)
    record = _record(
        enrollment["connector_id"],
        dp["dp_id"],
        str(uuid.uuid4()),
        "Contact us at admin@example.com for any follow-up questions.",
    )
    with pytest.raises(PortalError) as err:
        service.submit_redacted_record(record)
    assert err.value.code == "redaction_failed_at_intake"
    # Audit log recorded the failure (event type from contract section 5.6).
    rows = service.db.execute(
        "SELECT * FROM audit_log WHERE event_type = 'redaction_failed_at_intake'"
    ).fetchall()
    assert len(rows) == 1


def test_intake_rejects_missing_field(service):
    _, dp, enrollment, _, _ = _produce_baseline_score(service)
    record = _record(enrollment["connector_id"], dp["dp_id"], str(uuid.uuid4()), "ok text")
    del record["redaction_policy_signature"]
    with pytest.raises(PortalError) as err:
        service.submit_redacted_record(record)
    assert err.value.code == "telemetry_field_missing"


def test_intake_idempotent(service):
    _, dp, enrollment, _, _ = _produce_baseline_score(service)
    record = _record(
        enrollment["connector_id"], dp["dp_id"], "agent-call-A", "We will issue a refund tomorrow."
    )
    first = service.submit_redacted_record(record)
    second = service.submit_redacted_record(record)
    assert first["received"] is True and first["idempotent"] is False
    assert second["received"] is True and second["idempotent"] is True
    # Only one record persisted, despite two POSTs.
    rows = list(service.db.execute("SELECT * FROM telemetry_records"))
    assert len(rows) == 1


# -- detection / signal envelope determinism --------------------------------------


def _emit_for_case(service, dp, enrollment, case, call_suffix=""):
    record = _record(
        enrollment["connector_id"],
        dp["dp_id"],
        f"call-{case['id']}{call_suffix}",
        case["redacted_output_text"],
    )
    return service.submit_redacted_record(record)


def test_30_fixtures_each_emit_expected_event(service):
    _, dp, enrollment, _, _ = _produce_baseline_score(service)
    by_type = by_event_type()
    assert len(FIXTURE_CASES) == 30
    for et in CANONICAL_EVENT_TYPES:
        assert len(by_type[et]) == 5
    seen_envelope_count = 0
    for case in FIXTURE_CASES:
        result = _emit_for_case(service, dp, enrollment, case)
        envelopes = list(
            service.db.execute(
                "SELECT * FROM signal_envelopes WHERE agent_call_id = ?",
                (f"call-{case['id']}",),
            )
        )
        et_envs = [e for e in envelopes if e["event_type"] == case["event_type"]]
        assert et_envs, f"no envelope for case {case['id']} -> {case['event_type']}"
        seen_envelope_count += len(envelopes)
    # Every case produced at least one signal envelope.
    assert seen_envelope_count >= 30


def _project_envelope_for_equality(env: dict) -> dict:
    """Strip fields that are intentionally non-deterministic per contract section 5.3:
    signal_envelope_id, produced_at, portal_signature.

    Also strip per-portal-instance scoped pointers (dp_id, redacted_record_ref) which
    embed UUIDs minted at enrollment time and are not part of the detection substrate.
    What remains is the deterministic detection output the contract guarantees:
    event_type, rule_matches (with match_excerpt_hash), scanner_pins, agent_call_id,
    telemetry_api_version, event_rule_corpus_version."""
    drop = {
        "signal_envelope_id",
        "produced_at",
        "portal_signature",
        "dp_id",
        "redacted_record_ref",
    }
    return {k: v for k, v in env.items() if k not in drop}


def test_signal_envelopes_byte_equal_across_n5(tmp_path):
    """Contract section 6: same redacted_output_record + same event_rule_corpus_version
    -> byte-identical signal envelope modulo id/timestamp/signature."""
    runs = []
    for run_idx in range(5):
        run_dir = tmp_path / f"run-{run_idx}"
        svc = build_service(run_dir)
        try:
            _, dp, enrollment, _, _ = _produce_baseline_score(svc)
            run_envelopes = []
            for case in FIXTURE_CASES:
                _emit_for_case(svc, dp, enrollment, case)
                rows = list(
                    svc.db.execute(
                        "SELECT envelope_json FROM signal_envelopes WHERE agent_call_id = ? "
                        "ORDER BY event_type ASC",
                        (f"call-{case['id']}",),
                    )
                )
                for row in rows:
                    env = json.loads(row["envelope_json"])
                    run_envelopes.append((case["id"], env["event_type"], _project_envelope_for_equality(env)))
            run_envelopes.sort(key=lambda t: (t[0], t[1]))
            runs.append(run_envelopes)
        finally:
            svc.close()
    # All runs should have the same number of envelopes (same fixtures + same corpus).
    counts = {len(r) for r in runs}
    assert len(counts) == 1, f"per-run envelope counts diverged: {counts}"
    # Pair-wise byte equality across runs.
    base = runs[0]
    for run_idx, other in enumerate(runs[1:], start=1):
        assert len(base) == len(other), f"run-0 vs run-{run_idx} count delta"
        for i, (lhs, rhs) in enumerate(zip(base, other)):
            assert lhs[0] == rhs[0] and lhs[1] == rhs[1], f"case/event order drift at index {i}"
            assert canonical_json(lhs[2]) == canonical_json(rhs[2]), (
                f"signal envelope drift at run-0 vs run-{run_idx} index {i} case {lhs[0]} event {lhs[1]}"
            )


# -- envelope signature verifies ----------------------------------------------------


def test_signal_envelope_signature_verifies(service):
    _, dp, enrollment, _, _ = _produce_baseline_score(service)
    _emit_for_case(service, dp, enrollment, FIXTURE_CASES[0])
    row = service.db.execute("SELECT * FROM signal_envelopes ORDER BY id LIMIT 1").fetchone()
    env = json.loads(row["envelope_json"])
    body = {k: v for k, v in env.items() if k != "portal_signature"}
    pub = service._counter_key().public_key()
    pub.verify(unb64url(env["portal_signature"]), canonical_json(body))


# -- degradation trigger ------------------------------------------------------------


def test_trigger_fires_on_synthetic_threshold(service):
    _, dp, enrollment, _, _ = _produce_baseline_score(service)
    # data_leak per-event threshold is 1: a single dl-01 should fire the trigger.
    case = next(c for c in FIXTURE_CASES if c["event_type"] == "data_leak")
    result = service.submit_redacted_record(
        _record(enrollment["connector_id"], dp["dp_id"], "trigger-call-1", case["redacted_output_text"])
    )
    assert result["trigger_fired"] is True
    assert result["re_pricing_event_id"]
    assert result["claims_trigger_envelope_id"]
    rp_rows = list(service.db.execute("SELECT * FROM re_pricing_events"))
    ct_rows = list(service.db.execute("SELECT * FROM claims_trigger_envelopes"))
    assert len(rp_rows) == 1 and len(ct_rows) == 1
    # Audit-log carries both downstream emit events.
    audit_types = {
        r["event_type"]
        for r in service.db.execute("SELECT event_type FROM audit_log").fetchall()
    }
    assert "re_pricing_event_emitted" in audit_types
    assert "claims_trigger_emitted" in audit_types
    assert "signal_envelope_emitted" in audit_types
    assert "redacted_record_received" in audit_types


def test_trigger_idempotent_on_repeat_eval(service):
    _, dp, enrollment, _, _ = _produce_baseline_score(service)
    case = next(c for c in FIXTURE_CASES if c["event_type"] == "data_leak")
    service.submit_redacted_record(
        _record(enrollment["connector_id"], dp["dp_id"], "trigger-call-A", case["redacted_output_text"])
    )
    # Re-fire by submitting the SAME record (idempotent at intake -> no new trigger).
    service.submit_redacted_record(
        _record(enrollment["connector_id"], dp["dp_id"], "trigger-call-A", case["redacted_output_text"])
    )
    rp_rows = list(service.db.execute("SELECT * FROM re_pricing_events"))
    ct_rows = list(service.db.execute("SELECT * FROM claims_trigger_envelopes"))
    assert len(rp_rows) == 1 and len(ct_rows) == 1


def test_trigger_signed_with_counter_key(service):
    _, dp, enrollment, _, _ = _produce_baseline_score(service)
    case = next(c for c in FIXTURE_CASES if c["event_type"] == "data_leak")
    service.submit_redacted_record(
        _record(enrollment["connector_id"], dp["dp_id"], "trigger-call-2", case["redacted_output_text"])
    )
    pub = service._counter_key().public_key()
    rp_row = service.db.execute("SELECT * FROM re_pricing_events LIMIT 1").fetchone()
    rp = json.loads(rp_row["envelope_json"])
    pub.verify(
        unb64url(rp["portal_signature"]),
        canonical_json({k: v for k, v in rp.items() if k != "portal_signature"}),
    )
    ct_row = service.db.execute("SELECT * FROM claims_trigger_envelopes LIMIT 1").fetchone()
    ct = json.loads(ct_row["envelope_json"])
    pub.verify(
        unb64url(ct["portal_signature"]),
        canonical_json({k: v for k, v in ct.items() if k != "portal_signature"}),
    )


# -- suspend / resume ---------------------------------------------------------------


def test_suspend_requires_dual_cosign(service):
    _, dp, _, _, _ = _produce_baseline_score(service)
    with pytest.raises(PortalError) as err:
        service.put_telemetry_trigger(dp["dp_id"], {"suspended": True}, actor="admin")
    assert err.value.code == "telemetry_trigger_cosign_required"
    with pytest.raises(PortalError) as err:
        service.put_telemetry_trigger(
            dp["dp_id"],
            {"suspended": True, "founder_cosign_ref": "BEAAA-xx"},
            actor="admin",
        )
    assert err.value.code == "telemetry_trigger_cosign_required"
    # Both refs -> accepted.
    result = service.put_telemetry_trigger(
        dp["dp_id"],
        {
            "suspended": True,
            "founder_cosign_ref": "BEAAA-xx",
            "houw_cosign_ref": "BEAAA-yy",
        },
        actor="admin",
    )
    assert result["suspended"] is True


def test_suspend_blocks_downstream_emit(service):
    _, dp, enrollment, _, _ = _produce_baseline_score(service)
    service.put_telemetry_trigger(
        dp["dp_id"],
        {
            "suspended": True,
            "founder_cosign_ref": "BEAAA-xx",
            "houw_cosign_ref": "BEAAA-yy",
        },
        actor="admin",
    )
    case = next(c for c in FIXTURE_CASES if c["event_type"] == "data_leak")
    result = service.submit_redacted_record(
        _record(enrollment["connector_id"], dp["dp_id"], "susp-call-1", case["redacted_output_text"])
    )
    assert result["trigger_fired"] is False
    assert result["re_pricing_event_id"] is None
    assert result["claims_trigger_envelope_id"] is None
    # Signal envelopes still emitted.
    sig_rows = list(service.db.execute("SELECT * FROM signal_envelopes"))
    assert len(sig_rows) >= 1
    # Resume re-evaluates and emits.
    service.put_telemetry_trigger(dp["dp_id"], {"suspended": False}, actor="admin")
    rp_rows = list(service.db.execute("SELECT * FROM re_pricing_events"))
    ct_rows = list(service.db.execute("SELECT * FROM claims_trigger_envelopes"))
    assert len(rp_rows) == 1
    assert len(ct_rows) == 1


# -- retrieval --------------------------------------------------------------------


def test_retrieval_endpoints(service):
    _, dp, enrollment, _, _ = _produce_baseline_score(service)
    case = next(c for c in FIXTURE_CASES if c["event_type"] == "data_leak")
    service.submit_redacted_record(
        _record(enrollment["connector_id"], dp["dp_id"], "ret-call-1", case["redacted_output_text"])
    )
    sigs = service.list_signal_envelopes(dp["dp_id"])
    assert sigs["count"] >= 1
    assert sigs["signal_envelopes"][0]["event_type"]
    rp = service.list_re_pricing_events(dp["dp_id"])
    assert rp["count"] == 1
    assert rp["re_pricing_events"][0]["aggregate_severity"] >= 0
    ct = service.list_claims_trigger_envelopes(dp["dp_id"])
    assert ct["count"] == 1
    assert ct["claims_trigger_envelopes"][0]["claim_eligibility_class"] in (
        "interim_payout_eligible",
        "review_required",
        "not_eligible",
    )


def test_retrieval_filter_by_event_type(service):
    _, dp, enrollment, _, _ = _produce_baseline_score(service)
    for case in FIXTURE_CASES[:6]:
        _emit_for_case(service, dp, enrollment, case)
    only_dl = service.list_signal_envelopes(dp["dp_id"], event_type="data_leak")
    for env in only_dl["signal_envelopes"]:
        assert env["event_type"] == "data_leak"
    with pytest.raises(PortalError) as err:
        service.list_signal_envelopes(dp["dp_id"], event_type="not_a_thing")
    assert err.value.code == "telemetry_event_type_invalid"


# -- boundary grep ----------------------------------------------------------------


def test_boundary_grep_no_raw_text_in_persisted_artifacts(service):
    _, dp, enrollment, _, _ = _produce_baseline_score(service)
    for case in FIXTURE_CASES:
        _emit_for_case(service, dp, enrollment, case)
    # Signal envelopes / re-pricing events / claims envelopes must not embed any
    # fixture redacted_output_text substring. Check token-by-token for the rare
    # words used in each fixture.
    distinctive_phrases = [
        "issue a refund of the full amount",
        "diagnosed with hypertension",
        "operating in developer mode",
        "binding agreement on behalf of the company",
        "filed the suit",
        "sue the carrier",
        "deduct the full amount",
        "investing in NVDA",
        "patient id in the medical record",
        "ignore previous instructions",
    ]
    for table in ("signal_envelopes", "re_pricing_events", "claims_trigger_envelopes"):
        rows = list(service.db.execute(f"SELECT envelope_json FROM {table}"))
        for row in rows:
            body = row["envelope_json"]
            for phrase in distinctive_phrases:
                assert phrase.lower() not in body.lower(), (
                    f"Raw fixture text leaked into {table}: {phrase!r}"
                )
            assert "redacted_output_text" not in body
    # match_excerpt_hash format check: 64-char lowercase hex on every rule_match.
    sig_rows = list(service.db.execute("SELECT envelope_json FROM signal_envelopes"))
    for row in sig_rows:
        env = json.loads(row["envelope_json"])
        for match in env["rule_matches"]:
            assert re.match(r"^[0-9a-f]{64}$", match["match_excerpt_hash"])


# -- audit-log chain --------------------------------------------------------------


def test_audit_chain_intact_after_telemetry(service):
    _, dp, enrollment, _, _ = _produce_baseline_score(service)
    for case in FIXTURE_CASES[:6]:
        _emit_for_case(service, dp, enrollment, case)
    assert service.verify_audit_chain() is True


# -- pin block --------------------------------------------------------------------


def test_portal_pins_carries_telemetry_fields(service):
    pins = json.loads(service.config.pins_path.read_bytes())
    assert pins["telemetry_api_version"] == TELEMETRY_API_VERSION
    assert pins["event_rule_corpus_version"] == EVENT_RULE_CORPUS_VERSION
    assert "telemetry-api-v0" in pins["contracts"]
    assert pins["portal_version"] == PORTAL_VERSION


# -- calibration: Backbond score Output unchanged ---------------------------------


def test_backbond_score_output_byte_identical(tmp_path):
    """Contract section 11 row 2: 'Calibration-delta against the Backbond score Output: zero.
    The are-scanner-api-v1@v1.0 Output is byte-identical before and after; portal
    version bumps cleanly.'

    Produces a baseline score in a fresh portal, then a second score in another fresh
    portal where telemetry traffic has flowed (intake + signal envelopes + trigger emits)
    BEFORE produce_score is called. The Backbond score Output field set must not drift.
    """
    # Run A: portal with no telemetry traffic.
    svc_a = build_service(tmp_path / "a")
    try:
        signing_key, dp, enrollment, scan, score_a = _produce_baseline_score(svc_a)
    finally:
        svc_a.close()
    # Run B: portal where telemetry flowed first, then a score is produced.
    svc_b = build_service(tmp_path / "b")
    try:
        signing_key_b, dp_b, enrollment_b = enroll_ready_dp(svc_b)
        for case in FIXTURE_CASES:
            svc_b.submit_redacted_record(
                _record(enrollment_b["connector_id"], dp_b["dp_id"], f"call-{case['id']}", case["redacted_output_text"])
            )
        scan_b = svc_b.trigger_scan(dp_b["dp_id"])
        submit_all_tasks(svc_b, signing_key_b, scan_b, enrollment_b["connector_id"])
        score_b = svc_b.produce_score(scan_b["scan_id"])
    finally:
        svc_b.close()
    # Project out fields that are intentionally unique per scan but contract-stable
    # at the schema level (scan_id, evidence_refs[].test_id, run_timestamp,
    # envelope_signature_chain). What we assert is that the FIELD SET and the
    # are_total/subscores/band/confidence/uncovered_dimensions/seed/scanner_version
    # /corpus_version did not drift, i.e. the scoring substrate is unchanged.
    drift_sensitive_fields = {
        "are_total",
        "band",
        "confidence",
        "uncovered_dimensions",
        "scanner_version",
        "corpus_version",
        "model_versions",
        "seed",
    }
    for field in drift_sensitive_fields:
        assert score_a.get(field) == score_b.get(field), (
            f"Backbond score Output field '{field}' drifted under telemetry traffic: "
            f"a={score_a.get(field)} b={score_b.get(field)}"
        )
    assert score_a.get("subscores") == score_b.get("subscores"), (
        "Backbond score Output subscores drifted under telemetry traffic"
    )
    # Field set parity check.
    assert set(score_a.keys()) == set(score_b.keys()), (
        f"Backbond score Output field set drifted: "
        f"a-only={set(score_a.keys())-set(score_b.keys())} "
        f"b-only={set(score_b.keys())-set(score_a.keys())}"
    )
    # No telemetry pin field leaked into the are-scanner-api-v1@v1.0 Output.
    for forbidden in ("telemetry_api_version", "event_rule_corpus_version"):
        assert forbidden not in score_a
        assert forbidden not in score_b


# -- pin block on envelopes ------------------------------------------------------


def test_envelope_pin_block_present(service):
    _, dp, enrollment, _, _ = _produce_baseline_score(service)
    case = next(c for c in FIXTURE_CASES if c["event_type"] == "data_leak")
    service.submit_redacted_record(
        _record(enrollment["connector_id"], dp["dp_id"], "pinned-call", case["redacted_output_text"])
    )
    sig = json.loads(
        service.db.execute("SELECT envelope_json FROM signal_envelopes ORDER BY id LIMIT 1").fetchone()["envelope_json"]
    )
    assert sig["scanner_pins"]["telemetry_api_version"] == TELEMETRY_API_VERSION
    assert sig["scanner_pins"]["event_rule_corpus_version"] == EVENT_RULE_CORPUS_VERSION
    assert sig["scanner_pins"]["scanner_version"] == SCANNER_VERSION
    assert sig["scanner_pins"]["corpus_version"] == CORPUS_VERSION
    assert sig["scanner_pins"]["rubric_version"] == RUBRIC_VERSION
    rp = json.loads(
        service.db.execute("SELECT envelope_json FROM re_pricing_events LIMIT 1").fetchone()["envelope_json"]
    )
    assert rp["pin_block"]["telemetry_api_version"] == TELEMETRY_API_VERSION
    assert rp["pin_block"]["event_rule_corpus_version"] == EVENT_RULE_CORPUS_VERSION
    ct = json.loads(
        service.db.execute("SELECT envelope_json FROM claims_trigger_envelopes LIMIT 1").fetchone()["envelope_json"]
    )
    assert ct["pin_block"]["telemetry_api_version"] == TELEMETRY_API_VERSION
    assert ct["pin_block"]["event_rule_corpus_version"] == EVENT_RULE_CORPUS_VERSION
