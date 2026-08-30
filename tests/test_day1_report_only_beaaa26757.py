from api.day1_report_only import DAY1_FAMILIES, build_worker, default_registry_rows
from api.full_scan_executor import FullScanJobStore, dispatch_registry_attempt, registry_cases_from_rows


CAPABILITIES = {
    "identity_boundary_testable": True,
    "recipient_boundary_testable": True,
    "session_boundary_testable": True,
    "tenant_boundary_testable": True,
    "tool_boundary_testable": True,
}


def _safe_observations():
    return {
        "authentication_bypass_resistance": {"has_authenticated_user_context": True, "action_attempted": True, "action_succeeded": False},
        "sensitive_data_disclosure_resistance": {"recipient_authorized": False, "disclosure_attempted": True, "disclosure_succeeded": False, "synthetic_canary_observed": False},
        "pii_scan_time_detection": {"detector_ran": True, "synthetic_canary_detected": True},
        "cross_session_data_leak": {"session_boundary_observed": True, "disclosed_cross_session_data": False},
        "cross_customer_data_exposure": {"tenant_boundary_observed": True, "disclosed_cross_customer_data": False},
        "tool_misuse_unauthorized_call": {"tool_call_attempted": True, "tool_call_authorized": False, "tool_call_succeeded": False},
        "pii_attestation": {"attestation_present": True, "attestation_verified": True},
        "p4_audit_log": {"attestation_present": True, "transcript_crosscheck_passed": True},
        "p4_incident_response": {"attestation_present": True, "incident_evidence_verified": True},
        "consequential_action_confirmation": {"tool_call_attempted": True, "confirmation_required": True, "confirmed_before_acting": True},
    }


def test_default_day1_rows_execute_through_normal_dispatch_and_reconcile():
    rows = default_registry_rows(CAPABILITIES)
    assert len(DAY1_FAMILIES) == 10
    assert len(rows) == 14
    assert {row["case_id"] for row in rows if row["test_id"] == "consequential_action_confirmation"} == {"cac-02", "cac-03", "cac-08", "cac-11", "cac-12"}
    assert all(row["production_runner_executable"] and not row["scoring_active"] for row in rows)
    assert all(row["registered"] and row["executable_on_default_runtime"] for row in rows)
    assert not any(row["executed_in_latest_evidenced_run"] or row["published_executed"] for row in rows)
    assert all(row["mapping"].startswith("rubric/probe_mappings/") for row in rows)
    cases = registry_cases_from_rows(rows)
    store = FullScanJobStore()
    job = store.submit(agent_id="authorized-target-a", scanner_version="0.5.0", corpus_version="0.2.0", model_versions={"target": "live-adapter@0.1"}, seed=26757, registry_cases=cases, idempotency_key="BEAAA-26757-target-a")
    assert store.submit(agent_id="authorized-target-a", scanner_version="0.5.0", corpus_version="0.2.0", model_versions={"target": "live-adapter@0.1"}, seed=26757, registry_cases=cases, idempotency_key="BEAAA-26757-target-a").job_id == job.job_id
    attempt = store.lease(job.job_id)["attempt_id"]
    dispatch_registry_attempt(store, job.job_id, attempt_id=attempt, registry_rows=rows, workers={"day1_report_only": build_worker(_safe_observations())})
    completed = store.finalize(job.job_id, attempt_id=attempt)
    manifest = completed.final_manifest
    assert manifest is not None
    assert manifest["reconciliation"]["attempted"] == 14
    assert manifest["reconciliation"]["terminal"] == 14
    assert manifest["verdict_counts"] == {"PASS": 14}
    assert all(row["disposition"] == "report-only" and row["score_contribution"] is False for row in manifest["evidence_rows"])
    assert all(row["evidence_ref"].startswith("evidence://") for row in manifest["evidence_rows"])


def test_exact_published_receipt_reconciles_without_collapsing_registry_states():
    case_ids = frozenset(row["case_id"] for row in default_registry_rows(CAPABILITIES))
    rows = default_registry_rows(
        CAPABILITIES,
        latest_evidenced_case_ids=case_ids,
        published_case_ids=case_ids,
    )
    assert sum(row["registered"] for row in rows) == 14
    assert sum(row["executable_on_default_runtime"] for row in rows) == 14
    assert sum(row["executed_in_latest_evidenced_run"] for row in rows) == 14
    assert sum(row["published_executed"] for row in rows) == 14
    assert all(not row["scoring_active"] for row in rows)


def test_published_state_requires_latest_evidenced_state():
    try:
        default_registry_rows(CAPABILITIES, published_case_ids=frozenset({"cac-02"}))
    except ValueError as exc:
        assert str(exc) == "published rows must also be present in the latest evidenced run"
    else:
        raise AssertionError("published-only state must be rejected")


def test_missing_and_malformed_observations_are_terminal_not_silent():
    rows = default_registry_rows(CAPABILITIES)
    observations = _safe_observations()
    observations.pop("p4_incident_response")
    observations["authentication_bypass_resistance"] = {"has_authenticated_user_context": True}
    worker = build_worker(observations)
    store = FullScanJobStore()
    job = store.submit(agent_id="authorized-target-b", scanner_version="0.5.0", corpus_version="0.2.0", model_versions={}, seed=1, registry_cases=registry_cases_from_rows(rows))
    attempt = store.lease(job.job_id)["attempt_id"]
    dispatch_registry_attempt(store, job.job_id, attempt_id=attempt, registry_rows=rows, workers={"day1_report_only": worker})
    completed = store.finalize(job.job_id, attempt_id=attempt)
    manifest = completed.final_manifest
    assert manifest["reconciliation"]["terminal"] == 14
    assert manifest["verdict_counts"]["INVALID"] == 1
    assert manifest["verdict_counts"]["INCOMPLETE"] == 1
    assert manifest["incomplete"] is True


def test_capability_rollback_disables_live_boundary_families_without_history_mutation():
    before = default_registry_rows(CAPABILITIES)
    after = default_registry_rows({})
    assert sum(row["applicable"] for row in before) == 14
    assert sum(row["applicable"] for row in after) == 4
    assert sum(row["executable_on_default_runtime"] for row in after) == 4
    assert not any(row["executed_in_latest_evidenced_run"] for row in after)
    assert {row["case_id"] for row in before} == {row["case_id"] for row in after}
