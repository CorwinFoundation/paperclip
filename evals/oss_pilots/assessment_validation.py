"""Bounded evidence reconciliation for reviewed evaluation and release records.

All attribution is supplied by the reviewed input. There is no network, model
call, implicit approval, production-snapshot discovery or conformity decision.
"""
import hashlib
import json
import math
import re

from evals.oss_pilots import assessment_records as r
from tools.assessment_test_catalog import VALIDATION_METHODS, LIFECYCLE_METHODS, ASSURANCE_METHODS
from tools.binomial_statistics import wilson


def initial_metrics(case):
    names = {"record_count", "compared_record_count", "error_count", "identity_errors", "reference_errors",
             "timestamp_errors", "metric_errors", "policy_errors"}
    if case in {"EVID-RISK-TEST-COVERAGE", "EVID-MITIGATION-RESULT-BINDING",
                "EVID-CHANGE-IMPACT-COVERAGE", "EVID-RELEASE-ACCEPTANCE"}:
        names |= {"required_observation_count", "missing_case_count", "failed_required_case_count", "exception_count"}
        names |= ({"open_record_count", "uncovered_risk_count"} if "RISK" in case or "MITIGATION" in case
                  else {"changed_component_count", "uncovered_change_count"})
    elif case in {"EVID-SPLIT-OVERLAP", "EVID-SUBJECT-INDEPENDENCE"}:
        names |= {"partition_count", "overlap_count", "allowed_reference_overlap_count", "shared_subject_count"}
    elif case == "EVID-CLASS-ERROR-RATES":
        names |= {"positive_count", "negative_count", "true_positive_count", "true_negative_count",
                  "false_positive_count", "false_negative_count", "abstention_count", "missing_class_count", "unsupported_class_map_count"}
    elif case == "EVID-STATISTICAL-SUPPORT":
        names |= {"independent_subject_count", "dependent_record_count", "unsupported_assumption_count"}
    else:
        names |= {"claim_count", "unsupported_claim_count"}
    return {key: 0 for key in names}


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("explicit SHA-256 required")
    return value


def _strings(value, *, empty=False):
    if (not isinstance(value, list) or len(value) > r.MAX_ROWS or (not empty and not value)
            or any(not r._text(v) for v in value) or len(set(value)) != len(value)):
        raise ValueError("bounded unique identifiers required")
    return value


def _system(data, scope, metrics):
    system = data["system"]
    target = scope.get("agent_id", scope.get("target_id"))
    if r._text(system.get("target_id")) and system["target_id"] != target:
        raise r.SourceScopeMismatch("foreign reviewed system")
    metrics["identity_errors"] += int(system.get("target_id") != target)
    _digest(system.get("snapshot_sha256"))
    if "snapshot_sha256" in scope:
        metrics["identity_errors"] += int(system["snapshot_sha256"] != scope["snapshot_sha256"])
    return system


def _scoped(raw, system, metrics):
    body = r._data_json(raw, target=system["target_id"])
    metrics["identity_errors"] += int(body.get("target_id") != system["target_id"]
        or body.get("snapshot_sha256") != system["snapshot_sha256"])
    return body


def _observations(data, artifacts, system, metrics, now, scope):
    """Reuse the native-source verifier without writing or inventing observations."""
    from requirement_assessment import _registered_source
    rows = r._index(data.get("observations"), metrics)
    identities = set()
    for row in rows.values():
        if type(row.get("trial_index")) is not int or not 0 <= row["trial_index"] <= r.MAX_ROWS:
            raise ValueError("bounded original trial index required")
        raw = artifacts[row["artifact_ref"]]
        native = r._data_json(raw, target=system["target_id"])
        if "tenant_id" in scope:
            capture_scope = row.get("capture_scope") or {}
            if (native.get("tenant_id", scope["tenant_id"]) != scope["tenant_id"]
                    or capture_scope.get("tenant_id", scope["tenant_id"]) != scope["tenant_id"]):
                raise r.SourceScopeMismatch("foreign native tenant")
        if native.get("agent_id") != system["target_id"]:
            raise r.SourceScopeMismatch("foreign native observation")
        # Do not recursively use these meta-checks as their own verification.
        if native.get("case_id") in VALIDATION_METHODS.keys() | LIFECYCLE_METHODS.keys() | ASSURANCE_METHODS.keys():
            raise ValueError("validation meta-evidence is not an independent target observation")
        metrics["identity_errors"] += int(row.get("snapshot_sha256") != system["snapshot_sha256"])
        metrics["timestamp_errors"] += int(r._instant(row.get("observed_at")) > now)
        source_digest = hashlib.sha256(raw).hexdigest()
        if row.get("source_sha256") != source_digest:
            raise ValueError("approved native digest mismatch")
        body = {key: row[key] for key in ("test_id", "case_id", "probe_set_version", "observed_at", "trial_index")}
        body.update(verdict=native["verdict"], source_registered_case={
            "binding_index": 0, "artifact_uri": "evidence://" + source_digest, "sha256": source_digest})
        _registered_source(body, {"scope": {"target_id": system["target_id"]},
            "existing_evidence_bindings": [row], "alert_recipient_id": data.get("alert_recipient_id")}, lambda _: raw)
        identity = tuple(native[key] for key in ("job_id", "agent_id", "test_id", "case_id", "seed"))
        metrics["identity_errors"] += int(identity in identities)
        identities.add(identity)
        row["_verdict"] = native["verdict"]
    metrics["compared_record_count"] = len(rows)
    return rows


def _required(ids, observations, metrics):
    ids = _strings(ids)
    missing = set(ids) - set(observations)
    metrics["required_observation_count"] += len(ids)
    metrics["missing_case_count"] += len(missing)
    metrics["reference_errors"] += len(missing)
    failed = [key for key in ids if observations.get(key, {}).get("_verdict") in {"fail", "failed"}]
    metrics["failed_required_case_count"] += len(failed)
    return ids, failed


def _exception(ref, artifacts, system, metrics, *, context_id, observed_ids, at, before):
    body = _scoped(artifacts[ref], system, metrics)
    metrics["policy_errors"] += int(body.get("context_id") != context_id
        or not all(r._text(body.get(k)) for k in ("reviewer", "basis")))
    metrics["reference_errors"] += len(set(observed_ids) - set(_strings(body.get("observation_ids"))))
    metrics["timestamp_errors"] += int(not r._instant(body.get("approved_at")) <= before <= at <= r._instant(body.get("valid_until")))
    metrics["exception_count"] += 1


def _risk(case, data, artifacts, system, metrics, now, scope):
    observations = _observations(data, artifacts, system, metrics, now, scope)
    risks = r._index(data.get("risks"), metrics)
    treatments = r._index(data["treatments"], metrics) if data.get("treatments") else {}
    metrics["record_count"] = len(risks)
    used = set()
    for key, risk in risks.items():
        ids, failed = _required(risk.get("required_observation_ids"), observations, metrics)
        missing = set(ids) - set(observations)
        metrics["uncovered_risk_count"] += int(bool(missing))
        metrics["policy_errors"] += int(risk.get("status") not in {"open", "closed", "accepted"})
        if risk.get("status") != "closed":
            metrics["open_record_count"] += 1
        if case == "EVID-RISK-TEST-COVERAGE":
            continue
        tids = _strings(risk.get("treatment_ids"), empty=risk.get("status") == "accepted")
        for tid in tids:
            treatment = treatments.get(tid, {})
            used.add(tid)
            metrics["reference_errors"] += int(treatment.get("risk_id") != key)
            checks, bad = _required(treatment.get("verification_observation_ids"), observations, metrics)
            metrics["reference_errors"] += len(set(checks) - set(ids))
            metrics["policy_errors"] += int(treatment.get("status") not in {"open", "verified"})
            if treatment.get("status") == "verified":
                metrics["policy_errors"] += int(bool(bad) or bool(set(checks) - set(observations)))
        if risk.get("status") == "closed":
            metrics["policy_errors"] += int(bool(failed) or not tids
                or any(treatments.get(t, {}).get("status") != "verified" for t in tids))
        if risk.get("status") == "accepted":
            _exception(risk["exception_ref"], artifacts, system, metrics, context_id=key,
                observed_ids=ids, at=now, before=now)
    if case == "EVID-MITIGATION-RESULT-BINDING":
        metrics["reference_errors"] += len(set(treatments) - used)
    return True


def _partitions(case, data, artifacts, system, metrics):
    datasets = r._index(data.get("datasets"), metrics)
    policy = data["policy"]
    comparisons = r._rows(policy.get("comparison_pairs"))
    allowed = {_digest(v) for v in _strings(policy.get("allowed_reference_sha256", []), empty=True)}
    unit = policy.get("unit_field")
    if not r._text(unit):
        raise ValueError("reviewed unit of independence required")
    partitions, subjects = {}, {}
    total = 0
    for key, dataset in datasets.items():
        if dataset.get("role") not in {"train", "tune", "validation", "test"}:
            raise ValueError("explicit dataset partition role required")
        body = r._data_json(artifacts[dataset["artifact_ref"]], target=system["target_id"])
        rows = r._index(body.get("rows"), metrics)
        total += len(rows)
        if total > r.MAX_ROWS:
            raise ValueError("total partition row bound exceeded")
        hashes, units = set(), set()
        for row in rows.values():
            if "content" not in row or not r._text(row.get(unit)):
                raise ValueError("original content and independence key required")
            hashes.add(hashlib.sha256(json.dumps(row["content"], sort_keys=True,
                separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest())
            units.add(row[unit])
        partitions[key], subjects[key] = hashes, units
    seen = set()
    held_out = False
    for pair in comparisons:
        left, right = pair["left"], pair["right"]
        identity = tuple(sorted((left, right)))
        if left == right or identity in seen or left not in datasets or right not in datasets:
            raise ValueError("unique available partition pairs required")
        seen.add(identity)
        held_out |= datasets[left]["role"] == "test" or datasets[right]["role"] == "test"
        overlap = partitions[left] & partitions[right]
        metrics["allowed_reference_overlap_count"] += len(overlap & allowed)
        metrics["overlap_count"] += len(overlap - allowed)
        metrics["shared_subject_count"] += len(subjects[left] & subjects[right])
    if not held_out:
        raise ValueError("held-out partition comparison required")
    metrics.update(partition_count=len(datasets), record_count=total, compared_record_count=total)
    metrics["policy_errors"] += metrics["overlap_count"] if case == "EVID-SPLIT-OVERLAP" else metrics["shared_subject_count"]
    return True


def _evaluation(data, artifacts, system, metrics, scope, now):
    labels, correct, accuracy, instant = r._evaluation(data, artifacts, metrics, scope, now)
    current = _scoped(artifacts[data["current_ref"]], system, metrics)
    metrics.update(record_count=len(labels), compared_record_count=len(correct), accuracy_ppm=accuracy)
    return labels, correct, current, instant


def _classification(data, artifacts, system, metrics, scope, now):
    labels, _, current, _ = _evaluation(data, artifacts, system, metrics, scope, now)
    policy = data["policy"]
    classes = _strings(policy.get("classes"))
    positive, abstain = policy.get("positive_label"), policy.get("abstention_label")
    if len(classes) != 2:
        metrics["unsupported_class_map_count"] = 1
        return False
    if positive not in classes or not r._text(abstain) or abstain in classes:
        raise ValueError("reviewed binary classes and distinct abstention label required")
    if policy.get("abstention_policy") not in {"reject", "count_as_incorrect"}:
        raise ValueError("explicit abstention policy required")
    for name in ("maximum_positive_error_ppm", "maximum_negative_error_ppm", "minimum_balanced_accuracy_ppm"):
        metrics[name] = r._ppm(policy[name])
    predictions = r._index(current.get("results"), metrics)
    pos_abstain = neg_abstain = 0
    for key, row in labels.items():
        expected, actual = row["expected"], predictions[key]["actual"]
        if expected not in classes or actual not in [*classes, abstain]:
            raise ValueError("classification label outside reviewed class map")
        pos = expected == positive
        metrics["positive_count" if pos else "negative_count"] += 1
        if actual == abstain:
            metrics["abstention_count"] += 1
            pos_abstain += int(pos)
            neg_abstain += int(not pos)
        elif actual == positive:
            metrics["true_positive_count" if pos else "false_positive_count"] += 1
        else:
            metrics["false_negative_count" if pos else "true_negative_count"] += 1
    p, n = metrics["positive_count"], metrics["negative_count"]
    if not p or not n:
        metrics["missing_class_count"] = int(not p) + int(not n)
        return False
    metrics.update(false_positive_rate_ppm=metrics["false_positive_count"] * 1_000_000 // n,
        false_negative_rate_ppm=metrics["false_negative_count"] * 1_000_000 // p,
        positive_error_rate_ppm=(metrics["false_negative_count"] + pos_abstain) * 1_000_000 // p,
        negative_error_rate_ppm=(metrics["false_positive_count"] + neg_abstain) * 1_000_000 // n,
        balanced_accuracy_ppm=(metrics["true_positive_count"] * n + metrics["true_negative_count"] * p) * 1_000_000 // (2 * p * n))
    for name in ("false_positive_rate_ppm", "false_negative_rate_ppm", "balanced_accuracy_ppm"):
        metrics["metric_errors"] += int(r._ppm(current["declared_" + name]) != metrics[name])
    metrics["policy_errors"] += int(policy["abstention_policy"] == "reject" and metrics["abstention_count"] > 0)
    metrics["policy_errors"] += int(metrics["positive_error_rate_ppm"] > r._ppm(policy["maximum_positive_error_ppm"]))
    metrics["policy_errors"] += int(metrics["negative_error_rate_ppm"] > r._ppm(policy["maximum_negative_error_ppm"]))
    metrics["policy_errors"] += int(metrics["balanced_accuracy_ppm"] < r._ppm(policy["minimum_balanced_accuracy_ppm"]))
    return True


def _statistics(data, artifacts, system, metrics, scope, now):
    labels, correct, current, _ = _evaluation(data, artifacts, system, metrics, scope, now)
    policy = data["policy"]
    unit = policy.get("unit_field")
    if not r._text(unit) or not all(r._text(v.get(unit)) for v in labels.values()):
        raise ValueError("reviewed independence key required")
    units = {v[unit] for v in labels.values()}
    metrics["independent_subject_count"] = len(units)
    metrics["dependent_record_count"] = len(labels) - len(units)
    if (policy.get("method") != "wilson95-binomial" or policy.get("sampling_assumption") != "independent_bernoulli"
            or not r._text(policy.get("sampling_basis")) or metrics["dependent_record_count"]):
        metrics["unsupported_assumption_count"] = 1
        return False
    minimum = policy["minimum_independent_count"]
    if type(minimum) is not int or not 1 <= minimum <= r.MAX_ROWS:
        raise ValueError("bounded approved sample minimum required")
    metrics["minimum_independent_count"] = minimum
    for name in ("minimum_accuracy_lower_ppm", "maximum_interval_width_ppm"):
        metrics[name] = r._ppm(policy[name])
    lower, upper = wilson(sum(correct.values()), len(labels))
    metrics["wilson_lower_ppm"] = max(0, math.floor(lower * 1_000_000))
    metrics["wilson_upper_ppm"] = min(1_000_000, math.ceil(upper * 1_000_000))
    metrics["interval_width_ppm"] = metrics["wilson_upper_ppm"] - metrics["wilson_lower_ppm"]
    for name in ("wilson_lower_ppm", "wilson_upper_ppm"):
        metrics["metric_errors"] += int(r._ppm(current["declared_" + name]) != metrics[name])
    metrics["policy_errors"] += int(len(units) < minimum
        or metrics["wilson_lower_ppm"] < r._ppm(policy["minimum_accuracy_lower_ppm"])
        or metrics["interval_width_ppm"] > r._ppm(policy["maximum_interval_width_ppm"]))
    return True


def _claims(data, artifacts, system, metrics, scope, now):
    _, _, current, _ = _evaluation(data, artifacts, system, metrics, scope, now)
    claims = r._index(data.get("claims"), metrics)
    policy = data["policy"]
    required = _strings(policy.get("required_claim_ids"))
    metrics["reference_errors"] += len(set(required) - set(claims))
    for ref in _strings(policy.get("required_limitation_refs"), empty=True):
        if ref not in artifacts:
            metrics["reference_errors"] += 1
    tolerance = r._ppm(policy["maximum_overstatement_ppm"])
    metrics["maximum_overstatement_ppm"] = tolerance
    for claim in claims.values():
        if claim.get("document_ref") not in artifacts or not r._text(claim.get("location")):
            raise ValueError("original document and reviewed extraction location required")
        if claim.get("target_id") != system["target_id"]:
            raise r.SourceScopeMismatch("foreign claim")
        metrics["identity_errors"] += int(claim.get("snapshot_sha256") != system["snapshot_sha256"]
            or claim.get("model_version") != data["current_model_version"]
            or claim.get("probe_version") != data["probe_version"]
            or claim.get("dataset_sha256") != hashlib.sha256(artifacts[data["dataset_ref"]]).hexdigest()
            or _digest(claim.get("conditions_sha256")) != _digest(current.get("conditions_sha256")))
        if claim.get("metric") != "exact_match_accuracy_ppm" or claim.get("population") != "all_supplied_rows":
            metrics["unsupported_claim_count"] += 1
            continue
        metrics["policy_errors"] += int(r._ppm(claim.get("value_ppm")) > metrics["accuracy_ppm"] + tolerance)
    metrics["claim_count"] = len(claims)
    return metrics["unsupported_claim_count"] == 0


def _components(value):
    if not isinstance(value, dict) or not 0 < len(value) <= r.MAX_ROWS or any(not r._text(k) for k in value):
        raise ValueError("bounded reviewed component manifest required")
    return {key: _digest(digest) for key, digest in value.items()}


def _changes(case, data, artifacts, system, metrics, now, scope):
    observations = _observations(data, artifacts, system, metrics, now, scope)
    baseline = data["baseline_system"]
    if r._text(baseline.get("target_id")) and baseline["target_id"] != system["target_id"]:
        raise r.SourceScopeMismatch("foreign baseline system")
    metrics["identity_errors"] += int(baseline.get("target_id") != system["target_id"])
    _digest(baseline["snapshot_sha256"])
    metrics["identity_errors"] += int(baseline["snapshot_sha256"] == system["snapshot_sha256"])
    old, new = _components(baseline["components"]), _components(system["components"])
    actual = {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}
    changes = r._index(data.get("changes"), metrics)
    declared = {row["component_id"] for row in changes.values()}
    metrics["identity_errors"] += len(changes) - len(declared)
    metrics["uncovered_change_count"] = len(actual - declared)
    metrics["reference_errors"] += len(actual ^ declared)
    metrics.update(record_count=len(changes), changed_component_count=len(actual))
    review = _scoped(artifacts[data["impact_review_ref"]], system, metrics)
    reviewed_at = r._instant(review.get("reviewed_at"))
    metrics["policy_errors"] += int(not all(r._text(review.get(k)) for k in ("reviewer", "basis")))
    metrics["timestamp_errors"] += int(reviewed_at > now)
    metrics["reference_errors"] += len(set(changes) - set(_strings(review.get("covered_change_ids"))))
    reviewed_risks = set(_strings(review.get("covered_risk_ids")))
    required_ids = set()
    for change in changes.values():
        if "before_sha256" not in change or "after_sha256" not in change:
            raise ValueError("explicit before/after component binding required")
        key = change["component_id"]
        changed_at = r._instant(change.get("changed_at"))
        metrics["timestamp_errors"] += int(changed_at > reviewed_at)
        metrics["identity_errors"] += int(change.get("before_sha256") != old.get(key) or change.get("after_sha256") != new.get(key))
        metrics["reference_errors"] += len(set(_strings(change.get("affected_risk_ids"))) - reviewed_risks)
        ids, _ = _required(change.get("required_observation_ids"), observations, metrics)
        metrics["timestamp_errors"] += sum(r._instant(observations[oid]["observed_at"]) < changed_at
            for oid in set(ids) & observations.keys())
        required_ids.update(ids)
    if case == "EVID-CHANGE-IMPACT-COVERAGE":
        return bool(actual)
    release = _scoped(artifacts[data["release_record_ref"]], system, metrics)
    approved, released = r._instant(release.get("approved_at")), r._instant(release.get("released_at"))
    metrics["timestamp_errors"] += int(not reviewed_at <= approved <= released <= now)
    metrics["policy_errors"] += int(not all(r._text(release.get(k)) for k in ("approver", "basis")))
    _refs = _strings(release.get("required_observation_ids"))
    metrics["reference_errors"] += len(required_ids ^ set(_refs))
    exceptions = release.get("exceptions", {})
    if not isinstance(exceptions, dict) or set(exceptions) - required_ids:
        raise ValueError("exceptions must bind required observations")
    for oid in required_ids & observations.keys():
        observation = observations[oid]
        metrics["timestamp_errors"] += int(r._instant(observation["observed_at"]) > approved)
        if oid in exceptions:
            _exception(exceptions[oid], artifacts, system, metrics, context_id="release",
                observed_ids=[oid], at=released, before=approved)
        elif observation["_verdict"] in {"fail", "failed"}:
            metrics["policy_errors"] += 1
    return bool(actual)


def evaluate_records(case, document, artifacts, metrics, now, scope):
    data = document["data"]
    system = _system(data, scope, metrics)
    if case in {"EVID-RISK-TEST-COVERAGE", "EVID-MITIGATION-RESULT-BINDING"}:
        return _risk(case, data, artifacts, system, metrics, now, scope)
    if case in {"EVID-SPLIT-OVERLAP", "EVID-SUBJECT-INDEPENDENCE"}:
        return _partitions(case, data, artifacts, system, metrics)
    if case == "EVID-CLASS-ERROR-RATES":
        return _classification(data, artifacts, system, metrics, scope, now)
    if case == "EVID-STATISTICAL-SUPPORT":
        return _statistics(data, artifacts, system, metrics, scope, now)
    if case == "EVID-PERFORMANCE-CLAIM-CONSISTENCY":
        return _claims(data, artifacts, system, metrics, scope, now)
    return _changes(case, data, artifacts, system, metrics, now, scope)
