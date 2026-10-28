"""Bounded reconciliation of supplied lifecycle records; no external acquisition.

Policies, completeness, reviewer identities and statistical assumptions are reviewed
declarations. These checks make no model calls and no conformity determination.
Public metrics use policy-order ordinals, never private class/group/person names.
"""
import hashlib
import math

from evals.oss_pilots import assessment_records as r, assessment_validation as v
from tools.binomial_statistics import wilson


def initial_metrics():
    return dict.fromkeys(("record_count", "compared_record_count", "error_count", "identity_errors",
                          "reference_errors", "timestamp_errors", "metric_errors", "policy_errors"), 0)


def _count(value, *, maximum=r.MAX_ROWS):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("bounded positive reviewed count required")
    return value


def _names(value):
    values = v._strings(value)
    if len(values) > 16:
        raise ValueError("at most sixteen reviewed classes or groups")
    return values


def _empty_rows(value, metrics):
    return {} if value == [] else r._index(value, metrics)


def _evaluation(data, artifacts, system, metrics, scope, now):
    # Shared labelled datasets may omit a target declaration. An explicitly
    # foreign declaration must not be rebound by a matching evaluation wrapper.
    r._data_json(artifacts[data["dataset_ref"]], target=system["target_id"])
    return v._evaluation(data, artifacts, system, metrics, scope, now)


def _declaration(declared, measured):
    return (isinstance(declared, dict) and set(declared) == set(measured)
            and all(type(value) is int and value >= 0 for value in declared.values())
            and declared == measured)


def _monitoring(case, data, artifacts, system, metrics, scope, now, as_of):
    policy = data["policy"]
    start, end = r._instant(policy["start_at"]), r._instant(policy["end_at"])
    cadence = _count(policy["cadence_seconds"], maximum=31_536_000)
    maximum_age = _count(policy["maximum_age_seconds"], maximum=31_536_000)
    minimum = _count(policy["minimum_window_count"])
    span = (end - start).total_seconds()
    if start >= end or end > now or span % cadence or not 1 <= span // cadence <= 366:
        raise ValueError("bounded complete elapsed monitoring schedule required")
    expected = int(span // cadence)
    metrics.update(required_window_count=expected, observed_window_count=0, missing_window_count=0,
                   stale_window_count=0, insufficient_window_count=0, maximum_age_seconds=maximum_age,
                   minimum_window_count=minimum, maximum_drop_ppm=r._ppm(policy["maximum_drop_ppm"]),
                   maximum_observed_drop_ppm=0, regressed_window_count=0)
    windows = _empty_rows(data.get("windows"), metrics)
    if len(windows) > 366:
        raise ValueError("monitoring window bound exceeded")
    if any(type(w.get("index")) is not int for w in windows.values()):
        raise ValueError("integer monitoring slot required")
    seen, results, sources, conditions = set(), {}, set(), set()
    for window in sorted(windows.values(), key=lambda w: w["index"]):
        slot = window["index"]
        if type(slot) is not int or not 0 <= slot < expected or slot in seen:
            raise ValueError("unique approved monitoring slot required")
        seen.add(slot)
        ref = window["evaluation_ref"]
        digest = hashlib.sha256(artifacts[ref]).hexdigest()
        metrics["identity_errors"] += int(digest in sources)
        sources.add(digest)
        selected = dict(data, current_ref=ref)
        labels, correct, current, _ = _evaluation(selected, artifacts, system, metrics, scope, now)
        conditions.add(v._digest(current["conditions_sha256"]))
        observed = r._instant(current["observed_at"])
        metrics["timestamp_errors"] += int(not slot * cadence <= (observed - start).total_seconds() < (slot + 1) * cadence)
        metrics["insufficient_window_count"] += int(len(labels) < minimum)
        results[slot] = sum(correct.values()) * 1_000_000 // len(labels)
        metrics[f"window_{slot}_count"] = len(labels)
        metrics[f"window_{slot}_accuracy_ppm"] = results[slot]
    metrics.update(record_count=len(windows), compared_record_count=len(windows), observed_window_count=len(seen),
                   missing_window_count=expected - len(seen))
    if windows:
        latest = max(r._instant(r._data_json(artifacts[w["evaluation_ref"]])["observed_at"]) for w in windows.values())
        metrics["stale_window_count"] = int((as_of - latest).total_seconds() > maximum_age)
    metrics["policy_errors"] += metrics["stale_window_count"]
    metrics["identity_errors"] += int(len(conditions) > 1)
    if case == "EVID-MONITORING-TREND":
        # A missing first period cannot silently replace the approved baseline.
        if 0 not in results or len(results) < 2:
            return False
        baseline = results[0]
        for slot, accuracy in results.items():
            metrics[f"window_{slot}_drop_ppm"] = max(0, baseline - accuracy)
        drops = [max(0, baseline - value) for key, value in results.items() if key != 0]
        metrics.update(baseline_accuracy_ppm=baseline, maximum_observed_drop_ppm=max(drops),
                       regressed_window_count=sum(drop > metrics["maximum_drop_ppm"] for drop in drops))
        metrics["policy_errors"] += metrics["regressed_window_count"]
    return not (metrics["missing_window_count"] or metrics["insufficient_window_count"])


def _multiclass(data, artifacts, system, metrics, scope, now):
    labels, _, current, _ = _evaluation(data, artifacts, system, metrics, scope, now)
    policy = data["policy"]
    classes = _names(policy["classes"])
    abstain = policy["abstention_label"]
    if len(classes) < 2 or not r._text(abstain) or abstain in classes or policy["abstention_policy"] not in {"reject", "count_as_incorrect"}:
        raise ValueError("explicit multiclass and abstention policy required")
    minimum = _count(policy["minimum_class_support"])
    metrics.update(class_count=len(classes), record_count=len(labels), compared_record_count=len(labels),
                   abstention_count=0, missing_denominator_count=0, insufficient_class_count=0, minimum_class_support=minimum)
    for key in ("minimum_precision_ppm", "minimum_recall_ppm", "minimum_f1_ppm", "maximum_abstention_ppm"):
        metrics[key] = r._ppm(policy[key])
    lookup = {label: i for i, label in enumerate(classes)}
    matrix = [[0] * len(classes) for _ in classes]
    abstentions = [0] * len(classes)
    predictions = r._index(current["results"], metrics)
    for key, label in labels.items():
        actual, predicted = label["expected"], predictions[key]["actual"]
        if actual not in lookup or predicted not in [*classes, abstain]:
            raise ValueError("unmapped observed class")
        i = lookup[actual]
        if predicted == abstain:
            abstentions[i] += 1
        else:
            matrix[i][lookup[predicted]] += 1
    metrics["abstention_count"] = sum(abstentions)
    metrics["abstention_rate_ppm"] = sum(abstentions) * 1_000_000 // len(labels)
    metrics["policy_errors"] += int(metrics["abstention_rate_ppm"] > metrics["maximum_abstention_ppm"]
        or (policy["abstention_policy"] == "reject" and sum(abstentions) > 0))
    declared = current["declared_multiclass"]
    if not isinstance(declared, list) or len(declared) != len(classes):
        raise ValueError("per-class declared measurements required in policy order")
    meaningful = True
    for i in range(len(classes)):
        support = sum(matrix[i]) + abstentions[i]
        predicted = sum(row[i] for row in matrix)
        tp = matrix[i][i]
        counts = dict(support=support, predicted=predicted, true_positive=tp,
                      false_positive=predicted - tp, false_negative=support - tp, abstention=abstentions[i])
        rates = {}
        if predicted: rates["precision_ppm"] = tp * 1_000_000 // predicted
        if support: rates["recall_ppm"] = tp * 1_000_000 // support
        if support + predicted: rates["f1_ppm"] = 2 * tp * 1_000_000 // (support + predicted)
        if not predicted or not support:
            metrics["missing_denominator_count"] += 1
            meaningful = False
        if support < minimum:
            metrics["insufficient_class_count"] += 1
            meaningful = False
        metrics["metric_errors"] += int(not _declaration(declared[i], counts | rates))
        for key, value in (counts | rates).items(): metrics[f"class_{i}_{key}"] = value
        for j, value in enumerate(matrix[i]): metrics[f"confusion_{i}_{j}"] = value
        for name in ("precision", "recall", "f1"):
            if name + "_ppm" in rates:
                metrics["policy_errors"] += int(rates[name + "_ppm"] < metrics[f"minimum_{name}_ppm"])
    claimed_matrix = current.get("declared_confusion_matrix")
    if (not isinstance(claimed_matrix, list) or len(claimed_matrix) != len(classes)
            or any(not isinstance(row, list) or len(row) != len(classes)
                   or any(type(value) is not int or value < 0 for value in row) for row in claimed_matrix)
            or claimed_matrix != matrix):
        metrics["metric_errors"] += 1
    return meaningful


def _subgroups(data, artifacts, system, metrics, scope, now):
    labels, correct, current, _ = _evaluation(data, artifacts, system, metrics, scope, now)
    policy = data["policy"]
    groups = _names(policy["required_groups"])
    unit = policy["unit_field"]
    minimum = _count(policy["minimum_independent_count"])
    metrics.update(record_count=len(labels), compared_record_count=len(labels), required_group_count=len(groups),
                   insufficient_group_count=0, dependent_record_count=0, unsupported_assumption_count=0,
                   minimum_independent_count=minimum)
    for key in ("minimum_group_lower_ppm", "maximum_interval_width_ppm", "maximum_gap_ppm"):
        metrics[key] = r._ppm(policy[key])
    if not r._text(unit) or not all(r._text(row.get(unit)) and r._text(row.get("group")) for row in labels.values()):
        raise ValueError("reviewed group and independence keys required")
    metrics["dependent_record_count"] = len(labels) - len({row[unit] for row in labels.values()})
    if (policy.get("method") != "wilson95-binomial" or policy.get("sampling_assumption") != "independent_bernoulli"
            or not r._text(policy.get("sampling_basis")) or metrics["dependent_record_count"]):
        metrics["unsupported_assumption_count"] = 1
        return False
    declared = current["declared_subgroups"]
    if not isinstance(declared, list) or len(declared) != len(groups):
        raise ValueError("group declarations required in policy order")
    rates = []
    for i, group in enumerate(groups):
        ids = [key for key, row in labels.items() if row["group"] == group]
        count, successes = len(ids), sum(correct[key] for key in ids)
        values = {"count": count, "correct": successes}
        if count:
            lower, upper = wilson(successes, count)
            values.update(error_rate_ppm=(count - successes) * 1_000_000 // count,
                          wilson_lower_ppm=max(0, math.floor(lower * 1_000_000)),
                          wilson_upper_ppm=min(1_000_000, math.ceil(upper * 1_000_000)))
            values["interval_width_ppm"] = values["wilson_upper_ppm"] - values["wilson_lower_ppm"]
            rates.append(values["error_rate_ppm"])
        if count < minimum:
            metrics["insufficient_group_count"] += 1
        else:
            metrics["policy_errors"] += int(values["wilson_lower_ppm"] < metrics["minimum_group_lower_ppm"]
                or values["interval_width_ppm"] > metrics["maximum_interval_width_ppm"])
        metrics["metric_errors"] += int(not _declaration(declared[i], values))
        for key, value in values.items(): metrics[f"group_{i}_{key}"] = value
    if rates:
        metrics["observed_group_gap_ppm"] = max(rates) - min(rates)
        metrics["policy_errors"] += int(metrics["observed_group_gap_ppm"] > metrics["maximum_gap_ppm"])
    return metrics["insufficient_group_count"] == 0


def _reviewed(ref, artifacts, system, metrics, now):
    body = v._scoped(artifacts[ref], system, metrics)
    metrics["policy_errors"] += int(not all(r._text(body.get(key)) for key in ("reviewer", "basis")))
    metrics["timestamp_errors"] += int(r._instant(body["reviewed_at"]) > now)
    return body


def _governance(data, artifacts, system, metrics, now, as_of):
    required = r._index(data["policy"]["required_reviews"], metrics)
    reviews = _empty_rows(data["reviews"], metrics)
    actions = _empty_rows(data["actions"], metrics)
    metrics.update(record_count=len(reviews), required_review_count=len(required), missing_review_count=0,
                   review_count=len(reviews), action_count=len(actions), open_action_count=0,
                   overdue_action_count=0, closed_action_count=0, missing_evidence_count=0)
    linked, times, origins_by_action = set(), {}, {}
    total_links = 0
    metrics["reference_errors"] += len(set(reviews) - set(required))
    for key, requirement in required.items():
        if requirement["kind"] not in {"audit", "management_review"}:
            raise ValueError("reviewed audit or management review schedule required")
        due = r._instant(requirement["due_at"])
        if due > now:
            raise ValueError("required review must be due by assessment time")
        ids = v._strings(requirement["required_action_ids"], empty=True)
        total_links += len(ids)
        if total_links > r.MAX_ROWS:
            raise ValueError("total review-action reference bound exceeded")
        linked.update(ids)
        for aid in ids: origins_by_action.setdefault(aid, set()).add(key)
        if key not in reviews or reviews[key].get("artifact_ref") not in artifacts:
            metrics["missing_review_count"] += 1
            continue
        body = _reviewed(reviews[key]["artifact_ref"], artifacts, system, metrics, now)
        at = r._instant(body["reviewed_at"])
        times[key] = at
        metrics["identity_errors"] += int(body.get("id") != key or body.get("kind") != requirement["kind"])
        metrics["timestamp_errors"] += int(at > due)
        metrics["reference_errors"] += len(set(ids) ^ set(v._strings(body["action_ids"], empty=True)))
    metrics["reference_errors"] += len(set(actions) - linked)
    metrics["missing_evidence_count"] += len(linked - set(actions))
    for key, action in actions.items():
        origins = v._strings(action["source_review_ids"])
        expected_origins = origins_by_action.get(key, set())
        metrics["reference_errors"] += len(set(origins) ^ expected_origins)
        metrics["policy_errors"] += int(not r._text(action.get("owner")) or action.get("status") not in {"open", "closed"})
        due = r._instant(action["due_at"])
        if action.get("status") != "closed":
            metrics["open_action_count"] += 1
            metrics["overdue_action_count"] += int(due < as_of)
            continue
        metrics["closed_action_count"] += 1
        if action.get("verification_ref") not in artifacts:
            metrics["missing_evidence_count"] += 1
            continue
        body = _reviewed(action["verification_ref"], artifacts, system, metrics, now)
        closed, verified = r._instant(action["closed_at"]), r._instant(body["reviewed_at"])
        metrics["identity_errors"] += int(body.get("action_id") != key)
        metrics["policy_errors"] += int(body.get("outcome") != "effective")
        metrics["missing_evidence_count"] += int(body.get("evidence_ref") not in artifacts)
        metrics["timestamp_errors"] += int(not verified <= closed <= now or closed > due
            or any(times[rid] > verified for rid in origins if rid in times))
    metrics["policy_errors"] += metrics["overdue_action_count"]
    metrics["compared_record_count"] = len(reviews) + len(actions)
    return not (metrics["missing_review_count"] or metrics["missing_evidence_count"])


def _competence(data, artifacts, system, metrics, now, as_of):
    assignments = r._index(data["policy"]["required_assignments"], metrics)
    completions = _empty_rows(data["completions"], metrics)
    metrics.update(record_count=len(assignments), required_assignment_count=len(assignments),
                   covered_assignment_count=0, missing_completion_count=0, expired_completion_count=0)
    used = set()
    indexed = {}
    for row in completions.values():
        pair = (row.get("assignment_id"), row.get("course_id"))
        if not all(r._text(value) for value in pair) or pair in indexed:
            raise ValueError("one unambiguous completion per assignment and course required")
        indexed[pair] = row
    roster, total_courses = set(), 0
    for assignment in assignments.values():
        if not all(r._text(assignment.get(k)) for k in ("person_id", "role")):
            raise ValueError("reviewed person and role required")
        active = r._instant(assignment["active_from"])
        required_by = r._instant(assignment["competence_required_by"])
        identity = (assignment["person_id"], assignment["role"])
        metrics["identity_errors"] += int(identity in roster)
        roster.add(identity)
        metrics["timestamp_errors"] += int(not active <= as_of <= r._instant(assignment["active_until"]))
        courses = v._strings(assignment["required_course_ids"])
        total_courses += len(courses)
        if total_courses > r.MAX_ROWS:
            raise ValueError("total competence requirement bound exceeded")
        covered = True
        for course in courses:
            row = indexed.get((assignment["id"], course))
            if row is None:
                metrics["missing_completion_count"] += 1
                covered = False
                continue
            used.add(row["id"])
            if row.get("artifact_ref") not in artifacts:
                metrics["missing_completion_count"] += 1
                covered = False
                continue
            body = _reviewed(row["artifact_ref"], artifacts, system, metrics, now)
            metrics["identity_errors"] += int(body.get("person_id") != assignment["person_id"]
                or body.get("role") != assignment["role"] or body.get("course_id") != course)
            completed, reviewed = r._instant(body["completed_at"]), r._instant(body["reviewed_at"])
            metrics["timestamp_errors"] += int(not completed <= reviewed <= required_by <= as_of)
            metrics["policy_errors"] += int(body.get("outcome") != "demonstrated")
            if r._instant(body["valid_until"]) < as_of:
                metrics["expired_completion_count"] += 1
                covered = False
            if body.get("evidence_ref") not in artifacts:
                metrics["missing_completion_count"] += 1
                covered = False
        metrics["covered_assignment_count"] += int(covered)
    metrics["reference_errors"] += len(set(completions) - used)
    metrics["compared_record_count"] = len(used)
    metrics["policy_errors"] += metrics["expired_completion_count"]
    return metrics["missing_completion_count"] == 0


def evaluate_records(case, document, artifacts, metrics, now, scope, *, evaluated_at):
    data = document["data"]
    system = v._system(data, scope, metrics)
    as_of = r._instant(evaluated_at)
    if case in {"EVID-MONITORING-COVERAGE", "EVID-MONITORING-TREND"}:
        return _monitoring(case, data, artifacts, system, metrics, scope, now, as_of)
    if case == "EVID-MULTICLASS-METRICS":
        return _multiclass(data, artifacts, system, metrics, scope, now)
    if case == "EVID-SUBGROUP-UNCERTAINTY":
        return _subgroups(data, artifacts, system, metrics, scope, now)
    if case == "EVID-GOVERNANCE-ACTIONS":
        return _governance(data, artifacts, system, metrics, now, as_of)
    if case == "EVID-COMPETENCE-COVERAGE":
        return _competence(data, artifacts, system, metrics, now, as_of)
    raise ValueError("unknown lifecycle method")
