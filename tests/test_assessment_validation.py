"""Evidence validation against independently specified faults and acceptance rules."""
import base64
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from evals.oss_pilots import assessment_records as records
from tools.assessment_test_catalog import VALIDATION_METHODS, VALIDATION_RECORD_CASES, VALIDATION_VERSION
from tests.assessment_validation_fixtures import sample, encode, blob, change_artifact, contradict
from tests.test_assessment_records import SCOPE, sample as legacy_sample


def run(case, document=None):
    document = sample(case, SCOPE) if document is None else document
    return records.run_case(case, SimpleNamespace(read=lambda _: encode(document)), scope=SCOPE)


def native_verdict(document, verdict):
    change_artifact(document, "native", lambda v: v.update(verdict=verdict))
    document["data"]["observations"][0]["source_sha256"] = document["artifacts"]["native"]["sha256"]


def dataset_change(document, mutate):
    change_artifact(document, "dataset", mutate)
    change_artifact(document, "current", lambda v: v.update(dataset_sha256=document["artifacts"]["dataset"]["sha256"]))


@pytest.mark.parametrize("case", VALIDATION_METHODS)
def test_new_case_originals_and_version_are_reproducible_and_claims_cannot_be_substituted(case):
    document = sample(case, SCOPE)
    capture = run(case, document)
    assert capture["verdict"] == records.verify(case, capture, scope=SCOPE) == "passed"
    assert capture["capture_version"] == records.case_version(case) == VALIDATION_VERSION
    assert base64.b64decode(capture["input_document"]["content_base64"]) == encode(document)
    changed = deepcopy(capture)
    changed["measurements"]["record_count"] += 1
    with pytest.raises(ValueError): records.verify(case, changed, scope=SCOPE)
    changed = deepcopy(capture)
    changed["capture_version"] = records.VERSION
    with pytest.raises(ValueError): records.verify(case, changed, scope=SCOPE)


@pytest.mark.parametrize("case", VALIDATION_METHODS)
def test_new_checks_detect_their_specific_faults_and_keep_failed_originals(case):
    document = sample(case, SCOPE)
    contradict(document, case)
    capture = run(case, document)
    assert capture["verdict"] == records.verify(case, capture, scope=SCOPE) == "failed"
    assert capture["measurements"]["error_count"] > 0
    assert base64.b64decode(capture["input_document"]["content_base64"]) == encode(document)


@pytest.mark.parametrize("case", VALIDATION_METHODS)
def test_absent_and_foreign_system_inputs_never_become_passes_or_retain_private_bytes(case):
    assert records.run_case(case, None, scope=SCOPE)["verdict"] == "inconclusive"
    document = sample(case, SCOPE)
    document["data"]["system"]["target_id"] = "foreign-private-system"
    capture = run(case, document)
    assert capture["verdict"] == "inconclusive" and "input_document" not in capture
    assert "foreign" not in json.dumps(capture)


def test_legacy_capture_version_and_exact_metric_shape_still_verify():
    case = "EVID-EVAL-RESULTS"
    capture = run(case, legacy_sample(case))
    assert capture["capture_version"] == "assessment-record-checks@1"
    assert set(capture["measurements"]) == records.LEGACY_METRICS
    assert records.verify(case, capture, scope=SCOPE) == "passed"


def test_risk_coverage_preserves_failed_results_and_missing_required_cases():
    document = sample("EVID-RISK-TEST-COVERAGE", SCOPE)
    native_verdict(document, "fail")
    capture = run("EVID-RISK-TEST-COVERAGE", document)
    assert capture["verdict"] == "passed"  # Complete coverage is not mitigation effectiveness.
    assert capture["measurements"]["failed_required_case_count"] == 1
    document["data"]["risks"][0]["required_observation_ids"].append("missing")
    capture = run("EVID-RISK-TEST-COVERAGE", document)
    assert capture["verdict"] == "failed" and capture["measurements"]["missing_case_count"] == 1
    assert capture["measurements"]["uncovered_risk_count"] == 1


@pytest.mark.parametrize("mutation", [
    lambda d: d["data"]["observations"][0].update(source_sha256="0" * 64),
    lambda d: d["data"]["observations"][0].update(reviewer=""),
    lambda d: d["data"]["observations"][0].update(snapshot_sha256="0" * 64),
    lambda d: d["data"]["observations"].append(d["data"]["observations"][0] | {"id": "relabelled"}),
    lambda d: change_artifact(d, "native", lambda v: v.update(execution_mode="offline_fixture")),
    lambda d: change_artifact(d, "native", lambda v: v.update(budget={"case_delta": {"target_calls": 0}})),
])
def test_native_result_binding_requires_original_measured_identity_and_review(mutation):
    case = "EVID-RISK-TEST-COVERAGE"
    document = sample(case, SCOPE)
    mutation(document)
    assert run(case, document)["verdict"] == "failed"


def test_foreign_native_source_is_not_retained():
    case = "EVID-RISK-TEST-COVERAGE"
    document = sample(case, SCOPE)
    change_artifact(document, "native", lambda v: v.update(agent_id="foreign", responses=["foreign secret"]))
    capture = run(case, document)
    assert capture["verdict"] == "inconclusive" and "input_document" not in capture


def test_foreign_native_tenant_cannot_be_rebound_by_a_matching_wrapper():
    case = "EVID-RISK-TEST-COVERAGE"
    document = sample(case, SCOPE)
    document["data"]["observations"][0]["capture_scope"] = {"tenant_id": "foreign", "scan_id": "old", "agent_id": "agent-A"}
    capture = run(case, document)
    assert capture["verdict"] == "inconclusive" and "input_document" not in capture


def test_mitigation_cannot_close_on_a_failed_test_and_acceptance_stays_explicit():
    case = "EVID-MITIGATION-RESULT-BINDING"
    document = sample(case, SCOPE)
    native_verdict(document, "fail")
    assert run(case, document)["verdict"] == "failed"
    risk = document["data"]["risks"][0]
    risk.update(status="accepted", exception_ref="exception")
    document["data"]["treatments"][0]["status"] = "open"
    document["artifacts"]["exception"] = blob(document["data"]["system"] | {
        "context_id": "r", "observation_ids": ["o"], "reviewer": "risk-owner", "basis": "explicit residual-risk decision",
        "approved_at": "2026-10-27T19:00:00Z", "valid_until": "2026-10-28T20:00:00Z"})
    capture = run(case, document)
    assert capture["verdict"] == "passed" and capture["measurements"]["exception_count"] == 1
    assert capture["measurements"]["failed_required_case_count"] > 0
    change_artifact(document, "exception", lambda v: v.update(valid_until="2026-10-27T19:00:00Z"))
    assert run(case, document)["verdict"] == "failed"


def test_split_overlap_uses_content_not_row_ids_and_requires_a_reviewed_exception():
    case = "EVID-SPLIT-OVERLAP"
    document = sample(case, SCOPE)
    change_artifact(document, "test", lambda v: v["rows"][0].update(content={"text": "training"}))
    capture = run(case, document)
    assert capture["verdict"] == "failed" and capture["measurements"]["overlap_count"] == 1
    digest = hashlib.sha256(b'{"text":"training"}').hexdigest()
    document["data"]["policy"]["allowed_reference_sha256"] = [digest]
    capture = run(case, document)
    assert capture["verdict"] == "passed" and capture["measurements"]["allowed_reference_overlap_count"] == 1
    assert capture["measurements"]["overlap_count"] == 0


def test_different_events_for_the_same_subject_are_not_independent_partitions():
    case = "EVID-SUBJECT-INDEPENDENCE"
    document = sample(case, SCOPE)
    change_artifact(document, "test", lambda v: v["rows"][0].update(id="new-event", subject_id="one"))
    capture = run(case, document)
    assert capture["verdict"] == "failed" and capture["measurements"]["shared_subject_count"] == 1
    assert capture["measurements"]["overlap_count"] == 0


@pytest.mark.parametrize("mutation", [
    lambda d: d["data"]["policy"].update(comparison_pairs=[]),
    lambda d: d["data"]["policy"].update(comparison_pairs=[{"left": "training", "right": "missing"}]),
    lambda d: d["data"]["policy"]["comparison_pairs"].append({"left": "held-out", "right": "training"}),
    lambda d: d["data"]["policy"].update(unit_field=""),
])
def test_partition_method_requires_explicit_valid_comparisons_and_independence_policy(mutation):
    case = "EVID-SPLIT-OVERLAP"
    document = sample(case, SCOPE)
    mutation(document)
    assert run(case, document)["verdict"] == "failed"


def test_aggregate_accuracy_cannot_hide_missing_positive_outcomes():
    case = "EVID-CLASS-ERROR-RATES"
    document = sample(case, SCOPE)
    def majority(v):
        for row in v["results"]: row["actual"] = "no"
        v.update(declared_accuracy_ppm=500000, declared_false_positive_rate_ppm=0,
                 declared_false_negative_rate_ppm=1_000_000, declared_balanced_accuracy_ppm=500000)
    change_artifact(document, "current", majority)
    capture = run(case, document)
    metrics = capture["measurements"]
    assert capture["verdict"] == "failed" and metrics["metric_errors"] == 0
    assert metrics["false_negative_count"] == 2 and metrics["positive_error_rate_ppm"] == 1_000_000
    assert metrics["balanced_accuracy_ppm"] == 500000


def test_abstentions_remain_in_error_and_accuracy_denominators():
    case = "EVID-CLASS-ERROR-RATES"
    document = sample(case, SCOPE)
    def abstain(v):
        v["results"][0]["actual"] = "ABSTAIN"
        v.update(declared_accuracy_ppm=750000, declared_balanced_accuracy_ppm=750000)
    change_artifact(document, "current", abstain)
    capture = run(case, document)
    assert capture["verdict"] == "failed"
    assert capture["measurements"]["abstention_count"] == 1
    assert capture["measurements"]["positive_error_rate_ppm"] == 500000
    document["data"]["policy"].update(maximum_positive_error_ppm=500000, minimum_balanced_accuracy_ppm=750000)
    assert run(case, document)["verdict"] == "passed"
    document["data"]["policy"]["abstention_policy"] = "reject"
    assert run(case, document)["verdict"] == "failed"


def test_missing_class_denominator_is_inconclusive_not_a_zero_error_pass():
    case = "EVID-CLASS-ERROR-RATES"
    document = sample(case, SCOPE)
    dataset_change(document, lambda v: [row.update(expected="yes") for row in v["rows"]])
    change_artifact(document, "current", lambda v: [row.update(actual="yes") for row in v["results"]])
    capture = run(case, document)
    assert capture["verdict"] == "inconclusive" and capture["measurements"]["negative_count"] == 0
    assert "false_positive_rate_ppm" not in capture["measurements"]


def test_multiclass_policy_is_an_explicit_unsupported_gap_not_a_false_model_failure():
    case = "EVID-CLASS-ERROR-RATES"
    document = sample(case, SCOPE)
    document["data"]["policy"]["classes"].append("other")
    capture = run(case, document)
    assert capture["verdict"] == "inconclusive" and capture["measurements"]["unsupported_class_map_count"] == 1
    assert "false_positive_rate_ppm" not in capture["measurements"]


def test_wilson_bounds_known_four_of_four_example_and_approved_sample_minimum():
    case = "EVID-STATISTICAL-SUPPORT"
    document = sample(case, SCOPE)
    capture = run(case, document)
    assert capture["measurements"]["wilson_lower_ppm"] == 510109
    assert capture["measurements"]["wilson_upper_ppm"] == 1_000_000
    document["data"]["policy"]["minimum_independent_count"] = 10
    capture = run(case, document)
    assert capture["verdict"] == "failed" and capture["measurements"]["metric_errors"] == 0


@pytest.mark.parametrize("unsupported", ["dependency", "method", "sampling_basis"])
def test_unsupported_statistical_assumptions_and_repeated_subjects_are_not_certified(unsupported):
    case = "EVID-STATISTICAL-SUPPORT"
    document = sample(case, SCOPE)
    if unsupported == "dependency":
        dataset_change(document, lambda v: v["rows"][1].update(subject_id="subject-0"))
    else:
        document["data"]["policy"][unsupported] = ""
    capture = run(case, document)
    assert capture["verdict"] == "inconclusive"
    assert "wilson_lower_ppm" not in capture["measurements"]


def test_claims_must_match_population_and_conditions_not_just_a_number():
    case = "EVID-PERFORMANCE-CLAIM-CONSISTENCY"
    document = sample(case, SCOPE)
    claim = document["data"]["claims"][0]
    claim["conditions_sha256"] = "e" * 64
    assert run(case, document)["verdict"] == "failed"
    claim["conditions_sha256"] = "f" * 64
    claim["population"] = "unmeasured-subgroup"
    assert run(case, document)["verdict"] == "inconclusive"
    claim["population"] = "all_supplied_rows"
    document["data"]["policy"]["required_claim_ids"].append("missing-claim")
    assert run(case, document)["verdict"] == "failed"


def test_claim_overstatement_is_recomputed_from_original_predictions():
    case = "EVID-PERFORMANCE-CLAIM-CONSISTENCY"
    document = sample(case, SCOPE)
    def inaccurate(v):
        v["results"][0]["actual"] = "no"
        v["declared_accuracy_ppm"] = 750000
    change_artifact(document, "current", inaccurate)
    capture = run(case, document)
    assert capture["verdict"] == "failed" and capture["measurements"]["metric_errors"] == 0
    document["data"]["claims"][0]["value_ppm"] = 750000
    assert run(case, document)["verdict"] == "passed"


def test_changed_retrieval_component_requires_impact_and_current_regression_evidence():
    case = "EVID-CHANGE-IMPACT-COVERAGE"
    document = sample(case, SCOPE)
    document["data"]["system"]["components"]["tool"] = "f" * 64
    capture = run(case, document)
    assert capture["verdict"] == "failed" and capture["measurements"]["uncovered_change_count"] == 1
    document = sample(case, SCOPE)
    document["data"]["observations"][0]["observed_at"] = "2026-10-27T16:00:00Z"
    assert run(case, document)["verdict"] == "failed"
    document = sample(case, SCOPE)
    change_artifact(document, "impact", lambda v: v.update(covered_change_ids=["other-change"]))
    assert run(case, document)["verdict"] == "failed"


def test_changed_component_risks_must_have_reviewed_impact_coverage():
    case = "EVID-CHANGE-IMPACT-COVERAGE"
    document = sample(case, SCOPE)
    document["data"]["changes"][0]["affected_risk_ids"] = ["unreviewed-risk"]
    assert run(case, document)["verdict"] == "failed"


def test_component_additions_require_explicit_null_before_binding():
    case = "EVID-CHANGE-IMPACT-COVERAGE"
    document = sample(case, SCOPE)
    document["data"]["baseline_system"]["components"].pop("knowledge")
    change = document["data"]["changes"][0]
    change.pop("before_sha256")
    assert run(case, document)["verdict"] == "failed"
    change["before_sha256"] = None
    assert run(case, document)["verdict"] == "passed"


@pytest.mark.parametrize("field,value", [
    ("approved_at", "2026-10-27T19:45:00Z"),
    ("approved_at", "2026-10-27T18:15:00Z"),
    ("snapshot_sha256", "0" * 64),
    ("required_observation_ids", ["different-observation"]),
    ("approver", ""),
])
def test_release_approval_binds_exact_snapshot_outcomes_and_order(field, value):
    case = "EVID-RELEASE-ACCEPTANCE"
    document = sample(case, SCOPE)
    change_artifact(document, "release", lambda v: v.update({field: value}))
    assert run(case, document)["verdict"] == "failed"


def test_release_exception_preserves_the_original_failed_result():
    case = "EVID-RELEASE-ACCEPTANCE"
    document = sample(case, SCOPE)
    native_verdict(document, "fail")
    assert run(case, document)["verdict"] == "failed"
    document["artifacts"]["exception"] = blob(document["data"]["system"] | {
        "context_id": "release", "observation_ids": ["o"], "reviewer": "owner", "basis": "explicit reviewed exception",
        "approved_at": "2026-10-27T18:45:00Z", "valid_until": "2026-10-27T20:00:00Z"})
    change_artifact(document, "release", lambda v: v.update(exceptions={"o": "exception"}))
    capture = run(case, document)
    assert capture["verdict"] == "passed" and capture["measurements"]["exception_count"] == 1
    assert capture["measurements"]["failed_required_case_count"] == 1
