"""Reconcile reviewed, scoped records without model calls or authenticity claims.

Adapters return original UTF-8 JSON bytes. No URI fetching, document judging,
synthetic fallback, universal performance threshold or production-control claim.
"""
import base64
import hashlib
import json
import math
from collections.abc import Mapping
from datetime import datetime, timezone

from evals.oss_pilots.log_quality import _instant

VERSION = "assessment-record-checks@1"
SCHEMA = "assessment-record-input/v1"
MAX_BYTES = 512 * 1024
MAX_ROWS = 2000
LIMITATION = ("Consistency of supplied reviewed records only; source authenticity, completeness, "
              "legal adequacy, representativeness and production controls require assessor review.")
CASE_METHODS = {
    "EVID-RISK-TRACE": "Reconcile risk, treatment and verification references, ownership and closure claims.",
    "EVID-DATA-LINEAGE": "Reconcile dataset hashes, parent lineage, declared permission references and approved row-quality rules.",
    "EVID-EVAL-RESULTS": "Recompute exact-match accuracy from labelled rows and bound observations against an approved threshold.",
    "EVID-SUBGROUP-COVERAGE": "Recompute approved subgroup sample coverage and accuracy disparity from bound labelled observations.",
    "EVID-CHANGE-REGRESSION": "Compare baseline and current exact-match results for the same labelled dataset and probe version.",
    "EVID-INCIDENT-CLOSURE": "Reconcile incident and corrective-action references, closure verification and chronology.",
}
METRICS = frozenset({"record_count", "compared_record_count", "covered_group_count", "open_record_count",
                     "error_count", "identity_errors", "reference_errors", "timestamp_errors", "metric_errors",
                     "policy_errors", "accuracy_ppm", "baseline_accuracy_ppm"})


class SourceScopeMismatch(PermissionError):
    """An explicitly foreign source must not be retained as failed evidence."""


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _json(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("bounded original bytes required")
    def finite(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("nonfinite number")
        return parsed
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_float=finite,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite number")))


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _data_json(raw, *, target=None):
    value = _json(raw)
    if not isinstance(value, dict):
        raise ValueError("structured records required")
    if target is not None and _text(value.get("target_id")) and value["target_id"] != target:
        raise SourceScopeMismatch("source target differs from requested scope")
    if (value.get("fixture") or value.get("is_fixture")
            or any(x in str(value.get("execution_kind", "")).lower() for x in ("fixture", "mock", "offline"))):
        raise ValueError("measured structured records required")
    return value


def _rows(value, *, allow_empty=False):
    if (not isinstance(value, list) or len(value) > MAX_ROWS or (not allow_empty and not value)
            or any(not isinstance(row, dict) for row in value)):
        raise ValueError("bounded records required")
    return value


def _index(rows, metrics):
    indexed = {}
    for row in _rows(rows):
        key = row.get("id")
        if not _text(key) or key in indexed:
            metrics["identity_errors"] += 1
        else:
            indexed[key] = row
    return indexed


def _blob(value):
    if not isinstance(value, dict):
        raise ValueError("original artifact bytes required")
    raw = base64.b64decode(value["content_base64"], validate=True)
    if not 0 < len(raw) <= MAX_BYTES or hashlib.sha256(raw).hexdigest() != value.get("sha256"):
        raise ValueError("artifact digest mismatch")
    return raw


def _header(raw, scope, evaluated_at):
    if (not isinstance(scope, Mapping)
            or set(scope) not in ({"tenant_id", "scan_id", "agent_id"}, {"target_id", "deployment_id", "snapshot_sha256"})
            or any(not _text(value) for value in scope.values())):
        raise ValueError("explicit assessment scope required")
    doc = _json(raw)
    if (not isinstance(doc, dict) or doc.get("schema") != SCHEMA or doc.get("scope") != scope
            or not _text(doc.get("source_id")) or doc.get("fixture") or doc.get("is_fixture")
            or any(x in str(doc.get("execution_kind", "")).lower() for x in ("mock", "fixture", "offline"))):
        raise ValueError("scoped source required")
    review = doc.get("review", {})
    if (not all(_text(review.get(k)) for k in ("reviewer", "basis", "reviewed_at"))
            or not _instant(doc.get("recorded_at")) <= _instant(review["reviewed_at"]) <= _instant(evaluated_at)):
        raise ValueError("reviewed source required")
    return doc


def _ppm(value):
    if type(value) is not int or not 0 <= value <= 1_000_000:
        raise ValueError("approved parts-per-million threshold required")
    return value


def _refs(value, available, metrics):
    if (not isinstance(value, list) or not value or any(not _text(v) for v in value)
            or len(set(value)) != len(value)):
        metrics["reference_errors"] += 1
        return []
    metrics["reference_errors"] += len(set(value) - set(available))
    return value


def _risk(data, artifacts, metrics, now):
    risks = _index(data.get("risks"), metrics)
    treatments = {} if data.get("treatments") == [] else _index(data.get("treatments"), metrics)
    metrics["record_count"] = len(risks)
    for rid, risk in risks.items():
        metrics["policy_errors"] += int(not _text(risk.get("owner")) or risk.get("status") not in {"open", "accepted", "closed"})
        _refs(risk.get("assessment_refs"), artifacts, metrics)
        if risk.get("status") == "accepted" and risk.get("treatment_ids") == []:
            refs = []
            _refs(risk.get("acceptance_refs"), artifacts, metrics)
            metrics["timestamp_errors"] += int(_instant(risk.get("accepted_at")) > now)
        else:
            refs = _refs(risk.get("treatment_ids"), treatments, metrics)
        for tid in refs:
            if tid in treatments:
                metrics["reference_errors"] += int(treatments[tid].get("risk_id") != rid)
        if risk.get("status") != "closed":
            metrics["open_record_count"] += 1
        else:
            metrics["policy_errors"] += int(not refs or any(treatments.get(t, {}).get("status") != "verified" for t in refs))
    for treatment in treatments.values():
        rid = treatment.get("risk_id")
        metrics["reference_errors"] += int(rid not in risks or treatment.get("id") not in risks.get(rid, {}).get("treatment_ids", []))
        metrics["policy_errors"] += int(not _text(treatment.get("owner")) or treatment.get("status") not in {"open", "verified"})
        if treatment.get("status") == "verified":
            _refs(treatment.get("verification_refs"), artifacts, metrics)
            metrics["policy_errors"] += int(treatment.get("verification_outcome") != "effective")
            metrics["timestamp_errors"] += int(_instant(treatment.get("verified_at")) > now)


def _lineage(data, artifacts, metrics, now):
    datasets = _index(data.get("datasets"), metrics)
    metrics["record_count"] = len(datasets)
    graph = {}
    types = {"string": str, "integer": int, "boolean": bool, "number": (int, float), "object": dict, "array": list}
    for did, dataset in datasets.items():
        parents = dataset.get("parent_ids")
        if not isinstance(parents, list) or any(not _text(p) for p in parents) or len(set(parents)) != len(parents):
            raise ValueError("lineage parents required")
        graph[did] = parents
        metrics["reference_errors"] += len(set(parents) - set(datasets))
        metrics["policy_errors"] += int(not all(_text(dataset.get(k)) for k in ("version", "source", "purpose")))
        _refs(dataset.get("permission_refs"), artifacts, metrics)
        artifact = dataset.get("artifact_ref")
        if artifact not in artifacts:
            metrics["reference_errors"] += 1
            continue
        rows = _rows(_data_json(artifacts[artifact]).get("rows"))
        rules = dataset.get("quality_rules", {})
        fields, identity = rules.get("fields"), rules.get("unique_id")
        if not isinstance(fields, dict) or not fields or not _text(identity) or identity not in fields:
            raise ValueError("approved quality rules required")
        seen = set()
        for row in rows:
            metrics["compared_record_count"] += 1
            for field, kind in fields.items():
                if kind not in types:
                    raise ValueError("unsupported quality rule")
                value = row.get(field)
                metrics["policy_errors"] += int(field not in row or not isinstance(value, types[kind])
                    or (kind in {"integer", "number"} and type(value) is bool))
            key = json.dumps(row.get(identity), sort_keys=True, allow_nan=False)
            metrics["identity_errors"] += int(identity not in row or row[identity] is None or key in seen)
            seen.add(key)
    # Linear topological traversal: deep customer lineage must not cause
    # recursive calls or repeated construction of the entire pending set.
    children = {key: [] for key in graph}
    indegree = {key: 0 for key in graph}
    for key, parents in graph.items():
        for parent in parents:
            if parent in graph:
                children[parent].append(key)
                indegree[key] += 1
    ready = [key for key, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        key = ready.pop()
        visited += 1
        for child in children[key]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    metrics["reference_errors"] += int(visited != len(graph))


def _evaluation(data, artifacts, metrics, scope, now, key="current"):
    dataset_ref = data["dataset_ref"]
    dataset_raw = artifacts[dataset_ref]
    labels = _index(_data_json(dataset_raw).get("rows"), metrics)
    for row in labels.values():
        if "expected" not in row:
            raise ValueError("ground truth required")
    target = scope.get("agent_id", scope.get("target_id"))
    observed = _data_json(artifacts[data[key + "_ref"]], target=target)
    metrics["identity_errors"] += int(observed.get("target_id") != target
        or observed.get("dataset_sha256") != hashlib.sha256(dataset_raw).hexdigest()
        or observed.get("probe_version") != data.get("probe_version")
        or not _text(observed.get("probe_version"))
        or not _text(data.get(key + "_model_version"))
        or observed.get("model_version") != data.get(key + "_model_version"))
    instant = _instant(observed.get("observed_at"))
    metrics["timestamp_errors"] += int(instant > now)
    predictions = _index(observed.get("results"), metrics)
    metrics["identity_errors"] += len(set(labels) ^ set(predictions))
    correct = {}
    for case, label in labels.items():
        prediction = predictions.get(case, {})
        correct[case] = ("actual" in prediction and json.dumps(prediction["actual"], sort_keys=True, allow_nan=False)
                         == json.dumps(label["expected"], sort_keys=True, allow_nan=False))
    accuracy = sum(correct.values()) * 1_000_000 // len(labels)
    metrics["metric_errors"] += int(_ppm(observed.get("declared_accuracy_ppm")) != accuracy)
    return labels, correct, accuracy, instant


def _evaluations(case, data, artifacts, metrics, scope, now):
    labels, correct, accuracy, instant = _evaluation(data, artifacts, metrics, scope, now)
    metrics.update(record_count=len(labels), compared_record_count=len(correct), accuracy_ppm=accuracy)
    policy = data.get("policy", {})
    if case == "EVID-EVAL-RESULTS":
        metrics["policy_errors"] += int(accuracy < _ppm(policy.get("minimum_accuracy_ppm")))
    elif case == "EVID-SUBGROUP-COVERAGE":
        groups = policy.get("required_groups")
        minimum = policy.get("minimum_group_size")
        if (not isinstance(groups, list) or not 1 <= len(groups) <= MAX_ROWS or any(not _text(g) for g in groups)
                or len(set(groups)) != len(groups) or type(minimum) is not int or not 1 <= minimum <= MAX_ROWS):
            raise ValueError("approved subgroup policy required")
        rates = []
        for group in groups:
            ids = [key for key, row in labels.items() if row.get("group") == group]
            metrics["policy_errors"] += int(len(ids) < minimum)
            if ids:
                metrics["covered_group_count"] += 1
                rates.append(sum(correct[key] for key in ids) * 1_000_000 // len(ids))
        metrics["policy_errors"] += int(not rates or max(rates) - min(rates) > _ppm(policy.get("maximum_gap_ppm")))
    else:
        _, _, baseline, baseline_at = _evaluation(data, artifacts, metrics, scope, now, "baseline")
        metrics["baseline_accuracy_ppm"] = baseline
        metrics["timestamp_errors"] += int(baseline_at >= instant)
        metrics["policy_errors"] += int(baseline - accuracy > _ppm(policy.get("maximum_drop_ppm")))


def _incidents(data, artifacts, metrics, now):
    incidents = _index(data.get("incidents"), metrics)
    actions = _index(data.get("actions"), metrics)
    closed = 0
    metrics["record_count"] = len(incidents)
    for iid, incident in incidents.items():
        opened = _instant(incident.get("opened_at"))
        metrics["timestamp_errors"] += int(opened > now)
        metrics["policy_errors"] += int(not _text(incident.get("owner")) or incident.get("status") not in {"open", "closed"})
        refs = _refs(incident.get("action_ids"), actions, metrics)
        if incident.get("status") != "closed":
            metrics["open_record_count"] += 1
            continue
        closed += 1
        closed_at = _instant(incident.get("closed_at"))
        metrics["timestamp_errors"] += int(not opened <= closed_at <= now)
        for aid in refs:
            action = actions.get(aid, {})
            metrics["reference_errors"] += int(action.get("incident_id") != iid)
            metrics["policy_errors"] += int(action.get("status") != "verified" or action.get("verification_outcome") != "effective"
                                            or not _text(action.get("owner")))
            _refs(action.get("verification_refs"), artifacts, metrics)
            metrics["timestamp_errors"] += int(not opened <= _instant(action.get("verified_at")) <= closed_at)
    for aid, action in actions.items():
        incident = incidents.get(action.get("incident_id"), {})
        metrics["reference_errors"] += int(aid not in incident.get("action_ids", []))
        metrics["policy_errors"] += int(not _text(action.get("owner")) or action.get("status") not in {"open", "verified"})
        if action.get("status") == "verified":
            _refs(action.get("verification_refs"), artifacts, metrics)
            metrics["policy_errors"] += int(action.get("verification_outcome") != "effective")
            metrics["timestamp_errors"] += int(not _instant(incident.get("opened_at")) <= _instant(action.get("verified_at")) <= now)
    return closed > 0


def evaluate(case_id, raw, *, scope, evaluated_at):
    """Recompute from original bytes; malformed private/scoping headers never persist."""
    if case_id not in CASE_METHODS:
        raise ValueError("unknown record check")
    doc = _header(raw, scope, evaluated_at)
    metrics = {key: 0 for key in METRICS}
    meaningful = True
    try:
        data = doc["data"]
        artifacts = {key: _blob(blob) for key, blob in doc.get("artifacts", {}).items()}
        now = _instant(doc["recorded_at"])
        if case_id == "EVID-RISK-TRACE":
            _risk(data, artifacts, metrics, now)
        elif case_id == "EVID-DATA-LINEAGE":
            _lineage(data, artifacts, metrics, now)
        elif case_id == "EVID-INCIDENT-CLOSURE":
            meaningful = _incidents(data, artifacts, metrics, now)
        else:
            _evaluations(case_id, data, artifacts, metrics, scope, now)
    except (KeyError, ValueError, TypeError, AttributeError, OverflowError, RecursionError):
        metrics["policy_errors"] += 1
    metrics["error_count"] = sum(value for key, value in metrics.items() if key.endswith("_errors"))
    verdict = "failed" if metrics["error_count"] else "passed" if meaningful and metrics["record_count"] else "inconclusive"
    return metrics, verdict


def verify(case_id, capture, *, scope, observed_at=None):
    if capture.get("capture_version") != VERSION:
        raise ValueError("unsupported record capture")
    if _instant(capture.get("evaluated_at")) > datetime.now(timezone.utc):
        raise ValueError("future record capture")
    if observed_at is not None and _instant(capture["evaluated_at"]) > _instant(observed_at):
        raise ValueError("capture postdates attributed observation")
    raw = _blob(capture.get("input_document"))
    metrics, verdict = evaluate(case_id, raw, scope=scope, evaluated_at=capture.get("evaluated_at"))
    if (metrics != capture.get("measurements") or verdict != capture.get("verdict")
            or any(type(v) is not int for v in capture.get("measurements", {}).values())):
        raise ValueError("record measurements contradict original bytes")
    return verdict


def run_case(case_id, adapter, *, scope):
    if case_id not in CASE_METHODS:
        raise ValueError("unknown record check")
    if adapter is None:
        return {"verdict": "inconclusive", "reason": "reviewed_record_adapter_not_configured"}
    try:
        raw = adapter.read(case_id)
        evaluated_at = datetime.now(timezone.utc).isoformat()
        metrics, verdict = evaluate(case_id, raw, scope=scope, evaluated_at=evaluated_at)
        return {"capture_version": VERSION, "method": CASE_METHODS[case_id], "observation_scope": LIMITATION,
                "evaluated_at": evaluated_at, "input_document": {"sha256": hashlib.sha256(raw).hexdigest(),
                    "content_base64": base64.b64encode(raw).decode("ascii")},
                "measurements": metrics, "verdict": verdict, "reason": "reviewed_records_reconciled"}
    except Exception:
        # Adapter errors, unreviewed documents and foreign scope bytes stay private.
        return {"verdict": "inconclusive", "reason": "reviewed_records_invalid_or_unavailable"}
