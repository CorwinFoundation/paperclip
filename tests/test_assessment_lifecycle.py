"""Known numerical examples, semantic faults, scope gaps and report privacy."""
import base64
from copy import deepcopy
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest

from evals.oss_pilots import assessment_records as records
from tools.assessment_test_catalog import LIFECYCLE_METHODS, LIFECYCLE_RECORD_CASES, LIFECYCLE_VERSION
from tests.assessment_lifecycle_fixtures import sample, contradict
from tests.assessment_validation_fixtures import encode, change_artifact
from tests.test_assessment_records import SCOPE, worker


def run(case, doc=None):
    doc = sample(case, SCOPE) if doc is None else doc
    return records.run_case(case, SimpleNamespace(read=lambda _: encode(doc)), scope=SCOPE)


@pytest.mark.parametrize("case", LIFECYCLE_METHODS)
def test_originals_version_and_measurements_reproduce_and_reject_substitution(case):
    doc = sample(case, SCOPE)
    capture = run(case, doc)
    assert capture["verdict"] == records.verify(case, capture, scope=SCOPE) == "passed"
    assert capture["capture_version"] == LIFECYCLE_VERSION
    assert base64.b64decode(capture["input_document"]["content_base64"]) == encode(doc)
    changed = deepcopy(capture)
    changed["measurements"]["record_count"] += 1
    with pytest.raises(ValueError): records.verify(case, changed, scope=SCOPE)
    changed = deepcopy(capture)
    changed["capture_version"] = "assessment-evidence-validation@1"
    with pytest.raises(ValueError): records.verify(case, changed, scope=SCOPE)


@pytest.mark.parametrize("case", LIFECYCLE_METHODS)
def test_specific_semantic_faults_fail_with_original_evidence_retained(case):
    doc = sample(case, SCOPE)
    contradict(doc, case)
    capture = run(case, doc)
    assert capture["verdict"] == records.verify(case, capture, scope=SCOPE) == "failed"
    assert base64.b64decode(capture["input_document"]["content_base64"]) == encode(doc)


@pytest.mark.parametrize("case", LIFECYCLE_METHODS)
def test_missing_adapter_and_foreign_private_inputs_are_unresolved_and_not_retained(case):
    assert records.run_case(case, None, scope=SCOPE)["verdict"] == "inconclusive"
    doc = sample(case, SCOPE)
    doc["data"]["system"]["target_id"] = "foreign-private-agent"
    capture = run(case, doc)
    assert capture["verdict"] == "inconclusive" and "input_document" not in capture


@pytest.mark.parametrize("case", ["EVID-MONITORING-COVERAGE", "EVID-MONITORING-TREND", "EVID-MULTICLASS-METRICS", "EVID-SUBGROUP-UNCERTAINTY"])
def test_foreign_labelled_dataset_cannot_be_rebound_by_a_matching_evaluation(case):
    doc = sample(case, SCOPE)
    change_artifact(doc, "dataset", lambda value: value.update(target_id="foreign-private-agent"))
    for key in ("current", "prior"):
        if key in doc["artifacts"]:
            change_artifact(doc, key, lambda value: value.update(dataset_sha256=doc["artifacts"]["dataset"]["sha256"]))
    capture = run(case, doc)
    assert capture["verdict"] == "inconclusive" and "input_document" not in capture
    assert "foreign-private-agent" not in json.dumps(capture)


def test_monitoring_missing_period_baseline_and_minimum_samples_do_not_pass():
    for case in ("EVID-MONITORING-COVERAGE", "EVID-MONITORING-TREND"):
        doc = sample(case, SCOPE)
        doc["data"]["windows"].pop(0)
        capture = run(case, doc)
        assert capture["verdict"] == "inconclusive"
        assert capture["measurements"]["missing_window_count"] == 1
        assert "baseline_accuracy_ppm" not in capture["measurements"]
        doc = sample(case, SCOPE)
        doc["data"]["policy"]["minimum_window_count"] = 5
        assert run(case, doc)["verdict"] == "inconclusive"


@pytest.mark.parametrize("mutation", [
    lambda d: d["data"]["windows"][1].update(index=0),
    lambda d: d["data"]["windows"][1].update(evaluation_ref="prior"),
    lambda d: d["data"]["policy"].update(end_at=(records._instant(d["data"]["policy"]["end_at"]) + timedelta(microseconds=500000)).isoformat()),
    lambda d: d["data"]["policy"].update(cadence_seconds=True),
    lambda d: change_artifact(d, "current", lambda v: v.update(observed_at=(records._instant(d["data"]["policy"]["start_at"]) + timedelta(hours=1)).isoformat())),
    lambda d: change_artifact(d, "current", lambda v: v.update(model_version="other-model")),
    lambda d: d["data"]["policy"].update(maximum_age_seconds=60),
])
def test_monitoring_rejects_ambiguous_slots_replayed_evidence_identity_drift_and_staleness(mutation):
    doc = sample("EVID-MONITORING-COVERAGE", SCOPE)
    mutation(doc)
    assert run("EVID-MONITORING-COVERAGE", doc)["verdict"] == "failed"


def test_trend_uses_original_predictions_and_customer_threshold():
    doc = sample("EVID-MONITORING-TREND", SCOPE)
    def regress(v):
        v["results"][0]["actual"] = "no"
        v["declared_accuracy_ppm"] = 750000
    change_artifact(doc, "current", regress)
    capture = run("EVID-MONITORING-TREND", doc)
    assert capture["verdict"] == "failed"
    assert capture["measurements"]["maximum_observed_drop_ppm"] == 250000
    assert capture["measurements"]["regressed_window_count"] == 1
    assert capture["measurements"]["metric_errors"] == 0
    doc["data"]["policy"]["maximum_drop_ppm"] = 250000
    assert run("EVID-MONITORING-TREND", doc)["verdict"] == "passed"


def test_freshness_uses_evaluation_time_and_captured_results_replay_at_the_saved_time():
    case = "EVID-MONITORING-COVERAGE"
    doc = sample(case, SCOPE)
    capture = run(case, doc)
    assert records.verify(case, capture, scope=SCOPE) == "passed"
    later = (records._instant(capture["evaluated_at"]) + timedelta(days=1)).isoformat()
    metrics, verdict = records.evaluate(case, encode(doc), scope=SCOPE, evaluated_at=later)
    assert verdict == "failed" and metrics["stale_window_count"] == 1
    assert records.verify(case, capture, scope=SCOPE) == "passed"


def test_empty_monitoring_history_is_a_gap_and_window_order_does_not_change_results():
    case = "EVID-MONITORING-TREND"
    doc = sample(case, SCOPE)
    before = run(case, doc)["measurements"]
    doc["data"]["windows"].reverse()
    assert run(case, doc)["measurements"] == before
    doc["data"]["windows"] = []
    capture = run(case, doc)
    assert capture["verdict"] == "inconclusive" and capture["measurements"]["missing_window_count"] == 2


def test_open_action_deadline_is_checked_even_when_it_postdates_the_source_record():
    case = "EVID-GOVERNANCE-ACTIONS"
    doc = sample(case, SCOPE)
    due = (records._instant(doc["recorded_at"]) + timedelta(minutes=1)).isoformat()
    doc["data"]["actions"][0].update(status="open", due_at=due)
    capture = run(case, doc)
    assert capture["verdict"] == "failed" and capture["measurements"]["overdue_action_count"] == 1


def test_multiclass_asymmetric_known_counts_include_abstentions_and_false_negatives():
    case = "EVID-MULTICLASS-METRICS"
    doc = sample(case, SCOPE)
    def imperfect(v):
        v["results"][0]["actual"] = "private-B"
        v["results"][3]["actual"] = "private-abstain"
        v["declared_accuracy_ppm"] = 666666
        v["declared_confusion_matrix"] = [[0, 1, 0], [0, 2, 0], [0, 0, 2]]
        v["declared_multiclass"][0] = dict(support=2, predicted=0, true_positive=0, false_positive=0,
            false_negative=2, abstention=1, recall_ppm=0, f1_ppm=0)
        v["declared_multiclass"][1].update(predicted=3, false_positive=1, precision_ppm=666666, f1_ppm=800000)
    change_artifact(doc, "current", imperfect)
    capture = run(case, doc)
    m = capture["measurements"]
    assert capture["verdict"] == "failed" and m["metric_errors"] == 0
    assert m["class_0_false_negative"] == 2 and m["class_0_abstention"] == 1
    assert m["abstention_count"] == 1 and m["abstention_rate_ppm"] == 166666
    assert m["class_1_precision_ppm"] == 666666 and m["class_1_f1_ppm"] == 800000
    assert "class_0_precision_ppm" not in m
    doc["data"]["policy"].update(minimum_precision_ppm=0, minimum_recall_ppm=0, minimum_f1_ppm=0, maximum_abstention_ppm=1_000_000)
    assert run(case, doc)["verdict"] == "inconclusive"
    doc["data"]["policy"]["abstention_policy"] = "reject"
    assert run(case, doc)["verdict"] == "failed"


@pytest.mark.parametrize("field", ["declared_confusion_matrix", "declared_multiclass"])
def test_declared_booleans_cannot_substitute_integer_counts(field):
    case = "EVID-MULTICLASS-METRICS"
    doc = sample(case, SCOPE)
    def mutate(v):
        if field == "declared_confusion_matrix": v[field][0][1] = False
        else: v[field][0]["false_positive"] = False
    change_artifact(doc, "current", mutate)
    assert run(case, doc)["verdict"] == "failed"


def test_subgroup_intervals_known_two_of_two_example_and_missing_group_remain_explicit():
    case = "EVID-SUBGROUP-UNCERTAINTY"
    doc = sample(case, SCOPE)
    m = run(case, doc)["measurements"]
    assert m["group_0_wilson_lower_ppm"] == 342380 and m["group_0_interval_width_ppm"] == 657620
    assert m["group_0_error_rate_ppm"] == 0
    doc["data"]["policy"]["required_groups"].append("absent-private-group")
    change_artifact(doc, "current", lambda v: v["declared_subgroups"].append({"count": 0, "correct": 0}))
    capture = run(case, doc)
    assert capture["verdict"] == "inconclusive" and capture["measurements"]["insufficient_group_count"] == 1
    assert "group_2_error_rate_ppm" not in capture["measurements"] and "group_2_wilson_lower_ppm" not in capture["measurements"]


def test_subgroup_error_and_uncertainty_thresholds_detect_a_real_performance_fault():
    case = "EVID-SUBGROUP-UNCERTAINTY"
    doc = sample(case, SCOPE)
    def worse(v):
        v["results"][0]["actual"] = "no"
        v["declared_accuracy_ppm"] = 750000
        v["declared_subgroups"][0] = dict(count=2, correct=1, error_rate_ppm=500000,
            wilson_lower_ppm=94531, wilson_upper_ppm=905469, interval_width_ppm=810938)
    change_artifact(doc, "current", worse)
    capture = run(case, doc)
    m = capture["measurements"]
    assert capture["verdict"] == "failed" and m["metric_errors"] == 0
    assert m["group_0_error_rate_ppm"] == m["observed_group_gap_ppm"] == 500000
    assert m["group_0_wilson_lower_ppm"] == 94531


def test_small_class_samples_are_distinct_from_undefined_denominators():
    case = "EVID-MULTICLASS-METRICS"
    doc = sample(case, SCOPE)
    doc["data"]["policy"]["minimum_class_support"] = 3
    capture = run(case, doc)
    assert capture["verdict"] == "inconclusive"
    assert capture["measurements"]["insufficient_class_count"] == 3
    assert capture["measurements"]["missing_denominator_count"] == 0
    assert capture["measurements"]["class_0_precision_ppm"] == 1_000_000


@pytest.mark.parametrize("kind", ["dependent", "method", "sample"])
def test_subgroup_unsupported_assumptions_and_small_samples_are_gaps(kind):
    case = "EVID-SUBGROUP-UNCERTAINTY"
    doc = sample(case, SCOPE)
    if kind == "dependent":
        change_artifact(doc, "dataset", lambda v: v["rows"][1].update(subject_id="subject-0"))
        change_artifact(doc, "current", lambda v: v.update(dataset_sha256=doc["artifacts"]["dataset"]["sha256"]))
    elif kind == "method": doc["data"]["policy"]["method"] = "unsupported"
    else: doc["data"]["policy"]["minimum_independent_count"] = 10
    capture = run(case, doc)
    assert capture["verdict"] == "inconclusive"
    if kind != "sample": assert "group_0_wilson_lower_ppm" not in capture["measurements"]


@pytest.mark.parametrize("mutation", [
    lambda d: d["data"]["actions"][0].update(owner=""),
    lambda d: d["data"]["actions"][0].update(source_review_ids=["audit"]),
    lambda d: d["data"]["actions"][0].update(closed_at=(records._instant(d["recorded_at"]) - timedelta(days=2)).isoformat()),
    lambda d: d["data"]["actions"][0].update(status="open", due_at=(records._instant(d["recorded_at"]) - timedelta(seconds=1)).isoformat()),
    lambda d: change_artifact(d, "audit", lambda v: v.update(action_ids=[])),
    lambda d: change_artifact(d, "completion", lambda v: v.update(action_id="other-action")),
])
def test_governance_action_links_owners_deadlines_and_closure_chronology(mutation):
    case = "EVID-GOVERNANCE-ACTIONS"
    doc = sample(case, SCOPE)
    mutation(doc)
    assert run(case, doc)["verdict"] == "failed"


def test_missing_review_and_completion_records_are_gaps_not_document_presence_passes():
    case = "EVID-GOVERNANCE-ACTIONS"
    for key in ("audit", "completion", "proof"):
        doc = sample(case, SCOPE)
        doc["artifacts"].pop(key)
        assert run(case, doc)["verdict"] == "inconclusive"
    case = "EVID-COMPETENCE-COVERAGE"
    doc = sample(case, SCOPE)
    doc["data"]["completions"] = []
    assert run(case, doc)["verdict"] == "inconclusive"


@pytest.mark.parametrize("field,value", [
    ("person_id", "wrong-person"), ("role", "other-role"), ("course_id", "other-course"),
    ("outcome", "attended"), ("valid_until", "expired"),
    ("completed_at", "after_review"),
])
def test_competence_requires_current_demonstrated_outcomes_for_the_active_person_and_role(field, value):
    case = "EVID-COMPETENCE-COVERAGE"
    doc = sample(case, SCOPE)
    def mutate(v):
        replacement = value
        if value == "after_review": replacement = (records._instant(v["reviewed_at"]) + timedelta(seconds=1)).isoformat()
        if value == "expired": replacement = (records._instant(v["reviewed_at"]) - timedelta(seconds=1)).isoformat()
        v[field] = replacement
    change_artifact(doc, "competence", mutate)
    assert run(case, doc)["verdict"] == "failed"


def test_competence_deadline_is_customer_approved_and_need_not_precede_role_start():
    case = "EVID-COMPETENCE-COVERAGE"
    doc = sample(case, SCOPE)
    # An already active employee may complete an approved in-role qualification.
    assignment = doc["data"]["policy"]["required_assignments"][0]
    assignment["active_from"] = (records._instant(assignment["active_from"]) - timedelta(days=2)).isoformat()
    assert run(case, doc)["verdict"] == "passed"


@pytest.mark.parametrize("family,case", LIFECYCLE_RECORD_CASES.items())
def test_normal_worker_standard_json_html_and_every_public_measurement(tmp_path, family, case):
    from tools.operational_scan import registry_rows
    from standard_requirement_report import build_standard_requirement_report
    from report_card.requirements import render_requirements
    w = worker(tmp_path)
    row = next(r for r in registry_rows() if r["test_id"] == family)
    result = w(row, SimpleNamespace(job_id="job-A", scan_id="scan-A", agent_id="agent-A", seed=42))
    assert result.verdict == "pass" and result.target_calls == result.judge_calls == 0 and result.numeric_result is None
    manifest_row = vars(result) | {"test_id": family, "case_id": case, "case_version": row["version_pin"],
                                  "sha256": result.evidence_sha256}
    report = build_standard_requirement_report({}, manifest={"evidence_rows": [manifest_row]})
    public = report["evidence_inventory"]["artifacts"][0]
    assert public["diagnostic_measurements"] == result.diagnostic_measurements
    assert public["case_version"] == LIFECYCLE_VERSION and public["method"] == LIFECYCLE_METHODS[case]
    html = render_requirements({"requirements_evidence": report})
    assert case in html
    assert all(f"{key}: {value}" in html for key, value in result.diagnostic_measurements.items())
    assert "private-" not in json.dumps(report) and "private-" not in html and "content_base64" not in html
    for framework in report["frameworks"]:
        assert framework["mapping_version"].endswith("@8" if framework["framework_id"] in {"eu-ai-act","iso-iec-42001"} else "@2")
        assert bool([c for c in framework["controls"] if c["scan_observations"]]) == any(family in c["candidate_test_ids"] for c in framework["controls"])
        assert all(c["requirement_conclusion"] == "not assessed" for c in framework["controls"])
