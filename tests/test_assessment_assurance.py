"""Independent timing examples, semantic negatives, normal reports and privacy."""
import base64
from copy import deepcopy
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest

from evals.oss_pilots import assessment_records as records
from tools.assessment_test_catalog import ASSURANCE_RECORD_CASES, ASSURANCE_METHODS, ASSURANCE_VERSION
from tests.assessment_assurance_fixtures import sample, contradict
from tests.assessment_validation_fixtures import encode, change_artifact
from tests.test_assessment_records import SCOPE, worker


def run(case, doc=None):
    doc = sample(case, SCOPE) if doc is None else doc
    return records.run_case(case, SimpleNamespace(read=lambda _: encode(doc)), scope=SCOPE)


@pytest.mark.parametrize("case", ASSURANCE_METHODS)
def test_original_bytes_and_version_reproduce_and_substituted_metrics_fail(case):
    doc = sample(case, SCOPE)
    capture = run(case, doc)
    assert capture["verdict"] == records.verify(case, capture, scope=SCOPE) == "passed"
    assert capture["capture_version"] == ASSURANCE_VERSION
    assert base64.b64decode(capture["input_document"]["content_base64"]) == encode(doc)
    changed = deepcopy(capture)
    changed["measurements"]["record_count"] += 1
    with pytest.raises(ValueError): records.verify(case, changed, scope=SCOPE)
    changed = deepcopy(capture)
    changed["capture_version"] = "assessment-lifecycle-checks@1"
    with pytest.raises(ValueError): records.verify(case, changed, scope=SCOPE)


@pytest.mark.parametrize("case", ASSURANCE_METHODS)
def test_semantic_faults_fail_and_retain_original_failed_evidence(case):
    doc = sample(case, SCOPE)
    contradict(doc, case)
    capture = run(case, doc)
    assert capture["verdict"] == records.verify(case, capture, scope=SCOPE) == "failed"
    assert base64.b64decode(capture["input_document"]["content_base64"]) == encode(doc)


@pytest.mark.parametrize("case,ref", [("EVID-INCIDENT-NOTIFICATION", "decision"),
    ("EVID-SUPPLIER-ASSURANCE", "supplier"), ("EVID-IMPACT-ASSESSMENT-COVERAGE", "impact")])
def test_missing_adapter_or_originals_cannot_pass_and_foreign_bytes_are_not_retained(case, ref):
    assert records.run_case(case, None, scope=SCOPE)["verdict"] == "inconclusive"
    for missing in (ref, "proof"):
        doc = sample(case, SCOPE)
        doc["artifacts"].pop(missing)
        assert run(case, doc)["verdict"] == "inconclusive"
    for mutate in (lambda d: d["data"]["system"].update(target_id="foreign-private-target"),
                   lambda d: change_artifact(d, ref, lambda v: v.update(target_id="foreign-private-target"))):
        doc = sample(case, SCOPE)
        mutate(doc)
        capture = run(case, doc)
        assert capture["verdict"] == "inconclusive" and "input_document" not in capture
        assert "foreign-private-target" not in json.dumps(capture)
    doc = sample(case, SCOPE)
    change_artifact(doc, ref, lambda v: v.update(snapshot_sha256="0" * 64))
    assert run(case, doc)["verdict"] == "failed"


def test_incident_delays_are_recomputed_from_original_receipt_instants():
    capture = run("EVID-INCIDENT-NOTIFICATION")
    m = capture["measurements"]
    assert m["maximum_submission_delay_seconds"] == 7800
    assert m["maximum_delivery_delay_seconds"] == 8400
    assert m["required_notification_count"] == m["delivered_notification_count"] == 1
    assert m["reportable_incident_count"] == 1 and m["exempt_incident_count"] == 0


@pytest.mark.parametrize("case,ref", [("EVID-INCIDENT-NOTIFICATION", "receipt"),
    ("EVID-IMPACT-ASSESSMENT-COVERAGE", "changes"), ("EVID-IMPACT-ASSESSMENT-COVERAGE", "mitigation")])
def test_foreign_nested_originals_cannot_be_rebound_and_missing_originals_are_gaps(case, ref):
    doc = sample(case, SCOPE)
    change_artifact(doc, ref, lambda v: v.update(target_id="foreign-private-target"))
    capture = run(case, doc)
    assert capture["verdict"] == "inconclusive" and "input_document" not in capture
    doc = sample(case, SCOPE)
    doc["artifacts"].pop(ref)
    assert run(case, doc)["verdict"] == "inconclusive"


@pytest.mark.parametrize("mutation", [
    lambda d: change_artifact(d, "receipt", lambda v: v.update(status="queued")),
    lambda d: change_artifact(d, "receipt", lambda v: v.update(notification_id="wrong-notification")),
    lambda d: change_artifact(d, "receipt", lambda v: v.update(incident_id="wrong-incident")),
    lambda d: change_artifact(d, "receipt", lambda v: v.update(submitted_at=d["data"]["policy"]["required_incidents"][0]["aware_at"])),
    lambda d: d["data"]["policy"]["required_incidents"][0].update(escalation_due_at=d["data"]["policy"]["required_incidents"][0]["aware_at"]),
    lambda d: change_artifact(d, "decision", lambda v: v.update(notification_required=1)),
    lambda d: change_artifact(d, "decision", lambda v: v.update(notification_required=False)),
    lambda d: change_artifact(d, "decision", lambda v: v.update(required_notification_ids=[])),
    lambda d: d["data"]["policy"]["required_incidents"][0]["required_notifications"][0].update(deadline_event="unknown"),
])
def test_incident_recipient_identity_delivery_and_applicability_must_reconcile(mutation):
    case = "EVID-INCIDENT-NOTIFICATION"
    doc = sample(case, SCOPE)
    mutation(doc)
    assert run(case, doc)["verdict"] == "failed"


def test_notification_deadline_event_and_exact_boundary_are_reviewed_policy():
    case = "EVID-INCIDENT-NOTIFICATION"
    doc = sample(case, SCOPE)
    requirement = doc["data"]["policy"]["required_incidents"][0]["required_notifications"][0]
    submitted = doc["data"]["incidents"][0]["notifications"][0]["submitted_at"]
    requirement["due_at"] = submitted
    assert run(case, doc)["verdict"] == "passed"
    requirement["deadline_event"] = "delivered"
    capture = run(case, doc)
    assert capture["verdict"] == "failed" and capture["measurements"]["overdue_notification_count"] == 1
    requirement["due_at"] = (records._instant(submitted) + timedelta(minutes=10)).isoformat()
    assert run(case, doc)["verdict"] == "passed"


def test_missing_notification_stays_visible_and_overdue_is_checked_at_capture_time():
    case = "EVID-INCIDENT-NOTIFICATION"
    doc = sample(case, SCOPE)
    doc["data"]["incidents"][0]["notifications"] = []
    requirement = doc["data"]["policy"]["required_incidents"][0]["required_notifications"][0]
    requirement["due_at"] = (records._instant(doc["recorded_at"]) + timedelta(hours=1)).isoformat()
    capture = run(case, doc)
    assert capture["verdict"] == "inconclusive" and capture["measurements"]["missing_notification_count"] == 1
    later = (records._instant(requirement["due_at"]) + timedelta(seconds=1)).isoformat()
    metrics, verdict = records.evaluate(case, encode(doc), scope=SCOPE, evaluated_at=later)
    assert verdict == "failed" and metrics["overdue_notification_count"] == 1
    assert records.verify(case, capture, scope=SCOPE) == "inconclusive"


def test_reporting_exemption_requires_reviewed_decision_and_no_fabricated_receipt():
    case = "EVID-INCIDENT-NOTIFICATION"
    doc = sample(case, SCOPE)
    doc["data"]["policy"]["required_incidents"][0]["required_notifications"] = []
    doc["data"]["incidents"][0]["notifications"] = []
    change_artifact(doc, "decision", lambda v: v.update(notification_required=False, required_notification_ids=[]))
    capture = run(case, doc)
    assert capture["verdict"] == "passed" and capture["measurements"]["exempt_incident_count"] == 1
    assert capture["measurements"]["delivered_notification_count"] == 0
    doc["artifacts"].pop("decision")
    assert run(case, doc)["verdict"] == "inconclusive"


@pytest.mark.parametrize("field,value", [("supplier_id", "wrong"), ("component_sha256", "0" * 64),
    ("status", "rejected"), ("owner", ""), ("responsibilities", []), ("agreement_refs", [])])
def test_supplier_approval_binds_version_digest_responsibilities_and_agreements(field, value):
    doc = sample("EVID-SUPPLIER-ASSURANCE", SCOPE)
    change_artifact(doc, "supplier", lambda v: v.update({field: value}))
    assert run("EVID-SUPPLIER-ASSURANCE", doc)["verdict"] == "failed"


def test_supplier_inventory_is_complete_relative_to_supplied_manifest_and_missing_rows_are_gaps():
    case = "EVID-SUPPLIER-ASSURANCE"
    doc = sample(case, SCOPE)
    doc["data"]["components"] = []
    capture = run(case, doc)
    assert capture["verdict"] == "inconclusive" and capture["measurements"]["missing_component_count"] == 1
    doc = sample(case, SCOPE)
    doc["data"]["system"]["components"]["unreviewed-component"] = "d" * 64
    assert run(case, doc)["verdict"] == "failed"


@pytest.mark.parametrize("case,ref", [("EVID-SUPPLIER-ASSURANCE", "supplier"), ("EVID-IMPACT-ASSESSMENT-COVERAGE", "impact")])
def test_current_review_expiry_uses_capture_time_and_replay_freezes_that_time(case, ref):
    doc = sample(case, SCOPE)
    valid = (records._instant(doc["recorded_at"]) + timedelta(hours=1)).isoformat()
    change_artifact(doc, ref, lambda v: v.update(valid_until=valid))
    capture = run(case, doc)
    assert capture["verdict"] == "passed"
    metrics, verdict = records.evaluate(case, encode(doc), scope=SCOPE,
        evaluated_at=(records._instant(valid) + timedelta(seconds=1)).isoformat())
    assert verdict == "failed" and metrics["expired_review_count"] == 1
    assert records.verify(case, capture, scope=SCOPE) == "passed"


@pytest.mark.parametrize("ref,mutation", [
    ("impact", lambda v: v.update(intended_use_id="wrong-use")),
    ("impact", lambda v: v.update(risk_ids=[])),
    ("impact", lambda v: v.update(change_ids=[])),
    ("impact", lambda v: v["decisions"][0].update(id="wrong-risk")),
    ("impact", lambda v: v["decisions"][0].update(decision="unknown")),
    ("mitigation", lambda v: v.update(context_id="wrong-context")),
    ("mitigation", lambda v: v.update(status="proposed")),
    ("changes", lambda v: v["changes"][0].update(context_ids=["unknown-context"])),
])
def test_impact_scope_change_and_risk_decisions_require_exact_reviewed_links(ref, mutation):
    case = "EVID-IMPACT-ASSESSMENT-COVERAGE"
    doc = sample(case, SCOPE)
    change_artifact(doc, ref, mutation)
    assert run(case, doc)["verdict"] == "failed"


def test_impact_change_coverage_and_review_deadlines_are_enforced():
    case = "EVID-IMPACT-ASSESSMENT-COVERAGE"
    doc = sample(case, SCOPE)
    requirement = doc["data"]["policy"]["required_contexts"][0]
    requirement["required_by"] = (records._instant(requirement["required_by"]) - timedelta(hours=2)).isoformat()
    assert run(case, doc)["verdict"] == "failed"
    doc = sample(case, SCOPE)
    change_artifact(doc, "changes", lambda v: v["changes"].append(dict(id="omitted-change",
        changed_at=v["changes"][0]["changed_at"], context_ids=["private-context"])))
    assert run(case, doc)["verdict"] == "failed"
    doc = sample(case, SCOPE)
    doc["data"]["contexts"] = []
    assert run(case, doc)["verdict"] == "inconclusive"


def test_impact_mitigation_cannot_silently_include_a_conflicting_acceptance_claim():
    case = "EVID-IMPACT-ASSESSMENT-COVERAGE"
    doc = sample(case, SCOPE)
    change_artifact(doc, "impact", lambda v: v["decisions"][0].update(acceptance_ref="mitigation"))
    assert run(case, doc)["verdict"] == "failed"


def test_explicit_residual_risk_acceptance_has_its_own_current_original_decision():
    case = "EVID-IMPACT-ASSESSMENT-COVERAGE"
    doc = sample(case, SCOPE)
    change_artifact(doc, "impact", lambda v: v.update(decisions=[dict(id="private-risk", decision="accept", acceptance_ref="mitigation")]))
    valid = (records._instant(doc["recorded_at"]) + timedelta(days=1)).isoformat()
    change_artifact(doc, "mitigation", lambda v: v.update(status="accepted", valid_until=valid))
    capture = run(case, doc)
    assert capture["verdict"] == "passed" and capture["measurements"]["accepted_risk_count"] == 1
    change_artifact(doc, "mitigation", lambda v: v.update(valid_until=doc["recorded_at"]))
    assert run(case, doc)["verdict"] == "failed"
    doc["artifacts"].pop("mitigation")
    assert run(case, doc)["verdict"] == "inconclusive"


@pytest.mark.parametrize("case,field", [("EVID-INCIDENT-NOTIFICATION", "required_incidents"),
    ("EVID-SUPPLIER-ASSURANCE", "required_components"), ("EVID-IMPACT-ASSESSMENT-COVERAGE", "required_contexts")])
def test_empty_duplicate_and_over_limit_policy_identities_cannot_pass(case, field):
    for mode in ("empty", "duplicate", "over_limit"):
        doc = sample(case, SCOPE)
        row = doc["data"]["policy"][field][0]
        doc["data"]["policy"][field] = [] if mode == "empty" else [deepcopy(row) for _ in range(2 if mode == "duplicate" else records.MAX_ROWS + 1)]
        assert run(case, doc)["verdict"] != "passed"


def test_total_link_bound_applies_across_supplier_policy_and_original_review():
    case = "EVID-SUPPLIER-ASSURANCE"
    doc = sample(case, SCOPE)
    ids = [str(i) for i in range(1001)]
    doc["data"]["policy"]["required_components"][0]["required_responsibilities"] = ids
    change_artifact(doc, "supplier", lambda v: v.update(responsibilities=[dict(id=i, owner="owner") for i in ids]))
    capture = run(case, doc)
    assert capture["verdict"] == "failed" and capture["measurements"]["policy_errors"] > 0


@pytest.mark.parametrize("case", ASSURANCE_METHODS)
def test_assurance_results_cannot_recursively_supply_independent_risk_verification(case):
    from tests.assessment_validation_fixtures import sample as validation_sample
    doc = validation_sample("EVID-RISK-TEST-COVERAGE", SCOPE)
    change_artifact(doc, "native", lambda v: v.update(case_id=case))
    capture = run("EVID-RISK-TEST-COVERAGE", doc)
    assert capture["verdict"] == "failed"


@pytest.mark.parametrize("family,case", ASSURANCE_RECORD_CASES.items())
def test_normal_worker_reports_every_metric_in_json_html_and_both_frameworks_without_private_content(tmp_path, family, case):
    from tools.operational_scan import registry_rows
    from standard_requirement_report import build_standard_requirement_report
    from report_card.requirements import render_requirements
    result = worker(tmp_path)(next(r for r in registry_rows() if r["test_id"] == family),
        SimpleNamespace(job_id="job-A", scan_id="scan-A", agent_id="agent-A", seed=42))
    assert result.verdict == "pass" and result.target_calls == result.judge_calls == 0 and result.numeric_result is None
    report = build_standard_requirement_report({}, manifest={"evidence_rows": [vars(result) | dict(
        test_id=family, case_id=case, case_version=ASSURANCE_VERSION, sha256=result.evidence_sha256)]})
    public = report["evidence_inventory"]["artifacts"][0]
    assert public["diagnostic_measurements"] == result.diagnostic_measurements
    assert public["case_version"] == ASSURANCE_VERSION and public["method"] == ASSURANCE_METHODS[case]
    html = render_requirements({"requirements_evidence": report})
    assert case in html and all(f"{k}: {v}" in html for k, v in result.diagnostic_measurements.items())
    assert "private-" not in html and "private-" not in json.dumps(report) and "content_base64" not in html
    for framework in report["frameworks"]:
        assert framework["mapping_version"].endswith("@10" if framework["framework_id"] in {"eu-ai-act","iso-iec-42001"} else "@1" if framework["framework_id"] in {"owasp-llm-top10-2026","owasp-agentic-top10-2026"} else "@4")
        assert bool([c for c in framework["controls"] if c["scan_observations"]]) == any(family in c["candidate_test_ids"] for c in framework["controls"])
        assert all(c["requirement_conclusion"] == "not assessed" for c in framework["controls"])
