"""Default scan-report crosswalk; scan observations are not conformity findings."""
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from requirement_assessment import build_requirement_assessment, load_registry
from tools.assessment_test_catalog import RECORD_FAMILIES, LOG_CHALLENGE_FAMILY, SYNTHETIC_FAMILIES, SYNTHETIC_DIALOGUE_METRICS, SYNTHETIC_PRECISION_METRICS, SYNTHETIC_EXPOSURE_METRICS
from evals.oss_pilots.assessment_records import METRICS as RECORD_METRICS

DIAGNOSTIC_METRICS = frozenset({"expected_event_count", "captured_event_count", "identity_errors",
    "payload_errors", "timestamp_errors", "repeat_difference_count", "false_alert_count", "missing_alert_count"}) | RECORD_METRICS | SYNTHETIC_DIALOGUE_METRICS | SYNTHETIC_PRECISION_METRICS | SYNTHETIC_EXPOSURE_METRICS


def build_standard_requirement_report(
    score: Mapping[str, Any], *, manifest: Mapping[str, Any] | None = None,
    scan_id: str | None = None,
) -> dict[str, Any]:
    """Reuse the requirement registry without inventing an approved evidence plan.

    Normal reports expose a metadata crosswalk. A reported verdict/URI is never
    promoted to byte-verified evidence, an applicable control or certification.
    The existing approved-plan importer remains the assessment boundary.
    """
    registry = load_registry()
    report = build_requirement_assessment({}, registry=registry)
    # This crosswalk performs no evidence evaluation. Use the scan's timestamp
    # when supplied; repeated reads must not acquire a new assessment time.
    report["evaluation_date"] = score.get("run_timestamp") or score.get("scored_at")
    report["report_mode"] = "scan observation crosswalk"
    report["approval_identity_status"] = "no approved assessment plan supplied"
    report["scan_id"] = scan_id or score.get("scan_id")
    report["source_manifest_hash"] = manifest.get("manifest_hash") if isinstance(manifest, Mapping) else None
    sources = list(score.get("evidence_refs") or [])
    if isinstance(manifest, Mapping):
        sources.extend(manifest.get("evidence_rows") or [])
    observations = []
    seen = {}
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        family = source.get("test_id")
        case = source.get("case_id") or source.get("probe_id")
        if not isinstance(family, str) or not isinstance(case, str):
            continue
        source_scan = source.get("scan_id")
        if source_scan and report["scan_id"] and source_scan != report["scan_id"]:
            continue
        uri = source.get("artifact_uri") or source.get("evidence_ref")
        verdict = source.get("verdict") or "unresolved"
        identity = (family, case, str(uri), str(verdict))
        # Public report metadata only: no trace, prompt, response, auth or log data.
        observation = {"test_id": family, "case_id": case,
                       "reported_verdict": str(verdict),
                       "evidence_integrity": "not verified in this report",
                       "score_contribution": source.get("score_contribution") is True}
        for key, value in (("artifact_uri", uri), ("case_version", source.get("case_version")),
                           ("sha256", source.get("sha256")), ("status", source.get("status"))):
            if isinstance(value, str):
                if key == "sha256" and not re.fullmatch(r"[0-9a-fA-F]{64}", value):
                    observation["digest_status"] = "invalid reported digest"
                else:
                    observation[key] = value
        if family in SYNTHETIC_FAMILIES:
            observation["observation_scope"] = "actual target replies and inert tools on generated synthetic tasks; native production controls and workload accuracy not evaluated"
        elif family == "operational_oversight":
            observation["observation_scope"] = "synthetic scanner-mediated tools; native production controls not evaluated"
        elif family in {"operational_logging", "operational_logging_quality", LOG_CHALLENGE_FAMILY}:
            observation["observation_scope"] = "scoped synthetic diagnostic log path; retention and production coverage not evaluated"
        elif family in RECORD_FAMILIES:
            observation["observation_scope"] = "consistency of supplied reviewed records; authenticity, completeness and adequacy require assessor review"
        for key in ("target_calls", "judge_calls", "duration_ms"):
            if type(source.get(key)) is int and source[key] >= 0:
                observation[key] = source[key]
        metrics = source.get("diagnostic_measurements")
        if isinstance(metrics, Mapping):
            observation["diagnostic_measurements"] = {k: v for k, v in metrics.items()
                if k in DIAGNOSTIC_METRICS and type(v) is int and 0 <= v <= 10**9}
        method = registry.get("test_catalog", {}).get(family, {}).get("case_methods", {}).get(case)
        if isinstance(method, str):
            observation["method"] = method
        if identity in seen:
            # A score projection can omit the version/status retained by the
            # full manifest. Keep one acquisition and fill missing metadata.
            seen[identity].update({k: v for k, v in observation.items() if k not in seen[identity]})
            continue
        seen[identity] = observation
        observations.append(observation)
    for framework in report["frameworks"]:
        for control in framework["controls"]:
            candidates = set(control["candidate_test_ids"])
            matches = [dict(row) for row in observations if row["test_id"] in candidates]
            control["scan_observations"] = matches
            legacy = set(control.get("legacy_candidate_test_ids", []))
            control["unobserved_test_ids"] = sorted(candidates - legacy - {row["test_id"] for row in matches})
            control["unresolved_mapping_concepts"] = sorted(legacy)
            control["catalog_case_gaps"] = [
                {"test_id": family, "case_id": case}
                for family in sorted(candidates)
                for case in registry.get("test_catalog", {}).get(family, {}).get("case_ids", [])
                if not any(row["test_id"] == family and row["case_id"] == case for row in matches)
            ]
            control["nonterminal_test_ids"] = sorted({
                row["test_id"] for row in matches
                if row["reported_verdict"].lower() not in {"pass", "passed", "fail", "failed"}
            })
            control["documents_requiring_review"] = list(control["required_document_ids"])
            control["scan_observation_status"] = "observations available" if matches else "no scan observations"
        framework["observed_control_count"] = sum(bool(c["scan_observations"]) for c in framework["controls"])
        framework["control_count"] = len(framework["controls"])
    report["limitations"].append(
        "Reported scan verdicts are metadata, not approved-plan or byte-verified assessments. "
        "Unobserved tests may be outside the selected pack or unsupported by the target interface."
    )
    documents = {}
    for framework in report["frameworks"]:
        for control in framework["controls"]:
            for document_id in control["required_document_ids"]:
                documents.setdefault(document_id, []).append({"framework_id": framework["framework_id"],
                                                             "control_id": control["control_id"]})
    report["evidence_inventory"] = {
        "schema": "scan-evidence-inventory/v1", "integrity_status": "reported metadata; bytes not verified",
        "reported_case_count": len({(o["test_id"], o["case_id"]) for o in observations}),
        "reported_observation_count": len(observations), "artifacts": observations,
        "with_artifact_reference": sum(bool(o.get("artifact_uri")) for o in observations),
        "with_reported_digest": sum(bool(o.get("sha256")) for o in observations),
        "missing_artifact_references": [{"test_id": o["test_id"], "case_id": o["case_id"]}
                                        for o in observations if not o.get("artifact_uri")],
        "document_review_register": [{"document_id": key, "supports": value,
            "review_status": "not supplied or reviewed by this crosswalk", "applicability": "assessor review required"}
            for key, value in sorted(documents.items())],
        "assessment_export": "Operator export with an approved plan and locally verified original artifacts is required for an assessor evidence pack.",
    }
    from exposure_history import build_exposure_history
    report["exposure_history"] = build_exposure_history(
        report, prior=score.get("_exposure_history_prior"), context=score.get("exposure_comparison_context"),
        subject_key=score.get("exposure_subject_key"),
    )
    return report
