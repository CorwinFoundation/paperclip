"""Reconcile customer-reviewed assurance records using existing private artifacts.

No discovery, legal classification, sending, source authentication or conformity
decision. Completeness is relative to supplied inventories and approved policy.
"""
import math

from evals.oss_pilots import assessment_records as r, assessment_validation as v, assessment_lifecycle as lifecycle


class _Links:
    def __init__(self):
        self.count = 0

    def add(self, values):
        self.count += len(values)
        if self.count > r.MAX_ROWS:
            raise ValueError("total assurance reference bound exceeded")
        return values


def _rows(value, metrics):
    return {} if value == [] else r._index(value, metrics)


def _proof(ref, artifacts, metrics):
    if not r._text(ref):
        raise ValueError("explicit original artifact reference required")
    present = ref in artifacts
    metrics["missing_evidence_count"] += int(not present)
    return present


def _reviewed(ref, artifacts, system, metrics, now):
    if not _proof(ref, artifacts, metrics):
        return None
    body = lifecycle._reviewed(ref, artifacts, system, metrics, now)
    _proof(body["evidence_ref"], artifacts, metrics)
    return body


def _notifications(data, artifacts, system, metrics, now, as_of):
    required = r._index(data["policy"]["required_incidents"], metrics)
    actual = _rows(data["incidents"], metrics)
    metrics.update(record_count=len(required), required_incident_count=len(required), missing_incident_count=0,
        reportable_incident_count=0, exempt_incident_count=0, required_notification_count=0,
        delivered_notification_count=0, missing_notification_count=0, overdue_notification_count=0,
        overdue_escalation_count=0, maximum_submission_delay_seconds=0, maximum_delivery_delay_seconds=0)
    metrics["reference_errors"] += len(set(actual) - set(required))
    links = _Links()
    for key, requirement in required.items():
        aware, escalation_due = r._instant(requirement["aware_at"]), r._instant(requirement["escalation_due_at"])
        metrics["timestamp_errors"] += int(aware > now or escalation_due < aware)
        expected = _rows(requirement["required_notifications"], metrics)
        links.add(expected)
        metrics["required_notification_count"] += len(expected)
        decision = _reviewed(requirement["decision_ref"], artifacts, system, metrics, now)
        if decision is not None:
            if type(decision.get("notification_required")) is not bool:
                raise ValueError("reviewed reporting applicability decision required")
            metrics["identity_errors"] += int(decision.get("incident_id") != key
                or r._instant(decision["aware_at"]) != aware)
            metrics["timestamp_errors"] += int(r._instant(decision["reviewed_at"]) < aware)
            metrics["policy_errors"] += int(decision["notification_required"] != bool(expected))
            decided_ids = links.add(v._strings(decision["required_notification_ids"], empty=True))
            metrics["reference_errors"] += len(set(expected) ^ set(decided_ids))
            metrics["reportable_incident_count" if decision["notification_required"] else "exempt_incident_count"] += 1
        incident = actual.get(key)
        if incident is None:
            metrics["missing_incident_count"] += 1
            metrics["overdue_escalation_count"] += int(escalation_due < as_of)
            observed = {}
        else:
            escalated = r._instant(incident["escalated_at"])
            metrics["timestamp_errors"] += int(not aware <= escalated <= now)
            metrics["overdue_escalation_count"] += int(escalated > escalation_due)
            observed = _rows(incident["notifications"], metrics)
            links.add(observed)
            metrics["reference_errors"] += len(set(observed) - set(expected))
            metrics["compared_record_count"] += 1
        for nid, notification in expected.items():
            due = r._instant(notification["due_at"])
            event = notification["deadline_event"]
            if not r._text(notification.get("recipient_id")) or event not in {"submitted", "delivered"}:
                raise ValueError("approved recipient and deadline event required")
            metrics["timestamp_errors"] += int(due < aware)
            row = observed.get(nid)
            if row is None:
                metrics["missing_notification_count"] += 1
                metrics["overdue_notification_count"] += int(due < as_of)
                continue
            submitted = r._instant(row["submitted_at"])
            metrics["timestamp_errors"] += int(not aware <= submitted <= now)
            metrics["maximum_submission_delay_seconds"] = max(metrics["maximum_submission_delay_seconds"],
                max(0, math.ceil((submitted - aware).total_seconds())))
            if event == "submitted":
                metrics["overdue_notification_count"] += int(submitted > due)
            if not _proof(row["delivery_ref"], artifacts, metrics):
                metrics["missing_notification_count"] += 1
                if event == "delivered": metrics["overdue_notification_count"] += int(due < as_of)
                continue
            receipt = v._scoped(artifacts[row["delivery_ref"]], system, metrics)
            delivered = r._instant(receipt["delivered_at"])
            metrics["identity_errors"] += int(receipt.get("incident_id") != key
                or receipt.get("notification_id") != nid or receipt.get("recipient_id") != notification["recipient_id"]
                or r._instant(receipt["submitted_at"]) != submitted)
            metrics["policy_errors"] += int(receipt.get("status") != "delivered")
            metrics["timestamp_errors"] += int(not submitted <= delivered <= now)
            _proof(receipt["evidence_ref"], artifacts, metrics)
            if event == "delivered": metrics["overdue_notification_count"] += int(delivered > due)
            metrics["maximum_delivery_delay_seconds"] = max(metrics["maximum_delivery_delay_seconds"],
                max(0, math.ceil((delivered - aware).total_seconds())))
            metrics["delivered_notification_count"] += int(receipt.get("status") == "delivered")
            metrics["compared_record_count"] += 1
    metrics["policy_errors"] += metrics["overdue_notification_count"] + metrics["overdue_escalation_count"]
    return not (metrics["missing_incident_count"] or metrics["missing_notification_count"] or metrics["missing_evidence_count"])


def _suppliers(data, artifacts, system, metrics, now, as_of):
    manifest = v._components(system["components"])
    required = r._index(data["policy"]["required_components"], metrics)
    actual = _rows(data["components"], metrics)
    metrics.update(record_count=len(manifest), required_component_count=len(manifest), missing_component_count=0,
        approved_component_count=0, supplier_count=0, expired_review_count=0, responsibility_count=0, agreement_count=0)
    metrics["reference_errors"] += len(set(required) ^ set(manifest)) + len(set(actual) - set(manifest))
    suppliers, links = set(), _Links()
    for key, requirement in required.items():
        duties = links.add(v._strings(requirement["required_responsibilities"]))
        row = actual.get(key)
        if row is None:
            metrics["missing_component_count"] += 1
            continue
        if not all(r._text(row.get(k)) for k in ("supplier_id", "version")):
            raise ValueError("explicit supplier and component version required")
        suppliers.add(row["supplier_id"])
        body = _reviewed(row["review_ref"], artifacts, system, metrics, now)
        if body is None:
            continue
        metrics["identity_errors"] += int(body.get("component_id") != key or body.get("supplier_id") != row["supplier_id"]
            or body.get("component_version") != row["version"] or body.get("component_sha256") != manifest.get(key))
        reviewed, valid = r._instant(body["reviewed_at"]), r._instant(body["valid_until"])
        metrics["timestamp_errors"] += int(valid < reviewed)
        metrics["expired_review_count"] += int(valid < as_of)
        metrics["policy_errors"] += int(body.get("status") != "approved" or not r._text(body.get("owner")))
        responsibilities = r._index(body["responsibilities"], metrics)
        links.add(responsibilities)
        metrics["reference_errors"] += len(set(duties) ^ set(responsibilities))
        metrics["policy_errors"] += sum(not r._text(d.get("owner")) for d in responsibilities.values())
        agreements = links.add(v._strings(body["agreement_refs"]))
        for ref in agreements: _proof(ref, artifacts, metrics)
        metrics["responsibility_count"] += len(responsibilities)
        metrics["agreement_count"] += len(agreements)
        metrics["compared_record_count"] += 1
        metrics["approved_component_count"] += int(body.get("status") == "approved" and valid >= as_of)
    metrics["supplier_count"] = len(suppliers)
    metrics["policy_errors"] += metrics["expired_review_count"]
    return not (metrics["missing_component_count"] or metrics["missing_evidence_count"])


def _impacts(data, artifacts, system, metrics, now, as_of):
    required = r._index(data["policy"]["required_contexts"], metrics)
    actual = _rows(data["contexts"], metrics)
    metrics.update(record_count=len(required), required_context_count=len(required), missing_context_count=0,
        covered_context_count=0, impact_risk_count=0, mitigated_risk_count=0, accepted_risk_count=0,
        impact_change_count=0, expired_review_count=0)
    metrics["reference_errors"] += len(set(actual) - set(required))
    manifest = _reviewed(data["change_manifest_ref"], artifacts, system, metrics, now)
    if manifest is None:
        return False
    changes = _rows(manifest["changes"], metrics)
    metrics["impact_change_count"] = len(changes)
    links, by_context = _Links(), {key: set() for key in required}
    for key, change in changes.items():
        contexts = links.add(v._strings(change["context_ids"]))
        changed = r._instant(change["changed_at"])
        metrics["timestamp_errors"] += int(changed > r._instant(manifest["reviewed_at"]))
        metrics["reference_errors"] += len(set(contexts) - set(required))
        for context in contexts:
            if context in by_context: by_context[context].add(key)
    for key, requirement in required.items():
        if not r._text(requirement.get("intended_use_id")):
            raise ValueError("explicit reviewed intended use required")
        groups = links.add(v._strings(requirement["required_group_ids"], empty=True))
        risks = links.add(v._strings(requirement["required_risk_ids"]))
        change_ids = links.add(v._strings(requirement["change_ids"], empty=True))
        metrics["reference_errors"] += len(set(change_ids) ^ by_context[key])
        required_by, due = r._instant(requirement["required_by"]), r._instant(requirement["review_due_at"])
        metrics["timestamp_errors"] += int(required_by > now)
        row = actual.get(key)
        if row is None:
            metrics["missing_context_count"] += 1
            continue
        body = _reviewed(row["assessment_ref"], artifacts, system, metrics, now)
        if body is None:
            continue
        metrics["identity_errors"] += int(body.get("context_id") != key or body.get("intended_use_id") != requirement["intended_use_id"])
        reviewed, valid = r._instant(body["reviewed_at"]), r._instant(body["valid_until"])
        metrics["timestamp_errors"] += int(reviewed > min(required_by, due) or valid < reviewed
            or any(r._instant(changes[c]["changed_at"]) > reviewed for c in by_context[key]))
        metrics["expired_review_count"] += int(valid < as_of)
        for field, expected in (("group_ids", groups), ("risk_ids", risks), ("change_ids", change_ids)):
            observed = links.add(v._strings(body[field], empty=True))
            metrics["reference_errors"] += len(set(expected) ^ set(observed))
        decisions = r._index(body["decisions"], metrics)
        links.add(decisions)
        metrics["reference_errors"] += len(set(risks) ^ set(decisions))
        for risk, decision in decisions.items():
            metrics["impact_risk_count"] += 1
            if decision["decision"] == "mitigate":
                metrics["policy_errors"] += int("acceptance_ref" in decision)
                refs = links.add(v._strings(decision["mitigation_refs"]))
                metrics["mitigated_risk_count"] += 1
            elif decision["decision"] == "accept":
                metrics["policy_errors"] += int("mitigation_refs" in decision)
                refs = [decision["acceptance_ref"]]
                links.add(refs)
                metrics["accepted_risk_count"] += 1
            else:
                raise ValueError("explicit mitigation or residual-risk acceptance decision required")
            for ref in refs:
                disposition = _reviewed(ref, artifacts, system, metrics, now)
                if disposition is None: continue
                metrics["identity_errors"] += int(disposition.get("context_id") != key or disposition.get("risk_id") != risk)
                metrics["timestamp_errors"] += int(r._instant(disposition["reviewed_at"]) > reviewed)
                metrics["policy_errors"] += int(disposition.get("status") != ("approved" if decision["decision"] == "mitigate" else "accepted"))
                if decision["decision"] == "accept":
                    metrics["policy_errors"] += int(r._instant(disposition["valid_until"]) < as_of)
        metrics["covered_context_count"] += 1
        metrics["compared_record_count"] += 1
    metrics["policy_errors"] += metrics["expired_review_count"]
    return not (metrics["missing_context_count"] or metrics["missing_evidence_count"])


def evaluate_records(case, document, artifacts, metrics, now, scope, *, evaluated_at):
    data = document["data"]
    system = v._system(data, scope, metrics)
    metrics["missing_evidence_count"] = 0
    method = {"EVID-INCIDENT-NOTIFICATION": _notifications, "EVID-SUPPLIER-ASSURANCE": _suppliers,
              "EVID-IMPACT-ASSESSMENT-COVERAGE": _impacts}[case]
    return method(data, artifacts, system, metrics, now, r._instant(evaluated_at))
