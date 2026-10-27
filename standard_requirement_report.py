"""Default scan-report crosswalk; scan observations are not conformity findings."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from requirement_assessment import build_requirement_assessment, load_registry


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
                observation[key] = value
        if family == "operational_oversight":
            observation["observation_scope"] = "synthetic scanner-mediated tools; native production controls not evaluated"
        elif family == "operational_logging":
            observation["observation_scope"] = "scoped synthetic diagnostic log path; retention and production coverage not evaluated"
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
            control["unobserved_test_ids"] = sorted(candidates - {row["test_id"] for row in matches})
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
    return report
