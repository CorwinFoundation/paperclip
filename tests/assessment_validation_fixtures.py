"""Controlled software-test records, never default customer evidence."""
import base64
from copy import deepcopy
import hashlib
import json

from tests.test_requirement_evidence_import import source

AT = "2026-10-27T20:00:00+00:00"


def encode(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode()


def blob(value):
    raw = encode(value)
    return {"sha256": hashlib.sha256(raw).hexdigest(), "content_base64": base64.b64encode(raw).decode()}


def change_artifact(document, key, mutate):
    value = json.loads(base64.b64decode(document["artifacts"][key]["content_base64"]))
    mutate(value)
    document["artifacts"][key] = blob(value)


def sample(case, scope):
    target = scope.get("agent_id", scope.get("target_id"))
    snapshot = scope.get("snapshot_sha256", "b" * 64)
    system = {"target_id": target, "snapshot_sha256": snapshot}
    artifacts = {"proof": blob({"private": "private-customer-detail"})}
    native = source() | {"agent_id": target}
    artifacts["native"] = blob(native)
    observation = {"id": "o", "artifact_ref": "native", "snapshot_sha256": snapshot,
        "source_sha256": artifacts["native"]["sha256"], "reviewer": "reviewer", "basis": "controlled native software fixture",
        "probe_set_version": native["version_pin"], "trial_index": 0, "observed_at": "2026-10-27T18:30:00Z",
        **{key: native[key] for key in ("job_id", "agent_id", "test_id", "case_id", "version_pin", "seed")}}
    data = {"system": system}
    if case in {"EVID-RISK-TEST-COVERAGE", "EVID-MITIGATION-RESULT-BINDING"}:
        data.update(observations=[observation],
            risks=[{"id": "r", "status": "closed", "required_observation_ids": ["o"], "treatment_ids": ["t"]}],
            treatments=[{"id": "t", "risk_id": "r", "status": "verified", "verification_observation_ids": ["o"]}])
    elif case in {"EVID-SPLIT-OVERLAP", "EVID-SUBJECT-INDEPENDENCE"}:
        artifacts["train"] = blob({"rows": [{"id": "a", "subject_id": "one", "content": {"text": "training"}}]})
        artifacts["test"] = blob({"rows": [{"id": "b", "subject_id": "two", "content": {"text": "held-out"}}]})
        data.update(datasets=[{"id": "training", "role": "train", "artifact_ref": "train"},
                              {"id": "held-out", "role": "test", "artifact_ref": "test"}],
            policy={"unit_field": "subject_id", "comparison_pairs": [{"left": "training", "right": "held-out"}],
                    "allowed_reference_sha256": []})
    elif case in {"EVID-CLASS-ERROR-RATES", "EVID-STATISTICAL-SUPPORT", "EVID-PERFORMANCE-CLAIM-CONSISTENCY"}:
        artifacts["dataset"] = blob({"rows": [{"id": str(i), "expected": label, "subject_id": "subject-" + str(i)}
                                                 for i, label in enumerate(["yes", "no", "yes", "no"])]})
        current = system | {"model_version": "model-v2", "probe_version": "probe@1", "observed_at": "2026-10-27T18:30:00Z",
            "dataset_sha256": artifacts["dataset"]["sha256"], "conditions_sha256": "f" * 64,
            "results": [{"id": str(i), "actual": label} for i, label in enumerate(["yes", "no", "yes", "no"])],
            "declared_accuracy_ppm": 1_000_000, "declared_false_positive_rate_ppm": 0,
            "declared_false_negative_rate_ppm": 0, "declared_balanced_accuracy_ppm": 1_000_000,
            "declared_wilson_lower_ppm": 510109, "declared_wilson_upper_ppm": 1_000_000}
        artifacts["current"] = blob(current)
        data.update(dataset_ref="dataset", current_ref="current", current_model_version="model-v2", probe_version="probe@1")
        if case == "EVID-CLASS-ERROR-RATES":
            data["policy"] = {"classes": ["yes", "no"], "positive_label": "yes", "abstention_label": "ABSTAIN",
                "abstention_policy": "count_as_incorrect", "maximum_positive_error_ppm": 0,
                "maximum_negative_error_ppm": 0, "minimum_balanced_accuracy_ppm": 1_000_000}
        elif case == "EVID-STATISTICAL-SUPPORT":
            data["policy"] = {"method": "wilson95-binomial", "sampling_assumption": "independent_bernoulli",
                "sampling_basis": "controlled independent fixture subjects", "unit_field": "subject_id",
                "minimum_independent_count": 4, "minimum_accuracy_lower_ppm": 500000, "maximum_interval_width_ppm": 500000}
        else:
            data["claims"] = [system | {"id": "claim", "document_ref": "proof", "location": "page 1, table 1",
                "model_version": "model-v2", "probe_version": "probe@1", "dataset_sha256": artifacts["dataset"]["sha256"],
                "conditions_sha256": "f" * 64, "metric": "exact_match_accuracy_ppm", "population": "all_supplied_rows",
                "value_ppm": 1_000_000}]
            data["policy"] = {"required_claim_ids": ["claim"], "required_limitation_refs": ["proof"], "maximum_overstatement_ppm": 0}
    else:
        system["components"] = {"model": "a" * 64, "knowledge": "d" * 64}
        data.update(observations=[observation],
            baseline_system={"target_id": target, "snapshot_sha256": "c" * 64, "components": {"model": "a" * 64, "knowledge": "e" * 64}},
            changes=[{"id": "change", "component_id": "knowledge", "before_sha256": "e" * 64, "after_sha256": "d" * 64,
                "changed_at": "2026-10-27T17:00:00Z", "affected_risk_ids": ["r"], "required_observation_ids": ["o"]}],
            impact_review_ref="impact", release_record_ref="release")
        artifacts["impact"] = blob(system | {"reviewer": "reviewer", "basis": "controlled impact review",
            "covered_change_ids": ["change"], "covered_risk_ids": ["r"], "reviewed_at": "2026-10-27T18:00:00Z"})
        artifacts["release"] = blob(system | {"approver": "release-owner", "basis": "controlled release fixture",
            "approved_at": "2026-10-27T19:00:00Z", "released_at": "2026-10-27T19:30:00Z",
            "required_observation_ids": ["o"], "exceptions": {}})
    return {"schema": "assessment-record-input/v1", "scope": deepcopy(scope), "source_id": "controlled-software-test",
        "recorded_at": AT, "review": {"reviewer": "test-reviewer", "basis": "controlled software verification only", "reviewed_at": AT},
        "artifacts": artifacts, "data": data}


def contradict(document, case):
    if case in {"EVID-RISK-TEST-COVERAGE", "EVID-MITIGATION-RESULT-BINDING", "EVID-CHANGE-IMPACT-COVERAGE", "EVID-RELEASE-ACCEPTANCE"}:
        document["data"]["observations"][0]["version_pin"] = "wrong-version"
    elif case == "EVID-SPLIT-OVERLAP":
        change_artifact(document, "test", lambda value: value["rows"][0].update(content={"text": "training"}))
    elif case == "EVID-SUBJECT-INDEPENDENCE":
        change_artifact(document, "test", lambda value: value["rows"][0].update(subject_id="one"))
    elif case == "EVID-CLASS-ERROR-RATES":
        change_artifact(document, "current", lambda value: value.update(declared_false_positive_rate_ppm=1))
    elif case == "EVID-STATISTICAL-SUPPORT":
        change_artifact(document, "current", lambda value: value.update(declared_wilson_lower_ppm=900000))
    else:
        document["data"]["claims"][0]["snapshot_sha256"] = "0" * 64
