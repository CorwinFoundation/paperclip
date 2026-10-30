"""Case-level exposure history over scoped scan evidence metadata.

Comparability concerns the observed test contract and declared target settings.
It never establishes unchanged native deployment controls or universal safety.
"""
from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import re

SCHEMA = "agent-exposure-history/v1"
PASS, FAIL = {"pass", "passed"}, {"fail", "failed"}
MAX_ENTRIES = 10000
CONTEXT_KEYS = ("target_fingerprint", "configuration_fingerprint")
# Existing shared scanner/listener/static taxonomy (docs/exposure-taxonomy-v0.1.md).
EXPOSURE_IDS = {
    "synthetic_workflow_revocation": "exposure.unapproved_consequential_action",
    "synthetic_approval_action_binding": "exposure.unapproved_consequential_action",
    "synthetic_memory_lifecycle": "exposure.scope_creep",
    "synthetic_delegation_containment": "exposure.unapproved_consequential_action",
    "synthetic_tool_definition_change": "exposure.off_policy_tool_call",
    "synthetic_auxiliary_disclosure": "exposure.secret_egress",
    "synthetic_workflow_budget": "exposure.off_policy_tool_call",
    "synthetic_privacy_differencing": "exposure.secret_egress",
    "synthetic_canary_disclosure": "exposure.secret_egress",
    "synthetic_cumulative_disclosure": "exposure.secret_egress",
    "consequential_action_confirmation": "exposure.unapproved_consequential_action",
    "revocation_execution_time_authorization": "exposure.unapproved_consequential_action",
}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _context(value):
    value = value if isinstance(value, Mapping) else {}
    return {k: value[k] for k in CONTEXT_KEYS if isinstance(value.get(k), str) and re.fullmatch(r"[0-9a-f]{64}", value[k])}


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.utcoffset() is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


def build_exposure_history(report, *, prior=None, context=None, subject_key=None):
    """Conservatively reconcile one scan with the last persisted projection.

    Callers bind subject_key to tenant and agent through trusted persistence.
    Missing identity/settings, conflicting outcomes, or changed test versions
    cannot clear an earlier exposure. No source report is mutated.
    """
    now, scan_id = report.get("evaluation_date"), report.get("scan_id")
    ctx = _context(context)
    prior = prior if isinstance(prior, Mapping) else {}
    prior_valid = (prior.get("schema") == SCHEMA and subject_key is not None
                   and prior.get("subject_key") == subject_key and _time(now) is not None
                   and _time(prior.get("evaluated_at")) is not None
                   and _time(prior["evaluated_at"]) < _time(now) and prior.get("scan_id") != scan_id)
    previous = {e["case_key"]: e for e in prior.get("entries", []) if isinstance(e, Mapping) and isinstance(e.get("case_key"), str)} if prior_valid else {}
    current = {}
    for observation in report.get("evidence_inventory", {}).get("artifacts", []):
        family, case = observation.get("test_id"), observation.get("case_id")
        if not isinstance(family, str) or not isinstance(case, str):
            continue
        current.setdefault(fingerprint([family, case]), []).append(observation)
    if len(set(previous) | set(current)) > MAX_ENTRIES:
        raise ValueError("exposure history exceeds case limit")
    entries = []
    for key in sorted(set(previous) | set(current)):
        old, observations = previous.get(key, {}), current.get(key, [])
        head = observations[0] if observations else old
        versions = {o.get("case_version") for o in observations}
        verdicts = {str(o.get("reported_verdict", "unresolved")).lower() for o in observations}
        failed = bool(verdicts & FAIL)
        if not old and not failed:
            continue  # Passing coverage remains in the evidence inventory.
        candidate = EXPOSURE_IDS.get(head.get("test_id"))
        failed_metrics = [o.get("diagnostic_measurements", {}) for o in observations
                          if str(o.get("reported_verdict", "")).lower() in FAIL]
        def observed_metric(name):
            return any(isinstance(m, Mapping) and type(m.get(name)) is int and m[name] > 0 for m in failed_metrics)
        taxonomy_id = ("exposure.secret_egress" if observed_metric("private_marker_leak_count") else
                       candidate if candidate != "exposure.secret_egress" and observed_metric("unsafe_attempt_count") else None)
        completed_pass = bool(verdicts) and verdicts <= PASS and all(o.get("status") in {"complete", "completed"} for o in observations)
        terminal_pass = completed_pass and len(versions) == 1 and None not in versions
        comparable = (bool(old) and len(ctx) == len(CONTEXT_KEYS) and old.get("comparison_context") == ctx
                      and len(versions) == 1 and old.get("case_version") in versions)
        open_before = old.get("exposure_open") is True
        state = "newly_observed" if failed else "no_exposure_observed" if terminal_pass else "unobserved"
        if failed and old.get("last_seen"):
            state = "persistent" if open_before else "reopened"
        elif open_before:
            state = "cleared_on_comparable_retest" if terminal_pass and comparable else "not_comparable" if completed_pass else "unobserved"
        entry = {"case_key": key, "test_id": head.get("test_id"), "case_id": head.get("case_id"),
                 "exposure_id": (taxonomy_id or (old.get("exposure_id") if open_before else None)) if failed else old.get("exposure_id"),
                 "candidate_exposure_id": candidate,
                 "classification_basis": "reported mechanism-specific diagnostic" if taxonomy_id else "family association only; failure mechanism requires review",
                 "state": state, "exposure_open": failed or (open_before and state != "cleared_on_comparable_retest"),
                 "first_seen": old.get("first_seen") or (now if failed else None),
                 "last_seen": now if failed else old.get("last_seen"),
                 "last_observed_at": now if observations else old.get("last_observed_at"),
                 "case_version": next(iter(versions)) if len(versions) == 1 and None not in versions else None,
                 "comparison_context": ctx if observations and (failed or terminal_pass) else deepcopy(old.get("comparison_context", {})),
                 "comparison_status": "comparable observed contract" if comparable else "insufficient or changed observed conditions",
                 "reported_verdicts": sorted(verdicts), "observation_count": len(observations),
                 "evidence": [{k: o[k] for k in ("artifact_uri", "sha256", "case_version") if k in o} for o in observations],
                 "evidence_basis": "reported scan metadata; source bytes not verified by history",
                 "native_effects": "not inferred from scanner sandbox effects"}
        # Preserve the unresolved exposure's original comparison conditions.
        if open_before and not failed and state != "cleared_on_comparable_retest":
            entry["case_version"] = old.get("case_version")
            entry["comparison_context"] = deepcopy(old.get("comparison_context", {}))
        events = deepcopy(old.get("events", []))[-19:]
        event = {"scan_id": scan_id, "observed_at": now, "state": state,
                 "observed_exposure_id": taxonomy_id,
                 "evidence": deepcopy(entry["evidence"])}
        events.append(event)
        entry["events"] = events
        entries.append(entry)
    return {"schema": SCHEMA, "scan_id": scan_id, "evaluated_at": now, "subject_key": subject_key,
            "prior_scan_id": prior.get("scan_id") if prior_valid else None, "comparison_context": ctx,
            "summary": dict(sorted(Counter(e["state"] for e in entries).items())), "entries": entries,
            "integrity_status": "reported scan metadata; original evidence requires verification",
            "limitations": ["A passing comparable retest means not reproduced under the observed conditions.",
                "Skipped, incomplete, conflicting or unsupported observations do not clear prior exposures.",
                "Configuration fingerprints describe supplied settings; hidden native changes remain unobserved.",
                "Events retain the latest twenty observations per case; first and last exposure times are preserved."]}


def history_from_registered_sources(sources, *, approved_scope):
    """Derive export history only from accepted, locally verified originals.

    sources are supplied by the approved-plan exporter after byte reconciliation.
    Each historical original must pass that same importer; this function never
    fetches references or accepts an embedded report's history as evidence.
    """
    groups = {}
    for original, ref in sources:
        capture = original.get("capture") or {}
        scope = capture.get("scope") or {}
        timestamp = capture.get("evaluated_at")
        if not _time(timestamp) or not all(isinstance(scope.get(k), str) and scope[k] for k in ("tenant_id", "agent_id", "scan_id")):
            continue
        subject = fingerprint([approved_scope, scope["tenant_id"], scope["agent_id"]])
        key = (subject, scope["scan_id"])
        group = groups.setdefault(key, {"evaluation_date": timestamp, "scan_id": scope["scan_id"],
                                      "evidence_inventory": {"artifacts": []}, "models": set()})
        if _time(timestamp) > _time(group["evaluation_date"]):
            group["evaluation_date"] = timestamp
        models = {t.get("model_id") for t in capture.get("trials", []) if isinstance(t.get("model_id"), str)}
        group["models"].update(models)
        group["evidence_inventory"]["artifacts"].append({"test_id": original["test_id"], "case_id": original["case_id"],
            "case_version": ref.get("probe_set_version"), "reported_verdict": original.get("verdict"),
            "status": "complete",  # The approved-plan importer accepted the terminal original.
            "diagnostic_measurements": capture.get("measurements", {}),
            "sha256": ref["source_registered_case"]["sha256"],
            "artifact_uri": "artifacts/" + ref["source_registered_case"]["sha256"] + ".json"})
    histories = {}
    for (subject, _), report in sorted(groups.items(), key=lambda item: (_time(item[1]["evaluation_date"]), item[0][1])):
        context = {"configuration_fingerprint": fingerprint(approved_scope)}
        if report["models"]:
            context["target_fingerprint"] = fingerprint(sorted(report["models"]))
        history = build_exposure_history(report, prior=histories.get(subject), context=context, subject_key=subject)
        history["integrity_status"] = "derived from accepted original bytes included in this pack; collection authenticity requires review"
        for entry in history["entries"]:
            entry["evidence_basis"] = "derived from locally verified, approved-plan-bound original captures"
        histories[subject] = history
    return {"schema": "assessor-exposure-history/v1", "scope": deepcopy(approved_scope),
            "subjects": list(histories.values()),
            "basis": "only accepted original captures packaged under the approved plan",
            "limitations": ["Historical scans must be explicitly bound and accepted by the same approved plan.",
                "This pack may contain only a subset of scan observations; omitted evidence never clears an exposure.",
                "Native configuration continuity is not established by declared snapshot/model identities."]}
