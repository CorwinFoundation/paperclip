"""Explicit synthetic qualification records, never customer defaults."""
import base64
from datetime import datetime, timedelta, timezone
import json

from tests.assessment_validation_fixtures import sample as validation_sample, blob, change_artifact


def _current_times(doc):
    # Keep operational validity relative to the actual capture time without
    # changing the independent numerical examples. Persisted captures replay
    # against their original evaluated_at, never against a moving clock.
    shift = datetime.now(timezone.utc) - datetime.fromisoformat(doc["recorded_at"]) - timedelta(minutes=5)
    def move(value):
        if isinstance(value, list): return [move(v) for v in value]
        if isinstance(value, dict): return {k: move(v) for k, v in value.items()}
        if isinstance(value, str) and len(value) > 19 and value[4:5] == "-" and value[10:11] == "T":
            try: return (datetime.fromisoformat(value.replace("Z", "+00:00")) + shift).isoformat()
            except ValueError: pass
        return value
    doc = move(doc)
    for key, artifact in doc["artifacts"].items():
        doc["artifacts"][key] = blob(move(json.loads(base64.b64decode(artifact["content_base64"]))))
    return doc


def sample(case, scope):
    doc = validation_sample("EVID-STATISTICAL-SUPPORT", scope)
    data, artifacts = doc["data"], doc["artifacts"]
    system = data["system"]
    if case in {"EVID-MONITORING-COVERAGE", "EVID-MONITORING-TREND"}:
        data["policy"] = {"start_at": "2026-10-27T16:00:00Z", "end_at": "2026-10-27T20:00:00Z",
            "cadence_seconds": 7200, "maximum_age_seconds": 7200, "minimum_window_count": 4,
            "maximum_drop_ppm": 0}
        artifacts["prior"] = artifacts["current"]
        change_artifact(doc, "prior", lambda v: v.update(observed_at="2026-10-27T17:00:00Z"))
        data["windows"] = [{"id": "first", "index": 0, "evaluation_ref": "prior"},
                           {"id": "second", "index": 1, "evaluation_ref": "current"}]
    elif case == "EVID-MULTICLASS-METRICS":
        labels = ["private-A", "private-B", "private-C"] * 2
        artifacts["dataset"] = blob({"rows": [{"id": str(i), "expected": label} for i, label in enumerate(labels)]})
        declared = [dict(support=2, predicted=2, true_positive=2, false_positive=0, false_negative=0,
                         abstention=0, precision_ppm=1_000_000, recall_ppm=1_000_000, f1_ppm=1_000_000) for _ in range(3)]
        change_artifact(doc, "current", lambda v: v.update(dataset_sha256=artifacts["dataset"]["sha256"],
            results=[{"id": str(i), "actual": label} for i, label in enumerate(labels)],
            declared_multiclass=declared, declared_confusion_matrix=[[2, 0, 0], [0, 2, 0], [0, 0, 2]]))
        data["policy"] = {"classes": labels[:3], "abstention_label": "private-abstain",
            "abstention_policy": "count_as_incorrect", "minimum_class_support": 2,
            "minimum_precision_ppm": 1_000_000, "minimum_recall_ppm": 1_000_000,
            "minimum_f1_ppm": 1_000_000, "maximum_abstention_ppm": 0}
    elif case == "EVID-SUBGROUP-UNCERTAINTY":
        dataset = {"rows": [{"id": str(i), "expected": label, "subject_id": "subject-" + str(i),
                             "group": "private-group-" + str(i % 2)} for i, label in enumerate(["yes", "no", "yes", "no"])]}
        artifacts["dataset"] = blob(dataset)
        change_artifact(doc, "current", lambda v: v.update(dataset_sha256=artifacts["dataset"]["sha256"],
            declared_subgroups=[dict(count=2, correct=2, error_rate_ppm=0, wilson_lower_ppm=342380,
                                    wilson_upper_ppm=1_000_000, interval_width_ppm=657620) for _ in range(2)]))
        data["policy"] = {"required_groups": ["private-group-0", "private-group-1"], "unit_field": "subject_id",
            "method": "wilson95-binomial", "sampling_assumption": "independent_bernoulli",
            "sampling_basis": "controlled independent software fixtures", "minimum_independent_count": 2,
            "minimum_group_lower_ppm": 300000, "maximum_interval_width_ppm": 700000, "maximum_gap_ppm": 0}
    elif case == "EVID-GOVERNANCE-ACTIONS":
        data["policy"] = {"required_reviews": [
            {"id": "audit", "kind": "audit", "due_at": "2026-10-27T19:00:00Z", "required_action_ids": ["action"]},
            {"id": "management", "kind": "management_review", "due_at": "2026-10-27T19:00:00Z", "required_action_ids": ["action"]}]}
        data["reviews"] = [{"id": "audit", "artifact_ref": "audit"}, {"id": "management", "artifact_ref": "management"}]
        for key, kind in [("audit", "audit"), ("management", "management_review")]:
            artifacts[key] = blob(system | dict(id=key, kind=kind, reviewer="private-reviewer", basis="reviewed original record",
                reviewed_at="2026-10-27T17:00:00Z", action_ids=["action"]))
        data["actions"] = [dict(id="action", owner="private-owner", status="closed", due_at="2026-10-27T20:00:00Z",
            source_review_ids=["audit", "management"], verification_ref="completion", closed_at="2026-10-27T19:00:00Z")]
        artifacts["completion"] = blob(system | dict(action_id="action", outcome="effective", reviewer="private-reviewer",
            basis="reviewed completion original", reviewed_at="2026-10-27T18:00:00Z", evidence_ref="proof"))
    elif case == "EVID-COMPETENCE-COVERAGE":
        data["policy"] = {"required_assignments": [dict(id="assignment", person_id="private-person", role="operator",
            active_from="2026-10-27T18:00:00Z", active_until="2026-11-27T20:00:00Z",
            competence_required_by="2026-10-27T18:00:00Z", required_course_ids=["oversight"])]}
        data["completions"] = [dict(id="completion", assignment_id="assignment", course_id="oversight", artifact_ref="competence")]
        artifacts["competence"] = blob(system | dict(person_id="private-person", role="operator", course_id="oversight",
            outcome="demonstrated", completed_at="2026-10-27T16:00:00Z", reviewed_at="2026-10-27T17:00:00Z",
            valid_until="2026-11-27T20:00:00Z", reviewer="private-reviewer", basis="reviewed competence assessment", evidence_ref="proof"))
    else:
        raise ValueError(case)
    return _current_times(doc)


def contradict(doc, case):
    if case.startswith("EVID-MONITORING"):
        change_artifact(doc, "current", lambda v: v.update(conditions_sha256="0" * 64))
    elif case == "EVID-MULTICLASS-METRICS":
        change_artifact(doc, "current", lambda v: v["declared_multiclass"][0].update(recall_ppm=0))
    elif case == "EVID-SUBGROUP-UNCERTAINTY":
        change_artifact(doc, "current", lambda v: v["declared_subgroups"][0].update(wilson_lower_ppm=900000))
    elif case == "EVID-GOVERNANCE-ACTIONS":
        change_artifact(doc, "completion", lambda v: v.update(outcome="ineffective"))
    else:
        change_artifact(doc, "competence", lambda v: v.update(outcome="attended"))
