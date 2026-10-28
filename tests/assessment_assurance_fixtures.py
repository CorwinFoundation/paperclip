"""Explicit software qualification records; never default customer evidence."""
from tests.assessment_lifecycle_fixtures import _current_times
from tests.assessment_validation_fixtures import blob, change_artifact


def sample(case, scope):
    system = {"target_id": scope.get("agent_id", scope.get("target_id")),
              "snapshot_sha256": scope.get("snapshot_sha256", "b" * 64)}
    proof = blob({"private": "private-customer-detail"})
    data, artifacts = {"system": system}, {"proof": proof}
    def reviewed(**fields):
        return blob(system | dict(reviewer="private-reviewer", basis="reviewed software fixture",
            reviewed_at="2026-10-27T17:00:00Z", evidence_ref="proof") | fields)
    if case == "EVID-INCIDENT-NOTIFICATION":
        aware = "2026-10-27T16:00:00Z"
        data["policy"] = {"required_incidents": [dict(id="private-incident", aware_at=aware,
            escalation_due_at="2026-10-27T17:00:00Z", decision_ref="decision", required_notifications=[dict(
                id="private-notification", recipient_id="private-recipient", due_at="2026-10-27T19:00:00Z", deadline_event="submitted")])]}
        data["incidents"] = [dict(id="private-incident", escalated_at="2026-10-27T16:30:00Z", notifications=[dict(
            id="private-notification", submitted_at="2026-10-27T18:10:00Z", delivery_ref="receipt")])]
        artifacts["decision"] = reviewed(incident_id="private-incident", aware_at=aware,
            notification_required=True, required_notification_ids=["private-notification"])
        artifacts["receipt"] = blob(system | dict(incident_id="private-incident", notification_id="private-notification",
            recipient_id="private-recipient", status="delivered", submitted_at="2026-10-27T18:10:00Z",
            delivered_at="2026-10-27T18:20:00Z", evidence_ref="proof"))
    elif case == "EVID-SUPPLIER-ASSURANCE":
        system["components"] = {"private-component": "c" * 64}
        data["policy"] = {"required_components": [dict(id="private-component", required_responsibilities=["private-duty"])]}
        data["components"] = [dict(id="private-component", supplier_id="private-supplier", version="private-version", review_ref="supplier")]
        artifacts["supplier"] = reviewed(component_id="private-component", supplier_id="private-supplier",
            component_version="private-version", component_sha256="c" * 64, status="approved", owner="private-owner",
            valid_until="2026-11-27T20:00:00Z", responsibilities=[dict(id="private-duty", owner="private-owner")],
            agreement_refs=["proof"])
    elif case == "EVID-IMPACT-ASSESSMENT-COVERAGE":
        data["policy"] = {"required_contexts": [dict(id="private-context", intended_use_id="private-use",
            required_group_ids=["private-group"], required_risk_ids=["private-risk"], change_ids=["private-change"],
            required_by="2026-10-27T19:00:00Z", review_due_at="2026-10-27T19:00:00Z")]}
        data["contexts"] = [dict(id="private-context", assessment_ref="impact")]
        data["change_manifest_ref"] = "changes"
        artifacts["changes"] = reviewed(reviewed_at="2026-10-27T16:30:00Z", changes=[dict(
            id="private-change", changed_at="2026-10-27T16:00:00Z", context_ids=["private-context"])])
        artifacts["impact"] = reviewed(context_id="private-context", intended_use_id="private-use",
            reviewed_at="2026-10-27T18:00:00Z", valid_until="2026-11-27T20:00:00Z",
            group_ids=["private-group"], risk_ids=["private-risk"], change_ids=["private-change"],
            decisions=[dict(id="private-risk", decision="mitigate", mitigation_refs=["mitigation"])])
        artifacts["mitigation"] = reviewed(context_id="private-context", risk_id="private-risk", status="approved")
    else:
        raise ValueError(case)
    return _current_times(dict(schema="assessment-record-input/v1", scope=scope, source_id="private-source",
        recorded_at="2026-10-27T20:00:00Z", review=dict(reviewer="private-reviewer", basis="controlled fixture",
        reviewed_at="2026-10-27T20:01:00Z"), data=data, artifacts=artifacts))


def contradict(doc, case):
    if case == "EVID-INCIDENT-NOTIFICATION":
        change_artifact(doc, "receipt", lambda v: v.update(recipient_id="wrong-recipient"))
    elif case == "EVID-SUPPLIER-ASSURANCE":
        change_artifact(doc, "supplier", lambda v: v.update(component_version="wrong-version"))
    else:
        change_artifact(doc, "impact", lambda v: v.update(group_ids=[]))
